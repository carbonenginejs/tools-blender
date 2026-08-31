"""Installs this repository's add-on over the one Blender actually loads.

Blender loads add-ons from its own scripts directory, NOT from a checkout, and
nothing warns you when the two drift apart. The failure is quiet and expensive:
the panel keeps working, so a change that has been written, tested and pushed
appears to have no effect, and the natural conclusion is that the change is
wrong rather than absent. A month-old copy once made a hull load with the old
approximate materials while the repository had built the accurate ones.

Usage:
    blender --background --factory-startup --python scripts/install_addon.py
    blender --background --factory-startup --python scripts/install_addon.py -- --dry-run

Copies rather than links, because a link across drives is not portable and
Blender's importer caches by path either way. Stale `__pycache__` is removed, or
Python will happily import last month's bytecode.
"""

from __future__ import annotations

import os
import shutil
import sys

import bpy

PACKAGES = {
    "carbon_eve_resources": ("addons", "carbon_eve_resources"),
    "carbon_cmf": ("packages", "carbon-cmf", "src", "carbon_cmf"),
    "carbon_granny": ("packages", "carbon-granny", "src", "carbon_granny"),
    "carbon_gr2": ("packages", "carbon-gr2", "src", "carbon_gr2"),
    "carbon_gsf": ("packages", "carbon-gsf", "src", "carbon_gsf"),
}


def repository_addons() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "addons")


def repository_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def blender_addons() -> str:
    return os.path.join(bpy.utils.user_resource("SCRIPTS", path="addons", create=True))


def install(dry_run: bool = False) -> int:
    source_root = repository_root()
    target_root = blender_addons()
    print(f"  from {source_root}")
    print(f"  to   {target_root}")

    installed = 0
    for package, relative_source in PACKAGES.items():
        source = os.path.join(source_root, *relative_source)
        target = os.path.join(target_root, package)
        if not os.path.isdir(source):
            print(f"  ! {package} is not in the repository")
            continue

        existing = "replacing" if os.path.isdir(target) else "installing"
        print(f"  {existing} {package}")
        if dry_run:
            continue

        if os.path.isdir(target):
            shutil.rmtree(target)
        # Bytecode from the previous copy would otherwise be imported in
        # preference to the source that just landed.
        shutil.copytree(source, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        installed += 1

    if not dry_run:
        print(f"  installed {installed} package(s); restart Blender to load them")
    return installed


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    install(dry_run="--dry-run" in arguments)
