"""Where a resource's bytes are kept.

The index already says where every file lives -- `<shard>/<hash>_<md5>` in its
second column -- so that string is used as given and never recomputed. Working
the address out ourselves meant getting it right for a second time, and we did
not: the hash is taken over the LOWERCASED path, so every path with a capital
in it addressed a file that was not there and downloaded again on every load.

Nothing gets an extension here. The stored name is EVE's, exactly as given. A
file we translate -- a BC7 texture Blender cannot read -- is the one thing that
does, and it takes the same name with `.png` on the end.
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


def stored_path(root, location: str, logical_path: str = ""):
    """Where the file at one index location is kept, or None.

    No extension. This is EVE's own layout, byte for byte, so a file is found
    whoever put it there -- us, tools-core, or the game. Blender reads a DDS
    with no extension perfectly well (verified: 2048x1024, four channels), so
    there is nothing to gain by renaming what we were given.

    Only a file we TRANSLATED gets an extension, and it gets it by sitting
    beside its source under the same name -- see dds.reader.derived_path.

    None when the location is not an address, which is the caller's cue to fall
    back to the cache's human-readable layout.
    """

    return shard_path(Path(root) / "ResFiles", location)


def shard_path(root, location: str):
    """The `<shard>/<name>` part, under whatever root is given.

    Separate from `stored_path` because a folder somebody PICKS is the root of
    the shard tree whatever it happens to be called -- they may have renamed
    it, or pointed straight at it. Only our own cache is guaranteed to have
    `ResFiles` in the middle, so only our own path may assume it.
    """

    parts = parse(location)
    if parts is None:
        return None
    name = f"{parts['path_hash']}_{parts['checksum']}"
    return Path(root) / parts["shard"] / name


def readable_path(root, logical_path: str):
    """The cache's other layout: the logical path as a person would write it.

    Where a file with no usable address goes, and the same place the optional
    local-files folder is read from, so nothing new has to know about it.
    """

    relative = str(logical_path or "").split(":/", 1)[-1].strip("/")
    return Path(root) / relative if relative else None


def display_name(logical_path: str) -> str:
    """What to call this resource in Blender: `ab1_t1_a`.

    The stored file is named by its address, and an image datablock takes its
    name from the file, so the shader editor was a list of thirty-two hex
    digits. The logical path already carries the name the artist knows.
    """

    tail = str(logical_path or "").replace(chr(92), "/").rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[0] or tail


def export_destination(folder, logical_path: str, source):
    """Where one resource goes when a person asks for it by name.

    The logical layout -- `dx9/model/ship/amarr/ab1/ab1_t1_a.dds` -- because
    that is the layout the optional local-files folder READS. So an export
    folder is directly usable as an authored one: export, edit a file, and the
    next load picks up the edit instead of the cached original.

    The extension comes from the file being written, not from the logical path.
    A BC7 texture is exported as the PNG we translated it to, since that is the
    one anybody can open, and it must not be handed out under a `.dds` name.
    """

    destination = readable_path(folder, logical_path)
    if destination is None:
        return None
    suffix = Path(str(source)).suffix.lower()
    if suffix and suffix != destination.suffix.lower():
        destination = destination.with_suffix(suffix)
    return destination
