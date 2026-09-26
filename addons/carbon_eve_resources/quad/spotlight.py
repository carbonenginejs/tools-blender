"""EveSpotlightSet: `spotlightconepool.fx` (the cone) and `spotlightglowpool.fx`
(the glow at its base).

Measured from TQ 3542233 `fx/*.sm_depth`. Carbon draws each spotlight as an
instance of `CONE_QUAD_COUNT` and `SPRITE_QUAD_COUNT` quads in the additive
batch (ONE+ONE, RGB only), and the vertex stages build the shapes from the
vertex index (`EveSpotlightSet.cpp:54-55`, `Tr2QuadRenderer.cpp:206-233`).

The cone is static in the item's space, so it is built here as a mesh. The
glow faces the camera and shrinks as the spotlight turns away, so it is a
geometry-nodes modifier over a two-quad mesh, driven from the object and the
scene camera the way `fx.attach_vertex_view` is.

Blender adaptations, none of them Carbon's:

- The glow pass has depth testing OFF (`RS_ZENABLE`/`RS_ZWRITEENABLE` = 0) and
  instead fades by sixteen `DepthMap` taps around the glow's centre (PS0-213).
  Blender has neither a scene depth map nor a per-material depth-test switch,
  so the billboard is depth tested and moved toward the camera by half its
  extent: the fixture it sits on no longer clips it, while hull in front of
  the centre still hides it.
- `EveSceneFogVolumeMap` fog is absent (factor one), as for every scene texture.
- `activation` and the booster factor are half floats in Carbon's vertex; the
  driven per-ship values are not rounded here.
- The view follows the scene camera, as the fx vertex view does; a viewport
  navigated away from the camera is not what the glow faces.
"""
import math

import bpy

from . import nodes
from .graph import Graph
from .planeglow import half


VERSION = 1
PI, QUARTER_PI, HALF_PI = 3.1415927410125732, 0.7853981852531433, 1.5707963705062866
CONE_QUAD_COUNT, SPRITE_QUAD_COUNT = 4, 2            # EveSpotlightSet.cpp:54-55
#: `Selectors`: the corner each `vertex % 4` takes, in both vertex stages.
SELECTORS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
#: The sprite quad never shrinks below this facing (glow VS6-8).
SPRITE_MIN_FACING = 0.15000000596046448
FIN_NORMAL = "carbon_spotlight_fin_normal"
GLOW_CORNER, GLOW_QUAD = "carbon_glow_corner", "carbon_glow_quad"


def _mesh(name, positions, uvs, attributes, quad_count):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], [tuple(range(q * 4, q * 4 + 4)) for q in range(quad_count)])
    mesh.update()
    layer = mesh.uv_layers.new(name="UV0")
    for loop in mesh.loops:
        layer.data[loop.index].uv = uvs[loop.vertex_index]
    for attribute, (kind, values) in attributes.items():
        data = mesh.attributes.new(name=attribute, type=kind, domain="POINT").data
        if kind == "FLOAT_VECTOR":
            data.foreach_set("vector", [c for value in values for c in value])
        else:
            data.foreach_set("value", values)
    return mesh


def cone_mesh(name, z_offset):
    """Cone VS0-11: four fins through the axis, 45 degrees apart.

    Corner `c` of quad `q` sits at angle `q*pi/4 + pi*Selectors[c].x` on the
    unit circle and height `zOffset + Selectors[c].y`; the fin normal is at
    `q*pi/4 + pi/2`. The texcoord is `Selectors[c] * (1, -1) + (0, 1)`, which
    is `Selectors[c]` itself after the image-space V flip.
    """
    positions, uvs, normals = [], [], []
    for quad in range(CONE_QUAD_COUNT):
        normal_angle = quad * QUARTER_PI + HALF_PI
        for x, y in SELECTORS:
            angle = quad * QUARTER_PI + PI * x
            positions.append((math.cos(angle), math.sin(angle), z_offset + y))
            normals.append((math.cos(normal_angle), math.sin(normal_angle), 0.0))
            uvs.append((x, y))
    return _mesh(name, positions, uvs, {FIN_NORMAL: ("FLOAT_VECTOR", normals)}, CONE_QUAD_COUNT)


