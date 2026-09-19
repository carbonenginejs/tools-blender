"""Decode authored uncompressed DDS volume mip chains into ordered RGBA slices.

The subresource layout follows runtime formats/dds/core/helpers.js: each mip
contains all Z slices, and width, height and depth halve independently. This
does not turn a 3D texture into a Blender 2D texture or invent lower mip levels.
"""
from dataclasses import dataclass
import struct

from .reader import DdsError, header_of


@dataclass(frozen=True, slots=True)
class VolumeLevel:
    width: int
    height: int
    depth: int
    rgba: bytes


def decode_volume(data: bytes) -> tuple[VolumeLevel, ...]:
    """Read the legacy RGB/BGRA volume formats used by the resource corpus.

    RGBA is returned in source Z/Y/X order with encoded RGB values unchanged.
    Color interpretation and linear filtering belong to the sampler.
    """
    header = header_of(data)
    if not header.is_volume:
        raise DdsError("DDS is not a volume texture")
    flags, pitch = struct.unpack_from("<I", data, 8)[0], struct.unpack_from("<I", data, 20)[0]
    mip_count = max(struct.unpack_from("<I", data, 28)[0], 1)
    pixel_flags = struct.unpack_from("<I", data, 80)[0]
    bits, *masks = struct.unpack_from("<IIIII", data, 88)
    if not pixel_flags & 0x40 or pixel_flags & 0x4 or bits not in (24, 32):
        raise DdsError("DDS volume decoder requires uncompressed 24/32-bit RGB")
    dimensions = (header.width, header.height, header.depth)
    if min(dimensions) < 1 or mip_count > max(dimensions).bit_length():
        raise DdsError("DDS volume dimensions or mip count are invalid")
    stride = bits // 8
    if flags & 0x8 and pitch not in (0, header.width * stride):
        raise DdsError("Padded DDS volume rows are not supported")
    shifts = []
    occupied = 0
    for channel, mask in enumerate(masks):
        if channel == 3 and not pixel_flags & 1:
            mask = 0
        if not mask and channel == 3:
            shifts.append(None)
            continue
        shift = (mask & -mask).bit_length() - 1
        if shift < 0 or mask != 255 << shift or shift % 8 or shift + 8 > bits or occupied & mask:
            raise DdsError("DDS volume requires disjoint byte-sized RGB channels")
        occupied |= mask
        shifts.append(shift // 8)
    offset = header.data_offset
    levels = []
    for mip in range(mip_count):
        width, height, depth = (max(value >> mip, 1) for value in dimensions)
        count = width * height * depth
        end = offset + count * stride
        if end > len(data):
            raise DdsError(f"DDS volume mip {mip} is truncated")
        source = memoryview(data)[offset:end]
        rgba = bytearray(count * 4)
        for channel, shift in enumerate(shifts):
            rgba[channel::4] = bytes([255]) * count if shift is None else source[shift::stride].tobytes()
        levels.append(VolumeLevel(width, height, depth, bytes(rgba)))
        offset = end
    return tuple(levels)
