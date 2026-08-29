"""Turns an EVE nebula cube into a world environment Blender can load.

Ported from `scripts/prepare_environment.mjs`, which did the same job through
Node and the runtime's DDS reader. This add-on is standalone, so it does it
here instead.

Three things have to happen to a nebula before Blender will take it:

* it is BC6H, an HDR block format Blender does not read -- `bc6h.py` decodes it;
* it is a CUBE, and the Environment Texture node wants equirectangular;
* it is FLOAT, and the values run well above one. A PNG would clip exactly the
  bright detail an environment map exists to provide, so it is written as
  Radiance `.hdr`, which is the simplest format that keeps it.

There is no `bpy` in here on purpose. The decode is the expensive part and this
runs in the fetch pool's child process, the same way the BC7 textures do, so
the window keeps drawing while it works.
"""

from __future__ import annotations

import math
from array import array
import struct

from . import bc6h


#: DDSCAPS2_CUBEMAP and the six face flags.
DDSCAPS2_CUBEMAP = 0x200
DDSCAPS2_CUBEMAP_FACES = 0xFC00

#: How wide one cube face is rebuilt, in pixels. 0 means the cube's own size.
#:
#: Every block is decoded either way -- a face is only whole if all of it is
#: read -- so this reduces AFTER decoding, by averaging, which costs almost
#: nothing next to the decode and does not alias. Subsampling blocks instead
#: was four times cheaper and looked it: the sky came out visibly soft, which
#: is what "low res" meant.
FACE_SIZE = 0

#: How wide the equirectangular image is. 0 means twice the face, which is
#: half the cube's own detail spread over the full turn -- a fair trade for a
#: file a quarter the size, on something that is always out of focus behind
#: the ship.
EQUIRECT_WIDTH = 0

#: DDS cube face order, which is also the order `sample_cube` indexes.
FACE_ORDER = ("+x", "-x", "+y", "-y", "+z", "-z")


class CubeError(RuntimeError):
    """Raised when a DDS is not a nebula cube this can convert."""


