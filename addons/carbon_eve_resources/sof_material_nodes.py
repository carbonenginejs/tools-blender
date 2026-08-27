"""A node group per SOF material, which the area shaders read.

A material is one node group, named once and shared. An area's shader links to
the groups its slots name: setting a slot repoints a reference, and editing a
material reaches every area reading it.

A slot that edits a colour takes a private copy and says `custom`, so a shared
material is not repainted for every other slot that names it.
"""

from __future__ import annotations

import bpy


#: Prefix for the groups this module owns, so they are recognisable in the
#: outliner and cannot collide with an authored group of the same name.
PREFIX = "SOF"

#: The three values a slot reads, and the socket type each needs.
OUTPUTS = (
    ("Diffuse", "NodeSocketColor", "diffuse"),
    ("Fresnel", "NodeSocketColor", "fresnel"),
    ("Gloss", "NodeSocketFloat", "gloss"),
)

#: Where a slot's values land on the quad group, by slot index.
MATERIAL_SOCKETS = ("Mtl{}DiffuseColor", "Mtl{}FresnelColor", "Mtl{}Gloss")
PATTERN_SOCKETS = ("PMtl{}DiffuseColor", "PMtl{}FresnelColor", "PMtl{}Gloss")


def group_name(material: str) -> str:
    return f"{PREFIX} {str(material or 'unnamed')}"


def material_group(material: str, values=None, *, rebuild: bool = False):
    """The node group for one named SOF material, made once and shared.

    An existing group is returned untouched: it IS the material.
    """

    name = group_name(material)
    tree = bpy.data.node_groups.get(name)
    if tree is not None and not rebuild:
        # Re-filling would undo an edit; `rebuild` asks for that explicitly.
        return tree
    if tree is None:
        tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
        # Blender drops zero-user datablocks on save.
        tree.use_fake_user = True

    tree.nodes.clear()
    for socket in list(tree.interface.items_tree):
        tree.interface.remove(socket)

    output = tree.nodes.new("NodeGroupOutput")
    output.location = (300, 0)
    for row, (label, kind, _field) in enumerate(OUTPUTS):
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type=kind)
        if kind == "NodeSocketColor":
            node = tree.nodes.new("ShaderNodeRGB")
        else:
            node = tree.nodes.new("ShaderNodeValue")
        node.name = label
        node.label = label
        node.location = (0, -220 * row)
        tree.links.new(node.outputs[0], output.inputs[row])

    tree["carbon_sof_material"] = str(material or "")
    if values:
        _fill(tree, values)
    return tree


def _fill(tree, values) -> None:
    """Writes a material's fetched values into its group."""

    for label, kind, field in OUTPUTS:
        node = tree.nodes.get(label)
        value = values.get(field) if hasattr(values, "get") else None
        if node is None or value is None:
            continue
        if kind == "NodeSocketColor":
            node.outputs[0].default_value = tuple(value)[:3] + (1.0,)
        else:
            node.outputs[0].default_value = float(value)


def group_values(tree) -> dict:
    """What a material group currently holds, for a panel to show."""

    values = {}
    if tree is None:
        return values
    for label, kind, field in OUTPUTS:
        node = tree.nodes.get(label)
        if node is None:
            continue
        if kind == "NodeSocketColor":
            values[field] = tuple(node.outputs[0].default_value)[:3]
        else:
            values[field] = float(node.outputs[0].default_value)
    return values


def quad_group_node(material):
    """The area's quad group node, which is what reads the slots."""

    if material is None or not material.use_nodes:
        return None
    return next((node for node in material.node_tree.nodes
                 if node.bl_idname == "ShaderNodeGroup" and node.node_tree
                 and node.node_tree.name.startswith(PREFIX) is False
                 and "Pattern" not in node.node_tree.name), None)


def bind_slot(material, index: int, tree, *, is_pattern: bool = False) -> int:
    """Links one material group into one slot of an area's shader.

    Returns how many sockets were linked; a socket the shader lacks is
    skipped.
    """

    quad = quad_group_node(material)
    if quad is None or tree is None:
        return 0

    node_tree = material.node_tree
    node_name = f"{PREFIX} slot {'P' if is_pattern else ''}{index}"
    node = node_tree.nodes.get(node_name)
    if node is None or node.bl_idname != "ShaderNodeGroup":
        node = node_tree.nodes.new("ShaderNodeGroup")
        node.name = node_name
        node.location = (quad.location.x - 320,
                         quad.location.y - 200 * (index + (4 if is_pattern else 0)))
    node.label = tree.get("carbon_sof_material", "") or tree.name
    node.node_tree = tree

    patterns = PATTERN_SOCKETS if is_pattern else MATERIAL_SOCKETS
    linked = 0
    for offset, pattern in enumerate(patterns):
        socket = quad.inputs.get(pattern.format(index))
        if socket is None:
            continue
        # Drop any existing link: Blender keeps the older of two sources.
        for link in list(node_tree.links):
            if link.to_socket == socket:
                node_tree.links.remove(link)
        node_tree.links.new(node.outputs[offset], socket)
        linked += 1
    return linked


def bound_group(material, index: int, *, is_pattern: bool = False):
    """The material group a slot is currently reading, or None."""

    if material is None or not material.use_nodes:
        return None
    node = material.node_tree.nodes.get(
        f"{PREFIX} slot {'P' if is_pattern else ''}{index}")
    return getattr(node, "node_tree", None) if node is not None else None


def make_private(tree, owner: str):
    """A private copy of a material, for a slot that has gone its own way.

    Editing a shared material reaches everything using it, so a slot wanting
    its own values takes a copy.
    """

    if tree is None:
        return None
    copy = tree.copy()
    copy.name = f"{PREFIX} custom {owner}"
    copy.use_fake_user = True
    copy["carbon_sof_material"] = "custom"
    return copy
