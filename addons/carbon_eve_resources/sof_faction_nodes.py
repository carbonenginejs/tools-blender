"""The faction's colours, as one source everything on the ship reads.

Most colours on a hull are the FACTION's, not the hull's. The faction carries
a colour set of thirty-eight named slots -- `primary`, `secondaryLight`,
`tertiarySpotlight`, `killmark`, `hull`, `glass` -- and a sprite, a spotlight
or a banner does not carry a colour of its own so much as name one of those.

That is checkable rather than assumed. An Abaddon's sprites come out
(0.761, 0.518, 0.271) and (0.541, 0.188, 0.106), which are that faction's
`primary` and `secondary` exactly; and the same hull under `empire_gallente`
gives different ones. The document hands us the resolved value, so the slot is
recovered by matching it back.

So the colours live in ONE node group per faction and every child reads it,
the same way the area shaders read a shared `SofMaterial` group. Change
`primary` there and every sprite, spotlight and banner that named it follows.

The set is EveSOFDataFactionColorSet in the runtime -- 44 types, of which the
factions on Tranquility populate thirty-eight.
"""

from __future__ import annotations

from typing import Mapping

import bpy


PREFIX = "SofFaction"

#: How close two colours must be to count as the same slot.
#:
#: They come from the same source and travel as float32 through JSON, so an
#: exact match is nearly always right; this is slack for the last bit, not a
#: search for something that looks similar. Too generous and a sprite would
#: bind to a neighbouring slot and follow the wrong colour ever after.
TOLERANCE = 1e-4


#: Colour TYPE by index, from EveSOFDataFactionColorSet.Types in the runtime.
#:
#: This is how a child names its colour where it names one at all: a decal
#: carries `glowColorType`, an index into this list, and the faction supplies
#: the value. That is better than matching a resolved colour back to a slot,
#: because it is what the data actually says rather than what two numbers
#: happen to agree on.
#:
#: The service answers with the same names in camelCase, which is the spelling
#: used here so a lookup needs no translation.
COLOUR_TYPES = (
    "primary", "secondary", "tertiary", "black", "white", "yellow",
    "orange", "red", "blue", "green", "cyan", "fire", "hull", "glass",
    "reactor", "darkhull", "booster", "killmark",
    "primaryLight", "secondaryLight", "tertiaryLight", "whiteLight",
    "primaryHologram", "secondaryHologram", "tertiaryHologram",
    "state0", "state1", "state2", "state3",
    "stateVulnerable", "stateInvulnerable",
    "primaryForcefield", "secondaryForcefield", "primaryBanner",
    "primaryFX", "secondaryFX",
    "primarySpotlight", "secondarySpotlight", "tertiarySpotlight",
    "primaryBillboard", "primaryWarpFx", "primaryAttackFX",
    "primarySiegeFX", "primaryDockedFX",
)


def slot_named(index):
    """The slot one colour TYPE index refers to, or None.

    None for an index outside the list rather than a guess: a colour bound to
    the wrong slot follows the wrong thing forever after, and silently.
    """

    try:
        index = int(index)
    except (TypeError, ValueError):
        return None
    if 0 <= index < len(COLOUR_TYPES):
        return COLOUR_TYPES[index]
    return None


def group_name(faction: str) -> str:
    return f"{PREFIX} {str(faction or 'unnamed')}"


def colour_slots(record) -> dict:
    """`{slot: (r, g, b, a)}` from a faction record, or empty.

    The service answers with `colorSet.colors`; a faction with none is not an
    error, it just has nothing for anybody to read.
    """

    colours = ((record or {}).get("colorSet") or {}).get("colors") or {}
    found = {}
    for name, value in colours.items():
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            found[str(name)] = tuple(float(v) for v in (list(value) + [1.0])[:4])
    return found


def faction_group(faction: str, slots, *, rebuild: bool = False):
    """The node group holding one faction's colours, made once and shared.

    An existing group is returned untouched: it IS the faction as far as the
    scene is concerned, and re-filling it would undo an edit somebody made.
    """

    name = group_name(faction)
    tree = bpy.data.node_groups.get(name)
    if tree is not None and not rebuild:
        return tree
    if tree is None:
        tree = bpy.data.node_groups.new(name, "ShaderNodeTree")

    tree.nodes.clear()
    for item in list(tree.interface.items_tree):
        tree.interface.remove(item)

    output = tree.nodes.new("NodeGroupOutput")
    output.location = (300, 0)

    for index, slot in enumerate(sorted(slots)):
        tree.interface.new_socket(name=slot, in_out="OUTPUT",
                                  socket_type="NodeSocketColor")
        source = tree.nodes.new("ShaderNodeRGB")
        source.location = (0, -220 * index)
        source.label = slot
        source.name = slot
        source.outputs[0].default_value = slots[slot]
        tree.links.new(source.outputs[0], output.inputs[index])

    tree["carbon_faction"] = str(faction)
    return tree


#: The runtime's SOF6 provenance properties: the original SOF selector kept
#: beside the resolved value, under its own source field name with an
#: underscore. Nothing here is invented -- no `_colorSet`, no `_colorTypes`,
#: no reverse-inference from a colour.
#:
#: `_colorType`      sprite, spotlight, plane and haze items, and their lights
#: `_glowColorType`  a decal whose glow was resolved from glowColorType
#: `_lightColor`     a hull light owning the SOF6 lightColor selector
#:
#: A colour written as a vector carries none of these, because it named no
#: slot: race booster colours, banner light colours and the damage emitters.
SELECTORS = ("_colorType", "_glowColorType", "_lightColor")


def slot_for(item, key="", slots=None, colour=None):
    """Which faction colour a thing NAMED, by its own selector.

    The selector is the answer, not the value. Matching a resolved colour back
    to a slot is a guess that happens to be right most of the time and cannot
    be right always: on amarrbase `primary` and `secondarySpotlight` hold the
    same colour, so a match binds to whichever comes first and carries the
    wrong label ever after.

    So the selector is read where the runtime provides one, and matching is
    left as the interim path for output that predates it. When the value came
    from a vector rather than a selector there is nothing to name and nothing
    is invented.
    """

    if isinstance(item, Mapping):
        for name in ((key,) if key else ()) + SELECTORS:
            if name and name in item:
                slot = slot_named(item.get(name))
                if slot is not None:
                    return slot
    return slot_of(slots, colour)


def slot_of(slots, colour, tolerance: float = TOLERANCE):
    """Which slot this colour IS, or None.

    The document gives the resolved colour and never says which slot it came
    from, so this is how a child is reconnected to its source. None means the
    colour is the item's own and stays a literal -- better a colour that does
    not follow the faction than one that follows the wrong slot.
    """

    if not slots or colour is None:
        return None
    wanted = tuple(float(v) for v in (list(colour) + [1.0])[:3])
    for name in sorted(slots):
        value = slots[name]
        if all(abs(value[index] - wanted[index]) <= tolerance
               for index in range(3)):
            return name
    return None
