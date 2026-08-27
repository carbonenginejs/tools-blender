"""Granny animation curve decoding and sampling."""

from __future__ import annotations

from bisect import bisect_right
import math
import struct
from typing import Any, MutableSequence


FORMAT_DA_KEYFRAMES_32F = 0
FORMAT_DA_K32F_C32F = 1
FORMAT_DA_IDENTITY = 2
FORMAT_DA_CONSTANT_32F = 3
FORMAT_D3_CONSTANT_32F = 4
FORMAT_D4_CONSTANT_32F = 5
FORMAT_DA_K16U_C16U = 6
FORMAT_DA_K8U_C8U = 7
FORMAT_D4N_K16U_C15U = 8
FORMAT_D4N_K8U_C7U = 9
FORMAT_D3_K16U_C16U = 10
FORMAT_D3_K8U_C8U = 11
FORMAT_D9I1_K16U_C16U = 12
FORMAT_D9I3_K16U_C16U = 13
FORMAT_D9I1_K8U_C8U = 14
FORMAT_D9I3_K8U_C8U = 15
FORMAT_D3I1_K32F_C32F = 16
FORMAT_D3I1_K16U_C16U = 17
FORMAT_D3I1_K8U_C8U = 18

D4N_SCALE_TABLE = (
    1.4142135, 0.70710677, 0.35355338, 0.35355338,
    0.35355338, 0.17677669, 0.17677669, 0.17677669,
    -1.4142135, -0.70710677, -0.35355338, -0.35355338,
    -0.35355338, -0.17677669, -0.17677669, -0.17677669,
)
D4N_OFFSET_TABLE = (
    -0.70710677, -0.35355338, -0.53033006, -0.17677669,
    0.17677669, -0.17677669, -0.088388346, 0.0,
    0.70710677, 0.35355338, 0.53033006, 0.17677669,
    -0.17677669, 0.17677669, 0.088388346, -0.0,
)
D4N_SCALE_TABLE_MULTIPLIER_16 = 0.000030518509
D4N_SCALE_TABLE_MULTIPLIER_8 = 0.0078740157


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def knot_scale_from_trunc(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", (int(value) & 0xFFFF) << 16))[0]


def _exact_div(numerator: int, divisor: int, label: str) -> int:
    if divisor == 0 or numerator % divisor:
        raise ValueError(f"gr2reader: curve {label}: {numerator} is not divisible by {divisor}")
    return numerator // divisor


def _knots(values, count: int, scale: float) -> list[float]:
    return [f32(values[index] / scale) for index in range(count)]


def _truncated_knots(values, count: int, truncated_scale: int) -> list[float]:
    return _knots(values, count, knot_scale_from_trunc(truncated_scale))


def identity_controls(dimension: int) -> list[float]:
    if dimension == 3:
        return [0.0, 0.0, 0.0]
    if dimension == 4:
        return [0.0, 0.0, 0.0, 1.0]
    if dimension == 9:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    raise ValueError(f"gr2reader: invalid curve dimension {dimension}")


def _decode_keyframes(curve: dict[str, Any], dimension: int):
    decoded_dimension = int(curve.get("dimension") or dimension)
    controls = curve.get("controls") or []
    count = _exact_div(len(controls), decoded_dimension, "DaKeyframes32f controls/dimension")
    return list(range(count)), [f32(value) for value in controls], decoded_dimension


def _decode_float_knots(curve: dict[str, Any], dimension: int):
    knots = [f32(value) for value in curve.get("knots") or []]
    controls = [f32(value) for value in curve.get("controls") or []]
    decoded_dimension = (
        _exact_div(len(controls), len(knots), "DaK32fC32f controls/knots")
        if knots
        else dimension
    )
    return knots, controls, decoded_dimension


def _decode_identity(curve: dict[str, Any], dimension: int):
    decoded_dimension = int(curve.get("dimension") or dimension)
    return [0.0], identity_controls(decoded_dimension), decoded_dimension


def _decode_constant(curve: dict[str, Any], dimension: int):
    controls = [f32(value) for value in curve.get("controls") or []]
    return [0.0], controls, len(controls) or dimension


def _decode_constant3(curve: dict[str, Any], _dimension: int):
    controls = list(curve.get("controls") or [0.0, 0.0, 0.0])[:3]
    return [0.0], [f32(value) for value in controls], 3


def _decode_constant4(curve: dict[str, Any], _dimension: int):
    controls = list(curve.get("controls") or [0.0, 0.0, 0.0, 1.0])[:4]
    return [0.0], [f32(value) for value in controls], 4


def _decode_dak(curve: dict[str, Any], _dimension: int):
    scales_offsets = curve.get("controlScaleOffsets") or []
    values = curve.get("knotsControls") or []
    dimension = _exact_div(len(scales_offsets), 2, "DaK controlScaleOffsets/2")
    count = _exact_div(len(values), dimension + 1, "DaK knotsControls/(dim+1)")
    knots = _truncated_knots(values, count, curve.get("oneOverKnotScaleTrunc", 0))
    controls = [0.0] * (count * dimension)
    for index in range(count):
        for component in range(dimension):
            controls[index * dimension + component] = f32(
                values[count + index * dimension + component] * scales_offsets[component]
                + scales_offsets[dimension + component]
            )
    return knots, controls, dimension


def _quat16(a: int, b: int, c: int, scales, offsets) -> list[float]:
    swizzle1 = ((b & 0x8000) >> 14) | (c >> 15)
    return _quat_common(a, b, c, scales, offsets, swizzle1, 0x7FFF, 0x8000)


def _quat8(a: int, b: int, c: int, scales, offsets) -> list[float]:
    swizzle1 = ((b & 0x80) >> 6) | ((c & 0x80) >> 7)
    return _quat_common(a, b, c, scales, offsets, swizzle1, 0x7F, 0x80)


def _quat_common(a, b, c, scales, offsets, swizzle1, mask, sign_mask):
    swizzle2 = (swizzle1 + 1) & 3
    swizzle3 = (swizzle2 + 1) & 3
    swizzle4 = (swizzle3 + 1) & 3
    data_a = (a & mask) * scales[swizzle2] + offsets[swizzle2]
    data_b = (b & mask) * scales[swizzle3] + offsets[swizzle3]
    data_c = (c & mask) * scales[swizzle4] + offsets[swizzle4]
    data_d = math.sqrt(max(0.0, 1.0 - data_a * data_a - data_b * data_b - data_c * data_c))
    if a & sign_mask:
        data_d = -data_d
    output = [0.0] * 4
    output[swizzle2] = f32(data_a)
    output[swizzle3] = f32(data_b)
    output[swizzle4] = f32(data_c)
    output[swizzle1] = f32(data_d)
    return output


def _decode_d4n(curve: dict[str, Any], _dimension: int, *, eight_bit: bool):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 4, "D4n knotsControls/4")
    knots = _knots(values, count, curve.get("oneOverKnotScale", 1.0))
    selector = int(curve.get("scaleOffsetTableEntries", 0)) & 0xFFFFFFFF
    multiplier = D4N_SCALE_TABLE_MULTIPLIER_8 if eight_bit else D4N_SCALE_TABLE_MULTIPLIER_16
    scales = [
        f32(D4N_SCALE_TABLE[(selector >> (component * 4)) & 0xF] * multiplier)
        for component in range(4)
    ]
    offsets = [
        f32(D4N_OFFSET_TABLE[(selector >> (component * 4)) & 0xF])
        for component in range(4)
    ]
    decode_quat = _quat8 if eight_bit else _quat16
    controls: list[float] = []
    for index in range(count):
        base = count + index * 3
        controls.extend(decode_quat(values[base], values[base + 1], values[base + 2], scales, offsets))
    return knots, controls, 4


