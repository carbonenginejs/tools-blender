"""The SOF enums, read from what the runtime generated.

These are not written here. `scripts/generate_sof_enums.py` reads them out of
the runtime's own classes and writes `sof_enums.json` beside this module, which
is committed and ships with the add-on -- the same arrangement as the generated
shader interface, and for the same reason: an enum typed out by hand is a copy
that drifts without saying so.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_NAME = "sof_enums.json"
SCHEMA = "carbon.sof-enums"

_cache = {}


def load() -> dict:
    """The generated document, read once."""

    if "document" not in _cache:
        source = Path(__file__).with_name(DATA_NAME)
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RuntimeError(
                f"{DATA_NAME} is missing; run scripts/generate_sof_enums.py") from None
        if document.get("schema") != SCHEMA:
            raise RuntimeError(f"{source} is not {SCHEMA} data")
        _cache["document"] = document
    return _cache["document"]


def names(enum: str) -> list:
    """One enum as a list indexed by value, lowercased.

    A gap in the numbering is an empty string rather than a shifted list: a
    shifted list is the failure the generator exists to prevent.
    """

    entry = (load().get("enums") or {}).get(enum)
    if entry is None:
        raise RuntimeError(f"No {enum!r} in {DATA_NAME}")
    return list(entry.get("names") or [])


def value(enum: str, member: str):
    """One member's number, by its name in the class."""

    entry = (load().get("enums") or {}).get(enum) or {}
    return (entry.get("members") or {}).get(member)
