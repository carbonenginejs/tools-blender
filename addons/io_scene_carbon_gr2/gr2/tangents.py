"""Packed Carbon/GR2 tangent-frame and mesh-normal helpers."""

from __future__ import annotations

import math


TANGENT_TAU = 6.28318548
TANGENT_PI = 3.14159274


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

    normals = [0.0] * (vertex_count * 3)
    tangents = [0.0] * (vertex_count * 3)
    binormals = [0.0] * (vertex_count * 3)
    for index in range(vertex_count):
        source = index * 4
        target = index * 3
        angle0 = packed[source] * TANGENT_TAU - TANGENT_PI
        angle1 = packed[source + 1] * TANGENT_TAU - TANGENT_PI
        angle2 = packed[source + 2] * TANGENT_TAU - TANGENT_PI
        angle3 = packed[source + 3] * TANGENT_TAU - TANGENT_PI
        sin1 = abs(math.sin(angle1))
        sin3 = abs(math.sin(angle3))
        tangent = (
            sin1 * math.cos(angle0),
            sin1 * math.sin(angle0),
            math.cos(angle1),
        )
        binormal = (
            sin3 * math.cos(angle2),
            sin3 * math.sin(angle2),
            math.cos(angle3),
        )
        if sin1 < 1e-6 and sin3 < 1e-6:
            continue
        sign = 1.0 if angle1 > 0 and angle3 > 0 else -1.0
        normal = (
            (tangent[1] * binormal[2] - tangent[2] * binormal[1]) * sign,
            (tangent[2] * binormal[0] - tangent[0] * binormal[2]) * sign,
            (tangent[0] * binormal[1] - tangent[1] * binormal[0]) * sign,
        )
        normals[target : target + 3] = normal
        tangents[target : target + 3] = tangent
        binormals[target : target + 3] = binormal
    vertex["normal"] = normals
    vertex["tangent"] = tangents
    vertex["binormal"] = binormals
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
