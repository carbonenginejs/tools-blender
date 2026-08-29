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

import bpy


PREFIX = "SofFaction"

#: How close two colours must be to count as the same slot.
#:
#: They come from the same source and travel as float32 through JSON, so an
#: exact match is nearly always right; this is slack for the last bit, not a
#: search for something that looks similar. Too generous and a sprite would
#: bind to a neighbouring slot and follow the wrong colour ever after.
TOLERANCE = 1e-4


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
