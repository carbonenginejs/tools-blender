"""CMF-native graph construction from deinterleaved shared geometry."""

from __future__ import annotations

import math
import re

from .binary import CmfError
from .constants import ELEMENT_TYPE_SIZE, USAGE
from .uvdensity import calculate_uv_densities


VERTEX_CHANNELS = (
    ("position", "Position", 3, 0, "Float32"),
    ("normal", "Normal", 3, 0, "Float32"),
    ("tangent", "Tangent", 3, 0, "Float32"),
    ("binormal", "Binormal", 3, 0, "Float32"),
    ("texcoord0", "TexCoord", 2, 0, "Float32"),
    ("texcoord1", "TexCoord", 2, 1, "Float32"),
    ("color0", "Color", 4, 0, "Float32"),
    ("blendIndice", "BoneIndices", 4, 0, "UInt16"),
    ("blendWeight", "BoneWeights", 4, 0, "Float32"),
    ("packedTangent", "PackedTangent", 4, 0, "Int16Norm"),
    ("packedTangentLegacy", "PackedTangentLegacy", 4, 0, "UInt16Norm"),
)


def build_shared_from_cmf(source: dict, *, flatten_lods: bool = False) -> dict:
    """Build the deinterleaved shared graph used by geometry consumers.

    CMF stores LOD vertex/index buffers below each mesh, whereas the shared
    geometry contract exposes the first LOD directly on the mesh.  Blender can
    request ``flatten_lods`` to expose every additional LOD as a conventional
    mesh named ``"<name> LOD <index>"``; its existing LOD filtering can then be
    reused without adding a second mesh construction path.

    Skeletons and animations deliberately remain in their CMF-native shape.
    They are already format-independent data, and preserving that shape keeps
    this adapter useful to exporters instead of baking Blender or GR2 details
    into the CMF package.
    """

    if not isinstance(source, dict):
        raise CmfError("CMF shared graph source must be a dictionary")
    meshes = source.get("meshes") or []
    skeletons = source.get("skeletons") or []
    animations = source.get("animations") or []
    if not isinstance(meshes, list):
        raise CmfError("CMF graph meshes must be a list")
    if not isinstance(skeletons, list):
        raise CmfError("CMF graph skeletons must be a list")
    if not isinstance(animations, list):
        raise CmfError("CMF graph animations must be a list")

    shared_meshes = []
    for mesh in meshes:
        if not isinstance(mesh, dict):
            raise CmfError("CMF graph mesh entries must be dictionaries")
        lods = mesh.get("lods") or []
        if not isinstance(lods, list):
            raise CmfError("CMF graph mesh LODs must be a list")
        selected = list(enumerate(lods)) if flatten_lods else list(enumerate(lods[:1]))
        if not selected:
            selected = [(0, {})]
        for lod_index, lod in selected:
            if not isinstance(lod, dict):
                raise CmfError("CMF graph mesh LOD entries must be dictionaries")
            shared = _shared_mesh(mesh, lod, lod_index, flatten_lods)
            if not flatten_lods:
                shared["lods"] = [
                    {**_shared_mesh(mesh, item, index, False), "lods": [],
                     "threshold": item.get("threshold", 0xFFFFFFFF)}
                    for index, item in enumerate(lods)
                ]
            shared_meshes.append(shared)

    return {
        "cmfVersion": source.get("version"),
        "metadata": source.get("metadata"),
        "meshes": shared_meshes,
        "skeletons": list(skeletons),
        "animations": list(animations),
    }


