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


#: The same trick for a nebula. It is a bigger job than a texture -- six cube
#: faces of BC6H, about twenty seconds -- which is exactly why it belongs in
#: another process rather than in front of the artist.
#: It also takes over a minute, so it reports where it has got to. A job that
#: says nothing for that long is indistinguishable from one that has hung.
ENVIRONMENT_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from carbon_eve_resources.dds import environment


def say(line):
    sys.stdout.write("PROGRESS " + line + "\\n")
    sys.stdout.flush()


environment.convert_file(sys.argv[2], sys.argv[3], progress=say)
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

    return _run(SCRIPT, source, destination, timeout=timeout)


#: A nebula is six faces of block decoding: over a minute, where a texture is
#: seconds.
ENVIRONMENT_TIMEOUT = 900.0


def convert_environment(source, destination, *, progress=None,
                        timeout: float = ENVIRONMENT_TIMEOUT):
    """Turns one nebula cube into a Radiance `.hdr`. True when it wrote it."""

    return _run(ENVIRONMENT_SCRIPT, source, destination, timeout=timeout,
                progress=progress)


def _run(script: str, source, destination, *, timeout: float, progress=None):
    """One child, one job. False means fall back rather than fail."""

    python = python_executable()
    if python is None:
        return False
    command = [str(python), "-c", script, str(addons_directory()),
               str(source), str(destination)]
    if progress is None:
        try:
            done = subprocess.run(command, capture_output=True, timeout=timeout,
                                  creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return False
        return _finished(done.returncode, done.stderr)

    # Reading the child's output line by line, so a long job can say where it
    # has got to. The wait is on a PIPE, which releases the GIL -- the whole
    # reason the work is in a child at all.
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        for raw in child.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("PROGRESS "):
                progress(line[9:])
        child.wait(timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        child.kill()
        return False
    return _finished(child.returncode, child.stderr.read())


def _finished(code: int, stderr: bytes) -> bool:
    if code == 0:
        return True
    if code != 2 and stderr:
        print(f"[CarbonEngineJS SOF] worker child: "
              f"{stderr.decode('utf-8', 'replace').strip()[:200]}")
    return False
