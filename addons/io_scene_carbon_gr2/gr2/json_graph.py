"""Projection from reflected Granny objects to stable GR2 JSON-shaped data."""

from __future__ import annotations

import math
from typing import Any

from .curves import f32
from .reader import MEMBER_TYPES, TypedList


_INV255 = f32(1.0 / 255.0)
_INV65535 = f32(1.0 / 65535.0)
_INV127 = f32(1.0 / 127.0)
_INV32767 = f32(1.0 / 32767.0)

_VERTEX_CHANNELS = (
    ("position", "Position", 3),
    ("blendIndice", "BoneIndices", 4),
    ("tangent", "Tangent", 4),
    ("normal", "Normal", 3),
    ("texcoord0", "TextureCoordinates0", 2),
    ("texcoord1", "TextureCoordinates1", 2),
    ("binormal", "Binormal", 4),
    ("blendWeight", "BoneWeights", 4),
)

_CURVE_FORMATS = {
    "DaKeyframes32f": 0,
    "DaK32fC32f": 1,
    "DaIdentity": 2,
    "DaConstant32f": 3,
    "D3Constant32f": 4,
    "D4Constant32f": 5,
    "DaK16uC16u": 6,
    "DaK8uC8u": 7,
    "D4nK16uC15u": 8,
    "D4nK8uC7u": 9,
    "D3K16uC16u": 10,
    "D3K8uC8u": 11,
    "D9I1K16uC16u": 12,
    "D9I3K16uC16u": 13,
    "D9I1K8uC8u": 14,
    "D9I3K8uC8u": 15,
    "D3I1K32fC32f": 16,
    "D3I1K16uC16u": 17,
    "D3I1K8uC8u": 18,
}


def _finite(value: Any) -> float:
    value = float(value)
    return value if math.isfinite(value) else 0.0


def _signed_i32(value: Any) -> int:
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _convert(value: Any, member_type: int) -> float:
    if member_type == MEMBER_TYPES["NormalUInt8"]:
        return f32(value * _INV255)
    if member_type == MEMBER_TYPES["NormalUInt16"]:
        return f32(value * _INV65535)
    if member_type == MEMBER_TYPES["BinormalInt8"]:
        return f32(value * _INV127)
    if member_type == MEMBER_TYPES["BinormalInt16"]:
        return f32(value * _INV32767)
    return f32(value)


def _scalar_array(values) -> list[Any]:
    if not values:
        return []
    if isinstance(values[0], dict):
        first_key = next(iter(values[0]), None)
        return [item.get(first_key) for item in values] if first_key is not None else []
    return list(values)


def _copy_channel(vertices, member_name: str, destination_width: int) -> list[float]:
    member = next(
        (item for item in getattr(vertices, "granny_type", []) if item.get("name") == member_name),
        None,
    )
    if member is None:
        return []
    source_width = member["array_width"] if member["array_width"] > 1 else 1
    copy_width = min(source_width, destination_width)
    output = [0.0] * (len(vertices) * destination_width)
    for index, vertex in enumerate(vertices):
        raw = vertex.get(member_name)
        source = raw if isinstance(raw, list) else [raw]
        for component in range(copy_width):
            output[index * destination_width + component] = _finite(
                _convert(source[component], member["type"])
            )
    return output


def _emit_vertex_channels(vertices) -> dict[str, list[float]]:
    return {
        output_name: _copy_channel(vertices, source_name, width)
        for output_name, source_name, width in _VERTEX_CHANNELS
    }


def _float_array(values) -> list[float]:
    return [_finite(f32(value)) for value in _scalar_array(values)]


def _uint_array(values) -> list[int]:
    return [int(value) & 0xFFFFFFFF for value in _scalar_array(values)]


def _curve_header(curve_data: dict[str, Any]):
    for name, value in curve_data.items():
        if name.startswith("CurveDataHeader"):
            return value
    return None