def _shared_mesh(mesh: dict, lod: dict, lod_index: int, flatten_lods: bool) -> dict:
    base_name = mesh.get("name") or ""
    name = f"{base_name} LOD {lod_index}" if flatten_lods and lod_index else base_name
    bounds = mesh.get("bounds") or {}
    descriptions = (mesh.get("morphTargets") or {}).get("targets") or []
    lod_targets = lod.get("morphTargets") or []
    morph_targets = []
    for index, target in enumerate(lod_targets):
        target = target if isinstance(target, dict) else {}
        description = descriptions[index] if index < len(descriptions) else {}
        description = description if isinstance(description, dict) else {}
        morph_targets.append(
            {
                "name": target.get("name") or description.get("name") or "",
                "maxDisplacement": target.get(
                    "maxDisplacement", description.get("maxDisplacement", 0.0)
                ),
                "dataIsDeltas": False,
                "vertex": target.get("vertex") or {},
            }
        )

    return {
        "name": name,
        "morphTargets": morph_targets,
        "minBounds": list(bounds.get("min") or [0, 0, 0]),
        "maxBounds": list(bounds.get("max") or [0, 0, 0]),
        "boneBindings": [
            {
                "name": binding.get("name") or "",
                "minBounds": list((binding.get("bounds") or {}).get("min") or [0, 0, 0]),
                "maxBounds": list((binding.get("bounds") or {}).get("max") or [0, 0, 0]),
            }
            for binding in mesh.get("boneBindings") or []
            if isinstance(binding, dict)
        ],
        "vertex": lod.get("vertex") or mesh.get("vertex") or {},
        "indices": lod.get("indices") or mesh.get("indices") or [],
        "lods": [],
        "topology": mesh.get("topology") or "TriangleList",
        "skeleton": mesh.get("skeleton"),
        "uvDensities": mesh.get("uvDensities"),
    }


def build_cmf_from_shared(source: dict) -> dict:
    """Normalize a shared geometry/skeleton/animation graph to CMF v1 shape.

    The returned graph includes decoded ``vertex``/``indices`` convenience
    data. BufferViews describe the future packed layout but no binary writer is
    involved.
    """

    if not isinstance(source, dict):
        raise CmfError("CMF graph source must be a dictionary")
    meshes = source.get("meshes")
    if meshes is None and "vertex" in source:
        meshes = [source]
    if meshes is None:
        meshes = []
    if not isinstance(meshes, list):
        raise CmfError("CMF graph meshes must be a list")
    return {
        "version": 1,
        "metadata": _metadata(source.get("metadata")),
        "meshes": [_build_mesh(mesh or {}) for mesh in meshes],
        "skeletons": list(source.get("skeletons") or []),
        "animations": list(source.get("animations") or []),
    }


def _build_mesh(mesh: dict) -> dict:
    source_lods = mesh.get("lods") or [mesh]
    built = []
    for index, lod in enumerate(source_lods):
        threshold = lod.get("threshold", 0xFFFFFFFF if index == 0 else None)
        if type(threshold) is not int or not 0 <= threshold <= 0xFFFFFFFF:
            raise CmfError("CMF each LOD requires a uint32 threshold")
        source = {**mesh, **lod}
        source["skeleton"] = mesh.get("skeleton")
        source["boneBindings"] = mesh.get("boneBindings") or []
        if index:
            source["morphTargets"] = lod.get("morphTargets") or []
        base_targets = mesh.get("morphTargets") or []
        if isinstance(base_targets, list) and isinstance(source.get("morphTargets"), list):
            source["morphTargets"] = [
                {**(base_targets[target_index] if target_index < len(base_targets) else {}), **target}
                for target_index, target in enumerate(source["morphTargets"])
            ]
        source["threshold"] = threshold
        built.append(_build_lod_mesh(source))
    result = built[0]
    for index, other in enumerate(built[1:], 1):
        if other["decl"] != result["decl"] or other["topology"] != result["topology"]:
            raise CmfError(f"CMF LOD {index} must use the base declaration and topology")
        if len(other["areas"]) != len(result["areas"]):
            raise CmfError(f"CMF LOD {index} must use the base areas")
        if (other["morphTargets"]["decl"] != result["morphTargets"]["decl"] or
                [target["name"] for target in other["morphTargets"]["targets"]] !=
                [target["name"] for target in result["morphTargets"]["targets"]]):
            raise CmfError(f"CMF LOD {index} must use the base morph declaration and names")
        threshold = other["lods"][0]["threshold"]
        if not 0 <= threshold < built[index - 1]["lods"][0]["threshold"]:
            raise CmfError("CMF LOD thresholds must strictly descend")
    result["lods"] = [item["lods"][0] for item in built]
    if result["lods"][0]["threshold"] != 0xFFFFFFFF:
        raise CmfError("CMF first LOD threshold must be 0xffffffff")
    for index, target in enumerate(result["morphTargets"]["targets"]):
        target["maxDisplacement"] = max(item["lods"][0]["morphTargets"][index]["maxDisplacement"] for item in built)
    for index, area in enumerate(result["areas"]):
        area["affectedByMorphTargets"] = any(item["areas"][index]["affectedByMorphTargets"] for item in built)
    return result


