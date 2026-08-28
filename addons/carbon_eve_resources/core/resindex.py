"""The EVE resource index: a logical path -> where its bytes are stored.

    eveclient_TQ.json          the current build
    eveonline_<build>.txt      the app index, which DECLARES the res index
      app:/resfileindex.txt    -> <shard>/<pathHash>_<md5>
    binaries.eveonline.com/<that>   the res index, 122k rows

With it, a resource needs no per-file question of anyone: the row carries the
storage location, and the bytes come from `resources.eveonline.com/<location>`.

Rows are `logicalPath,location,checksum,uncompressedSize,compressedSize[,mode]`
of which only the first two are required
(`runtime/src/tools/fileindex/CjsFileIndexEntry.parse`).

This raw index does NOT list the `effect.dx11`/`effect.dx12` shaders -- those
exist only in tools-core's composed overlays. Geometry and textures, which is
all a ship needs here, are all present.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from .tools_remote import USER_AGENT


BINARIES = "https://binaries.eveonline.com"
RESOURCES = "https://resources.eveonline.com"

#: The client metadata file, by channel token. Case sensitive: `eveclient_tq`
#: is a 404.
METADATA = "eveclient_{token}.json"
APP_INDEX = "eveonline_{build}.txt"

#: The app-index row that declares the main resource index.
DECLARATION = re.compile(r"^app:/resfileindex\.txt$", re.IGNORECASE)


class IndexError_(RuntimeError):
    """Raised when the index cannot be obtained."""


def _read(url: str, *, opener=urlopen, timeout: float = 180.0) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise IndexError_(f"{url}: {exc}") from exc


def current_build(token: str = "TQ", *, opener=urlopen) -> str:
    """The build the channel is on."""

    import json

    payload = _read(f"{BINARIES}/{METADATA.format(token=token)}", opener=opener)
    build = str((json.loads(payload.decode("utf-8")) or {}).get("build") or "")
    if not build:
        raise IndexError_("client metadata carried no build")
    return build


def index_location(build: str, *, opener=urlopen) -> str:
    """Where the resource index for one build is stored.

    Read from the APP index rather than guessed: the resource index is itself
    a content-addressed file, so its name changes with every build.
    """

    text = _read(f"{BINARIES}/{APP_INDEX.format(build=build)}",
                 opener=opener).decode("utf-8", "replace")
    for line in text.splitlines():
        columns = line.split(",")
        if len(columns) >= 2 and DECLARATION.match(columns[0].strip()):
            return columns[1].strip()
    raise IndexError_(f"build {build} declares no resfileindex.txt")


def index_file(cache_root, build: str, *, opener=urlopen) -> Path:
    """The index on disk, downloaded once per build."""

    destination = Path(cache_root) / "indexes" / f"resfileindex-{build}.txt"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    payload = _read(f"{BINARIES}/{index_location(build, opener=opener)}",
                    opener=opener)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    return destination


def load(cache_root, build: str, *, opener=urlopen) -> dict:
    """`{logical path: storage location}` for one build.

    Logical paths are lowercased, as `CjsFileIndexEntry.normalizeLogicalPath`
    does, so a caller's casing does not decide whether a lookup hits.
    """

    found = {}
    with open(index_file(cache_root, build, opener=opener), "r",
              encoding="utf-8", errors="replace") as handle:
        for line in handle:
            columns = line.split(",", 2)
            if len(columns) < 2:
                continue
            path = columns[0].strip().lower()
            location = columns[1].strip()
            if path and location:
                found[path] = location
    if not found:
        raise IndexError_(f"the index for build {build} parsed as empty")
    return found


def source_url(location: str) -> str:
    """Where CCP serves one stored file."""

    return f"{RESOURCES}/{str(location).replace(chr(92), '/').strip('/')}"


def locate(index, logical_path: str) -> Optional[str]:
    """The storage location for one path, or None when the index lacks it."""

    return index.get(str(logical_path).strip().lower().replace("\\", "/"))
