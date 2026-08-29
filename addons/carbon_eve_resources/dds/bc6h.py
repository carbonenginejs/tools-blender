"""BC6H: the HDR block format EVE's nebulae are stored in.

Ported from `runtime/src/resource/formats/dds/core/bc6h.js`, which is the
CarbonEngineJS decoder and the authority here. The mode table and the bit
layouts come from the Khronos Data Format Specification section 20.2.

Why a second decoder at all, when the runtime already has one: that one is
JavaScript and reached through Node, and this add-on is standalone. An artist
installs a zip.

Unlike BC7 this one decodes to FLOAT, not bytes. That is the whole point of the
format -- a nebula is an environment map and its bright detail lives well above
one, which is exactly what an 8-bit path throws away.

The partition table and its anchors are shared with BC7 and live beside it.
"""

from __future__ import annotations

import math

from .bc7_tables import ANCHOR_2, PARTITIONS_2


#: The DXGI formats BC6H arrives as: typeless, unsigned, signed.
DXGI_BC6H_TYPELESS = 94
DXGI_BC6H_UF16 = 95
DXGI_BC6H_SF16 = 96

#: Which bit of the block feeds which endpoint channel, per mode.
#:
#: Each token is a channel letter, an endpoint letter and a bit number: `RW0`
#: is red, endpoint W, bit 0. `M` is the mode, `D` the partition shape, `NA`
#: unused. Transcribed rather than derived -- the layouts are a specification
#: table and there is no rule to recover them from.
MODE_LAYOUTS = (
    "M0 M1 GY4 BY4 BZ4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RX4 GZ4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 BZ2 RZ0 RZ1 RZ2 RZ3 RZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 GY5 GZ4 GZ5 RW0 RW1 RW2 RW3 RW4 RW5 RW6 BZ0 BZ1 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GW6 BY5 BZ2 GY4 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BZ3 BZ5 BZ4 RX0 RX1 RX2 RX3 RX4 RX5 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 GX5 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BX5 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 RY5 RZ0 RZ1 RZ2 RZ3 RZ4 RZ5 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RX4 RW10 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GW10 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BW10 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 BZ2 RZ0 RZ1 RZ2 RZ3 RZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RW10 GZ4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 GW10 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BW10 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 BZ0 BZ2 RZ0 RZ1 RZ2 RZ3 GY4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RW10 BY4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GW10 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BW10 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 BZ1 BZ2 RZ0 RZ1 RZ2 RZ3 BZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GY4 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BZ4 RX0 RX1 RX2 RX3 RX4 GZ4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 BZ2 RZ0 RZ1 RZ2 RZ3 RZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 GZ4 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 BZ2 GY4 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BZ3 BZ4 RX0 RX1 RX2 RX3 RX4 RX5 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 RY5 RZ0 RZ1 RZ2 RZ3 RZ4 RZ5 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 BZ0 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GY5 GY4 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 GZ5 BZ4 RX0 RX1 RX2 RX3 RX4 GZ4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 GX5 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BZ1 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 BZ2 RZ0 RZ1 RZ2 RZ3 RZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 BZ1 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 BY5 GY4 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BZ5 BZ4 RX0 RX1 RX2 RX3 RX4 GZ4 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 BZ0 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BX5 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 BZ2 RZ0 RZ1 RZ2 RZ3 RZ4 BZ3 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 GZ4 BZ0 BZ1 BY4 GW0 GW1 GW2 GW3 GW4 GW5 GY5 BY5 BZ2 GY4 BW0 BW1 BW2 BW3 BW4 BW5 GZ5 BZ3 BZ5 BZ4 RX0 RX1 RX2 RX3 RX4 RX5 GY0 GY1 GY2 GY3 GX0 GX1 GX2 GX3 GX4 GX5 GZ0 GZ1 GZ2 GZ3 BX0 BX1 BX2 BX3 BX4 BX5 BY0 BY1 BY2 BY3 RY0 RY1 RY2 RY3 RY4 RY5 RZ0 RZ1 RZ2 RZ3 RZ4 RZ5 D0 D1 D2 D3 D4",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RX4 RX5 RX6 RX7 RX8 RX9 GX0 GX1 GX2 GX3 GX4 GX5 GX6 GX7 GX8 GX9 BX0 BX1 BX2 BX3 BX4 BX5 BX6 BX7 BX8 BX9 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RX4 RX5 RX6 RX7 RX8 RW10 GX0 GX1 GX2 GX3 GX4 GX5 GX6 GX7 GX8 GW10 BX0 BX1 BX2 BX3 BX4 BX5 BX6 BX7 BX8 BW10 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RX4 RX5 RX6 RX7 RW11 RW10 GX0 GX1 GX2 GX3 GX4 GX5 GX6 GX7 GW11 GW10 BX0 BX1 BX2 BX3 BX4 BX5 BX6 BX7 BW11 BW10 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0",
    "M0 M1 M2 M3 M4 RW0 RW1 RW2 RW3 RW4 RW5 RW6 RW7 RW8 RW9 GW0 GW1 GW2 GW3 GW4 GW5 GW6 GW7 GW8 GW9 BW0 BW1 BW2 BW3 BW4 BW5 BW6 BW7 BW8 BW9 RX0 RX1 RX2 RX3 RW15 RW14 RW13 RW12 RW11 RW10 GX0 GX1 GX2 GX3 GW15 GW14 GW13 GW12 GW11 GW10 BX0 BX1 BX2 BX3 BW15 BW14 BW13 BW12 BW11 BW10 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0 NA0",
)

