"""GR2 semantic graph to CMF-native interim graph conversion."""

from __future__ import annotations

import math
import struct

from carbon_cmf import build_cmf_from_shared

from .curves import FORMAT_DA_KEYFRAMES_32F, decode_curve, f32, sample_curve


class Gr2CmfError(ValueError):
    """GR2 data cannot be represented faithfully by CMF."""


def _error(message: str) -> Gr2CmfError:
    return Gr2CmfError(f"CMF GR2 convert: {message}")


def project_cmf(root: dict, *, sample_rate: float = 30.0) -> dict:
    """Project a decoded GR2 semantic graph into canonical CMF v1 data."""

    if not isinstance(root, dict):
        raise _error("source must be a dictionary")
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise _error("sample_rate must be a positive finite number")

    source_skeletons = list(root.get("skeletons") or [])
    skeleton_index_by_identity = {
        id(skeleton): index
        for index, skeleton in enumerate(source_skeletons)
        if isinstance(skeleton, dict)
    }
    models = list(root.get("models") or [])
    model_skeletons: list[int | None] = []
    for model in models:
        skeleton = (model or {}).get("skeleton")
        if not _is_gr2_skeleton(skeleton):
            model_skeletons.append(None)
            continue
        identity = id(skeleton)
        if identity not in skeleton_index_by_identity:
            skeleton_index_by_identity[identity] = len(source_skeletons)
            source_skeletons.append(skeleton)
        model_skeletons.append(skeleton_index_by_identity[identity])

    source_meshes = list(root.get("meshes") or [])
    assignments: list[int | None] = [None] * len(source_meshes)
    assignment_models: list[int | None] = [None] * len(source_meshes)
    for model_index, model in enumerate(models):
        skeleton_index = model_skeletons[model_index]
        if skeleton_index is None:
            continue
        for binding_index, mesh_index in enumerate((model or {}).get("meshBindings") or []):
            if mesh_index == -1:
                continue
            if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(source_meshes):
                raise _error(
                    f"model {model_index} mesh binding {binding_index} references mesh {mesh_index} "
                    f"outside 0..{len(source_meshes) - 1}"
                )
            if assignments[mesh_index] not in (None, skeleton_index):
                raise _error(
                    f"mesh {mesh_index} is bound to skeleton {assignments[mesh_index]} by model "
                    f"{assignment_models[mesh_index]} and skeleton {skeleton_index} by model {model_index}"
                )
            assignments[mesh_index] = skeleton_index
            assignment_models[mesh_index] = model_index

    meshes = []
    for mesh_index, source_mesh in enumerate(source_meshes):
        mesh = dict(source_mesh or {})
        authored = mesh.get("skeleton")
        assigned = assignments[mesh_index]
        if authored is not None:
            if not isinstance(authored, int) or not 0 <= authored < len(source_skeletons):
                raise _error(f"mesh {mesh_index} has invalid skeleton index {authored}")
            if assigned is not None and assigned != authored:
                raise _error(
                    f"mesh {mesh_index} declares skeleton {authored} but its model binds {assigned}"
                )
        elif assigned is not None:
            mesh["skeleton"] = assigned
        elif mesh.get("boneBindings"):
            if len(source_skeletons) == 1:
                mesh["skeleton"] = 0
            elif len(source_skeletons) > 1:
                raise _error(f"mesh {mesh_index} has bone bindings but no unambiguous skeleton")
        meshes.append(mesh)

    skeletons = [_convert_skeleton(skeleton) for skeleton in source_skeletons]
    animations = [
        _convert_animation(animation, sample_rate=sample_rate)
        for animation in root.get("animations") or []
    ]
    metadata = {
        "entries": [
            {"key": "sourceFormat", "value": "gr2"},
            {"key": "grannyFileSource", "value": str(root.get("grannyFileSource") or "")},
            {
                "key": "grannyFileFormatRevision",
                "value": str(root.get("grannyFileFormatRevision", "")),
            },
        ]
    }
    return build_cmf_from_shared(
        {
            "metadata": metadata,
            "meshes": meshes,
            "skeletons": skeletons,
            "animations": animations,
        }
    )


def _is_gr2_skeleton(value) -> bool:
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("bones"), list)
        and value["bones"]
        and isinstance(value["bones"][0], dict)
    )