def _decode_d3k(curve: dict[str, Any], _dimension: int):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 4, "D3K knotsControls/4")
    knots = _truncated_knots(values, count, curve.get("oneOverKnotScaleTrunc", 0))
    scales = curve.get("controlScales") or [1.0, 1.0, 1.0]
    offsets = curve.get("controlOffsets") or [0.0, 0.0, 0.0]
    controls = [0.0] * (count * 3)
    for index in range(count):
        for component in range(3):
            controls[index * 3 + component] = f32(
                values[count + index * 3 + component] * scales[component] + offsets[component]
            )
    return knots, controls, 3


def _decode_d9i1(curve: dict[str, Any], _dimension: int):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 2, "D9I1 knotsControls/2")
    knots = _truncated_knots(values, count, curve.get("oneOverKnotScaleTrunc", 0))
    scales = curve.get("controlScales")
    offsets = curve.get("controlOffsets")
    scale = scales[0] if isinstance(scales, list) else curve.get("controlScale", 1.0)
    offset = offsets[0] if isinstance(offsets, list) else curve.get("controlOffset", 0.0)
    controls = [0.0] * (count * 9)
    for index in range(count):
        value = f32(values[count + index] * scale + offset)
        controls[index * 9] = value
        controls[index * 9 + 4] = value
        controls[index * 9 + 8] = value
    return knots, controls, 9


