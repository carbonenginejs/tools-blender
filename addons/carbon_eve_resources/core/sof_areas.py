"""Puts each built material back in touch with the area that decided it.

A faction stores four material names PER AREA TYPE, so areas of different types
hold different materials in the same slot number.

`Tr2MeshArea` does not carry the area type -- it is consumed during the build --
so the type is recovered from the hull record and matched by INDEX. Nothing
here resolves a colour.

No ``bpy`` import in the matching; testable with the standard library.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import sof_resolution


#: Where a hull record keeps its areas. Every bucket is a draw ORDER, not a
#: kind of material -- an area's type is on the area, not the bucket -- so all
#: five are read and none is treated as special.
AREA_BUCKETS = ("opaqueAreas", "decalAreas", "transparentAreas",
                "additiveAreas", "distortionAreas")


def hull_areas(hull_record: Mapping[str, Any] | None) -> tuple[dict, ...]:
    """Every area a hull record declares, flattened, in document order."""

    found = []
    for bucket in AREA_BUCKETS:
        for area in (hull_record or {}).get(bucket) or []:
            if not isinstance(area, Mapping):
                continue
            found.append({
                "name": str(area.get("name") or ""),
                "index": _int(area.get("index")),
                "count": max(1, _int(area.get("count"), default=1)),
                "areaType": _int(area.get("areaType")),
                "blockedMaterials": _int(area.get("blockedMaterials")),
                "shader": str(area.get("shader") or "").rsplit("/", 1)[-1].lower(),
                "bucket": bucket,
            })
    return tuple(found)


def match_area(areas: Sequence[Mapping[str, Any]], *, name: str = "",
               index: int = -1, shader: str = "") -> dict | None:
    """The hull area a built material came from, or None if it cannot be told.

    Matched on the INDEX first, because that is what routes an area onto a
    geometry's material slots and is therefore the one field that has to be
    unique. Name is not: `mde3_t3` has two areas both called `area_sails`, so
    a name match would pick the first and quietly give the second area the
    first one's mask.

    Returns None rather than a guess. A material with no area is left showing
    what it was built with, which is at least true; giving it a made-up area
    type would let an edit paint it with materials it never had.
    """

    wanted = _int(index, default=-1)
    for area in areas:
        if area["index"] == wanted and wanted >= 0:
            if name and area["name"] and area["name"] != name:
                continue
            return dict(area)

    # Nothing matched on index. Fall back to a shader match only when it is
    # UNAMBIGUOUS -- one area with that shader -- because a wrong area type is
    # worse than none.
    if shader:
        candidates = [area for area in areas if area["shader"] == shader]
        if len(candidates) == 1:
            return dict(candidates[0])
    return None


def stamp_material(material, area: Mapping[str, Any] | None) -> bool:
    """Records an area's type and mask on the material it built.

    Custom properties rather than a registered group: these are facts ABOUT the
    material that a person never edits, and they have to survive a save and
    reload without an add-on registered to read them.
    """

    if material is None:
        return False
    if area is None:
        material["carbon_area_type"] = -1
        material["carbon_area_type_name"] = "unknown"
        material["carbon_blocked_materials"] = 0
        return False
    material["carbon_area_type"] = int(area.get("areaType", 0))
    material["carbon_area_type_name"] = sof_resolution.area_type_name(area.get("areaType"))
    material["carbon_blocked_materials"] = int(area.get("blockedMaterials", 0))
    if area.get("name"):
        material["carbon_area"] = area["name"]
    return True


def stamp_ship(objects: Iterable, hull_record: Mapping[str, Any] | None) -> dict:
    """Stamps every material of a ship, and says what it managed.

    Reports the misses as well as the hits: an area type nobody could recover
    is the difference between an edit reaching the right areas and reaching all
    of them, and that is worth saying out loud rather than discovering later.
    """

    areas = hull_areas(hull_record)
    result = {"materials": 0, "matched": 0, "unmatched": [], "types": {}}
    if not areas:
        return result

    seen = set()
    for target in objects:
        for slot in getattr(target, "material_slots", []):
            material = slot.material
            if material is None or material.name in seen:
                continue
            seen.add(material.name)
            result["materials"] += 1
            # The runtime says which area type it used, so where that is on
            # the material there is nothing to recover: matching is for output
            # that predates the annotation.
            authored = material.get("carbon_area_type")
            area = None
            if authored is not None:
                area = next((a for a in areas
                             if _int(a.get("areaType"), default=-1) == _int(authored, default=-2)),
                            {"areaType": _int(authored, default=0)})
            if area is None:
                area = match_area(
                    areas,
                    name=str(material.get("carbon_area", "") or ""),
                    index=_int(material.get("carbon_area_index"), default=-1),
                    shader=str(material.get("carbon_area_shader", "") or ""),
                )
            if stamp_material(material, area):
                result["matched"] += 1
                name = sof_resolution.area_type_name(area.get("areaType"))
                result["types"][name] = result["types"].get(name, 0) + 1
            else:
                result["unmatched"].append(material.name)
    return result


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