def _emit_curve(curve: dict[str, Any] | None) -> dict[str, Any]:
    curve_data = curve and curve.get("CurveData")
    if not curve_data:
        return {"format": 0, "degree": 0, "error": "no curve data"}
    header = _curve_header(curve_data) or {"Format": 0, "Degree": 0}
    curve_format = int(header.get("Format", 0))
    output: dict[str, Any] = {
        "format": curve_format,
        "degree": int(header.get("Degree", 0)),
    }

    if curve_format == _CURVE_FORMATS["DaIdentity"]:
        pass
    elif curve_format == _CURVE_FORMATS["DaKeyframes32f"]:
        output["dimension"] = int(curve_data.get("Dimension", 0))
        output["controls"] = _float_array(curve_data.get("Controls"))
    elif curve_format == _CURVE_FORMATS["DaConstant32f"]:
        output["controls"] = _float_array(curve_data.get("Controls"))
    elif curve_format == _CURVE_FORMATS["D3Constant32f"]:
        output["controls"] = [
            _finite(f32(value))
            for value in list(curve_data.get("Controls") or [0.0, 0.0, 0.0])[:3]
        ]
    elif curve_format == _CURVE_FORMATS["D4Constant32f"]:
        output["controls"] = [
            _finite(f32(value))
            for value in list(curve_data.get("Controls") or [0.0, 0.0, 0.0, 0.0])[:4]
        ]
    elif curve_format == _CURVE_FORMATS["DaK32fC32f"]:
        output["knots"] = _float_array(curve_data.get("Knots"))
        output["controls"] = _float_array(curve_data.get("Controls"))
    elif curve_format in (
        _CURVE_FORMATS["DaK16uC16u"],
        _CURVE_FORMATS["DaK8uC8u"],
    ):
        output["oneOverKnotScaleTrunc"] = int(curve_data.get("OneOverKnotScaleTrunc", 0))
        output["controlScaleOffsets"] = _float_array(curve_data.get("ControlScaleOffsets"))
        output["knotsControls"] = _uint_array(curve_data.get("KnotsControls"))
    elif curve_format in (
        _CURVE_FORMATS["D4nK16uC15u"],
        _CURVE_FORMATS["D4nK8uC7u"],
    ):
        output["scaleOffsetTableEntries"] = int(curve_data.get("ScaleOffsetTableEntries", 0))
        output["oneOverKnotScale"] = _finite(f32(curve_data.get("OneOverKnotScale", 0.0)))
        output["knotsControls"] = _uint_array(curve_data.get("KnotsControls"))
    elif curve_format in (
        _CURVE_FORMATS["D3K16uC16u"],
        _CURVE_FORMATS["D3K8uC8u"],
        _CURVE_FORMATS["D3I1K16uC16u"],
        _CURVE_FORMATS["D3I1K8uC8u"],
        _CURVE_FORMATS["D9I3K16uC16u"],
        _CURVE_FORMATS["D9I3K8uC8u"],
    ):
        output["oneOverKnotScaleTrunc"] = int(curve_data.get("OneOverKnotScaleTrunc", 0))
        output["controlScales"] = [
            _finite(f32(value))
            for value in curve_data.get("ControlScales") or [0.0, 0.0, 0.0]
        ]
        output["controlOffsets"] = [
            _finite(f32(value))
            for value in curve_data.get("ControlOffsets") or [0.0, 0.0, 0.0]
        ]
        output["knotsControls"] = _uint_array(curve_data.get("KnotsControls"))
    elif curve_format == _CURVE_FORMATS["D3I1K32fC32f"]:
        output["controlScales"] = [
            _finite(f32(value))
            for value in curve_data.get("ControlScales") or [0.0, 0.0, 0.0]
        ]
        output["controlOffsets"] = [
            _finite(f32(value))
            for value in curve_data.get("ControlOffsets") or [0.0, 0.0, 0.0]
        ]
        output["knotsControls"] = _float_array(curve_data.get("KnotsControls"))
    elif curve_format in (
        _CURVE_FORMATS["D9I1K16uC16u"],
        _CURVE_FORMATS["D9I1K8uC8u"],
    ):
        output["oneOverKnotScaleTrunc"] = int(curve_data.get("OneOverKnotScaleTrunc", 0))
        output["controlScales"] = [_finite(f32(curve_data.get("ControlScale", 0.0)))]
        output["controlOffsets"] = [_finite(f32(curve_data.get("ControlOffset", 0.0)))]
        output["knotsControls"] = _uint_array(curve_data.get("KnotsControls"))
    else:
        output["error"] = f"Unknown format {curve_format}"
    return output


