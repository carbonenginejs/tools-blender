"""Granny State semantic projection built on the ordinary GR2 container."""

from __future__ import annotations

import re
from typing import Any

from carbon_granny import RawGr2, is_gstate_root


_GR2_REFERENCE = re.compile(r"\.gr2(?:;|$)", re.IGNORECASE)
_REFERENCE_KEY = re.compile(r"file|path|source|hint", re.IGNORECASE)


def is_gsf_raw(raw: RawGr2) -> bool:
    return is_gstate_root(raw.file_info)


def _collect_references(value: Any, seen=None, output=None, output_seen=None):
    if seen is None:
        seen = set()
    if output is None:
        output = []
    if output_seen is None:
        output_seen = set()
    if not isinstance(value, (dict, list)) or id(value) in seen:
        return output
    seen.add(id(value))
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for key, child in items:
        if (
            isinstance(child, str)
            and _REFERENCE_KEY.search(str(key))
            and _GR2_REFERENCE.search(child)
            and child not in output_seen
        ):
            output.append(child)
            output_seen.add(child)
        elif isinstance(child, (dict, list)):
            _collect_references(child, seen, output, output_seen)
    return output


def project_gsf(raw: RawGr2) -> dict[str, Any]:
    if not is_gsf_raw(raw):
        raise ValueError("expected Granny State root schema")
    root = raw.file_info
    animation_sets = []
    animation_references = []
    animation_reference_seen = set()
    for index, animation_set in enumerate(root.get("AnimationSets") or []):
        references = _collect_references(animation_set)
        for reference in references:
            if reference not in animation_reference_seen:
                animation_references.append(reference)
                animation_reference_seen.add(reference)
        animation_sets.append(
            {
                "index": index,
                "sourceFileReferences": references,
                "raw": animation_set,
            }
        )
    model_references = _collect_references(
        {
            "ModelNameHint": root.get("ModelNameHint"),
            "RetargetSourceModelNameHint": root.get("RetargetSourceModelNameHint"),
        }
    )
    dependencies = [
        {"kind": "model", "reference": reference} for reference in model_references
    ]
    dependencies.extend(
        {"kind": "animation", "reference": reference}
        for reference in animation_references
        if reference not in model_references
    )
    return {
        "format": "gsf",
        "container": {
            "family": "granny",
            "revision": raw.version,
            "sectionCount": raw.section_count,
        },
        "character": {
            "modelNameHint": root.get("ModelNameHint"),
            "modelIndexHint": root.get("ModelIndexHint", -1),
            "retargetSourceModelNameHint": root.get("RetargetSourceModelNameHint"),
            "retargetSourceModelIndexHint": root.get("RetargetSourceModelIndexHint", -1),
        },
        "stateMachine": root.get("StateMachine"),
        "animationSlots": root.get("AnimationSlots"),
        "animationSets": animation_sets,
        "modelReferences": model_references,
        "animationReferences": animation_references,
        "dependencies": dependencies,
        "uniqueTokenCount": root.get("NumUniqueTokenized", 0),
        "editorData": root.get("EditorData"),
        "extendedData": root.get("ExtendedData"),
    }


__all__ = ["is_gsf_raw", "project_gsf"]
