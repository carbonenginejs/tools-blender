"""Decoding in another PROCESS, because a thread is not enough.

Moving the decode to a worker thread did not free the main thread, and the
measurement said so plainly: with eight decode threads running, Blender's main
thread got 9.6% of the ticks it gets when idle, with stalls up to 330ms. That
is the GIL. Pure-Python work holds it, so "in a thread" and "off the main
thread" are different claims, and only the second one keeps a window alive.

A separate process has its own interpreter and its own GIL. The calling thread
then does nothing but wait on a pipe -- which DOES release the GIL -- so the
main thread runs at full speed, and the decodes finally run in parallel.

Blender ships a `python.exe` beside itself; that is what runs. If it cannot be
found or the child fails for any reason, the caller decodes in-process instead:
slow and rude, but never broken.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


#: Windows: no console window for each of these. Zero elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Enough for a 4096-square texture on a slow machine. A decode that takes
#: longer than this is stuck, and waiting forever would hang the fetch.
TIMEOUT = 300.0


def python_executable():
    """Blender's own interpreter, or None.

    `sys.executable` inside Blender is BLENDER, so running that would start a
    second Blender per texture. The bundled interpreter sits under `sys.prefix`.
    """

    for candidate in ("bin/python.exe", "bin/python3", "bin/python"):
        found = Path(sys.prefix) / candidate
        if found.is_file():
            return found
    return None


#: What the child runs. Kept here rather than in a file of its own so there is
#: one place to read: it puts the add-on on the path, imports the decoder --
#: which must not import bpy, and does not -- and writes the PNG.
SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from carbon_eve_resources.dds import reader
made = reader.decode_to_png(sys.argv[2], sys.argv[3])
sys.exit(0 if made else 2)
"""


def addons_directory() -> Path:
    """The folder the add-on package sits in, for the child's path."""

    return Path(__file__).resolve().parents[2]


def decode(source, destination, *, timeout: float = TIMEOUT):
    """Decodes one texture in a child process. True when it wrote the PNG.

    False means the caller should fall back rather than assume a failure is
    fatal: an interpreter that cannot be found, a child that dies, a texture
    that is not BC7 at all.
    """

    python = python_executable()
    if python is None:
        return False
    try:
        done = subprocess.run(
            [str(python), "-c", SCRIPT, str(addons_directory()),
             str(source), str(destination)],
            capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode == 0:
        return True
    if done.returncode != 2 and done.stderr:
        print(f"[CarbonEngineJS SOF] decode child: "
              f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return False