def _block_bytes(width: int, height: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16


def inspect(data: bytes) -> dict:
    """`width`, `height`, `faces`, `dxgi`, `offset` for a cube DDS."""

    from .reader import header_of

    header = header_of(data)
    if not header.caps2 & DDSCAPS2_CUBEMAP:
        raise CubeError("not a cube map")
    if header.caps2 & DDSCAPS2_CUBEMAP_FACES != DDSCAPS2_CUBEMAP_FACES:
        raise CubeError("cube map is missing faces")
    if not bc6h.is_bc6h(header.dxgi_format):
        raise CubeError(f"unsupported cube format: DXGI {header.dxgi_format}")
    return {
        "width": header.width,
        "height": header.height,
        "faces": 6,
        "dxgi": header.dxgi_format,
        "offset": header.data_offset,
        "face_bytes": _block_bytes(header.width, header.height),
    }


def decode_face(data: bytes, info: dict, index: int, size: int = FACE_SIZE):
    """One cube face as `size` x `size` RGB floats, row-major.

    EVERY block is decoded and every texel it carries is kept: a face is only
    whole if all of it is read, and the 16 texels in a block come out of the
    same arithmetic whether they are used or thrown away. Reducing afterwards,
    by averaging, is a proper box filter and costs almost nothing beside the
    decode.

    Returned as a flat float sequence with its width, so the caller need not
    know whether numpy was available.
    """

    width = info["width"]
    blocks = max(1, width // 4)
    signed = info["dxgi"] == bc6h.DXGI_BC6H_SF16
    base = info["offset"] + index * info["face_bytes"]

    decode = bc6h.decode_block
    pixels = array("f", bytes(4 * width * width * 3))
    stride = width * 3

    for by in range(blocks):
        top = by * 4
        for bx in range(blocks):
            offset = base + (by * blocks + bx) * 16
            block = decode(data[offset:offset + 16], signed)
            left = bx * 12                       # 4 texels, 3 channels each
            for y in range(4):
                at = (top + y) * stride + left
                source = y * 16
                pixels[at:at + 3] = array("f", block[source:source + 3])
                pixels[at + 3:at + 6] = array("f", block[source + 4:source + 7])
                pixels[at + 6:at + 9] = array("f", block[source + 8:source + 11])
                pixels[at + 9:at + 12] = array("f", block[source + 12:source + 15])

    if size and size < width:
        pixels = _box_filter(pixels, width, size)
        width = size
    return pixels, width


def _box_filter(pixels, width: int, size: int):
    """Averages a square RGB image down to `size`, by whole pixel blocks."""

    step = max(1, width // size)
    size = width // step
    try:
        import numpy

        grid = numpy.asarray(pixels, dtype=numpy.float32)
        grid = grid.reshape(width, width, 3)[:size * step, :size * step]
        grid = grid.reshape(size, step, size, step, 3).mean(axis=(1, 3))
        return array("f", grid.astype(numpy.float32).ravel().tobytes())
    except ImportError:                  # pragma: no cover - numpy ships with Blender
        out = array("f", bytes(4 * size * size * 3))
        weight = float(step * step)
        for y in range(size):
            for x in range(size):
                red = green = blue = 0.0
                for sy in range(step):
                    row = (y * step + sy) * width * 3
                    for sx in range(step):
                        at = row + (x * step + sx) * 3
                        red += pixels[at]
                        green += pixels[at + 1]
                        blue += pixels[at + 2]
                at = (y * size + x) * 3
                out[at] = red / weight
                out[at + 1] = green / weight
                out[at + 2] = blue / weight
        return out


def sample_cube(faces, x: float, y: float, z: float):
    """The colour a direction sees. DDS face order is +x, -x, +y, -y, +z, -z."""

    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        major = ax
        if x > 0:
            index, u, v = 0, -z, -y
        else:
            index, u, v = 1, z, -y
    elif ay >= az:
        major = ay
        if y > 0:
            index, u, v = 2, x, z
        else:
            index, u, v = 3, x, -z
    else:
        major = az
        if z > 0:
            index, u, v = 4, x, -y
        else:
            index, u, v = 5, -x, -y

    pixels, size = faces[index]
    if major <= 0.0:
        return 0.0, 0.0, 0.0
    fx = min(size - 1, max(0, int((u / major * 0.5 + 0.5) * size)))
    fy = min(size - 1, max(0, int((v / major * 0.5 + 0.5) * size)))
    at = (fy * size + fx) * 3
    return pixels[at], pixels[at + 1], pixels[at + 2]


def eve_direction(x: float, y: float, z: float):
    """A Blender direction (Z-up) as an EVE one (Y-up).

    A quarter turn about X and nothing else, so its determinant is +1. The
    obvious-looking alternatives -- negating a pair of axes to make the poles
    come out right -- are the same turn with a MIRROR in it, and a mirrored sky
    is wrong however plausible it looks: the nebula reads backwards and no
    amount of rotating in the scene will put it right.
    """

    return x, z, -y


def to_equirectangular(faces, width: int):
    """The six faces resampled onto a sphere, as `width` x `width // 2` RGB.

    Blender's Environment Texture node reads an equirectangular image with the
    FIRST scanline at the zenith and +Z up; EVE is Y-up. So a pixel's Blender
    direction is built first, then rotated into EVE's axes:

        EVE = (x, z, -y)

    which is a quarter turn about X and nothing else. That matters: the
    obvious-looking `(-x, -z, y)` is the same turn with a mirror in it, and a
    mirrored sky is wrong however plausible it looks -- the nebula reads
    backwards and no amount of rotating in the scene will put it right. The
    determinant is the tell, +1 against -1, and it was -1 here.

    Which way the sky FACES is genuinely a scene decision, and stays one: it
    is a turn about the vertical, adjustable with a Mapping node.
    """

    height = width // 2
    try:
        return _equirectangular_fast(faces, width, height)
    except ImportError:                  # pragma: no cover - numpy ships with Blender
        pass

    out = array("f", bytes(4 * width * height * 3))
    for py in range(height):
        # First row is the zenith, which is what the Radiance `-Y` header
        # declares and what Blender then expects.
        elevation = (0.5 - (py + 0.5) / height) * math.pi
        up, radius = math.sin(elevation), math.cos(elevation)
        for px in range(width):
            phi = ((px + 0.5) / width - 0.5) * 2.0 * math.pi
            east, north = radius * math.cos(phi), radius * math.sin(phi)
            red, green, blue = sample_cube(
                faces, *eve_direction(east, north, up))
            at = (py * width + px) * 3
            out[at] = red
            out[at + 1] = green
            out[at + 2] = blue

    return out, width, height


def _equirectangular_fast(faces, width: int, height: int):
    """The same sphere, done for every pixel at once.

    Four million directions is a minute of scalar Python and a moment of
    numpy. The face selection is the same rule `sample_cube` states -- largest
    component wins, and its sign picks the face -- written as masks so the
    whole image goes through it together.
    """

    import numpy

    elevation = (0.5 - (numpy.arange(height, dtype=numpy.float64) + 0.5)
                 / height) * numpy.pi
    phi = ((numpy.arange(width, dtype=numpy.float64) + 0.5) / width
           - 0.5) * 2.0 * numpy.pi
    up = numpy.sin(elevation)[:, None] * numpy.ones(width)
    radius = numpy.cos(elevation)[:, None]
    east = radius * numpy.cos(phi)[None, :]
    north = radius * numpy.sin(phi)[None, :]

    x, y, z = eve_direction(east, north, up)
    ax, ay, az = numpy.abs(x), numpy.abs(y), numpy.abs(z)

    out = numpy.zeros((height, width, 3), dtype=numpy.float32)
    on_x = (ax >= ay) & (ax >= az)
    on_y = ~on_x & (ay >= az)
    on_z = ~on_x & ~on_y

    # (mask, face, major, u, v) -- exactly the branches sample_cube takes.
    for mask, index, major, u, v in (
            (on_x & (x > 0), 0, ax, -z, -y),
            (on_x & (x <= 0), 1, ax, z, -y),
            (on_y & (y > 0), 2, ay, x, z),
            (on_y & (y <= 0), 3, ay, x, -z),
            (on_z & (z > 0), 4, az, x, -y),
            (on_z & (z <= 0), 5, az, -x, -y)):
        if not mask.any():
            continue
        pixels, size = faces[index]
        # asarray, not frombuffer: a caller may hand in a plain list.
        face = numpy.asarray(pixels, dtype=numpy.float32).reshape(size, size, 3)
        scale = numpy.where(major[mask] > 0, major[mask], 1.0)
        fx = numpy.clip(((u[mask] / scale) * 0.5 + 0.5) * size, 0,
                        size - 1).astype(numpy.int32)
        fy = numpy.clip(((v[mask] / scale) * 0.5 + 0.5) * size, 0,
                        size - 1).astype(numpy.int32)
        out[mask] = face[fy, fx]

    return array("f", out.ravel().tobytes()), width, height


def encode_radiance(pixels, width: int, height: int) -> bytes:
    """Float RGB as a Radiance `.hdr`: one shared exponent per pixel.

    Written flat rather than run-length encoded, which the format allows and
    every reader accepts. Blender loads it as HDR and the values above one
    survive, which is the entire reason for not writing a PNG.
    """

    header = (b"#?RADIANCE\n"
              b"FORMAT=32-bit_rle_rgbe\n\n"
              + f"-Y {height} +X {width}\n".encode("ascii"))

    try:
        return bytes(header) + _radiance_body(pixels, width, height)
    except ImportError:                  # pragma: no cover - numpy ships with Blender
        pass

    body = bytearray(width * height * 4)
    for index in range(width * height):
        at = index * 3
        red, green, blue = pixels[at], pixels[at + 1], pixels[at + 2]
        peak = max(red, green, blue)
        out = index * 4
        if not peak > 1e-32:
            continue                     # already zero, and RGBE zero is 0,0,0,0
        exponent = math.ceil(math.log2(peak))
        scale = 256.0 / (2.0 ** exponent)
        body[out] = min(255, max(0, int(red * scale)))
        body[out + 1] = min(255, max(0, int(green * scale)))
        body[out + 2] = min(255, max(0, int(blue * scale)))
        body[out + 3] = exponent + 128

    return bytes(header) + bytes(body)


def _radiance_body(pixels, width: int, height: int) -> bytes:
    """The same RGBE, for every pixel at once."""

    import numpy

    grid = numpy.asarray(pixels, dtype=numpy.float32).reshape(-1, 3)
    grid = grid.astype(numpy.float64)
    peak = grid.max(axis=1)
    lit = peak > 1e-32

    body = numpy.zeros((grid.shape[0], 4), dtype=numpy.uint8)
    if lit.any():
        exponent = numpy.ceil(numpy.log2(peak[lit]))
        scale = 256.0 / numpy.power(2.0, exponent)
        body[lit, :3] = numpy.clip(
            numpy.floor(grid[lit] * scale[:, None]), 0, 255).astype(numpy.uint8)
        body[lit, 3] = (exponent + 128).astype(numpy.uint8)
    return body.tobytes()


def convert(data: bytes, face_size: int = FACE_SIZE,
            width: int = EQUIRECT_WIDTH, progress=None) -> bytes:
    """A cube DDS in, a Radiance `.hdr` out.

    `progress` is called with a line per face, because this is the slow part
    and a job that says nothing for a minute reads as a job that has hung.
    """

    return _convert(data, face_size, width, progress)[0]


def _convert(data: bytes, face_size: int, width: int, progress):
    """`(hdr bytes, (direction, colour))`, so the sphere is built once."""

    info = inspect(data)
    faces = []
    for index in range(6):
        if progress is not None:
            progress(f"Decoding nebula face {index + 1} of 6")
        faces.append(decode_face(data, info, index, face_size))

    if progress is not None:
        progress("Wrapping the nebula onto a sphere")
    width = width or faces[0][1] * 2
    pixels, width, height = to_equirectangular(faces, width)
    return (encode_radiance(pixels, width, height),
            brightest_direction(pixels, width, height))


def brightest_direction(pixels, width: int, height: int):
    """Where the sky is brightest, as a Blender direction, with its colour.

    The nebula file names an intensity and an ambient colour but NO sun
    direction, so there is nothing to read: EVE's sun is a scene property, not
    something the nebula carries. What the nebula does carry is a picture of
    where its light comes from, and the brightest part of it is the obvious
    answer -- a key light that disagrees with its own background is the one
    thing everybody notices.

    Returned as `((x, y, z), (r, g, b))`, the direction the light TRAVELS,
    which is the negation of where the bright part is.
    """

    try:
        import numpy
    except ImportError:                  # pragma: no cover - numpy ships with Blender
        return (0.0, 0.0, -1.0), (1.0, 1.0, 1.0)

    grid = numpy.asarray(pixels, dtype=numpy.float32).reshape(height, width, 3)
    # Luminance, not the sum: a bright blue patch and a bright grey one are
    # not equally the sun.
    luminance = (grid[:, :, 0] * 0.2126 + grid[:, :, 1] * 0.7152
                 + grid[:, :, 2] * 0.0722)

    # Softened first, so one hot pixel does not decide where the sun is. The
    # sun is a REGION of the sky, and on a 4096-wide image a 33-pixel box is
    # about three degrees.
    box = max(1, width // 128)
    if box > 1:
        trimmed = luminance[:height - height % box, :width - width % box]
        pooled = trimmed.reshape(trimmed.shape[0] // box, box,
                                 trimmed.shape[1] // box, box).mean(axis=(1, 3))
        py, px = numpy.unravel_index(int(pooled.argmax()), pooled.shape)
        py, px = py * box + box // 2, px * box + box // 2
    else:
        py, px = numpy.unravel_index(int(luminance.argmax()), luminance.shape)

    elevation = (0.5 - (py + 0.5) / height) * math.pi
    phi = ((px + 0.5) / width - 0.5) * 2.0 * math.pi
    radius = math.cos(elevation)
    towards = (radius * math.cos(phi), radius * math.sin(phi),
               math.sin(elevation))

    patch = grid[max(0, py - box):py + box + 1, max(0, px - box):px + box + 1]
    colour = patch.reshape(-1, 3).mean(axis=0)
    peak = float(colour.max()) or 1.0
    return (-towards[0], -towards[1], -towards[2]), tuple(
        float(channel / peak) for channel in colour)


def convert_file(source, destination, face_size: int = FACE_SIZE,
                 width: int = EQUIRECT_WIDTH, progress=None):
    """Converts one cube on disk, skipping the work when it is already done.

    Writes a `.sun` beside the image holding the direction and colour the sky
    itself implies. It is a sidecar rather than a return value because a cache
    HIT skips the conversion entirely, and the sun is still needed then.
    """

    from pathlib import Path

    source, destination = Path(source), Path(destination)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    made, (direction, colour) = _convert(source.read_bytes(), face_size,
                                         width, progress)
    destination.write_bytes(made)
    sun_path(destination).write_text(
        " ".join(f"{value:.6f}" for value in tuple(direction) + tuple(colour)),
        "utf-8")
    return destination


def sun_path(destination):
    """Where the sun sidecar for one environment lives."""

    from pathlib import Path

    return Path(destination).with_suffix(".sun")


def read_sun(destination):
    """`(direction, colour)` from the sidecar, or None when there is none."""

    try:
        parts = [float(value) for value
                 in sun_path(destination).read_text("utf-8").split()]
    except (OSError, ValueError):
        return None
    if len(parts) != 6:
        return None
    return tuple(parts[:3]), tuple(parts[3:])