def _emit_variant(value: Any, seen=None):
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return value if isinstance(value, int) else _finite(f32(value))
    identity = id(value)
    if identity in seen:
        return None
    seen.add(identity)
    if isinstance(value, (list, tuple)):
        output = [_emit_variant(item, seen) for item in value]
    elif isinstance(value, dict):
        output = {key: _emit_variant(item, seen) for key, item in value.items()}
    else:
        output = str(value)
    seen.remove(identity)
    return output


def _add_extended(output: dict[str, Any], value: Any) -> None:
    if isinstance(value, dict):
        output["extendedData"] = _emit_variant(value)


def _emit_morph_target(target: dict[str, Any]) -> dict[str, Any]:
    vertex_data = target.get("VertexData") or {}
    return {
        "name": target.get("ScalarName") or "",
        "dataIsDeltas": bool(target.get("DataIsDeltas")),
        "vertex": _emit_vertex_channels(vertex_data.get("Vertices") or TypedList()),
    }


def _mapped_annotation_rows(annotation_set: dict[str, Any], vertex_count: int):
    annotations = annotation_set.get("VertexAnnotations") or TypedList()
    mapping = _scalar_array(annotation_set.get("VertexAnnotationIndices"))
    rows = TypedList(granny_type=getattr(annotations, "granny_type", []))
    vertex_indices: list[int] = []
    if annotation_set.get("IndicesMapFromVertexToAnnotation"):
        for vertex_index, annotation_index in enumerate(mapping[:vertex_count]):
            if not isinstance(annotation_index, int) or not 0 <= annotation_index < len(annotations):
                continue
            rows.append(annotations[annotation_index])
            vertex_indices.append(vertex_index)
    else:
        count = min(len(mapping), len(annotations)) if mapping else len(annotations)
        for annotation_index in range(count):
            vertex_index = mapping[annotation_index] if mapping else annotation_index
            if not isinstance(vertex_index, int) or not 0 <= vertex_index < vertex_count:
                continue
            rows.append(annotations[annotation_index])
            vertex_indices.append(vertex_index)
    identity = len(rows) == vertex_count and all(
        value == index for index, value in enumerate(vertex_indices)
    )
    return rows, None if identity else vertex_indices


def _emit_annotation_target(annotation_set: dict[str, Any], vertex_count: int):
    if not annotation_set or not annotation_set.get("VertexAnnotations"):
        return None
    rows, vertex_indices = _mapped_annotation_rows(annotation_set, vertex_count)
    if not rows:
        return None
    output = {
        "name": annotation_set.get("Name") or "",
        "dataIsDeltas": True,
        "vertex": _emit_vertex_channels(rows),
    }
    if vertex_indices is not None:
        output["vertexIndices"] = vertex_indices
    return output


def _emit_mesh(mesh: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "name": mesh.get("Name") or "",
        "minBounds": [0.0, 0.0, 0.0],
        "maxBounds": [0.0, 0.0, 0.0],
        "boneBindings": [
            {
                "name": binding.get("BoneName") or "",
                "minBounds": [_finite(f32(value)) for value in binding.get("OBBMin") or [0, 0, 0]],
                "maxBounds": [_finite(f32(value)) for value in binding.get("OBBMax") or [0, 0, 0]],
            }
            for binding in mesh.get("BoneBindings") or []
        ],
    }
    vertex_data = mesh.get("PrimaryVertexData") or {}
    vertices = vertex_data.get("Vertices") or TypedList()
    output["vertex"] = _emit_vertex_channels(vertices)
    output["morphTargets"] = [
        _emit_morph_target(target) for target in mesh.get("MorphTargets") or []
    ]
    for annotation_set in vertex_data.get("VertexAnnotationSets") or []:
        target = _emit_annotation_target(annotation_set, len(vertices))
        if target:
            output["morphTargets"].append(target)

    topology = mesh.get("PrimaryTopology") or {}
    indices32 = _scalar_array(topology.get("Indices"))
    indices16 = _scalar_array(topology.get("Indices16"))
    groups = topology.get("Groups") or []
    output["indices"] = []
    indices = indices32 if indices32 else [int(value) & 0xFFFF for value in indices16]
    bytes_per_index = 4 if indices32 else (2 if indices16 else 0)
    if indices:
        for group in groups:
            triangle_count = int(group.get("TriCount", 0))
            start = int(group.get("TriFirst", 0)) * 3
            output["indices"].append(
                {
                    "name": f"area_{int(group.get('MaterialIndex', 0))}",
                    "bytesPerIndex": bytes_per_index,
                    "faces": [
                        int(indices[start + index]) & 0xFFFFFFFF
                        for index in range(triangle_count * 3)
                    ],
                }
            )
    return output


