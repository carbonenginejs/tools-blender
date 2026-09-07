"""Where this add-on may write, and making sure it can.

Two rules that were never in one place, which is how a 47MB parsed geometry
ended up inside somebody's EVE install:

1. **A file WE generate belongs in OUR cache.** Never beside its source, because
   a source may be somebody's game folder. Writing there puts our output in
   their input, and adding a file changes that directory whether or not it
   overwrites anything. `derived` is the only way to name such a path.
2. **The folder has to exist before the write.** That line was copied to
   fourteen separate call sites and each one was somebody remembering. A write
   that fails because a folder is missing does not announce itself - it just
   means the work is done again on every load.

The rules are one rule, and keeping them apart is what let the geometry path
get both halves wrong while the texture path had both right.
"""

from __future__ import annotations

from pathlib import Path


#: The roots this add-on knows about, filled in by `service_access`.
#:
#: `cache` is the only one we may WRITE to. The other two are read-only source
#: material: a folder of authored files, or a copy of - or a real - EVE install.
ROOTS = {"cache": None, "local": None, "resfiles": None}


def ensure_folder(folder):
    """Makes a folder, and hands it back. Safe to call when it exists."""

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ensure_parent(path):
    """Makes the folder a file is about to be written into, and hands back the file.

    Use at EVERY write site. It says nothing about whether the path is one we
    are allowed to write to - an export goes where a person asked, and that is
    their business. `derived` is what decides where OUR OWN output may land.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def derived(source, suffix: str, *, append: bool = False):
    """Where a file we generate FROM `source` belongs. Always inside our cache.

    Beside the source when the source is already ours: same folder, same name,
    plus `suffix`. The source is addressed by its CONTENT, so the derived file
    inherits that - shared by everything using it, and pruned with its build.

    A source read from a local folder is MIRRORED into the cache at the same
    relative position, so the derived file still lands somewhere stable and the
    folder it came from is left exactly as it was found. That is the whole
    point: a local folder may be a live EVE install.

    `append` adds the suffix to the name (`x` -> `x.parsed`); the default
    replaces an existing extension (`x.dds` -> `x.png`). Both are in use and
    they are not interchangeable - changing one would strand everything already
    derived under the other spelling.

    The folder is created, so a caller can write to the result directly.
    """

    source = Path(source)
    cache = ROOTS.get("cache")

    def named(path):
        path = Path(path)
        return (path.with_name(path.name + suffix) if append
                else path.with_suffix(suffix))

    if not cache:
        # Nothing configured to write to. Beside the source is the old
        # behaviour; a caller that cannot store its result treats it as one
        # that did not happen.
        return named(source)

    cache = Path(cache)
    for root in (cache, ROOTS.get("resfiles"), ROOTS.get("local")):
        if not root:
            continue
        try:
            relative = source.relative_to(Path(root))
        except ValueError:
            continue
        return ensure_parent(named(cache / relative))

    # Somewhere else entirely: keep it by name rather than refusing to derive.
    return ensure_parent(named(cache / "translated" / source.name))
