"""EvePlaneSet's `planeglow.fx`: two scrolled layers through an atlas mask, added.

Measured from TQ 3542233 `fx/planeglow.sm_depth`; `skinned_planeglow` compiles
to the same vertex and pixel bytecode. Carbon draws every plane as one instance
of a unit quad (`EvePlaneSet.cpp:45-60`, `:154-170`) in the additive batch,
which blends ONE+ONE with no culling and writes RGB only
(`Tr2EffectStateManager.cpp:144-172`), so the pixel alpha never reaches the
frame.

The per-plane instance data becomes per-material constants here: each plane is
its own object and material, and the group is shared. The layer transforms,
scrolls and blink data travel as half floats in Carbon's vertex (`Vector4_16`,
`EvePlaneSet.cpp:306-310`) and are rounded the same way.

Not reproduced: `EveSceneFogVolumeMap` (the scene fog, PS0-23) -- Blender has
no fog volume, so the factor is one, as for every scene texture here.
"""
import math
import struct

import bpy

from . import nodes
from .graph import Graph, constant_sockets


VERSION = 1
NAME = "CarbonShader planeglow"

#: `EvePlaneSetItem.blinkData`: rate, phase, unused, curve (VS29-75).
BLINK_SOCKETS = ("blinkData.x", "blinkData.y", "blinkData.z", "blinkData.w")


def half(value):
    """A float as Carbon's `Vector4_16` stores it."""
    return struct.unpack("<e", struct.pack("<e", float(value)))[0]


