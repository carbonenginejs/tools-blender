"""Generates the SOF enums the add-on needs, from the runtime's own classes.

These enums exist already -- in `runtime/src/sof` and again in ccpwgl -- so
typing them out here would be a third copy that drifts in silence. It already
cost something: a hand-written three-entry banner table named a hull's
VERTICAL_BANNER "3", and reading the wrong field alongside it invented a CEO
portrait and a vertical banner that `mde3_t3` does not have.

The runtime cannot simply be imported: its classes are decorated, so plain Node
refuses the source. The enum bodies are read out of the source text instead,
which is mechanical, and loud when it fails -- a class that stops matching
raises here rather than quietly emitting a short table.

    python scripts/generate_sof_enums.py [--runtime ../runtime]

Writes `addons/carbon_eve_resources/sof_enums.json`, which is committed and
ships with the add-on, in the same spirit as the generated shader interface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


#: Which class carries which enum, by the name the add-on asks for.
WANTED = {
    "bannerUsage": ("sof/hull/EveSOFDataHullBanner.js", "Usage"),
    "decalUsage": ("sof/hull/EveSOFDataHullDecalSetItem.js", "Usage"),
}

#: Members that count the enum rather than belong to it.
SENTINELS = ("_USAGE_COUNT", "_COUNT")


def read_enum(source: str, name: str) -> dict:
    """The members of one `static <name> = Object.freeze({...})` block."""

    match = re.search(r"static\s+%s\s*=\s*Object\.freeze\(\{(.*?)\}\)" % re.escape(name),
                      source, re.S)
    if match is None:
        raise SystemExit(f"No `static {name} = Object.freeze(...)` in the class")

    members = {}
    for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?\d+)", match.group(1)):
        if key in SENTINELS:
            continue
        members[key] = int(value)
    if not members:
        raise SystemExit(f"`{name}` parsed as empty, which cannot be right")
    return members


def as_names(members: dict) -> list:
    """The enum as a list indexed by value, lowercased for a consumer to read.

    A gap in the numbering becomes an empty string rather than a shifted list,
    because a shifted list is exactly the failure this generator exists to
    prevent.
    """

    highest = max(members.values())
    names = [""] * (highest + 1)
    for key, value in members.items():
        if value >= 0:
            names[value] = key.lower()
    return names


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    package = os.path.dirname(here)
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime",
                        default=os.path.join(os.path.dirname(package), "runtime"),
                        help="The runtime checkout to read the classes from")
    parser.add_argument("--out",
                        default=os.path.join(package, "addons", "carbon_eve_resources",
                                             "sof_enums.json"))
    arguments = parser.parse_args(argv)

    document = {
        "schema": "carbon.sof-enums",
        "version": 1,
        "source": "runtime/src/sof",
        "enums": {},
    }
    for wanted, (relative, enum) in WANTED.items():
        path = os.path.join(arguments.runtime, "src", relative)
        if not os.path.exists(path):
            raise SystemExit(f"{path} is not there; pass --runtime")
        with open(path, encoding="utf-8") as handle:
            members = read_enum(handle.read(), enum)
        document["enums"][wanted] = {
            "class": os.path.basename(relative)[:-3],
            "members": members,
            "names": as_names(members),
        }
        print(f"  {wanted}: {len(members)} members from {os.path.basename(relative)}")

    with open(arguments.out, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    print(f"  wrote {arguments.out}")


if __name__ == "__main__":
    # Blender passes its own arguments before `--`, so only what follows is
    # ours. Run either way: with the system Python, or through Blender.
    ours = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    main([argument for argument in ours if not argument.startswith("--background")
          and not argument.startswith("--factory")
          and argument != "--python" and not argument.endswith(".py")])
