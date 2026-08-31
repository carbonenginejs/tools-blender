"""Packed Carbon/GR2 tangent-frame and mesh-normal helpers."""

from __future__ import annotations

import math

from carbon_cmf.tangents import PACKED_TANGENT_LEGACY, unpack_packed_tangents


def unpack_mesh_tangents(mesh: dict) -> bool:
    vertex = mesh.get("vertex") or {}
    positions = vertex.get("position") or []
    packed = vertex.get("tangent") or []
    vertex_count = len(positions) // 3
    if (
        not vertex_count
        or len(packed) != vertex_count * 4
        or vertex.get("normal")
        or vertex.get("binormal")
    ):
        return False

    # EVE GR2 uses the polar singularity as an explicit missing-frame marker;
    # native CMF exposes the degenerate vectors instead, so opt in only here.
    frame = unpack_packed_tangents(
        packed,
        PACKED_TANGENT_LEGACY,
        zero_legacy_null=True,
    )
    vertex["normal"] = frame["normal"]
    vertex["tangent"] = frame["tangent"]
    vertex["binormal"] = frame["binormal"]
    return True


def generate_normals(positions, indices) -> list[float]:
    normals = [0.0] * len(positions)
    for triangle in range(0, len(indices), 3):
        offsets = [int(indices[triangle + item]) * 3 for item in range(3)]
        first, second, third = offsets
        edge1 = (
            positions[second] - positions[first],
            positions[second + 1] - positions[first + 1],
            positions[second + 2] - positions[first + 2],
        )
        edge2 = (
            positions[third] - positions[first],
            positions[third + 1] - positions[first + 1],
            positions[third + 2] - positions[first + 2],
        )
        face = (
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0],
        )
        for offset in offsets:
            normals[offset] += face[0]
            normals[offset + 1] += face[1]
            normals[offset + 2] += face[2]
    for offset in range(0, len(normals), 3):
        length = math.sqrt(
            normals[offset] ** 2 + normals[offset + 1] ** 2 + normals[offset + 2] ** 2
        ) or 1.0
        normals[offset] /= length
        normals[offset + 1] /= length
        normals[offset + 2] /= length
    return normals


__all__ = ["generate_normals", "unpack_mesh_tangents"]
