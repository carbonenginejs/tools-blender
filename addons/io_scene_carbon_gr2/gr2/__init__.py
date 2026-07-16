"""Public pure-Python GR2/GSF reader API."""

from __future__ import annotations

import os
import struct
from typing import Any

from .curves import decode_curve, decompress_animation_curves, sample_curve
from .gsf import is_gsf_raw, project_gsf
from .json_graph import emit_json
from .reader import RawGr2, read_raw
from .tangents import generate_normals, unpack_mesh_tangents


class Gr2Error(ValueError):
    pass


def _read_source(source: Any):
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
    """Read a render-geometry GR2 file into a stable plain-data graph."""

    try:
        raw = _read_source(source)
        if is_gsf_raw(raw):
            raise Gr2Error("Granny State files are not render geometry; use read_gsf")
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


def read_gsf(source: Any) -> dict:
    try:
        return project_gsf(_read_source(source))
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as error:
        raise Gr2Error(str(error)) from error


def is_gsf(source: Any) -> bool:
    try:
        return is_gsf_raw(_read_source(source))
    except (OSError, ValueError, TypeError, IndexError, struct.error):
        return False


def inspect(source: Any) -> dict:
    raw = _read_source(source)
    root = raw.file_info or {}
    if is_gsf_raw(raw):
        value = project_gsf(raw)
        return {
            "format": "gsf",
            "version": raw.version,
            "sectionCount": raw.section_count,
            "animationSlotCount": len(value.get("animationSlots") or []),
            "animationSetCount": len(value.get("animationSets") or []),
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
            "animations": count(root.get("Animations")),
            "materials": count(root.get("Materials")),
            "textures": count(root.get("Textures")),
        },
    }


__all__ = [
    "Gr2Error",
    "RawGr2",
    "decode_curve",
    "inspect",
    "is_gsf",
    "read_gr2",
    "read_gsf",
    "read_raw",
    "sample_curve",
]
