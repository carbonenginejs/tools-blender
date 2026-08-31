"""Pure-Python Granny State reader and dependency projection."""

from __future__ import annotations

import os
import struct
from typing import Any

from carbon_granny import RawGr2, read_raw

from .projection import is_gsf_raw, project_gsf

__version__ = "0.1.0"


class GsfError(ValueError):
    """A malformed or non-GState Granny document."""


def _read_source(source: Any) -> RawGr2:
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return read_raw(stream)
    return read_raw(source)


def read_gsf(source: Any) -> dict:
    """Read a GSF state document without resolving its external GR2 files."""

    try:
        return project_gsf(_read_source(source))
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as error:
        raise GsfError(str(error)) from error


def is_gsf(source: Any) -> bool:
    try:
        return is_gsf_raw(_read_source(source))
    except (OSError, ValueError, TypeError, IndexError, struct.error):
        return False


def inspect(source: Any) -> dict:
    raw = _read_source(source)
    value = project_gsf(raw)
    return {
        "format": "gsf",
        "version": raw.version,
        "sectionCount": raw.section_count,
        "animationSlotCount": len(value.get("animationSlots") or []),
        "animationSetCount": len(value.get("animationSets") or []),
        "sourceFileReferences": [
            dependency["reference"] for dependency in value.get("dependencies") or []
        ],
    }


__all__ = [
    "GsfError",
    "RawGr2",
    "inspect",
    "is_gsf",
    "is_gsf_raw",
    "project_gsf",
    "read_gsf",
    "__version__",
]
