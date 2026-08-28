"""What is left of the bundle builder: DNA validation, and one spawn detail.

The builder itself is gone. Bundles were folders of resolved resources written
by `cjs-sof-bundle`; ships are fetched directly now, and the reader for them
spoke a document format that is no longer written.

What survives is used elsewhere and had nowhere better to live: `normalize_dna`
validates a DNA string, and `_spawn_options` carries the Windows console
detail that any Node child needs.

No ``bpy`` dependency; testable with the standard library alone.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys


BUNDLE_SCRIPT = Path("bin") / "cjs-sof-bundle.js"
# A DNA is hull:faction:race plus optional command sections. Reject anything
# that could reach the shell or the filesystem rather than the SOF catalog.
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


def _spawn_options() -> dict:
    """Keeps the child's console and stdin predictable rather than inherited."""

    options = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = CREATE_NO_WINDOW
    return options