def _emit_bone(bone: dict[str, Any]) -> dict[str, Any]:
    transform = bone.get("LocalTransform") or bone.get("Transform") or {
        "flags": 0,
        "position": [0.0, 0.0, 0.0],
        "orientation": [0.0, 0.0, 0.0, 1.0],
        "scaleShear": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    }
    flags = int(transform.get("flags", 0))
    output = {
        "name": bone.get("Name") or "",
        "parentIndex": _signed_i32(bone.get("ParentIndex", 0)),
        "flag": flags,
    }
    if flags & 1:
        output["position"] = [_finite(f32(value)) for value in transform["position"]]
    if flags & 2:
        output["orientation"] = [_finite(f32(value)) for value in transform["orientation"]]
    if flags & 4:
        output["scaleShear"] = [_finite(f32(value)) for value in transform["scaleShear"]]
    _add_extended(output, bone.get("ExtendedData"))
    return output


def _emit_skeleton(skeleton: dict[str, Any] | None) -> dict[str, Any]:
    output = {
        "name": skeleton.get("Name") or "" if skeleton else "",
        "bones": [_emit_bone(bone) for bone in skeleton.get("Bones") or []] if skeleton else [],
    }
    if skeleton:
        _add_extended(output, skeleton.get("ExtendedData"))
    return output


def _emit_model(model: dict[str, Any], file_info: dict[str, Any]) -> dict[str, Any]:
    meshes = file_info.get("Meshes") or []
    output = {
        "name": model.get("Name") or "",
        "skeleton": _emit_skeleton(model.get("Skeleton")),
        "meshBindings": [
            next((index for index, mesh in enumerate(meshes) if mesh is (binding or {}).get("Mesh")), -1)
            for binding in model.get("MeshBindings") or []
        ],
    }
    _add_extended(output, model.get("ExtendedData"))
    return output


def _emit_animation(animation: dict[str, Any]) -> dict[str, Any]:
    output = {
        "name": animation.get("Name") or "",
        "duration": _finite(f32(animation.get("Duration", 0.0))),
        "timeStep": _finite(f32(animation.get("TimeStep", 0.0))),
        "oversampling": _finite(f32(animation.get("Oversampling", 0.0))),
        "defaultLoopCount": _signed_i32(animation.get("DefaultLoopCount", 0)),
        "flags": _signed_i32(animation.get("Flags", 0)),
        "trackGroups": [],
    }
    for group in animation.get("TrackGroups") or []:
        if not group:
            continue
        output["trackGroups"].append(
            {
                "name": group.get("Name") or "",
                "transformTracks": [
                    {
                        "name": track.get("Name") or "",
                        "flags": _signed_i32(track.get("Flags", 0)),
                        "orientation": _emit_curve(track.get("OrientationCurve")),
                        "position": _emit_curve(track.get("PositionCurve")),
                        "scaleShear": _emit_curve(track.get("ScaleShearCurve")),
                    }
                    for track in group.get("TransformTracks") or []
                ],
            }
        )
    _add_extended(output, animation.get("ExtendedData"))
    return output


def emit_json(file_info: dict[str, Any], version: int) -> dict[str, Any]:
    return {
        "grannyFileFormatRevision": _signed_i32(version),
        "grannyFileSource": file_info.get("FromFileName") or "",
        "meshes": [_emit_mesh(mesh) for mesh in file_info.get("Meshes") or [] if mesh],
        "models": [
            _emit_model(model, file_info) for model in file_info.get("Models") or [] if model
        ],
        "animations": [
            _emit_animation(animation)
            for animation in file_info.get("Animations") or []
            if animation
        ],
    }


__all__ = ["emit_json"]
