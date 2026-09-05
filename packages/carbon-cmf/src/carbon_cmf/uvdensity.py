"""Carbon LOD0 UV density calculation, including Float32 intermediate values."""

import math
import struct

from .binary import CmfError


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _length(values):
    squared = 0.0
    for value in values:
        squared = _f32(squared + _f32(_f32(value) * _f32(value)))
    return _f32(math.sqrt(squared))


def _density(position, uv, indices):
    count = len(position) // 3
    if not count or not uv or len(uv) % count:
        return 0.0
    width = len(uv) // count
    low = [_f32(min(position[axis::3])) for axis in range(3)]
    high = [_f32(max(position[axis::3])) for axis in range(3)]
    diameter = _length([_f32(b - a) for a, b in zip(low, high)])
    densities = []
    total_area = 0.0
    if len(indices) % 3:
        raise CmfError("CMF UV density triangle index count must be divisible by 3")
    for offset in range(0, len(indices), 3):
        triangle = indices[offset:offset + 3]
        if any(not isinstance(index, int) or not 0 <= index < count for index in triangle):
            raise CmfError("CMF UV density index is outside the vertex range")
        edges = []
        density = 0.0
        for edge in range(3):
            left, right = triangle[edge], triangle[(edge + 1) % 3]
            dx = _length([
                _f32(_f32(position[left * 3 + axis]) - _f32(position[right * 3 + axis]))
                for axis in range(3)
            ])
            if dx == 0:
                break
            edges.append(dx)
            squared = 0.0
            for axis in range(4):
                difference = (
                    _f32(_f32(uv[left * width + axis]) - _f32(uv[right * width + axis]))
                    if axis < width else 0.0
                )
                squared = _f32(squared + _f32(difference * difference))
            if squared:
                dy = _f32(_f32(math.sqrt(squared)) * diameter)
                ratio = _f32(dy / dx)
                # Carbon leaves density zero if the first UV edge is zero.
                density = ratio if edge == 0 else min(density, ratio)
        if len(edges) != 3:
            continue
        perimeter = _f32(_f32(_f32(edges[0] + edges[1]) + edges[2]) * 0.5)
        area = math.sqrt(max(
            perimeter * (perimeter - edges[0]) * (perimeter - edges[1]) * (perimeter - edges[2]),
            0.0,
        ))
        total_area += area
        densities.append((_f32(area), density))
    if not densities:
        return 0.0
    densities.sort(key=lambda value: value[1])
    discard_area = total_area * _f32(0.03)
    discarded = 0.0
    index = 0
    while discarded < discard_area and index < len(densities):
        discarded = _f32(discarded + densities[index][0])
        index += 1
    # Carbon's uvdensity.cpp can index past the end for small meshes. Match
    # the runtime's explicit safety adaptation: retain the last measured value.
    return densities[min(index, len(densities) - 1)][1]


def calculate_uv_densities(vertex, groups, declaration):
    """Return density entries through the largest TexCoord usage index, with zero holes."""
    elements = [item for item in declaration if item["usage"] == "TexCoord"]
    if not elements or not vertex.get("position"):
        return []
    result = [0.0] * (max(item["usageIndex"] for item in elements) + 1)
    indices = [index for group in groups for index in group.get("faces") or []]
    for element in elements:
        index = element["usageIndex"]
        result[index] = _density(vertex["position"], vertex.get(f"texcoord{index}") or [], indices)
    return result
