"""What a material slot resolved from, and how to write a DNA back out.

A hull carries no colours. It says which areas exist and which material SLOT
each one uses, and the values come from the DNA or from the faction -- which is
why editing a ship needs a builder that resolves and pushes down, rather than a
loader that reads a hull.

This module does NOT resolve the colours themselves. tools-core does that, and
duplicating it here would be a second implementation that drifts in silence
while looking right. What it does is the part Blender needs and Node cannot
give back: recompose a DNA after an edit, and say for each slot WHERE its value
came from, so a consumer can tell an override from a default -- a distinction
the resolved values themselves have lost by the time they arrive.

The grammar is the runtime's, from `runtime/src/sof/EveSOFDNA.js`:

    hull[;hull2...]:faction:race[:command?arg[;arg...]]...

with the commands sorted by name, exactly as `EveSOFDNA` composes `this.dna`.

No ``bpy`` import, so this is testable with the standard library alone.
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

    Sorted because `EveSOFDNA` sorts when it composes: a DNA that differs only
    in command order is the same ship, and one that does not round-trip to the
    same text cannot be compared, cached or used as a directory name.
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

    Written as `material`, never `mesh`, even when the DNA arrived spelling it
    `mesh`: they mean the same thing and one spelling in the output is one
    fewer thing for a reader to know.

    A slot set back to `none` is written out as `none` rather than dropped,
    because dropping it would look identical to a slot nobody touched while
    meaning the same thing -- and if every slot is `none` the command itself
    goes, which is what a ship with no overrides at all actually is.
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

    The four hull slots first, then the two pattern layers. A hull slot the DNA
    names is an override; anything else came from the faction, which the DNA
    does not spell out and cannot be read back from the resolved colour.
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


def _padded(values: tuple[str, ...], width: int) -> tuple[str, ...]:
    """`values` at exactly `width`, `none` for anything missing.

    A short material command is real -- a DNA may name one slot and stop -- and
    a reader that indexes past the end either throws or, worse, shifts every
    remaining slot up by one.
    """

    filled = list(values[:width])
    while len(filled) < width:
        filled.append(NONE)
    return tuple(filled)
