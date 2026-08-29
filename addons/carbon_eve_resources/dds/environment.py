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
import struct

from . import bc6h


#: DDSCAPS2_CUBEMAP and the six face flags.
DDSCAPS2_CUBEMAP = 0x200
DDSCAPS2_CUBEMAP_FACES = 0xFC00

#: How wide one cube face is rebuilt, in pixels.
#:
#: A face spans 90 degrees, so 256 across is about 1024 pixels for the full
#: turn -- generous for something that is only ever the backdrop. The cubes
#: are 2048 square with no mip chain, and decoding all six in full is over a
#: million blocks of pure Python; this is the difference between a nebula
#: arriving and a nebula being abandoned.
FACE_SIZE = 256

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
    """One cube face as `size` x `size` RGB floats.

    Reduced while decoding rather than after. Each 4x4 block averages to one
    output pixel and only every `step`-th block is touched, so the cost falls
    with the square of the reduction instead of being paid in full and thrown
    away. That samples a fraction of the area rather than filtering all of it,
    which on a nebula -- soft, low-frequency cloud -- is a difference nobody
    can see, and on a sharp texture would be aliasing.
    """

    blocks = max(1, info["width"] // 4)
    size = min(size, blocks)
    step = max(1, blocks // size)
    size = blocks // step

    signed = info["dxgi"] == bc6h.DXGI_BC6H_SF16
    base = info["offset"] + index * info["face_bytes"]
    pixels = [0.0] * (size * size * 3)

    for y in range(size):
        row = (y * step) * blocks
        for x in range(size):
            offset = base + (row + x * step) * 16
            block = bc6h.decode_block(data[offset:offset + 16], signed)
            red = green = blue = 0.0
            for texel in range(16):
                at = texel * 4
                red += block[at]
                green += block[at + 1]
                blue += block[at + 2]
            out = (y * size + x) * 3
            pixels[out] = red / 16.0
            pixels[out + 1] = green / 16.0
            pixels[out + 2] = blue / 16.0

    return pixels, size


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
    out = [0.0] * (width * height * 3)

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


def encode_radiance(pixels, width: int, height: int) -> bytes:
    """Float RGB as a Radiance `.hdr`: one shared exponent per pixel.

    Written flat rather than run-length encoded, which the format allows and
    every reader accepts. Blender loads it as HDR and the values above one
    survive, which is the entire reason for not writing a PNG.
    """

    header = (b"#?RADIANCE\n"
              b"FORMAT=32-bit_rle_rgbe\n\n"
              + f"-Y {height} +X {width}\n".encode("ascii"))
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


def convert(data: bytes, face_size: int = FACE_SIZE, width: int = 0) -> bytes:
    """A cube DDS in, a Radiance `.hdr` out.

    The default output is four times the face width, which is what a 90 degree
    face is worth spread over the full turn -- more would be interpolation
    rather than detail.
    """

    info = inspect(data)
    faces = [decode_face(data, info, index, face_size) for index in range(6)]
    width = width or faces[0][1] * 4
    pixels, width, height = to_equirectangular(faces, width)
    return encode_radiance(pixels, width, height)


def convert_file(source, destination, face_size: int = FACE_SIZE,
                 width: int = 0):
    """Converts one cube on disk, skipping the work when it is already done."""

    from pathlib import Path

    source, destination = Path(source), Path(destination)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(convert(source.read_bytes(), face_size, width))
    return destination