def _convert_skeleton(skeleton: dict) -> dict:
    bones = list(skeleton.get("bones") or [])
    names = [bone.get("name") or "" for bone in bones]
    if len(set(names)) != len(names):
        raise _error(f"skeleton {skeleton.get('name')!r} contains duplicate bone names")
    parents = []
    rest_transforms = []
    world_transforms = []
    for index, bone in enumerate(bones):
        parent = int(bone.get("parentIndex", -1))
        if parent >= index and parent != 0xFFFFFFFF:
            raise _error(f"bone {index} ({bone.get('name')}) has forward parent index {parent}")
        parent = 0xFFFFFFFF if parent < 0 or parent == 0xFFFFFFFF else parent
        parents.append(parent)
        scale_shear = list(
            bone.get("scaleShear")
            or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        )
        for component in (1, 2, 3, 5, 6, 7):
            if abs(scale_shear[component]) > 1e-7:
                raise _error(f"bone {bone.get('name')!r} rest transform contains shear")
        rest = {
            "position": list(bone.get("position") or [0.0, 0.0, 0.0])[:3],
            "rotation": _normalize_quaternion(
                list(bone.get("orientation") or [0.0, 0.0, 0.0, 1.0])[:4],
                f"bone {bone.get('name')!r}",
            ),
            "scale": [scale_shear[0], scale_shear[4], scale_shear[8]],
        }
        if not all(math.isfinite(value) for value in (*rest["position"], *rest["scale"])):
            raise _error(f"bone {bone.get('name')!r} contains non-finite rest values")
        rest_transforms.append(rest)
        local = _compose_transform(rest["position"], rest["rotation"], rest["scale"])
        world_transforms.append(
            local if parent == 0xFFFFFFFF else _multiply_matrix(local, world_transforms[parent])
        )
    return {
        "name": skeleton.get("name") or "",
        "bones": names,
        "parents": parents,
        "restTransforms": rest_transforms,
        "invBindTransforms": [_invert_matrix(matrix) for matrix in world_transforms],
        "boneMasks": [],
    }


def _convert_animation(animation: dict, *, sample_rate: float) -> dict:
    duration = float(animation.get("duration", 0.0))
    if not math.isfinite(duration) or duration < 0:
        raise _error(f"animation {animation.get('name')!r} duration must be finite and non-negative")
    channels = []
    curves = []

    def add_channel(target, target_type, source_curve, dimension, group_name):
        decoded = _decoded_curve(source_curve, dimension, target, target_type)
        if decoded is None:
            return
        if target_type == "BoneScale":
            _validate_scale_shear(decoded, target)
        curve = _convert_curve(
            decoded,
            target_dimension=3 if target_type == "BoneScale" else dimension,
            duration=duration,
            sample_rate=sample_rate,
            quaternion=target_type == "BoneRotation",
        )
        channels.append(
            {
                "target": target or "",
                "targetType": target_type,
                "curveIndex": len(curves),
                "sourceTrackGroup": group_name or "",
            }
        )
        curves.append(curve)

    for group in animation.get("trackGroups") or []:
        group_name = group.get("name") or ""
        for track in group.get("transformTracks") or []:
            name = track.get("name") or ""
            add_channel(name, "BonePosition", track.get("position"), 3, group_name)
            add_channel(name, "BoneRotation", track.get("orientation"), 4, group_name)
            add_channel(name, "BoneScale", track.get("scaleShear"), 9, group_name)
        for track in group.get("vectorTracks") or []:
            dimension = int(track.get("dimension") or (track.get("valueCurve") or {}).get("dimension") or 0)
            if dimension <= 0:
                raise _error(f"vector track {track.get('name')!r} has invalid dimension {dimension}")
            add_channel(
                track.get("name") or "",
                "MorphTarget" if dimension == 1 else "Other",
                track.get("valueCurve"),
                dimension,
                group_name,
            )
    return {
        "name": animation.get("name") or "",
        "channels": channels,
        "curves": curves,
        "duration": duration,
    }