def build_group(member):
    """The pixel stage and the vertex stage's blink, shared by every plane."""
    if member.name != "planeglow" or member.tier != "sm_depth":
        raise ValueError("The plane graph requires planeglow at sm_depth")
    tree = bpy.data.node_groups.get(NAME)
    if tree is not None and tree.get("carbon_planeglow_version") == VERSION:
        return tree
    tree = nodes._new_group(NAME)
    tree["carbon_source"] = member.target
    tree["carbon_effect_path"] = member.effect_path
    tree.interface.new_socket(name="BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")
    tree.interface.new_socket(name="Emission", in_out="OUTPUT", socket_type="NodeSocketColor")
    for texture in member.textures:
        nodes._socket(tree, texture, "NodeSocketColor", default=(0, 0, 0, 1))
    nodes._socket(tree, "Color", "NodeSocketColor", default=(1, 1, 1, 1))
    for label in BLINK_SOCKETS:
        nodes._socket(tree, label, "NodeSocketFloat", default=0.0)
    nodes._socket(tree, "activationStrength", "NodeSocketFloat", default=1.0)
    for constant in member.constants:
        for label, kind, default in constant_sockets(member, constant):
            nodes._socket(tree, label, kind, default=default)
    graph = Graph(tree, member)
    time = nodes.time_value(tree)
    # EvePlaneSet::AddToQuadRenderer: vertex.color = data.color * activation,
    # and the vertex stage multiplies it by the blink (VS76).
    color = graph.vector("SCALE", graph.color("Color"),
                         graph.math("MULTIPLY", graph.input.outputs["activationStrength"], blink(graph, time)))
    emission = graph.vector("MULTIPLY", graph.vector("SCALE", graph.tex("Layer1Map"), facing(graph)), graph.tex("Layer2Map"))
    emission = graph.vector("MULTIPLY", graph.vector("MULTIPLY", emission, graph.tex("MaskMap")), color)
    transparent = graph.nodes.new("ShaderNodeBsdfTransparent")
    emit = graph.nodes.new("ShaderNodeEmission")
    graph.bind(emission, emit.inputs["Color"])
    add = graph.nodes.new("ShaderNodeAddShader")
    graph.bind(transparent.outputs[0], add.inputs[0])
    graph.bind(emit.outputs[0], add.inputs[1])
    graph.bind(add.outputs[0], graph.output.inputs["BSDF"])
    graph.bind(emission, graph.output.inputs["Emission"])
    for index, node in enumerate(tree.nodes):
        node.location = ((index % 10) * 220, -(index // 10) * 180)
    tree["carbon_planeglow_version"] = VERSION
    return tree


def facing(graph):
    """PS24-33: `PlaneData.x * (|dot(view, normal)| - 1) + 1`.

    The normal is `PlaneNormal` (0, 0, 1) through the item rows without an
    inverse transpose (VS11-13), normalized per pixel; the view is normalized
    per pixel from the interpolated world position, which Incoming is.
    """
    transform = graph.nodes.new("ShaderNodeVectorTransform")
    transform.vector_type = "VECTOR"
    transform.convert_from, transform.convert_to = "OBJECT", "WORLD"
    transform.inputs[0].default_value = (0, 0, 1)
    normal = graph.vector("NORMALIZE", transform.outputs[0])
    geometry = graph.nodes.new("ShaderNodeNewGeometry")
    view = graph.vector("DOT_PRODUCT", geometry.outputs["Incoming"], normal)
    return graph.math("ADD", graph.math("MULTIPLY", graph.c("PlaneData", 0),
                                        graph.math("SUBTRACT", graph.math("ABSOLUTE", view), 1)), 1)


def blink(graph, time):
    """VS29-75: the blink curve selected by `trunc(|blinkData.w|)`."""
    rate, phase = graph.input.outputs["blinkData.x"], graph.input.outputs["blinkData.y"]
    kind = graph.math("TRUNC", graph.math("ABSOLUTE", graph.input.outputs["blinkData.w"]))
    # case 1: a sawtooth rise over rate*0.05, a linear fall until rate*0.2.
    t = graph.math("FRACT", graph.math("MULTIPLY_ADD", time, rate, phase))
    slow = graph.math("LESS_THAN", rate, 0.001999999862164259)
    rise = graph.mix(graph.math("MULTIPLY", rate, 0.05000000074505806), 1, slow)
    fall = graph.math("MULTIPLY", rate, 0.20000000298023224)
    falling = graph.math("SUBTRACT", 1, graph.math("DIVIDE", graph.math("SUBTRACT", t, rise), graph.math("SUBTRACT", fall, rise)))
    falling = graph.math("MULTIPLY", falling, graph.math("LESS_THAN", t, fall))
    pulse = graph.mix(falling, graph.math("DIVIDE", t, rise), graph.math("LESS_THAN", t, rise))
    # cases 2 and 3: a ramp up, and its inverse.
    ramp = graph.math("FRACT", graph.math("MULTIPLY", graph.math("ADD", phase, time), rate))
    # case 4: (sin(2pi * signed frac(rate * t) + 2pi * phase) + 1) / 2.
    x = graph.math("MULTIPLY", rate, time)
    sign = graph.math("SUBTRACT", 1, graph.math("MULTIPLY", graph.math("LESS_THAN", x, 0), 2))
    turns = graph.math("MULTIPLY", graph.math("FRACT", graph.math("ABSOLUTE", x)), sign)
    angle = graph.math("MULTIPLY_ADD", turns, 6.2831854820251465, graph.math("MULTIPLY", phase, 6.2831854820251465))
    wave = graph.math("MULTIPLY", graph.math("ADD", graph.math("SINE", angle), 1), 0.5)
    # case 0 and default: one.
    value = 1
    for case, curve in ((1, pulse), (2, ramp), (3, graph.math("SUBTRACT", 1, ramp)), (4, wave)):
        value = graph.mix(value, curve, graph.math("COMPARE", kind, case, 0))
    return value


def build_material(name, member, item, plane_data, images):
    """One plane's material: the vertex stage's coordinates, then the group.

    `plane_data` is the set's `PlaneData`; `images` maps the member's texture
    names to loaded images (or None when a map is missing). The group's
    `activationStrength` is driven with every other per-ship socket.
    """
    material = bpy.data.materials.new(name)
    material["carbon_source"] = member.target
    material["carbon_effect_identity"] = member.identity
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    else:
        material.blend_method = "BLEND"
    material.use_backface_culling = False
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    group = tree.nodes.new("ShaderNodeGroup")
    group.node_tree = build_group(member)
    tree.links.new(group.outputs["BSDF"], output.inputs["Surface"])

    def four(key, fallback):
        value = [float(v) for v in (item.get(key) or fallback)]
        return (value + list(fallback))[:4]

    colour = four("color", (1.0, 1.0, 1.0, 1.0))
    group.inputs["Color"].default_value = tuple(colour[:3]) + (1.0,)
    for label, value in zip(BLINK_SOCKETS, four("blinkData", (0.0, 0.0, 0.0, 0.0))):
        group.inputs[label].default_value = half(value)
    plane_data = [float(v) for v in plane_data]
    for lane, value in zip("xyzw", plane_data):
        group.inputs[f"PlaneData.{lane}"].default_value = value

    graph = Graph(tree, member, False)
    uv = graph.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UV0"
    # The authored texcoord: the UV map is PlaneTexCoord after the V flip.
    texcoord = graph.vector("ADD", graph.vector("MULTIPLY", uv.outputs["UV"], (1, -1, 1)), (0, 1, 0))
    time = nodes.time_value(tree)
    coordinates = {}
    for index in (1, 2):
        transform = [half(v) for v in four(f"layer{index}Transform", (1.0, 1.0, 0.0, 0.0))]
        scroll = [half(v) for v in four(f"layer{index}Scroll", (0.0, 0.0, 0.0, 0.0))]
        # VS14-19: uv * transform.xy + transform.zw + (scroll.xy * time + scroll.zw).
        moved = graph.vector("ADD", graph.vector("MULTIPLY", texcoord, (*transform[:2], 0)), (*transform[2:], 0))
        coordinates[f"Layer{index}Map"] = graph.vector(
            "ADD", moved, graph.vector("ADD", graph.vector("SCALE", (*scroll[:2], 0), time), (*scroll[2:], 0)))
    coordinates["MaskMap"] = mask_coordinates(graph, texcoord, int(item.get("maskAtlasID") or 0), plane_data)

    row = 300
    for texture in member.textures:
        node = graph.nodes.new("ShaderNodeTexImage")
        node.label = texture
        node.location = (-500, row)
        node.extension = "REPEAT"   # s0 wraps U and V
        node.image = images.get(texture)
        flipped = graph.vector("ADD", graph.vector("MULTIPLY", coordinates[texture], (1, -1, 1)), (0, 1, 0))
        graph.links.new(flipped, node.inputs["Vector"])
        graph.links.new(node.outputs["Color"], group.inputs[texture])
        row -= 300
    return material


def mask_coordinates(graph, texcoord, atlas, plane_data):
    """VS20-28: the atlas cell `maskAtlasID` selects in a (z*y) by (w*y) grid."""
    columns, rows = plane_data[2] * plane_data[1], plane_data[3] * plane_data[1]
    cell = atlas / columns if columns else math.inf
    whole = math.trunc(cell) if math.isfinite(cell) else cell
    x, y, _ = graph.split(texcoord)
    return graph.combine(graph.math("ADD", graph.math("DIVIDE", x, columns), cell - whole),
                         graph.math("DIVIDE", graph.math("ADD", y, whole), rows), 0)
