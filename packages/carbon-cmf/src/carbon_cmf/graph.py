"""CMF-native graph construction from deinterleaved shared geometry."""

from __future__ import annotations

import math

from .binary import CmfError
from .constants import ELEMENT_TYPE_SIZE


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
            shared_meshes.append(_shared_mesh(mesh, lod, lod_index, flatten_lods))

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
                "dataIsDeltas": True,
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
        "lods": [lod] if lod else [],
        "topology": mesh.get("topology") or "TriangleList",
        "skeleton": mesh.get("skeleton"),
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
    vertex = _normalize_vertex(mesh.get("vertex") or {})
    position_count = len(vertex.get("position") or []) // 3
    decl = _build_decl(vertex)
    stride = _stride(decl)
    indices = list(mesh.get("indices") or [])
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
                "threshold": 0xFFFFFFFF,
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
        "uvDensities": list(mesh.get("uvDensities") or []),
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
    return output


def _build_decl(vertex: dict) -> list[dict]:
    declaration = []
    offset = 0
    vertex_count = len(vertex.get("position") or []) // 3
    for name, usage, default_count, usage_index, element_type in VERTEX_CHANNELS:
        values = vertex.get(name) or []
        if not values:
            continue
        element_count = (
            4
            if name in ("tangent", "binormal") and vertex_count and len(values) == vertex_count * 4
            else default_count
        )
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
    channel_names = [
        name
        for name, *_ in VERTEX_CHANNELS
        if any((target.get("vertex") or {}).get(name) for target in source_targets)
    ]
    prototype = {name: [0] for name in channel_names}
    declaration = _build_decl(prototype)
    stride = _stride(declaration)
    targets = []
    lods = []
    for source in source_targets:
        vertex = {}
        for name in channel_names:
            values = list((source.get("vertex") or {}).get(name) or [])
            base = base_vertex.get(name) or []
            if values and not source.get("dataIsDeltas", True) and len(values) == len(base):
                values = [value - base[index] for index, value in enumerate(values)]
            vertex[name] = values
        position = vertex.get("position") or []
        maximum = max(
            (
                math.sqrt(sum(position[index + component] ** 2 for component in range(3)))
                for index in range(0, len(position) - 2, 3)
            ),
            default=0.0,
        )
        name = source.get("name") or ""
        displacement = float(source.get("maxDisplacement", maximum))
        vertex_count = max(
            (
                len(vertex.get(channel) or []) // next(
                    spec[2] for spec in VERTEX_CHANNELS if spec[0] == channel
                )
                for channel in channel_names
            ),
            default=0,
        )
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


def _metadata(value):
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("entries"), list):
        return {"entries": list(value["entries"])}
    if isinstance(value, dict):
        return {"entries": [{"key": str(key), "value": str(item)} for key, item in value.items()]}
    raise CmfError("CMF metadata must be a dictionary")


__all__ = ["build_cmf_from_shared", "build_shared_from_cmf"]