def _decoded_curve(curve, dimension: int, track: str, kind: str):
    if not curve:
        return None
    error = curve.get("error") or curve.get("Error")
    if error == "no curve data":
        return None
    if error:
        raise _error(f"track {track!r} {kind} curve: {error}")
    try:
        if isinstance(curve.get("knots"), list) and isinstance(curve.get("controls"), list):
            decoded = {
                "knots": list(curve["knots"]),
                "controls": list(curve["controls"]),
                "degree": int(curve.get("degree", 0)),
                "dimension": int(curve.get("dimension") or dimension),
            }
        else:
            decoded = decode_curve(curve, dimension)
    except (KeyError, TypeError, ValueError) as error:
        raise _error(f"track {track!r} {kind} curve: {error}") from error
    if decoded["dimension"] != dimension:
        raise _error(
            f"track {track!r} {kind} curve dimension {decoded['dimension']} does not match {dimension}"
        )
    if not decoded["knots"] or not decoded["controls"]:
        raise _error(f"track {track!r} {kind} curve decoded to no controls")
    if len(decoded["controls"]) % dimension:
        raise _error(f"track {track!r} {kind} curve control count is invalid")
    if not all(math.isfinite(float(value)) for value in (*decoded["knots"], *decoded["controls"])):
        raise _error(f"track {track!r} {kind} curve contains non-finite values")
    return {
        **decoded,
        "keyframed": curve.get("format") == FORMAT_DA_KEYFRAMES_32F,
    }


def _validate_scale_shear(curve: dict, track: str) -> None:
    for offset in range(0, len(curve["controls"]), 9):
        for component in (1, 2, 3, 5, 6, 7):
            if abs(curve["controls"][offset + component]) > 1e-7:
                raise _error(f"track {track!r} scaleShear curve contains shear")


def _convert_curve(
    curve: dict,
    *,
    target_dimension: int,
    duration: float,
    sample_rate: float,
    quaternion: bool,
) -> dict:
    dimension = curve["dimension"]
    control_count = len(curve["controls"]) // dimension

    def extract(index: int) -> list[float]:
        base = index * dimension
        if dimension == 9 and target_dimension == 3:
            return [
                curve["controls"][base],
                curve["controls"][base + 4],
                curve["controls"][base + 8],
            ]
        return list(curve["controls"][base : base + target_dimension])

    if curve.get("keyframed"):
        count = control_count if duration > 0 else 1
        times = [index * duration / control_count for index in range(count)]
        values = [value for index in range(count) for value in extract(index)]
        interpolation = "Step"
    elif len(curve["knots"]) <= 1 or control_count <= 1:
        times = [float(curve["knots"][0] if curve["knots"] else 0.0)]
        values = extract(0)
        interpolation = "Step"
    elif curve["degree"] <= 1:
        if len(curve["knots"]) != control_count:
            raise _error("degree 0/1 curve knot and control counts differ")
        times = list(curve["knots"])
        values = [value for index in range(control_count) for value in extract(index)]
        interpolation = "Step" if curve["degree"] <= 0 else "Linear"
    else:
        times, rows = _resample_curve(
            curve,
            target_dimension=target_dimension,
            duration=duration,
            sample_rate=sample_rate,
            quaternion=quaternion,
        )
        values = [value for row in rows for value in row]
        interpolation = "Linear"
    if quaternion:
        _normalize_quaternion_series(values)
    times = [f32(value) for value in times]
    values = [f32(value) for value in values]
    return {
        "valueDimension": target_dimension,
        "interpolation": interpolation,
        "knotType": "Float32",
        "valueType": "Float32",
        "knotCount": len(times),
        "knots": list(struct.pack(f"<{len(times)}f", *times)),
        "values": list(struct.pack(f"<{len(values)}f", *values)),
    }


