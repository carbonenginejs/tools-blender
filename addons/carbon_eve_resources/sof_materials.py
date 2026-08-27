"""The SOF material catalog: what a slot is called, and what that name holds.

Two jobs, both of which need tools-core and neither of which resolves anything:

- name a slot. The faction serves its table already flattened, keyed
  `areaType:slotIndex`, so a slot's material NAME is a lookup rather than a
  derivation -- with the same fall back to the primary area the runtime makes.
- fill a slot. A named material is a bag of vec4 parameters, so choosing one
  from the dropdown is a fetch and an assignment.

Nothing here re-implements the resolution chain. The DNA still wins over the
faction, and tools-core still decides what a built ship looks like; this only
lets a person see which material a slot is showing and swap it for another.

No ``bpy`` import, so the lookups are testable with the standard library.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import sof_resolution


#: How a material's own parameter names map onto a slot's fields. A material
#: carries more than these -- `DustDiffuseColor` among them -- and the extras
#: are left alone rather than guessed at.
PARAMETER_FIELDS = {
    "DiffuseColor": "diffuse",
    "FresnelColor": "fresnel",
    "Gloss": "gloss",
}

#: Cached per (target, build): the catalog is 1149 names and does not change
#: between requests, so re-fetching it per dropdown draw would make the panel
#: hit the service on every redraw.
_CATALOG: dict[tuple, tuple] = {}
_MATERIALS: dict[tuple, dict] = {}
_FACTIONS: dict[tuple, dict] = {}


def faction_material_names(faction_record: Mapping[str, Any] | None) -> dict:
    """The faction's `areaType:slot` table, as served.

    tools-core flattens it for us -- the same shape `EveSOFDataMgr` builds --
    so this is a read, not a projection.
    """

    area_materials = (faction_record or {}).get("areaMaterials") or {}
    names = area_materials.get("materialNames") or {}
    return dict(names) if isinstance(names, Mapping) else {}


def material_name_for(names: Mapping[str, str], area_type: int, index: int) -> str:
    """The material a faction gives one slot of one area type.

    Falls back to the PRIMARY area exactly as the runtime does: an area whose
    own type names nothing for a slot inherits primary's rather than going
    unpainted. mde3_t3's sails name only slot 4, so their other three slots are
    primary's -- which is why only slot 4 measured differently.

    Returns "" when neither names it, rather than inventing one.
    """

    key = f"{int(area_type)}:{int(index) - 1}"
    found = names.get(key)
    if found:
        return str(found)
    if int(area_type) == sof_resolution.TYPE_PRIMARY:
        return ""
    fallback = names.get(f"{sof_resolution.TYPE_PRIMARY}:{int(index) - 1}")
    return str(fallback or "")


def material_values(material_record: Mapping[str, Any] | None) -> dict:
    """One material's parameters, as the fields a slot shows.

    `Gloss` is a vec4 with the value in x -- every SOF parameter is a vec4,
    including the scalars -- so it is read as a number and the rest as colours.
    """

    parameters = (material_record or {}).get("parameters") or {}
    if not isinstance(parameters, Mapping):
        return {}
    values = {}
    for name, field in PARAMETER_FIELDS.items():
        value = parameters.get(name)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            continue
        numbers = [float(item) for item in value[:4]]
        if not numbers:
            continue
        values[field] = numbers[0] if field == "gloss" else tuple(numbers[:3])
    return values


def catalog(client=None, *, build: str = "latest", target: str = "eve") -> tuple:
    """Every material name, sorted, cached per build.

    Returns an empty tuple when the service cannot be reached: a dropdown with
    nothing in it is a visible, honest failure, and the slot keeps whatever
    name it already had.
    """

    key = (target, build)
    if key in _CATALOG:
        return _CATALOG[key]
    if client is None:
        return ()
    try:
        # The catalog answers with an ARRAY, so it needs the call that tolerates
        # one. `_request` insists on an object root and rejected every name in
        # the list with a message about the root.
        names = client.request_json("GET", f"/{target}/{build}/sof/materials")
    except Exception:
        return ()
    if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
        return ()
    _CATALOG[key] = tuple(sorted(str(name) for name in names if name))
    return _CATALOG[key]


def material(name: str, client=None, *, build: str = "latest",
             target: str = "eve") -> dict:
    """One named material, cached. Empty when it cannot be fetched."""

    wanted = str(name or "").strip()
    if not wanted:
        return {}
    key = (target, build, wanted)
    if key in _MATERIALS:
        return _MATERIALS[key]
    if client is None:
        return {}
    try:
        record = client._request("GET", f"/{target}/{build}/sof/materials/{wanted}")
    except Exception:
        return {}
    if not isinstance(record, Mapping):
        return {}
    _MATERIALS[key] = dict(record)
    return _MATERIALS[key]


def faction(name: str, client=None, *, build: str = "latest",
            target: str = "eve") -> dict:
    """One faction record, cached. Empty when it cannot be fetched."""

    wanted = str(name or "").strip()
    if not wanted:
        return {}
    key = (target, build, wanted)
    if key in _FACTIONS:
        return _FACTIONS[key]
    if client is None:
        return {}
    try:
        record = client._request("GET", f"/{target}/{build}/sof/factions/{wanted}")
    except Exception:
        return {}
    if not isinstance(record, Mapping):
        return {}
    _FACTIONS[key] = dict(record)
    return _FACTIONS[key]


def forget():
    """Drops the caches, for when the build changes under us."""

    _CATALOG.clear()
    _MATERIALS.clear()
    _FACTIONS.clear()