def glow_mesh(name):
    """Glow quads 0 (the sprite) and 1 (the flare); the modifier places them."""
    positions, uvs, corners, quads = [], [], [], []
    for quad in range(SPRITE_QUAD_COUNT):
        for x, y in SELECTORS:
            positions.append((x - .5, y - .5, 0.0))
            corners.append((x - .5, y - .5, 0.0))
            uvs.append((x, y))
            quads.append(float(quad))
    return _mesh(name, positions, uvs, {GLOW_CORNER: ("FLOAT_VECTOR", corners), GLOW_QUAD: ("FLOAT", quads)},
                 SPRITE_QUAD_COUNT)


def _group(name, member, colors):
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.get("carbon_spotlight_version") == VERSION:
        return tree, None
    tree = nodes._new_group(name)
    tree["carbon_source"] = member.target
    tree["carbon_effect_path"] = member.effect_path
    tree.interface.new_socket(name="BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")
    tree.interface.new_socket(name="Emission", in_out="OUTPUT", socket_type="NodeSocketColor")
    for texture in member.textures:
        nodes._socket(tree, texture, "NodeSocketColor", default=(0, 0, 0, 1))
    for label in colors:
        nodes._socket(tree, label, "NodeSocketColor", default=(1, 1, 1, 1))
    for label, default in (("activationStrength", 1.0), ("boosterGain", 1.0), ("boosterGainInfluence", 0.0)):
        nodes._socket(tree, label, "NodeSocketFloat", default=default)
    return tree, Graph(tree, member)


def _finish(graph, tree, emission):
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
    tree["carbon_spotlight_version"] = VERSION
    return tree


def _activation_and_boost(graph):
    """`activation * (1 + (boosterGain - 1) * boosterGainInfluence)`.

    `EveSpotlightSet::AddToQuadRenderer` (`:213-233`) writes both into the
    vertex; the vertex stage multiplies the colour by the first and the pixel
    stage by the second.
    """
    boost = graph.math("ADD", 1, graph.math("MULTIPLY", graph.math("SUBTRACT", graph.input.outputs["boosterGain"], 1),
                                            graph.input.outputs["boosterGainInfluence"]))
    return graph.math("MULTIPLY", graph.input.outputs["activationStrength"], boost)


def build_cone_group(member):
    """Cone PS24-28 and its per-vertex facing (VS21-34)."""
    if member.name != "spotlightconepool":
        raise ValueError("The cone graph requires spotlightconepool")
    tree, graph = _group("CarbonShader spotlightconepool", member, ("Color",))
    if graph is None:
        return tree
    view = graph.nodes.new("ShaderNodeAttribute")
    view.attribute_name = "carbon_fx_vertex_view"
    fin = graph.nodes.new("ShaderNodeAttribute")
    fin.attribute_name = FIN_NORMAL
    transform = graph.nodes.new("ShaderNodeVectorTransform")
    transform.vector_type = "VECTOR"
    transform.convert_from, transform.convert_to = "OBJECT", "WORLD"
    graph.bind(fin.outputs["Vector"], transform.inputs[0])
    # |dot| per vertex, interpolated: the fin normal is constant over a fin and
    # the camera stays on one side of it, so this equals the dot of the
    # interpolated per-vertex view.
    facing = graph.math("ABSOLUTE", graph.vector("DOT_PRODUCT", view.outputs["Vector"],
                                                  graph.vector("NORMALIZE", transform.outputs[0])))
    scale = graph.math("MULTIPLY", _activation_and_boost(graph), facing)
    emission = graph.vector("SCALE", graph.vector("MULTIPLY", graph.tex("TextureMap"), graph.color("Color")), scale)
    return _finish(graph, tree, emission)


def build_glow_group(member):
    """Glow PS214-217: the sprite or flare colour through TextureMap."""
    if member.name != "spotlightglowpool":
        raise ValueError("The glow graph requires spotlightglowpool")
    tree, graph = _group("CarbonShader spotlightglowpool", member, ("SpriteColor", "FlareColor"))
    if graph is None:
        return tree
    quad = graph.nodes.new("ShaderNodeAttribute")
    quad.attribute_name = GLOW_QUAD
    color = graph.mixv(graph.color("SpriteColor"), graph.color("FlareColor"), quad.outputs["Fac"])
    emission = graph.vector("SCALE", graph.vector("MULTIPLY", graph.tex("TextureMap"), color), _activation_and_boost(graph))
    return _finish(graph, tree, emission)


