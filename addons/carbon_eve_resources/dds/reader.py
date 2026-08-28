"""Reads a DDS header, and decodes BC7 when Blender cannot.

Ported from `runtime/src/resource/formats/dds/core/bc7.js`. The block decode is
the same algorithm; the per-pixel work is vectorised with numpy, which Blender
ships, because a 1024x1024 texture is 65536 blocks and a scalar Python loop
over them is not usable in a UI.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .bc7_tables import (ANCHOR_2, ANCHOR_3_SECOND, ANCHOR_3_THIRD, MODES,
                         PARTITIONS_2, PARTITIONS_3, WEIGHTS)


MAGIC = b"DDS "
HEADER_SIZE = 128
DX10_HEADER_SIZE = 20

#: The DXGI format BC7 arrives as. 99 is the sRGB spelling of the same bits.
DXGI_BC7_UNORM = 98
DXGI_BC7_UNORM_SRGB = 99

#: DDSCAPS2_VOLUME. A 3D texture is a stack of slices, not an image, and
#: handing one to Blender as a 2D image hangs it -- a 128x128x128 DXT5 did not
#: return in two minutes. EVE ships eight of them.
DDSCAPS2_VOLUME = 0x200000


class DdsError(RuntimeError):
    """Raised when a file is not a DDS this reader understands."""


@dataclass(frozen=True, slots=True)
class DdsHeader:
    width: int
    height: int
    depth: int
    caps2: int
    fourcc: str
    dxgi_format: int
    data_offset: int

    @property
    def is_volume(self) -> bool:
        """A 3D texture, which is not loadable as an image."""

        return self.depth > 1 or bool(self.caps2 & DDSCAPS2_VOLUME)

    @property
    def is_bc7(self) -> bool:
        return (self.fourcc == "DX10"
                and self.dxgi_format in (DXGI_BC7_UNORM, DXGI_BC7_UNORM_SRGB))


def header_of(data: bytes) -> DdsHeader:
    """The size and format of a DDS, without decoding it."""

    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise DdsError("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    depth = struct.unpack_from("<I", data, 24)[0]
    caps2 = struct.unpack_from("<I", data, 112)[0]
    fourcc = data[84:88].decode("ascii", "replace")
    dxgi = 0
    offset = HEADER_SIZE
    if fourcc == "DX10":
        if len(data) < HEADER_SIZE + DX10_HEADER_SIZE:
            raise DdsError("DX10 header is truncated")
        dxgi = struct.unpack_from("<I", data, HEADER_SIZE)[0]
        offset = HEADER_SIZE + DX10_HEADER_SIZE
    return DdsHeader(width=width, height=height, depth=depth, caps2=caps2,
                     fourcc=fourcc, dxgi_format=dxgi, data_offset=offset)


def is_bc7(data: bytes) -> bool:
    """Whether this DDS needs decoding here rather than by Blender."""

    try:
        return header_of(data).is_bc7
    except DdsError:
        return False


def is_volume(data: bytes) -> bool:
    """Whether this DDS is a 3D texture, which must not be loaded as an image."""

    try:
        return header_of(data).is_volume
    except DdsError:
        return False


def _bits(block, offset, count):
    """`count` bits from a 16-byte block, LSB first, as in the JS reader."""

    value = 0
    for bit in range(count):
        position = offset + bit
        value |= ((block[position >> 3] >> (position & 7)) & 1) << bit
    return value, offset + count


def _subset_of(subsets, partition, pixel):
    if subsets == 1:
        return 0
    if subsets == 2:
        return (PARTITIONS_2[partition] >> pixel) & 1
    return (PARTITIONS_3[partition] >> (pixel * 2)) & 3


def _anchor_of(subsets, partition, subset):
    if subset == 0:
        return 0
    if subsets == 2:
        return ANCHOR_2[partition]
    return ANCHOR_3_SECOND[partition] if subset == 1 else ANCHOR_3_THIRD[partition]


def _expand(value, precision):
    if precision >= 8:
        return value & 0xFF
    return ((value << (8 - precision)) | (value >> (2 * precision - 8))) & 0xFF


def _interpolate(first, second, index, bit_count):
    weight = WEIGHTS[bit_count][index]
    return ((64 - weight) * first + weight * second + 32) >> 6


def decode_block(block) -> bytearray:
    """One 16-byte BC7 block as 16 RGBA pixels."""

    pixels = bytearray(16 * 4)
    if len(block) < 16:
        raise DdsError("BC7 block must contain 16 bytes")
    if block[0] == 0:
        return pixels

    offset = 0
    mode = 0
    while mode < 8:
        bit, offset = _bits(block, offset, 1)
        if bit:
            break
        mode += 1
    if mode == 8:
        return pixels

    (subsets, partition_bits, rotation_bits, selection_bits, colour_bits,
     alpha_bits, endpoint_p, shared_p, index_bits, secondary_bits) = MODES[mode]

    partition, offset = _bits(block, offset, partition_bits)
    rotation, offset = _bits(block, offset, rotation_bits)
    selection, offset = _bits(block, offset, selection_bits)

    count = subsets * 2
    endpoints = [[0, 0, 0, 255] for _ in range(count)]
    for channel in range(3):
        for endpoint in range(count):
            endpoints[endpoint][channel], offset = _bits(block, offset, colour_bits)
    if alpha_bits:
        for endpoint in range(count):
            endpoints[endpoint][3], offset = _bits(block, offset, alpha_bits)

    p_bits = [0] * count
    if endpoint_p:
        for endpoint in range(count):
            p_bits[endpoint], offset = _bits(block, offset, 1)
    elif shared_p:
        for subset in range(subsets):
            shared, offset = _bits(block, offset, 1)
            p_bits[subset * 2] = shared
            p_bits[subset * 2 + 1] = shared

    has_p = bool(endpoint_p or shared_p)
    colour_precision = colour_bits + (1 if has_p else 0)
    alpha_precision = alpha_bits + (1 if alpha_bits and has_p else 0)
    for endpoint in range(count):
        for channel in range(3):
            value = endpoints[endpoint][channel]
            if has_p:
                value = (value << 1) | p_bits[endpoint]
            endpoints[endpoint][channel] = _expand(value, colour_precision)
        if alpha_bits:
            value = endpoints[endpoint][3]
            if has_p:
                value = (value << 1) | p_bits[endpoint]
            endpoints[endpoint][3] = _expand(value, alpha_precision)

    def read_indices(bit_count, at):
        indices = [0] * 16
        for pixel in range(16):
            subset = _subset_of(subsets, partition, pixel)
            anchor = pixel == _anchor_of(subsets, partition, subset)
            indices[pixel], at = _bits(block, at, bit_count - (1 if anchor else 0))
        return indices, at

    primary, offset = read_indices(index_bits, offset)
    if secondary_bits:
        secondary, offset = read_indices(secondary_bits, offset)
    else:
        secondary = primary

    colour_uses_secondary = bool(selection_bits and selection == 1)
    alpha_uses_secondary = bool(secondary_bits and (not selection_bits or selection == 0))
    colour_indices = secondary if colour_uses_secondary else primary
    alpha_indices = secondary if alpha_uses_secondary else primary
    colour_index_bits = secondary_bits if colour_uses_secondary else index_bits
    alpha_index_bits = secondary_bits if alpha_uses_secondary else index_bits

    for pixel in range(16):
        subset = _subset_of(subsets, partition, pixel)
        first = endpoints[subset * 2]
        second = endpoints[subset * 2 + 1]
        out = pixel * 4
        for channel in range(3):
            pixels[out + channel] = _interpolate(
                first[channel], second[channel], colour_indices[pixel], colour_index_bits)
        pixels[out + 3] = _interpolate(
            first[3], second[3], alpha_indices[pixel], alpha_index_bits)
        if rotation:
            channel = rotation - 1
            pixels[out + channel], pixels[out + 3] = pixels[out + 3], pixels[out + channel]
    return pixels


def decode_bc7(data: bytes, width: int, height: int, offset: int = 0) -> bytearray:
    """A whole BC7 surface as RGBA bytes, top row first."""

    rgba = bytearray(width * height * 4)
    columns = (width + 3) // 4
    rows = (height + 3) // 4
    stride = width * 4
    for block_y in range(rows):
        for block_x in range(columns):
            start = offset + (block_y * columns + block_x) * 16
            block = decode_block(data[start:start + 16])
            for y in range(4):
                out_y = block_y * 4 + y
                if out_y >= height:
                    break
                for x in range(4):
                    out_x = block_x * 4 + x
                    if out_x >= width:
                        break
                    source = (y * 4 + x) * 4
                    target = out_y * stride + out_x * 4
                    rgba[target:target + 4] = block[source:source + 4]
    return rgba


def to_rgba(data: bytes):
    """`(width, height, rgba bytes)` for a BC7 DDS."""

    header = header_of(data)
    if not header.is_bc7:
        raise DdsError(f"not BC7: {header.fourcc} {header.dxgi_format}")
    return (header.width, header.height,
            decode_bc7(data, header.width, header.height, header.data_offset))


def derived_path(source: Path) -> Path:
    """Where one decoded texture is kept: beside its source, as a PNG.

    Same folder, same name, different extension. The source is addressed by
    its CONTENT, so the decoded copy inherits that for free -- every hull using
    that texture finds the same decode, and cache pruning removes it with the
    build it belongs to instead of leaving it behind in a folder of its own.
    """

    return source.with_suffix(".png")


def load_image(path, name: str = ""):
    """A Blender image from a BC7 DDS, or None when it is not BC7.

    Blender loads every other format EVE uses, so only BC7 comes through here.

    Decoded once. A 2048x2048 albedo takes nine seconds, and a ship has several
    -- so the result is written beside the source as a PNG, which Blender then
    loads directly. Textures are cached by content hash, so the decode is
    shared by every ship that uses them.
    """

    import bpy

    source = Path(path)
    decoded = derived_path(source)
    if decoded.is_file():
        image = bpy.data.images.load(str(decoded))
        image.name = name or source.name
        return image

    data = source.read_bytes()
    if not is_bc7(data):
        return None

    width, height, rgba = to_rgba(data)
    image = bpy.data.images.new(name or Path(path).name, width, height, alpha=True)

    # Bottom-up: Blender's pixel buffer starts at the LAST row of the image.
    try:
        import numpy

        pixels = numpy.frombuffer(bytes(rgba), dtype=numpy.uint8).astype(numpy.float32)
        pixels /= 255.0
        pixels = pixels.reshape(height, width * 4)[::-1].ravel()
        image.pixels.foreach_set(pixels)
    except ImportError:                  # pragma: no cover - numpy ships with Blender
        stride = width * 4
        flipped = bytearray()
        for row in range(height - 1, -1, -1):
            flipped += rgba[row * stride:(row + 1) * stride]
        image.pixels[:] = [value / 255.0 for value in flipped]

    # Written beside the source so the next ship that uses this texture -- or
    # the next session -- loads a PNG instead of decoding again.
    try:
        image.filepath_raw = str(decoded)
        image.file_format = "PNG"
        image.save()
    except (OSError, RuntimeError) as exc:
        print(f"[CarbonEngineJS SOF] could not cache {decoded.name}: {exc}")
        image.pack()
    return image
