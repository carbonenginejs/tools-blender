"""CMF packed tangent-frame decoding shared with the GR2 adapter."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .binary import CmfError


PACKED_TANGENT = "PackedTangent"
PACKED_TANGENT_LEGACY = "PackedTangentLegacy"
TANGENT_TAU = 6.28318548
TANGENT_PI = 3.14159274


def decode_packed_tangent(
    packed: Sequence[float],
    usage: str,
    *,
    zero_legacy_null: bool = False,
) -> dict:
    """Decode one CMF quaternion or legacy angle tangent frame."""

    if len(packed) != 4:
        raise CmfError("CMF packed tangent frames require four components")
    values = [float(component) for component in packed]
    if not all(math.isfinite(component) for component in values):
        raise CmfError("CMF packed tangent frame contains non-finite components")
    if usage == PACKED_TANGENT:
        normal, tangent, binormal = _decode_quaternion(values)
        is_null = False
    elif usage == PACKED_TANGENT_LEGACY:
        normal, tangent, binormal, is_null = _decode_legacy(
            values,
            zero_null=zero_legacy_null,
        )
    else:
        raise CmfError(f"Unsupported CMF packed tangent usage {usage!r}")
    return {
        "normal": normal,
        "tangent": tangent,
        "binormal": binormal,
        "isNull": is_null,
    }


def unpack_packed_tangents(
    values: Sequence[float],
    usage: str,
    *,
    zero_legacy_null: bool = False,
) -> dict:
    """Expand a flat packed channel into flat normal, tangent and binormal channels."""

    if len(values) % 4:
        raise CmfError("CMF packed tangent channel length is not divisible by four")
    output = {"normal": [], "tangent": [], "binormal": []}
    for offset in range(0, len(values), 4):
        frame = decode_packed_tangent(
            values[offset : offset + 4],
            usage,
            zero_legacy_null=zero_legacy_null,
        )
        output["normal"].extend(frame["normal"])
        output["tangent"].extend(frame["tangent"])
        output["binormal"].extend(frame["binormal"])
    return output


def _decode_quaternion(packed: list[float]):
    # CMF stores quaternion xyz plus the normal handedness. The quaternion w is
    # made non-negative while packing, so it can be reconstructed unambiguously.
    x, y, z, normal_sign = packed
    x2, y2, z2 = x * x, y * y, z * z
    w = math.sqrt(max(0.0, min(1.0, 1.0 - x2 - y2 - z2)))
    xy, xz, yz = 2.0 * x * y, 2.0 * x * z, 2.0 * y * z
    xw, yw, zw = 2.0 * x * w, 2.0 * y * w, 2.0 * z * w
    tangent = [1.0 - 2.0 * y2 - 2.0 * z2, xy + zw, xz - yw]
    binormal = [xy - zw, 1.0 - 2.0 * x2 - 2.0 * z2, yz + xw]
    normal = [
        (xz + yw) * normal_sign,
        (yz - xw) * normal_sign,
        (1.0 - 2.0 * x2 - 2.0 * y2) * normal_sign,
    ]
    return normal, tangent, binormal


def _decode_legacy(packed: list[float], *, zero_null: bool):
    # The legacy channel stores spherical angles remapped to UNorm. Its two
    # polar-angle signs together carry the tangent-frame handedness.
    angle0, angle1, angle2, angle3 = [
        component * TANGENT_TAU - TANGENT_PI for component in packed
    ]
    sin1 = abs(math.sin(angle1))
    sin3 = abs(math.sin(angle3))
    is_null = sin1 < 1e-6 and sin3 < 1e-6
    if is_null and zero_null:
        zero = [0.0, 0.0, 0.0]
        return zero.copy(), zero.copy(), zero.copy(), True
    tangent = [
        sin1 * math.cos(angle0),
        sin1 * math.sin(angle0),
        math.cos(angle1),
    ]
    binormal = [
        sin3 * math.cos(angle2),
        sin3 * math.sin(angle2),
        math.cos(angle3),
    ]
    sign = 1.0 if angle1 > 0.0 and angle3 > 0.0 else -1.0
    normal = [
        (tangent[1] * binormal[2] - tangent[2] * binormal[1]) * sign,
        (tangent[2] * binormal[0] - tangent[0] * binormal[2]) * sign,
        (tangent[0] * binormal[1] - tangent[1] * binormal[0]) * sign,
    ]
    return normal, tangent, binormal, is_null


__all__ = [
    "PACKED_TANGENT",
    "PACKED_TANGENT_LEGACY",
    "TANGENT_PI",
    "TANGENT_TAU",
    "decode_packed_tangent",
    "unpack_packed_tangents",
]