def build_material(name, member, group, colors, influence, image, extension):
    """One spotlight part's material: its authored colours and its map."""
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
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    tree.links.new(node.outputs["BSDF"], output.inputs["Surface"])
    for label, value in colors.items():
        # Float_16 in the vertex (`EveSpotlightSet::Rebuild`, `:291-308`).
        node.inputs[label].default_value = tuple(half(v) for v in list(value)[:3]) + (1.0,)
    node.inputs["boosterGainInfluence"].default_value = 1.0 if influence else 0.0
    sample = tree.nodes.new("ShaderNodeTexImage")
    sample.label = "TextureMap"
    sample.location = (-400, 0)
    sample.extension = extension
    sample.image = image
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UV0"
    uv.location = (-600, 0)
    tree.links.new(uv.outputs["UV"], sample.inputs["Vector"])
    tree.links.new(sample.outputs["Color"], node.inputs["TextureMap"])
    return material


def attach_billboard(obj, scene, sprite_scale, unit):
    """Glow VS0-41 as geometry nodes: both quads face the camera.

    Per quad, `f = min(max(floor, dot(view, itemZ)), 1)` with floor 0.15 for
    the sprite and 0 for the flare; the sprite is `spriteScale.x` square, the
    flare `spriteScale.y` by `.z`; corners sit at `f * size * (corner - .5)`
    along the camera's right and up. Positions are world-space, brought back
    into the object's space through the inverse of its driven 3x3.
    `unit` converts Carbon's world units into this scene's.
    """
    tree = bpy.data.node_groups.new("Carbon Spotlight Glow " + obj.name, "GeometryNodeTree")
    tree["carbon_spotlight_glow"] = True
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    graph = Graph(tree, None, False)
    incoming = tree.nodes.new("NodeGroupInput")
    outgoing = tree.nodes.new("NodeGroupOutput")

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

    def column(owner, prefix, index):
        # RNA indexes matrix columns first.
        return graph.combine(*(driven(owner, f"{prefix}matrix_world[{index}][{row}]") for row in range(3)))

    def attribute(name, kind):
        node = tree.nodes.new("GeometryNodeInputNamedAttribute")
        node.data_type = kind
        node.inputs["Name"].default_value = name
        return node.outputs["Attribute"]

    axes = [column(obj, "", index) for index in range(3)]
    centre = column(obj, "", 3)
    right, up = (graph.vector("NORMALIZE", column(scene, "camera.", index)) for index in (0, 1))
    toward = graph.vector("SUBTRACT", column(scene, "camera.", 3), centre)
    view = graph.vector("NORMALIZE", toward)
    quad = attribute(GLOW_QUAD, "FLOAT")
    corner = graph.split(attribute(GLOW_CORNER, "FLOAT_VECTOR"))
    facing = graph.vector("DOT_PRODUCT", view, graph.vector("NORMALIZE", axes[2]))
    floor = graph.math("MULTIPLY", graph.math("SUBTRACT", 1, quad), SPRITE_MIN_FACING)
    facing = graph.math("MINIMUM", graph.math("MAXIMUM", floor, facing), 1)
    scale = [half(v) * unit for v in (list(sprite_scale) + [0.0, 0.0, 0.0])[:3]]
    width = graph.math("MULTIPLY", graph.mix(scale[0], scale[1], quad), facing)
    height = graph.math("MULTIPLY", graph.mix(scale[0], scale[2], quad), facing)
    offset = graph.vector("ADD", graph.vector("SCALE", right, graph.math("MULTIPLY", corner[0], width)),
                          graph.vector("SCALE", up, graph.math("MULTIPLY", corner[1], height)))
    # Adaptation (see the module head): toward the camera by half the extent.
    offset = graph.vector("ADD", offset, graph.vector("SCALE", view,
                                                      graph.math("MULTIPLY", graph.math("MAXIMUM", width, height), .5)))
    # inverse(M) * offset, by the adjugate: rows are the cross products of the
    # columns over the determinant.
    rows = [graph.vector("CROSS_PRODUCT", axes[1], axes[2]), graph.vector("CROSS_PRODUCT", axes[2], axes[0]),
            graph.vector("CROSS_PRODUCT", axes[0], axes[1])]
    determinant = graph.vector("DOT_PRODUCT", axes[0], rows[0])
    local = graph.combine(*(graph.math("DIVIDE", graph.vector("DOT_PRODUCT", row, offset), determinant) for row in rows))
    place = tree.nodes.new("GeometryNodeSetPosition")
    graph.bind(incoming.outputs["Geometry"], place.inputs["Geometry"])
    graph.bind(local, place.inputs["Position"])
    graph.bind(place.outputs["Geometry"], outgoing.inputs["Geometry"])
    modifier = obj.modifiers.new("Carbon Spotlight Glow", "NODES")
    modifier.node_group = tree
    return modifier
