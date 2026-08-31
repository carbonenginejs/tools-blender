"""Pure-Python GR2 semantic reader with a CMF interchange projection."""

from __future__ import annotations

import os
import struct
from typing import Any

from carbon_granny import RawGr2, is_gstate_root, read_raw

from .cmf import project_cmf
from .curves import decode_curve, decompress_animation_curves, sample_curve
from .json_graph import emit_json
from .tangents import generate_normals, unpack_mesh_tangents

__version__ = "0.1.0"
GRAPH_SCHEMA_VERSION = 2


class Gr2Error(ValueError):
    """A malformed or semantically incompatible GR2 document."""


def _read_source(source: Any) -> RawGr2:
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return read_raw(stream)
    return read_raw(source)


def read_gr2(
    source: Any,
    *,
    decompress_curves: bool = True,
    unpack_tangents: bool = True,
    rebuild_missing_normals: bool = False,
) -> dict:
    """Read GR2 bytes into the stable Granny-shaped semantic graph."""

    try:
        raw = _read_source(source)
        if is_gstate_root(raw.file_info):
            raise Gr2Error("Granny State files are not render geometry; use carbon_gsf.read_gsf")
        result = emit_json(raw.file_info, raw.version)
        if decompress_curves:
            decompress_animation_curves(result)
        for mesh in result.get("meshes") or []:
            if unpack_tangents:
                unpack_mesh_tangents(mesh)
            vertex = mesh.get("vertex") or {}
            if rebuild_missing_normals and not vertex.get("normal"):
                faces = [
                    index
                    for group in mesh.get("indices") or []
                    for index in group.get("faces") or []
                ]
                if not vertex.get("position") or not faces:
                    raise Gr2Error(
                        f"cannot rebuild normals for mesh {mesh.get('name')!r}: "
                        "positions or triangle indices are missing"
                    )
                vertex["normal"] = generate_normals(vertex["position"], faces)
        return result
    except Gr2Error:
        raise
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as error:
        raise Gr2Error(str(error)) from error


def read_gr2_as_cmf(source: Any, *, sample_rate: float = 30.0, **read_options) -> dict:
    """Read GR2 bytes and project them into the canonical CMF v1 graph."""

    return project_cmf(read_gr2(source, **read_options), sample_rate=sample_rate)


def inspect(source: Any) -> dict:
    """Return container and semantic counts without a Blender dependency."""

    raw = _read_source(source)
    root = raw.file_info or {}
    if is_gstate_root(raw.file_info):
        return {
            "format": "gsf",
            "version": raw.version,
            "sectionCount": raw.section_count,
            "animationSlotCount": len(root.get("AnimationSlots") or []),
            "animationSetCount": len(root.get("AnimationSets") or []),
        }
    count = lambda value: len([item for item in value or [] if item])
    return {
        "format": "gr2",
        "version": raw.version,
        "sectionCount": raw.section_count,
        "source": root.get("FromFileName") or "",
        "counts": {
            "meshes": count(root.get("Meshes")),
            "models": count(root.get("Models")),
            "skeletons": count(root.get("Skeletons")),
            "animations": count(root.get("Animations")),
            "materials": count(root.get("Materials")),
            "textures": count(root.get("Textures")),
        },
    }


__all__ = [
    "Gr2Error",
    "GRAPH_SCHEMA_VERSION",
    "RawGr2",
    "decode_curve",
    "inspect",
    "project_cmf",
    "read_gr2",
    "read_gr2_as_cmf",
    "sample_curve",
    "__version__",
]