def _resample_curve(curve, *, target_dimension, duration, sample_rate, quaternion):
    start = max(float(curve["knots"][0]), 0.0)
    end = duration if duration > 0 else float(curve["knots"][-1])
    if end < start:
        end = start
    span = end - start
    grid_count = max(1, math.ceil(span * sample_rate))
    seeds = {
        min(max(float(value), start), end)
        for value in curve["knots"]
    }
    seeds.update(start + span * index / grid_count for index in range(grid_count + 1))
    seeds = sorted(seeds)
    sample = [0.0] * curve["dimension"]

    def evaluate(time):
        sample_curve(sample, curve, time, duration=duration)
        if curve["dimension"] == 9 and target_dimension == 3:
            return [sample[0], sample[4], sample[8]]
        return list(sample[:target_dimension])

    def fits(first, second, actual):
        expected = [(a + b) * 0.5 for a, b in zip(first, second)]
        if quaternion:
            expected = _normalize_quaternion(expected, "rotation interpolation")
            actual = _normalize_quaternion(list(actual), "rotation sample")
            dot = min(1.0, abs(sum(a * b for a, b in zip(expected, actual))))
            return 2 * math.acos(dot) <= 1e-3
        return all(abs(a - b) <= 1e-3 for a, b in zip(expected, actual))

    times = [seeds[0]]
    rows = [evaluate(seeds[0])]

    def refine(t0, v0, t1, v1, depth):
        if depth >= 20 or t1 - t0 <= 4e-6:
            return
        middle = (t0 + t1) * 0.5
        value = evaluate(middle)
        if fits(v0, v1, value):
            return
        refine(t0, v0, middle, value, depth + 1)
        times.append(middle)
        rows.append(value)
        refine(middle, value, t1, v1, depth + 1)

    previous_time = seeds[0]
    previous = rows[0]
    for next_time in seeds[1:]:
        value = evaluate(next_time)
        refine(previous_time, previous, next_time, value, 0)
        times.append(next_time)
        rows.append(value)
        previous_time = next_time
        previous = value
    ordered = sorted(zip(times, rows), key=lambda item: item[0])
    quantized_times = []
    quantized_rows = []
    for time, row in ordered:
        value = f32(time)
        if quantized_times and value == quantized_times[-1]:
            quantized_rows[-1] = row
        else:
            quantized_times.append(value)
            quantized_rows.append(row)
    return quantized_times, quantized_rows


def _normalize_quaternion(value: list[float], label: str) -> list[float]:
    length = math.sqrt(sum(component * component for component in value))
    if not length > 0 or not math.isfinite(length):
        raise _error(f"{label} contains a zero or non-finite quaternion")
    return [component / length for component in value]


def _normalize_quaternion_series(values: list[float]) -> None:
    previous = None
    for offset in range(0, len(values), 4):
        current = _normalize_quaternion(values[offset : offset + 4], "rotation curve")
        if previous and sum(a * b for a, b in zip(previous, current)) < 0:
            current = [-value for value in current]
        values[offset : offset + 4] = current
        previous = current


def _compose_transform(position, rotation, scale):
    x, y, z, w = rotation
    x2, y2, z2 = x + x, y + y, z + z
    xx, xy, xz = x * x2, x * y2, x * z2
    yy, yz, zz = y * y2, y * z2, z * z2
    wx, wy, wz = w * x2, w * y2, w * z2
    sx, sy, sz = scale
    return [
        (1 - (yy + zz)) * sx, (xy + wz) * sx, (xz - wy) * sx, 0,
        (xy - wz) * sy, (1 - (xx + zz)) * sy, (yz + wx) * sy, 0,
        (xz + wy) * sz, (yz - wx) * sz, (1 - (xx + yy)) * sz, 0,
        position[0], position[1], position[2], 1,
    ]


def _multiply_matrix(left, right):
    return [
        sum(left[row * 4 + index] * right[index * 4 + column] for index in range(4))
        for row in range(4)
        for column in range(4)
    ]


def _invert_matrix(matrix):
    source = [list(matrix[row * 4 : row * 4 + 4]) for row in range(4)]
    inverse = [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]
    for column in range(4):
        pivot_row = max(range(column, 4), key=lambda row: abs(source[row][column]))
        pivot = source[pivot_row][column]
        if abs(pivot) < 2.220446049250313e-16:
            raise _error("skeleton rest transform is not invertible")
        source[column], source[pivot_row] = source[pivot_row], source[column]
        inverse[column], inverse[pivot_row] = inverse[pivot_row], inverse[column]
        for index in range(4):
            source[column][index] /= pivot
            inverse[column][index] /= pivot
        for row in range(4):
            if row == column:
                continue
            factor = source[row][column]
            for index in range(4):
                source[row][index] -= factor * source[column][index]
                inverse[row][index] -= factor * inverse[column][index]
    return [0.0 if abs(value) < 1e-12 else value for row in inverse for value in row]


__all__ = ["Gr2CmfError", "project_cmf"]