def _decode_d9i3(curve: dict[str, Any], _dimension: int):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 4, "D9I3 knotsControls/4")
    knots = _truncated_knots(values, count, curve.get("oneOverKnotScaleTrunc", 0))
    scales = curve.get("controlScales") or [1.0, 1.0, 1.0]
    offsets = curve.get("controlOffsets") or [0.0, 0.0, 0.0]
    controls = [0.0] * (count * 9)
    for index in range(count):
        for component, target in enumerate((0, 4, 8)):
            controls[index * 9 + target] = f32(
                values[count + index * 3 + component] * scales[component] + offsets[component]
            )
    return knots, controls, 9


def _d3i1_controls(values, count, scales, offsets):
    controls = [0.0] * (count * 3)
    for index in range(count):
        value = values[count + index]
        for component in range(3):
            controls[index * 3 + component] = f32(value * scales[component] + offsets[component])
    return controls


def _decode_d3i1_float(curve: dict[str, Any], _dimension: int):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 2, "D3I1K32f knotsControls/2")
    knots = [f32(values[index]) for index in range(count)]
    return knots, _d3i1_controls(
        values,
        count,
        curve.get("controlScales") or [1.0, 1.0, 1.0],
        curve.get("controlOffsets") or [0.0, 0.0, 0.0],
    ), 3


def _decode_d3i1_int(curve: dict[str, Any], _dimension: int):
    values = curve.get("knotsControls") or []
    count = _exact_div(len(values), 2, "D3I1 knotsControls/2")
    knots = _truncated_knots(values, count, curve.get("oneOverKnotScaleTrunc", 0))
    return knots, _d3i1_controls(
        values,
        count,
        curve.get("controlScales") or [1.0, 1.0, 1.0],
        curve.get("controlOffsets") or [0.0, 0.0, 0.0],
    ), 3


DECODERS = (
    _decode_keyframes,
    _decode_float_knots,
    _decode_identity,
    _decode_constant,
    _decode_constant3,
    _decode_constant4,
    _decode_dak,
    _decode_dak,
    lambda curve, dimension: _decode_d4n(curve, dimension, eight_bit=False),
    lambda curve, dimension: _decode_d4n(curve, dimension, eight_bit=True),
    _decode_d3k,
    _decode_d3k,
    _decode_d9i1,
    _decode_d9i3,
    _decode_d9i1,
    _decode_d9i3,
    _decode_d3i1_float,
    _decode_d3i1_int,
    _decode_d3i1_int,
)


def decode_curve(curve: dict[str, Any], dimension: int) -> dict[str, Any]:
    if not curve or not isinstance(curve.get("format"), int):
        raise ValueError("gr2reader: decode_curve requires a numeric format")
    curve_format = curve["format"]
    if curve_format < 0 or curve_format >= len(DECODERS):
        raise ValueError(f"gr2reader: unsupported granny curve format {curve_format}")
    knots, controls, decoded_dimension = DECODERS[curve_format](curve, dimension)
    if dimension and decoded_dimension and decoded_dimension != dimension:
        raise ValueError(
            f"gr2reader: curve format {curve_format} decoded dimension "
            f"{decoded_dimension} does not match track dimension {dimension}"
        )
    return {
        "knots": knots,
        "controls": controls,
        "degree": int(curve.get("degree", 0)),
        "dimension": decoded_dimension or dimension,
    }