def _build_lod_mesh(mesh: dict) -> dict:
    vertex = _normalize_vertex(mesh.get("vertex") or {})
    if len(vertex.get("position") or []) % 3:
        raise CmfError("CMF position channel must contain complete vec3 values")
    position_count = len(vertex.get("position") or []) // 3
    decl = _build_decl(vertex)
    stride = _stride(decl)
    indices = list(mesh.get("indices") or [])
    topology = mesh.get("topology") or "TriangleList"
    if topology not in ("TriangleList", "PointList"):
        raise CmfError(f"CMF unsupported shared topology {topology!r}")
    for group in indices:
        faces = group.get("faces") or []
        if topology == "PointList" and faces:
            raise CmfError("CMF PointList cannot contain an index buffer")
        if any(type(index) is not int or not 0 <= index < position_count for index in faces):
            raise CmfError("CMF index is outside the vertex range")
    index_stride = _index_stride(indices)
    index_count = sum(len(group.get("faces") or []) for group in indices)
    bounds = _bounds(mesh)
    morph = _build_morph_targets(mesh, vertex)
    areas = []
    lod_areas = []
    first_triangle = 0
    for group in indices:
        faces = group.get("faces") or []
        if len(faces) % 3:
            raise CmfError(f"CMF mesh {mesh.get('name')!r} index group is not triangles")
        triangle_count = len(faces) // 3
        lod_areas.append({"firstElement": first_triangle, "elementCount": triangle_count})
        first_triangle += triangle_count
        areas.append(
            {
                "name": group.get("name") or "",
                "bounds": bounds,
                "bones": [],
                "affectedByBones": bool(mesh.get("boneBindings")),
                "affectedByMorphTargets": bool(morph["targets"]),
            }
        )
    lod_morphs = [
        {
            "vb": {
                "index": 0,
                "offset": 0,
                "size": target["vertexCount"] * morph["stride"],
                "stride": morph["stride"],
            },
            "vertex": target["vertex"],
            "name": target["name"],
            "maxDisplacement": target["maxDisplacement"],
        }
        for target in morph["lods"]
    ]
    return {
        "name": mesh.get("name") or "",
        "decl": decl,
        "lods": [
            {
                "vb": {"index": 0, "offset": 0, "size": position_count * stride, "stride": stride},
                "ib": {"index": 0, "offset": 0, "size": index_count * index_stride, "stride": index_stride},
                "areas": lod_areas,
                "morphTargets": lod_morphs,
                "threshold": mesh.get("threshold", 0xFFFFFFFF),
                "vertex": vertex,
                "indices": indices,
            }
        ],
        "areas": areas,
        "boneBindings": [
            {
                "name": binding.get("name") or "",
                "bounds": {
                    "min": list(binding.get("minBounds") or (binding.get("bounds") or {}).get("min") or [0, 0, 0]),
                    "max": list(binding.get("maxBounds") or (binding.get("bounds") or {}).get("max") or [0, 0, 0]),
                },
            }
            for binding in mesh.get("boneBindings") or []
        ],
        "morphTargets": {"decl": morph["decl"], "targets": morph["targets"]},
        "uvDensities": list(mesh["uvDensities"]) if mesh.get("uvDensities") is not None else
            calculate_uv_densities(vertex, indices, decl),
        "bounds": bounds,
        "audioOcclusionMesh": {
            "vertices": [],
            "indices": [],
            "bounds": {"min": [0, 0, 0], "max": [0, 0, 0]},
        },
        "topology": mesh.get("topology") or "TriangleList",
        "skeleton": mesh.get("skeleton"),
        "vertex": vertex,
        "indices": indices,
    }


