"""SOF DNA validation shared by the hosted-service entry points.

No ``bpy`` dependency; testable with the standard library alone.
"""

from __future__ import annotations

import re


# A DNA is hull:faction:race plus optional command sections. Restrict it to the
# grammar accepted by the SOF service rather than forwarding arbitrary text.
DNA_PATTERN = re.compile(r"^[A-Za-z0-9_:;?.\-]+$")
DNA_DIRECTORY_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class SofBuilderError(RuntimeError):
    """Raised when a DNA cannot be built into a bundle."""


def normalize_dna(value: str) -> str:
    dna = str(value or "").strip()
    if not dna:
        raise SofBuilderError("Enter a SOF DNA, for example cf1_t1:caldarinavy:caldari")
    if not DNA_PATTERN.match(dna):
        raise SofBuilderError(f"DNA contains unsupported characters: {dna}")
    if dna.count(":") < 2:
        raise SofBuilderError("A SOF DNA needs at least hull:faction:race")
    return dna