#: `(code, subsets, transformed, index bits, endpoint bits, delta bits)`.
MODES = (
    (0x00, 2, True, 3, 10, (5, 5, 5)),
    (0x01, 2, True, 3, 7, (6, 6, 6)),
    (0x02, 2, True, 3, 11, (5, 4, 4)),
    (0x06, 2, True, 3, 11, (4, 5, 4)),
    (0x0a, 2, True, 3, 11, (4, 4, 5)),
    (0x0e, 2, True, 3, 9, (5, 5, 5)),
    (0x12, 2, True, 3, 8, (6, 5, 5)),
    (0x16, 2, True, 3, 8, (5, 6, 5)),
    (0x1a, 2, True, 3, 8, (5, 5, 6)),
    (0x1e, 2, False, 3, 6, (6, 6, 6)),
    (0x03, 1, False, 4, 10, (10, 10, 10)),
    (0x07, 1, True, 4, 11, (9, 9, 9)),
    (0x0b, 1, True, 4, 12, (8, 8, 8)),
    (0x0f, 1, True, 4, 16, (4, 4, 4)),
)

MODE_BY_CODE = {mode[0]: index for index, mode in enumerate(MODES)}

_CHANNEL = {"R": 0, "G": 1, "B": 2}
_ENDPOINT = {"W": 0, "X": 1, "Y": 2, "Z": 3}


def _descriptors():
    """`[(endpoint, channel, bit) or ("shape", bit) or None]` per mode."""

    built = []
    for layout in MODE_LAYOUTS:
        row = []
        for token in layout.split(" "):
            field = token.rstrip("0123456789")
            bit = int(token[len(field):])
            if field in ("M", "NA"):
                row.append(None)
            elif field == "D":
                row.append(("shape", bit))
            else:
                row.append((_ENDPOINT[field[1]], _CHANNEL[field[0]], bit))
        built.append(tuple(row))
    return tuple(built)


DESCRIPTORS = _descriptors()

WEIGHTS_3 = (0, 9, 18, 27, 37, 46, 55, 64)
WEIGHTS_4 = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)


def is_bc6h(dxgi_format: int) -> bool:
    """Whether this DXGI format is BC6H, either sign."""

    return int(dxgi_format) in (DXGI_BC6H_TYPELESS, DXGI_BC6H_UF16,
                                DXGI_BC6H_SF16)


def _sign_extend(value: int, bits: int) -> int:
    shift = 32 - bits
    value = (value << shift) & 0xFFFFFFFF
    if value & 0x80000000:
        value -= 1 << 32
    return value >> shift


def _read_bit(block: bytes, bit: int) -> int:
    return (block[bit >> 3] >> (bit & 7)) & 1


def _read_bits(block: bytes, start: int, count: int) -> int:
    value = 0
    for bit in range(count):
        value |= _read_bit(block, start + bit) << bit
    return value


def _unquantize(component: int, bits: int, signed: bool) -> int:
    if signed:
        if bits >= 16:
            return component
        negative = component < 0
        magnitude = -component if negative else component
        maximum = (1 << (bits - 1)) - 1
        if magnitude == 0:
            value = 0
        elif magnitude >= maximum:
            value = 0x7FFF
        else:
            value = ((magnitude << 15) + 0x4000) >> (bits - 1)
        return -value if negative else value

    if bits >= 15:
        return component
    if component == 0:
        return 0
    if component == (1 << bits) - 1:
        return 0xFFFF
    return ((component << 16) + 0x8000) >> bits


def _finish_half(component: int, signed: bool) -> int:
    if signed:
        if component < 0:
            magnitude = -(((-component) * 31) >> 5)
        else:
            magnitude = (component * 31) >> 5
        return (0x8000 | -magnitude) if magnitude < 0 else magnitude
    return (component * 31) >> 6


