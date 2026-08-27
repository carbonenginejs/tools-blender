"""Runs `tools-core`'s SOF bundle builder for one DNA.

SOF/DNA composition belongs to Node. This module is only the launcher: it
finds the `cjs-sof-bundle` entry point, runs it with an explicit Node
executable, and reports where the finished bundle landed. It never parses DNA,
resolves hulls or factions, or decides what a build contains.

The module has no ``bpy`` dependency so it can be tested with the standard
library alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable, Optional, Sequence


BUNDLE_SCRIPT = Path("bin") / "cjs-sof-bundle.js"
# A DNA is hull:faction:race plus optional command sections. Reject anything
# that could reach the shell or the filesystem rather than the SOF catalog.
DNA_PATTERN = re.compile(r"^[A-Za-z0-9_:;?.\-]+$")
DNA_DIRECTORY_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class SofBuilderError(RuntimeError):
    """Raised when a DNA cannot be built into a bundle."""


@dataclass(frozen=True, slots=True)
class BundleBuild:
    dna: str
    directory: Path
    created: bool
    output: str = ""


def normalize_dna(value: str) -> str:
    dna = str(value or "").strip()
    if not dna:
        raise SofBuilderError("Enter a SOF DNA, for example cf1_t1:caldarinavy:caldari")
    if not DNA_PATTERN.match(dna):
        raise SofBuilderError(f"DNA contains unsupported characters: {dna}")
    if dna.count(":") < 2:
        raise SofBuilderError("A SOF DNA needs at least hull:faction:race")
    return dna


def bundle_directory_name(dna: str) -> str:
    """A stable, filesystem-safe directory name for one DNA."""

    return DNA_DIRECTORY_PATTERN.sub("_", normalize_dna(dna)).strip("_") or "bundle"


def resolve_bundle_script(tools_core_directory: Path | str) -> Path:
    root = Path(tools_core_directory).expanduser()
    if not root.is_dir():
        raise SofBuilderError(f"tools-core directory not found: {root}")
    script = root / BUNDLE_SCRIPT
    if not script.is_file():
        raise SofBuilderError(f"{script} not found; point at a tools-core checkout")
    return script


def resolve_node_executable(preferred: str = "") -> str:
    """Uses the configured Node, else the first one on PATH."""

    candidate = str(preferred or "").strip()
    if candidate:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
        located = shutil.which(candidate)
        if located:
            return located
        raise SofBuilderError(f"Node executable not found: {candidate}")
    located = shutil.which("node")
    if not located:
        raise SofBuilderError("Node.js was not found on PATH; set it in preferences")
    return located


def build_bundle(
    dna: str,
    *,
    tools_core_directory: Path | str,
    output_root: Path | str,
    cache_root: Optional[Path | str] = None,
    node_executable: str = "",
    build: str = "latest",
    target: str = "eve",
    raw_textures: bool = False,
    refresh: bool = False,
    timeout: float = 900.0,
    runner: Callable = subprocess.run,
) -> BundleBuild:
    """Builds one DNA into a bundle directory, reusing an existing build."""

    value = normalize_dna(dna)
    destination = Path(output_root).expanduser() / bundle_directory_name(value)
    if not refresh and (destination / "bundle.json").is_file():
        return BundleBuild(dna=value, directory=destination, created=False)

    script = resolve_bundle_script(tools_core_directory)
    command: list[str] = [
        resolve_node_executable(node_executable),
        str(script),
        "--dna",
        value,
        "--out",
        str(destination),
        "--target",
        str(target),
        "--build",
        str(build),
    ]
    if cache_root:
        command += ["--cache", str(Path(cache_root).expanduser())]
    if raw_textures:
        command.append("--raw-textures")

    output = _run(command, timeout=timeout, runner=runner)
    if not (destination / "bundle.json").is_file():
        raise SofBuilderError(
            f"tools-core did not write a bundle for {value}"
            + (f": {_tail(output)}" if output else "")
        )
    return BundleBuild(dna=value, directory=destination, created=True, output=output)


#: Windows: start the child without a console of its own.
#:
#: Blender's GUI process has no usable console, and a child that inherits its
#: handles gets a broken one rather than none. Node asserts on that inside
#: libuv before it runs a line of script -- "Assertion failed: process_title,
#: file src/win/util.c" and exit code 3221226505 -- which reads as tools-core
#: crashing when tools-core has not started yet.
CREATE_NO_WINDOW = 0x08000000


def _spawn_options() -> dict:
    """Keeps the child's console and stdin predictable rather than inherited."""

    options = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = CREATE_NO_WINDOW
    return options


def _run(command: Sequence[str], *, timeout: float, runner: Callable) -> str:
    try:
        completed = runner(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **_spawn_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SofBuilderError(f"tools-core timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise SofBuilderError(f"Could not run tools-core: {exc}") from exc

    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        raise SofBuilderError(
            f"tools-core exited with code {completed.returncode}"
            + (f": {_tail(output)}" if output else "")
        )
    return output


def _tail(output: str, limit: int = 300) -> str:
    text = " ".join(str(output or "").split())
    return text[-limit:]
