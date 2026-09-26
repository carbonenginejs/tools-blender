"""EVE's fxv5: an additive fresnel over two scrolled layers and a mask.

fxv5 is EVE's shader, and Frontier inherited it. TQ 3542233's depth PS0-29
and Frontier 3512930's are the same arithmetic, blended ONE+ONE, so both
targets' members build this one graph. The vertex view modifier here also
serves Frontier's fxheatv5.
"""
import bpy

from . import nodes
from .graph import Graph, constant_sockets


VERSION = 1


def build_group(member):
    if member.name != "fxv5" or member.tier != "sm_depth":
        raise ValueError("The fx graph requires fxv5 at sm_depth")
    name = f"CarbonShader {member.identity}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.get("carbon_fx_version") == VERSION:
        return tree
    tree = nodes._new_group(name)
    tree["carbon_source"] = member.target
    tree["carbon_effect_path"] = member.effect_path
    tree["carbon_effect_sha256"] = member.digest
    for label, kind, _ in nodes.OUTPUTS:
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type=kind)
    for label in ("Metallic", "Alpha"):
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type="NodeSocketFloat")
    for texture in member.textures:
        nodes._socket(tree, texture, "NodeSocketColor", default=(0, 0, 0, 1))
    nodes._socket(tree, "BaseColorAlpha", "NodeSocketFloat", default=member.constants["BaseColor"].default[3])
    nodes._socket(tree, "activationStrength", "NodeSocketFloat", default=1.0)
    for texture in member.textures:
        nodes._socket(tree, texture + "Alpha", "NodeSocketFloat", default=1.0)
    for constant in member.constants:
        for label, kind, default in constant_sockets(member, constant):
            nodes._socket(tree, label, kind, default=default)
    fresnel_layers(Graph(tree, member))
    # Keep generated arithmetic navigable without hand-maintaining positions.
    for index, node in enumerate(tree.nodes):
        node.location = ((index % 10) * 220, -(index // 10) * 180)
    tree["carbon_fx_version"] = VERSION
    return tree


def fresnel_layers(graph):
    # TQ 3542233 depth PS0-29; the pass blends ONE+ONE.
    view = graph.nodes.new("ShaderNodeAttribute")
    view.attribute_name = "carbon_fx_vertex_view"
    normal = graph.vector("NORMALIZE", graph.frame(0)[2])
    facing = graph.sat(graph.math("SUBTRACT", graph.vector("DOT_PRODUCT", view.outputs["Vector"], normal), graph.c("FresnelFactors", 2)))
    fresnel = graph.math("POWER", graph.math("SUBTRACT", 1, facing), graph.c("FresnelFactors", 0))
    strength = graph.c("FresnelFactors", 1)
    positive = graph.math("MULTIPLY", fresnel, graph.math("MAXIMUM", strength, 0))
    negative = graph.math("MULTIPLY", graph.math("SUBTRACT", 1, graph.math("MINIMUM", fresnel, 1)),
                          graph.math("MAXIMUM", graph.math("MULTIPLY", strength, -1), 0))
    color = graph.color("BaseColor")
    alpha = graph.input.outputs["BaseColorAlpha"]
    for texture in ("Layer1Map", "Layer2Map", "LayerMaskMap"):
        color = graph.vector("MULTIPLY", color, graph.tex(texture))
        alpha = graph.math("MULTIPLY", alpha, graph.input.outputs[texture + "Alpha"])
    activation = graph.input.outputs["activationStrength"]
    alpha = graph.math("MULTIPLY", alpha, activation)
    emission = graph.vector("SCALE", color, graph.math("MULTIPLY", activation, graph.math("ADD", positive, negative)))
    transparent = graph.nodes.new("ShaderNodeBsdfTransparent")
    emit = graph.nodes.new("ShaderNodeEmission")
    graph.bind(emission, emit.inputs["Color"])
    add = graph.nodes.new("ShaderNodeAddShader")
    graph.bind(transparent.outputs[0], add.inputs[0])
    graph.bind(emit.outputs[0], add.inputs[1])
    graph.bind(add.outputs[0], graph.output.inputs["BSDF"])
    graph.bind(emission, graph.output.inputs["Emission"])
    graph.bind(alpha, graph.output.inputs["Alpha"])
    graph.bind(normal, graph.output.inputs["Normal"])


def wire_coordinates(member, effect, material):
    """Both layers scroll over authored UV0; the mask reads it unscrolled."""
    graph = Graph(material.node_tree, member, False)
    values = member.defaults()
    values.update({c["name"]: c["value"] for c in effect.get("constParameters", [])})
    node = graph.nodes.new("ShaderNodeAttribute")
    node.attribute_name = "gr2_texcoord0"
    node.label = "Authored UV0"
    uv = node.outputs["Vector"]
    time = nodes.time_value(material.node_tree)
    assignments = {}
    for index in (1, 2):
        transform, scroll = values[f"Layer{index}Transform"], values[f"Layer{index}Scroll"]
        moved = graph.vector("ADD", graph.vector("MULTIPLY", uv, (*transform[:2], 0)), (*transform[2:], 0))
        moved = graph.vector("ADD", moved, graph.vector("SCALE", (*scroll[:2], 0), time))
        assignments[f"Layer{index}Map"] = graph.vector("ADD", moved, (*scroll[2:], 0))
    for node in list(graph.nodes):
        if node.bl_idname != "ShaderNodeTexImage":
            continue
        # Authored math first; convert to Blender image space only at the sample.
        coordinates = assignments.get(node.label, uv)
        coordinates = graph.vector("ADD", graph.vector("MULTIPLY", coordinates, (1, -1, 1)), (0, 1, 0))
        graph.links.new(coordinates, node.inputs["Vector"])


def attach_vertex_view(obj, scene):
    """Preserve FX vertex-normalized view and heat vertex camera distance.

    Blender adapter: shader Incoming normalizes per fragment. A point-domain
    attribute instead preserves the depth VS normalization before interpolation.
    Matrix coefficient drivers retain parent shear and work without 4.2 nodes.
    """
    modifier = next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                     and m.node_group.get("carbon_fx_vertex_view")), None)
    if (modifier and modifier.node_group.get("carbon_fx_owner") == obj
            and modifier.node_group.get("carbon_fx_vertex_version", 1) == 2):
        return modifier
    previous = modifier.node_group if modifier else None
    tree = bpy.data.node_groups.new("Carbon FX Vertex View " + obj.name, "GeometryNodeTree")
    tree["carbon_fx_vertex_view"] = True
    tree["carbon_fx_vertex_version"] = 2
    tree["carbon_fx_owner"] = obj
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    graph = Graph(tree, None, False)
    incoming = tree.nodes.new("NodeGroupInput")
    outgoing = tree.nodes.new("NodeGroupOutput")
    position = tree.nodes.new("GeometryNodeInputPosition").outputs["Position"]

    def driven(owner, path):
        node = tree.nodes.new("ShaderNodeValue")
        node.label = path
        driver = node.outputs[0].driver_add("default_value").driver
        driver.type = "AVERAGE"
        variable = driver.variables.new()
        variable.type = "SINGLE_PROP"
        variable.targets[0].id_type = "SCENE" if owner == scene else "OBJECT"
        variable.targets[0].id = owner
        variable.targets[0].data_path = path
        return node.outputs[0]

    world = []
    for row in range(3):
        # RNA indexes matrix columns first; mathutils indexes rows first.
        basis = graph.combine(*(driven(obj, f"matrix_world[{column}][{row}]") for column in range(3)))
        world.append(graph.math("ADD", graph.vector("DOT_PRODUCT", basis, position),
                                driven(obj, f"matrix_world[3][{row}]")))
    camera = graph.combine(*(driven(scene, f"camera.matrix_world[3][{axis}]") for axis in range(3)))
    delta = graph.vector("SUBTRACT", camera, graph.combine(*world))
    view = graph.vector("NORMALIZE", delta)
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type, store.domain = "FLOAT_VECTOR", "POINT"
    store.inputs["Name"].default_value = "carbon_fx_vertex_view"
    graph.bind(incoming.outputs["Geometry"], store.inputs["Geometry"])
    graph.bind(view, store.inputs["Value"])
    distance = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    distance.data_type, distance.domain = "FLOAT", "POINT"
    distance.inputs["Name"].default_value = "carbon_fx_vertex_distance"
    graph.bind(store.outputs["Geometry"], distance.inputs["Geometry"])
    graph.bind(graph.vector("LENGTH", delta), distance.inputs["Value"])
    graph.bind(distance.outputs["Geometry"], outgoing.inputs["Geometry"])
    if modifier is None:
        modifier = obj.modifiers.new("Carbon FX Vertex View", "NODES")
    modifier.node_group = tree
    if previous and previous.users == 0 and previous.get("carbon_fx_owner") == obj:
        bpy.data.node_groups.remove(previous)
    return modifier