def _normalize_vertex(vertex: dict) -> dict:
    output = {key: list(value or []) for key, value in vertex.items()}
    count = len(output.get("position") or []) // 3
    bone_indices = output.get("blendIndice") or []
    if count and len(bone_indices) == count * 4 and not output.get("blendWeight"):
        output["blendWeight"] = [value for _ in range(count) for value in (1.0, 0.0, 0.0, 0.0)]
    if count and len(output.get("tangent") or []) == count * 4 and not output.get("normal") and not output.get("binormal"):
        output["packedTangentLegacy"] = output.pop("tangent")
    # Decoded CMF convenience channels can accompany the original packed data.
    # Keep the source frame's declaration instead of emitting both layouts.
    for name in list(output):
        match = re.fullmatch(r"packedTangent(?:Legacy)?([0-9]*)", name)
        if match and output[name]:
            for base in ("normal", "tangent", "binormal"):
                output.pop(base + match[1], None)
    return output


def _build_decl(vertex: dict) -> list[dict]:
    declaration = []
    offset = 0
    vertex_count = len(vertex.get("position") or []) // 3
    specs = list(VERTEX_CHANNELS)
    known = {spec[0] for spec in specs}
    for name in vertex:
        if name in known:
            continue
        match = re.fullmatch(r"(normal|tangent|binormal|texcoord|color|packedTangentLegacy|packedTangent)([0-9]+)", name)
        if not match:
            if vertex[name]:
                raise CmfError(f"CMF unsupported vertex channel {name!r}")
            continue
        base, index = match[1], int(match[2])
        if index > 255:
            raise CmfError(f"CMF vertex usage index outside 0..255: {name}")
        usage = {"texcoord": "TexCoord", "color": "Color", "packedTangent": "PackedTangent",
                 "packedTangentLegacy": "PackedTangentLegacy"}.get(base, base.capitalize())
        element_type = {"packedTangent": "Int16Norm", "packedTangentLegacy": "UInt16Norm"}.get(base, "Float32")
        specs.append((name, usage, 2 if base == "texcoord" else 4 if base in ("color", "packedTangent", "packedTangentLegacy") else 3, index, element_type))
    specs.sort(key=lambda spec: (USAGE.index(spec[1]), spec[3]))
    for name, usage, default_count, usage_index, element_type in specs:
        values = vertex.get(name) or []
        if not values:
            continue
        element_count = len(values) // vertex_count if vertex_count else default_count
        if not 1 <= element_count <= 4:
            raise CmfError(f"CMF vertex channel {name!r} has invalid width {element_count}")
        if vertex_count and len(values) != vertex_count * element_count:
            raise CmfError(
                f"CMF vertex channel {name!r} contains {len(values)} values for {vertex_count} vertices"
            )
        declaration.append(
            {
                "usage": usage,
                "usageIndex": usage_index,
                "type": element_type,
                "elementCount": element_count,
                "offset": offset,
            }
        )
        offset += element_count * ELEMENT_TYPE_SIZE[element_type]
    return declaration


def _stride(declaration: list[dict]) -> int:
    return max(
        (
            element["offset"]
            + element["elementCount"] * ELEMENT_TYPE_SIZE[element["type"]]
            for element in declaration
        ),
        default=0,
    )


def _index_stride(groups: list[dict]) -> int:
    maximum = max(
        (int(index) for group in groups for index in group.get("faces") or []),
        default=0,
    )
    authored = max((int(group.get("bytesPerIndex") or 0) for group in groups), default=0)
    return 4 if maximum > 0xFFFF or authored == 4 else 2 if groups else 0


def _bounds(mesh: dict) -> dict:
    value = mesh.get("bounds") or {}
    return {
        "min": list(mesh.get("minBounds") or value.get("min") or [0, 0, 0]),
        "max": list(mesh.get("maxBounds") or value.get("max") or [0, 0, 0]),
    }


