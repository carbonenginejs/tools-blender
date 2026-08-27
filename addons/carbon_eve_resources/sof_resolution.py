"""DNA grammar, and what decided each material slot.

    hull[;hull2...]:faction:race[:command?arg[;arg...]]...

Lower case, commands sorted by name, as `EveSOFDNA` composes `this.dna`
(`runtime/src/sof/EveSOFDNA.js`).

Does NOT resolve colours -- tools-core does. This says where a slot's value
came from, which the resolved values no longer know.

No ``bpy`` import; testable with the standard library alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


#: A slot that says this is not an override. The search continues past it, so
#: `none` and "absent" resolve identically -- which is why a DNA of all-`none`
#: materials is a faction-coloured ship rather than a colourless one.
NONE = "none"

#: `mesh` is the spelling live EVE skins are authored with, and it means
#: `material`. The runtime normalizes one into the other on parse; anything
#: reading a DNA has to do the same or it drops a skin's materials entirely.
MATERIAL_ALIASES = ("material", "mesh")

#: How many material slots a hull area can name, and how many layers a pattern
#: paints. Both are fixed by the shader: `Mtl1..4` and `PMtl1..2` are constant
#: buffer registers, not a list that can grow.
MATERIAL_SLOTS = 4
PATTERN_SLOTS = 2

#: Where a slot's value came from. `DNA` is an override someone authored;
#: `FACTION` is what the ship would look like with no skin at all.
SOURCE_DNA = "dna"
SOURCE_FACTION = "faction"


#: The area types, by value, from `EveSOFDataArea.AreaType`. A faction stores
#: four material names PER AREA TYPE. 5 (Wreck) comes from the generic data,
#: and 11 doubles as TYPE_NO_OVERWRITE.
AREA_TYPES = (
    "primary", "glass", "sails", "reactor", "darkhull", "wreck",
    "rock", "monument", "ornament", "simpleprimary", "turret",
)
TYPE_PRIMARY = 0

#: An area whose own type names no material falls back to PRIMARY rather than
#: going unpainted, which is why a hull that authors only its primary area
#: still shades everything.
AREA_TYPE_FALLBACK = TYPE_PRIMARY


def area_type_name(area_type) -> str:
    """A readable name for an area type value."""

    try:
        index = int(area_type)
    except (TypeError, ValueError):
        return "?"
    if 0 <= index < len(AREA_TYPES):
        return AREA_TYPES[index]
    return f"type {index}"


def is_blocked(blocked_materials, index: int) -> bool:
    """Whether an area vetoes the DNA's override for slot `index` (1-based).

    `blockedMaterials` is a bitmask on the hull area. mde3_t3's sails carry 12
    -- bits 2 and 3 -- so they keep the faction's Mtl3 and Mtl4.
    """

    try:
        mask = int(blocked_materials or 0)
    except (TypeError, ValueError):
        return False
    slot = int(index) - 1
    if slot < 0 or slot >= 32:
        return False
    return bool((mask >> slot) & 1)


class DnaError(ValueError):
    """Raised when a DNA cannot be read as one."""


@dataclass(frozen=True, slots=True)
class SlotSource:
    """One material slot, and what decided it."""

    index: int
    source: str
    material: str = ""
    is_pattern: bool = False

    @property
    def from_dna(self) -> bool:
        return self.source == SOURCE_DNA

    def describe(self) -> str:
        if self.is_pattern:
            return f"pattern layer {self.index}: {self.material or NONE} (always from the DNA)"
        if self.from_dna:
            return f"material {self.index}: {self.material} (from the DNA)"
        return f"material {self.index}: from the faction"


@dataclass(frozen=True, slots=True)
class Dna:
    """A parsed DNA: the three components, plus whatever commands it carries."""

    hulls: tuple[str, ...]
    faction: str
    race: str
    commands: Mapping[str, tuple[str, ...]]

    @property
    def hull(self) -> str:
        return self.hulls[0] if self.hulls else ""

    def args(self, command: str) -> tuple[str, ...]:
        return tuple(self.commands.get(command, ()))

    @property
    def materials(self) -> tuple[str, ...]:
        """The four material arguments, `none`-filled to full width."""

        for name in MATERIAL_ALIASES:
            found = self.args(name)
            if found:
                return _padded(found, MATERIAL_SLOTS)
        return (NONE,) * MATERIAL_SLOTS

    @property
    def pattern(self) -> tuple[str, ...]:
        """The pattern name and its two layer materials, or empty if none."""

        return self.args("pattern")

    def compose(self) -> str:
        return compose(self.hulls, self.faction, self.race, self.commands)


def parse(dna: str) -> Dna:
    """Reads a DNA string the way the runtime's parser does.

    Lowercased, because the runtime lowercases before anything else -- a DNA
    authored `MATERIAL?...` is real and a case-sensitive reader rejects it.
    """

    text = str(dna or "").strip().lower()
    parts = text.split(":")
    if len(parts) < 3:
        raise DnaError("A SOF DNA needs at least hull:faction:race")

    commands: dict[str, tuple[str, ...]] = {}
    for section in parts[3:]:
        name, separator, payload = section.partition("?")
        if not separator:
            raise DnaError(f"Command section is missing its `?`: {section}")
        commands[name] = tuple(payload.split(";"))

    # `mesh` IS `material`. Normalizing on the way in means everything
    # downstream asks for one name and simply works.
    if "mesh" in commands and "material" not in commands:
        commands["material"] = commands["mesh"]

    hulls = tuple(part for part in parts[0].split(";") if part)
    if not hulls:
        raise DnaError("A SOF DNA needs a hull")
    return Dna(hulls=hulls, faction=parts[1], race=parts[2], commands=commands)


def compose(hulls, faction: str, race: str,
            commands: Mapping[str, Sequence[str]] | None = None) -> str:
    """Writes a DNA back out, commands sorted by name.

    Sorted, as `EveSOFDNA` sorts: a DNA differing only in command order is the
    same ship.
    """

    if isinstance(hulls, str):
        hulls = [hulls]
    hull = ";".join(part for part in (str(value).strip().lower() for value in hulls) if part)
    if not hull:
        raise DnaError("A SOF DNA needs a hull")
    dna = f"{hull}:{str(faction).strip().lower()}:{str(race).strip().lower()}"
    for name in sorted(commands or {}):
        args = [str(value).strip().lower() for value in commands[name]]
        if not args:
            continue
        dna += f":{name}?{';'.join(args)}"
    return dna


def with_materials(dna: str | Dna, materials: Sequence[str]) -> str:
    """The same DNA with its material slots replaced.

    Written as `material`, never `mesh`. Every slot `none` drops the command.
    """

    parsed = dna if isinstance(dna, Dna) else parse(dna)
    wanted = [str(value or NONE).strip().lower() or NONE
              for value in _padded(tuple(materials), MATERIAL_SLOTS)]
    commands = {name: args for name, args in parsed.commands.items()
                if name not in MATERIAL_ALIASES}
    if any(value != NONE for value in wanted):
        commands["material"] = tuple(wanted)
    return compose(parsed.hulls, parsed.faction, parsed.race, commands)


def with_pattern(dna: str | Dna, pattern: str, layers: Sequence[str] = ()) -> str:
    """The same DNA with its pattern command replaced, or removed if empty."""

    parsed = dna if isinstance(dna, Dna) else parse(dna)
    commands = {name: args for name, args in parsed.commands.items() if name != "pattern"}
    name = str(pattern or "").strip().lower()
    if name:
        commands["pattern"] = (name,) + tuple(
            str(value or NONE).strip().lower() or NONE
            for value in _padded(tuple(layers), PATTERN_SLOTS))
    return compose(parsed.hulls, parsed.faction, parsed.race, commands)


def slot_sources(dna: str | Dna) -> tuple[SlotSource, ...]:
    """What decided each material slot, in panel order.

    Four hull slots, then two pattern layers.
    """

    parsed = dna if isinstance(dna, Dna) else parse(dna)
    found = []
    for index, material in enumerate(parsed.materials, start=1):
        override = material != NONE and bool(material)
        found.append(SlotSource(
            index=index,
            source=SOURCE_DNA if override else SOURCE_FACTION,
            material=material if override else "",
        ))

    # A pattern's layers are ALWAYS in the DNA -- there is no faction fallback
    # for them, which is how a SKIN repaints a hull whose faction says nothing.
    pattern = parsed.pattern
    for index in range(1, PATTERN_SLOTS + 1):
        material = pattern[index] if len(pattern) > index else ""
        found.append(SlotSource(index=index, source=SOURCE_DNA,
                                material=material, is_pattern=True))
    return tuple(found)


def area_slot_sources(dna: str | Dna, area_type=TYPE_PRIMARY,
                      blocked_materials=0) -> tuple[SlotSource, ...]:
    """What decided each slot FOR ONE AREA, which is the honest question.

    Differs from `slot_sources` wherever `blockedMaterials` is set: that area
    keeps the faction's material while its neighbours take the skin's.
    """

    parsed = dna if isinstance(dna, Dna) else parse(dna)
    found = []
    for index, material in enumerate(parsed.materials, start=1):
        blocked = is_blocked(blocked_materials, index)
        override = bool(material) and material != NONE and not blocked
        found.append(SlotSource(
            index=index,
            source=SOURCE_DNA if override else SOURCE_FACTION,
            material=material if override else "",
        ))

    # Pattern layers never consult the area type; only the pattern TEXTURE is
    # per-area, which is a separate mechanism.
    pattern = parsed.pattern
    for index in range(1, PATTERN_SLOTS + 1):
        material = pattern[index] if len(pattern) > index else ""
        found.append(SlotSource(index=index, source=SOURCE_DNA,
                                material=material, is_pattern=True))
    return tuple(found)


def _padded(values: tuple[str, ...], width: int) -> tuple[str, ...]:
    """`values` at exactly `width`, `none` for anything missing.

    A short material command is real: a DNA may name one slot and stop.
    """

    filled = list(values[:width])
    while len(filled) < width:
        filled.append(NONE)
    return tuple(filled)