def _copy_control(output: MutableSequence[float], curve: dict[str, Any], index: int):
    dimension = curve["dimension"]
    offset = index * dimension
    for component in range(dimension):
        output[component] = curve["controls"][offset + component]
    return output


def sample_curve(
    output: MutableSequence[float],
    curve: dict[str, Any],
    time: float,
    *,
    cycle: bool = False,
    duration: float = 0.0,
    keyframed: bool = False,
):
    """Sample a decoded curve using Granny's segment conventions."""

    if not curve or not curve.get("knots") or not curve.get("controls") or not curve.get("dimension"):
        return output
    knots = curve["knots"]
    count = len(knots)
    dimension = curve["dimension"]
    control_count = len(curve["controls"]) // dimension
    if not count or not control_count:
        return output
    if keyframed:
        frame = int(control_count * time / duration) if duration > 0 else 0
        return _copy_control(output, curve, max(0, min(control_count - 1, frame)))

    knot = min(bisect_right(knots, time), count - 1)
    degree = curve.get("degree", 0)
    if degree <= 0 or count == 1 or control_count == 1:
        return _copy_control(output, curve, min(knot, control_count - 1))

    effective_duration = duration or knots[-1]
    if degree == 1:
        knot0 = (knot + count - 1) % count if cycle else (0 if knot == 0 else knot - 1)
        start = knots[knot0]
        end = knots[knot]
        local_time = time
        if cycle and end < start:
            end += effective_duration
        if cycle and local_time < start:
            local_time += effective_duration
        factor = (local_time - start) / (end - start) if end != start else 0.0
        for component in range(dimension):
            first = curve["controls"][knot0 * dimension + component]
            second = curve["controls"][knot * dimension + component]
            output[component] = first * (1.0 - factor) + second * factor
        return output

    k2 = (knot + count - 2) % count if cycle else (0 if knot == 0 else max(0, knot - 2))
    k1 = (knot + count - 1) % count if cycle else (0 if knot == 0 else knot - 1)
    time2 = knots[k2]
    time1 = knots[k1]
    knot_time = knots[knot]
    next_time = knots[(knot + 1) % count]
    local_time = time
    if time2 > knot_time:
        knot_time += effective_duration
        next_time += effective_duration
        local_time += effective_duration
    if time1 > knot_time:
        knot_time += effective_duration
        next_time += effective_duration
        local_time += effective_duration
    if next_time < knot_time:
        next_time += effective_duration

    d0 = knot_time - time1
    d1a = knot_time - time2
    d1b = next_time - time1
    l0 = (local_time - time1) / d0 if d0 else 0.0
    l1a = (local_time - time2) / d1a if d1a else 0.0
    l1b = (local_time - time1) / d1b if d1b else 0.0
    c2 = (l1a + l0) - l0 * l1a
    current = l0 * l1b
    previous = c2 - current
    c2 = 1.0 - c2
    for component in range(dimension):
        output[component] = (
            c2 * curve["controls"][k2 * dimension + component]
            + previous * curve["controls"][k1 * dimension + component]
            + current * curve["controls"][knot * dimension + component]
        )
    return output


def decompress_animation_curves(root: dict[str, Any]) -> dict[str, Any]:
    for animation in root.get("animations") or []:
        for group in animation.get("trackGroups") or []:
            for track in group.get("transformTracks") or []:
                for name, dimension in (("orientation", 4), ("position", 3), ("scaleShear", 9)):
                    curve = track.get(name)
                    if not curve or not isinstance(curve.get("format"), int) or curve.get("error"):
                        continue
                    decoded = decode_curve(curve, dimension)
                    curve["knots"] = decoded["knots"]
                    curve["controls"] = decoded["controls"]
                    curve["dimension"] = decoded["dimension"]
    return root


__all__ = [
    "decode_curve",
    "decompress_animation_curves",
    "f32",
    "identity_controls",
    "knot_scale_from_trunc",
    "sample_curve",
]