def _half_to_float(value: int) -> float:
    sign = -1.0 if value & 0x8000 else 1.0
    exponent = (value >> 10) & 0x1F
    mantissa = value & 0x03FF
    if exponent == 0:
        return sign * (2.0 ** -14) * (mantissa / 1024.0)
    if exponent == 0x1F:
        return sign * math.inf if mantissa == 0 else math.nan
    return sign * (2.0 ** (exponent - 15)) * (1.0 + mantissa / 1024.0)


def _prepare_endpoints(endpoints, subsets, transformed, endpoint_bits,
                       delta_bits, signed):
    if signed:
        for channel in range(3):
            endpoints[0][channel] = _sign_extend(endpoints[0][channel],
                                                 endpoint_bits)

    if signed or transformed:
        for endpoint in range(1, subsets * 2):
            for channel in range(3):
                endpoints[endpoint][channel] = _sign_extend(
                    endpoints[endpoint][channel], delta_bits[channel])

    if not transformed:
        return

    mask = (1 << endpoint_bits) - 1
    for endpoint in range(1, subsets * 2):
        for channel in range(3):
            value = (endpoints[0][channel]
                     + endpoints[endpoint][channel]) & mask
            if signed:
                value = _sign_extend(value, endpoint_bits)
            endpoints[endpoint][channel] = value


def decode_block(block: bytes, signed: bool = False):
    """One 16-byte block as 16 RGBA float pixels, row-major."""

    if len(block) < 16:
        raise ValueError("BC6H block must contain 16 bytes")

    low = _read_bits(block, 0, 2)
    code = low if low < 2 else _read_bits(block, 0, 5)
    index = MODE_BY_CODE.get(code, -1)
    if index < 0:
        # A reserved mode. Black rather than a guess, and opaque so the pixel
        # is visibly wrong instead of invisibly missing.
        return [0.0, 0.0, 0.0, 1.0] * 16

    _, subsets, transformed, index_bits, endpoint_bits, delta_bits = MODES[index]
    descriptor = DESCRIPTORS[index]
    header_bits = 82 if subsets == 2 else 65
    endpoints = [[0, 0, 0] for _ in range(4)]
    shape = 0

    for source_bit in range(header_bits):
        if not _read_bit(block, source_bit):
            continue
        target = descriptor[source_bit]
        if target is None:
            continue
        if target[0] == "shape":
            shape |= 1 << target[1]
        else:
            endpoints[target[0]][target[1]] |= 1 << target[2]

    _prepare_endpoints(endpoints, subsets, transformed, endpoint_bits,
                       delta_bits, signed)

    output = [0.0] * 64
    weights = WEIGHTS_3 if index_bits == 3 else WEIGHTS_4
    partition = PARTITIONS_2[shape] if subsets == 2 else 0
    anchor = ANCHOR_2[shape] if subsets == 2 else -1
    source_bit = header_bits

    for pixel in range(16):
        is_anchor = pixel == 0 or pixel == anchor
        bits = index_bits - (1 if is_anchor else 0)
        colour_index = _read_bits(block, source_bit, bits)
        source_bit += bits

        subset = ((partition >> pixel) & 1) if subsets == 2 else 0
        first = endpoints[subset * 2]
        second = endpoints[subset * 2 + 1]
        weight = weights[colour_index]
        at = pixel * 4

        for channel in range(3):
            value0 = _unquantize(first[channel], endpoint_bits, signed)
            value1 = _unquantize(second[channel], endpoint_bits, signed)
            interpolated = (value0 * (64 - weight) + value1 * weight + 32) >> 6
            output[at + channel] = _half_to_float(
                _finish_half(interpolated, signed))
        output[at + 3] = 1.0

    return output


def decode(source: bytes, width: int, height: int, row_pitch: int = 0,
           signed: bool = False):
    """A whole BC6H surface as RGBA floats, row-major from the top.

    Returned as a flat list of floats rather than bytes: the values run well
    above one and clamping them here would defeat the format.
    """

    pitch = row_pitch or ((width + 3) // 4) * 16
    rgba = [0.0] * (width * height * 4)
    for block_y in range((height + 3) // 4):
        for block_x in range((width + 3) // 4):
            offset = block_y * pitch + block_x * 16
            pixels = decode_block(source[offset:offset + 16], signed)
            for y in range(4):
                out_y = block_y * 4 + y
                if out_y >= height:
                    continue
                for x in range(4):
                    out_x = block_x * 4 + x
                    if out_x >= width:
                        continue
                    at = (y * 4 + x) * 4
                    to = (out_y * width + out_x) * 4
                    rgba[to:to + 4] = pixels[at:at + 4]
    return rgba