def _build_morph_targets(mesh: dict, base_vertex: dict) -> dict:
    source_targets = mesh.get("morphTargets") or []
    if isinstance(source_targets, dict):
        source_targets = source_targets.get("targets") or []
    if not source_targets:
        return {"decl": [], "targets": [], "lods": [], "stride": 0}
    if not base_vertex.get("position"):
        raise CmfError("CMF morph targets require a base position channel")
    names = [target.get("name") or "" for target in source_targets]
    if len(set(names)) != len(names):
        raise CmfError("CMF morph target names must be unique within a mesh")
    channel_names = {"position"}
    for target in source_targets:
        channel_names.update(name for name, values in (target.get("vertex") or {}).items() if values)
    if any(not base_vertex.get(name) for name in channel_names):
        raise CmfError("CMF morph channel is absent from the base vertex declaration")
    vertex_count = len(base_vertex["position"]) // 3
    widths = {name: len(base_vertex[name]) // vertex_count for name in channel_names}
    for name in channel_names:
        if re.fullmatch(r"(?:tangent|binormal)(?:[1-9][0-9]*)?", name):
            for target in source_targets:
                values = (target.get("vertex") or {}).get(name) or []
                count = _morph_source_count(target, vertex_count)
                if values and count and len(values) % count == 0 and len(values) // count in (3, 4):
                    widths[name] = len(values) // count
                    break
    # Only one prototype vertex is needed to describe the morph layout.
    prototype = {name: [0] * widths[name] for name in channel_names}
    declaration = _build_decl(prototype)
    stride = _stride(declaration)
    targets = []
    lods = []
    for source in source_targets:
        vertex = {}
        indices = source.get("vertexIndices")
        source_count = _morph_source_count(source, vertex_count)
        for name in channel_names:
            values = list((source.get("vertex") or {}).get(name) or [])
            base = base_vertex[name]
            width = widths[name]
            base_width = len(base) // vertex_count
            absolute = [base[row * base_width + component] if component < base_width else 0
                        for row in range(vertex_count) for component in range(width)]
            if values and len(values) != source_count * width:
                raise CmfError(f"CMF morph {name} length does not match its vertex count")
            for row in range(source_count if values else 0):
                index = indices[row] if indices is not None else row
                if not isinstance(index, int) or not 0 <= index < vertex_count:
                    raise CmfError(f"CMF morph {name} vertex index is outside the base vertex range")
                for component in range(width):
                    offset = index * width + component
                    value = values[row * width + component]
                    base_value = base[index * base_width + component] if component < base_width else 0
                    absolute[offset] = value + base_value if source.get("dataIsDeltas", True) else value
            vertex[name] = absolute
        position = vertex.get("position") or []
        maximum = max(
            (
                math.sqrt(sum((position[index + component] - base_vertex["position"][index + component]) ** 2 for component in range(3)))
                for index in range(0, len(position) - 2, 3)
            ),
            default=0.0,
        )
        name = source.get("name") or ""
        displacement = float(source.get("maxDisplacement", maximum))
        if not math.isfinite(displacement) or displacement < 0:
            raise CmfError("CMF morph maxDisplacement must be finite and non-negative")
        targets.append({"name": name, "maxDisplacement": displacement})
        lods.append(
            {
                "name": name,
                "maxDisplacement": displacement,
                "vertex": vertex,
                "vertexCount": vertex_count,
            }
        )
    return {"decl": declaration, "targets": targets, "lods": lods, "stride": stride}


def _morph_source_count(target, fallback):
    if target.get("vertexIndices") is not None:
        return len(target["vertexIndices"])
    vertex = target.get("vertex") or {}
    for name, _, width, _, _ in VERTEX_CHANNELS:
        if vertex.get(name):
            return len(vertex[name]) // width or fallback
    return fallback


def _metadata(value):
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("entries"), list):
        return {"entries": list(value["entries"])}
    if isinstance(value, dict):
        return {"entries": [{"key": str(key), "value": str(item)} for key, item in value.items()]}
    raise CmfError("CMF metadata must be a dictionary")


__all__ = ["build_cmf_from_shared", "build_shared_from_cmf"]
