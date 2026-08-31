"""Parsing a GR2 somewhere other than Blender's main thread.

Measured on the two biggest hull files in the cache: reading a GR2 takes 17 to
19 seconds and building the Blender data from it takes 1.1 to 1.6 -- so 92 to
94 per cent of a geometry import is pure Python that Blender has no part in,
and every second of it is a window that does not repaint.

The parse therefore runs in a child process, like the texture decode, and its
result is written beside the source. Two things follow:

* the FIRST load is off the main thread, and several files parse at once;
* every load after that skips the parse entirely -- 0.31s to read the result
  back against 16.7s to produce it.

Handing it over is nearly free: the round trip measured 0.48s against 16.7s of
parsing, three per cent. It is not free in SPACE -- a 2.7MB hull parses to
about 67MB -- which is why it lands in the cache, content-addressed like
everything else, where pruning drops it with the build it belongs to.
"""

from __future__ import annotations

import pickle
from pathlib import Path


#: The parsed form is a translated file, and translated files carry an
#: extension. `.parsed` rather than `.pickle` because what matters is what it
#: is TO US, not which library wrote it.
SUFFIX = ".parsed"

#: Bumped when the parser's output changes shape. An old file is then ignored
#: rather than fed to a newer importer that expects something else.
VERSION = 2

#: What the child runs. It imports the PARSER package, which is free of
#: Blender. The add-on module next to it is Blender's side of the importer and
#: imports bpy at the top, so a child that reached for `read_gr2` there died on
#: the import -- and said nothing, because the fallback then parsed in-process
#: exactly as before. A silent fallback is indistinguishable from a feature
#: that was never written.
SCRIPT = """
import pickle, sys
sys.path.insert(0, sys.argv[1])
from carbon_gr2 import read_gr2

parsed = read_gr2(sys.argv[2], decompress_curves=True,
                  unpack_tangents=sys.argv[4] == "1",
                  rebuild_missing_normals=sys.argv[5] == "1")
with open(sys.argv[3] + ".part", "wb") as handle:
    pickle.dump({"version": %d, "parsed": parsed}, handle,
                protocol=pickle.HIGHEST_PROTOCOL)
import os
os.replace(sys.argv[3] + ".part", sys.argv[3])
""" % VERSION


def cache_path(source) -> Path:
    """Where one file's parsed form lives: beside it, with the extension."""

    source = Path(source)
    return source.with_name(source.name + SUFFIX)


def read(source):
    """The parsed form if it is on disk and current, else None."""

    found = cache_path(source)
    try:
        if not found.is_file() or found.stat().st_size == 0:
            return None
        with open(found, "rb") as handle:
            envelope = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError,
            ImportError, ValueError) as exc:
        # A parsed file we cannot read is not an error worth stopping for: the
        # source is still there and parsing it again always works.
        print(f"[CarbonEngineJS SOF] ignoring {found.name}: {exc}")
        return None
    if not isinstance(envelope, dict) or envelope.get("version") != VERSION:
        return None
    return envelope.get("parsed")


def prepare(source, *, unpack_tangents: bool = True,
            rebuild_missing_normals: bool = False, timeout: float = 600.0):
    """Parses one GR2 in a child process, unless it is already done.

    True when the parsed form is on disk afterwards. False is not a failure to
    report -- it means the import will parse it itself, on the main thread,
    exactly as it always did.
    """

    from ..dds import worker

    source = Path(source)
    destination = cache_path(source)
    if destination.is_file() and destination.stat().st_size > 0:
        return True

    python = worker.python_executable()
    if python is None:
        return False

    import subprocess

    try:
        done = subprocess.run(
            [str(python), "-c", SCRIPT, str(worker.addons_directory()),
             str(source), str(destination),
             "1" if unpack_tangents else "0",
             "1" if rebuild_missing_normals else "0"],
            capture_output=True, timeout=timeout,
            creationflags=worker.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode == 0 and destination.is_file():
        return True
    if done.stderr:
        print(f"[CarbonEngineJS SOF] parse child: "
              f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return False
