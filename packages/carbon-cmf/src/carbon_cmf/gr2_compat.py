"""Explicit GR2 animation view; the native/shared CMF graph stays unchanged.

Mirrors runtime formats/cmf/core/gr2Compat.js. Sampling semantics come from
Carbon mesh/src/cmf/animation.cpp; the consumer must honor degree 0 (Step).
"""

import math

from .binary import CmfError
from .constants import ELEMENT_TYPE_SIZE
from .reader import _read_element


def _array(values, element_type, count):
    # Python read_cmf returns raw bytes as lists, even for UInt8Norm. Unlike
    # runtime's mixed decoded/raw input API, length alone cannot identify an
    # already-decoded array here: that would skip 8-bit normalization.
    size = ELEMENT_TYPE_SIZE.get(element_type)
    if size is None or len(values) != count * size:
        raise CmfError("CMF animation array has an invalid element type or size")
    try:
        data = bytes(values)
    except (ValueError, TypeError) as exc:
        raise CmfError("CMF animation array must contain raw bytes") from exc
    decoded = [_read_element(data, offset, element_type) for offset in range(0, len(data), size)]
    if not all(math.isfinite(value) for value in decoded):
        raise CmfError("CMF animation curve contains non-finite values")
    return decoded


def _curve(curve, dimension, *, scale=False, rotation=False):
    interpolation = curve.get("interpolation")
    count = curve.get("knotCount")
    if interpolation not in ("Step", "Linear"):
        raise CmfError(f"Unsupported CMF animation interpolation {interpolation!r}")
    if type(count) is not int or count < 1 or curve.get("valueDimension") != dimension:
        raise CmfError("CMF animation curve has an invalid count or dimension")
    knots = _array(curve.get("knots", []), curve.get("knotType"), count)
    values = _array(curve.get("values", []), curve.get("valueType"), count * dimension)
    if any(b < a for a, b in zip(knots, knots[1:])):
        raise CmfError("CMF animation knots are not ascending")
    if rotation:
        previous = None
        for offset in range(0, len(values), 4):
            q = values[offset:offset + 4]
            length = math.sqrt(sum(v * v for v in q))
            if not length:
                raise CmfError("CMF animation rotation has zero length")
            q = [v / length for v in q]
            if previous is not None and sum(a * b for a, b in zip(previous, q)) < 0:
                q = [-v for v in q]
            values[offset:offset + 4] = q
            previous = q
    if scale:
        values = [lane for offset in range(0, len(values), 3)
                  for lane in (values[offset], 0, 0, 0, values[offset + 1], 0, 0, 0, values[offset + 2])]
    return {"format": 1, "degree": 0 if interpolation == "Step" else 1,
            "knots": knots, "controls": values}


def build_gr2_animations(graph):
    """Convert native CMF channels into the importer's GR2 track-group view."""
    skeletons = graph.get("skeletons", [])
    result = []
    for animation in graph.get("animations", []):
        tracks, vectors, seen = {}, [], set()
        curves = animation.get("curves", [])
        for channel in animation.get("channels", []):
            target, kind = channel.get("target", ""), channel.get("targetType")
            if kind not in ("BonePosition", "BoneRotation", "BoneScale", "MorphTarget"):
                raise CmfError(f"Unsupported CMF animation target {kind!r}")
            if not target or (kind, target) in seen:
                raise CmfError("CMF animation target is empty or duplicated")
            seen.add((kind, target))
            index = channel.get("curveIndex")
            if type(index) is not int or not 0 <= index < len(curves):
                raise CmfError("CMF animation channel references a missing curve")
            source = curves[index]
            if kind == "MorphTarget":
                vectors.append({"name": target, "dimension": 1, "valueCurve": _curve(source, 1)})
                continue
            if sum(target in s.get("bones", []) for s in skeletons) != 1:
                raise CmfError(f"CMF animation bone {target!r} must resolve to one skeleton")
            track = tracks.setdefault(target, {"name": target, "flags": 0,
                **{field: {"format": 0, "degree": 0, "error": "no curve data"}
                   for field in ("position", "orientation", "scaleShear")}})
            if kind == "BonePosition":
                track["position"] = _curve(source, 3)
            elif kind == "BoneRotation":
                track["orientation"] = _curve(source, 4, rotation=True)
            else:
                track["scaleShear"] = _curve(source, 3, scale=True)
        groups = []
        for skeleton in skeletons:
            selected = [track for name, track in tracks.items() if name in skeleton.get("bones", [])]
            if selected:
                groups.append({"name": skeleton.get("name", ""), "transformTracks": selected, "vectorTracks": []})
        if vectors:
            groups.append({"name": "root", "transformTracks": [], "vectorTracks": vectors})
        result.append({"name": animation.get("name", ""), "duration": animation.get("duration", 0),
                       "timeStep": 0, "oversampling": 0, "defaultLoopCount": 0, "flags": 0,
                       "trackGroups": groups})
    return result
