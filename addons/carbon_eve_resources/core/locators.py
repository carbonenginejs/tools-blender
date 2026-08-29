"""Every locator on a hull, in one shape.

A locator is a named place on a hull: where a turret bolts on, where an engine
fires from, where a docking light sits, where the camera should look. Carbon
puts several unrelated things there, and the SOF reports them in two different
formats, which is why anything that wants one has been reading the document
its own way.

The document carries:

* ``locators`` -- ``EveLocator2``, a NAME and a 4x4 matrix. The name is the
  type: ``locator_turret_1a``, ``locator_booster_3``, ``locator_audio_booster``.
* ``locatorSets`` -- ``EveLocatorSets``, a set name and a list of
  position/direction/scale triples with a bone index. Eighteen of them on a
  cruiser, from ``damage`` to ``vds_ambient``.

The same booster appears in BOTH: once as ``locator_booster_3`` and once in the
``boosters`` set. They are the same place said twice, so this reports each
locator once, preferring the named form because it carries the name.

No ``bpy`` import; testable with the standard library.
"""

from __future__ import annotations

import re
from typing import NamedTuple


#: `locator_<kind>_<n>` -- the name IS the type, which is how Carbon finds a
#: hull's turret hardpoints without a table.
NAME_PATTERN = re.compile(r"^locator_([a-z0-9]+)(?:_(.*))?$", re.IGNORECASE)

#: Set names that mean the same thing as a `locator_<kind>` prefix, so the two
#: formats land under one kind rather than two.
SET_KINDS = {"boosters": "booster"}


class Locator(NamedTuple):
    """One place on a hull."""

    #: `turret`, `booster`, `audio`, or the set's own name for a set.
    kind: str
    #: The full authored name where there is one, else `<set>_<index>`.
    name: str
    #: Which set it came from, or "" for a named locator.
    set_name: str
    #: Its order within its kind, from zero.
    index: int
    #: 16 floats, row-major, translation in the LAST row -- or None when the
    #: locator was given as a triple instead.
    transform: tuple = None
    #: The triple, when there is one. `direction` is a QUATERNION despite the
    #: name, which is the SOF's spelling and not a mistake here.
    position: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scaling: tuple = (1.0, 1.0, 1.0)
    #: The bone it follows, or -1. Named locators do not carry one.
    bone_index: int = -1


def _kind_of(name: str) -> tuple:
    """`(kind, remainder)` for an authored locator name."""

    found = NAME_PATTERN.match(str(name or ""))
    if not found:
        return "locator", str(name or "")
    return found.group(1).lower(), found.group(2) or ""


def _triple(entry) -> tuple:
    """`(position, rotation, scaling)` from a set entry.

    `direction` holds a quaternion. It is a quaternion called a direction in
    the SOF itself, so it is read as one and not converted into a heading.
    """

    return (tuple(float(v) for v in (entry.get("position") or (0, 0, 0))),
            tuple(float(v) for v in
                  (entry.get("direction") or entry.get("rotation")
                   or (0, 0, 0, 1))),
            tuple(float(v) for v in
                  (entry.get("scale") or entry.get("scaling") or (1, 1, 1))))


def locators(document) -> list:
    """Every locator on a hull, named ones first.

    A locator that appears in both formats is reported ONCE, from the named
    form, because the name is the only thing that says which turret bay or
    which engine it is.
    """

    found, counts, seen = [], {}, set()

    for entry in (document.get("locators") or []):
        name = str(entry.get("name") or "")
        kind, _ = _kind_of(name)
        index = counts.get(kind, 0)
        counts[kind] = index + 1
        transform = entry.get("transform")
        found.append(Locator(
            kind=kind, name=name, set_name="", index=index,
            transform=tuple(float(v) for v in transform) if transform else None))
        seen.add(kind)

    for group in (document.get("locatorSets") or []):
        set_name = str(group.get("name") or "")
        kind = SET_KINDS.get(set_name, set_name)
        if kind in seen:
            # Already reported under its authored names. The set says the same
            # places with less information, so it would be duplicates.
            continue
        for index, entry in enumerate(group.get("locators") or []):
            position, rotation, scaling = _triple(entry)
            found.append(Locator(
                kind=kind, name=f"{set_name}_{index}", set_name=set_name,
                index=index, position=position, rotation=rotation,
                scaling=scaling,
                bone_index=int(entry.get("boneIndex", -1))))

    return found


def of_kind(found, kind: str) -> list:
    """Just one kind, in document order."""

    return [locator for locator in found if locator.kind == kind]


def kinds(found) -> dict:
    """`{kind: count}`, for reporting what a hull actually has."""

    counted = {}
    for locator in found:
        counted[locator.kind] = counted.get(locator.kind, 0) + 1
    return counted
