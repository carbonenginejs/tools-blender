"""Where a resource's bytes are kept.

The index already says where every file lives -- `<shard>/<hash>_<md5>` in its
second column -- so that string is used as given and never recomputed. Working
the address out ourselves meant getting it right for a second time, and we did
not: the hash is taken over the LOWERCASED path, so every path with a capital
in it addressed a file that was not there and downloaded again on every load.

The stored file keeps its extension. Nothing needs it to find the file, but
`ab1_t1_a.dds` opens in whatever a person double-clicks and `b40590f110b66d26_
f26d631c8e5491e4c1f3273b29019fce` does not.
"""

from __future__ import annotations

from pathlib import Path
import re


ADDRESS = re.compile(r"^([a-f0-9]{2})/([a-f0-9]{16})_([a-f0-9]{32})$")


def parse(stored) -> dict | None:
    """The parts of a stored address, or None when it is not one.

    None rather than raising: an index legitimately carries plain paths for
    overlay entries alongside content-addressed ones.
    """

    found = ADDRESS.match(str(stored).replace("\\", "/").lower())
    if found is None:
        return None
    return {"shard": found.group(1), "path_hash": found.group(2),
            "checksum": found.group(3)}


def extension(logical_path: str) -> str:
    """The extension to keep on the stored file, `.dds` and the like."""

    return Path(str(logical_path or "").split("?", 1)[0]).suffix.lower()


def stored_path(root, location: str, logical_path: str = ""):
    """Where the file at one index location is kept, or None.

    None when the location is not an address, which is the caller's cue to fall
    back to the cache's human-readable layout.
    """

    parts = parse(location)
    if parts is None:
        return None
    name = f"{parts['path_hash']}_{parts['checksum']}{extension(logical_path)}"
    return Path(root) / "ResFiles" / parts["shard"] / name


def readable_path(root, logical_path: str):
    """The cache's other layout: the logical path as a person would write it.

    Where a file with no usable address goes, and the same place the optional
    local-files folder is read from, so nothing new has to know about it.
    """

    relative = str(logical_path or "").split(":/", 1)[-1].strip("/")
    return Path(root) / relative if relative else None
