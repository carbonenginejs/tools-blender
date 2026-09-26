"""Optional Blender-native haze; deliberately not Carbon's distortion pass.

Carbon redraws overlapping areas into a UNORM screen-offset buffer. Eevee's
AOV writes cannot accumulate that buffer. The user-approved adaptation uses
a lifted refractive shell with procedural bump instead. Its distance, overlap
and silhouette behavior differ. The shared authored thermal field only masks
coverage; the shell, noise, IOR and strength are Blender presentation controls.
"""
import bpy

from . import frontier, nodes


STRENGTH = "carbon_native_heat_haze_strength"
GROWTH = "carbon_native_heat_haze_growth"
WIDTH = "carbon_native_heat_haze_width"


def _drive(socket, owner, prop):
    driver = socket.driver_add("default_value").driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.name = "value"
    variable.targets[0].id = owner
    variable.targets[0].data_path = f'["{prop}"]'


def _controls(owner):
    for prop, default, maximum, description in (
        (STRENGTH, 0.0, 5.0, "Native refraction strength; zero disables the haze shell"),
        (GROWTH, 1.0, 1.0, "Native haze thermal coverage; independent of game status controllers"),
        (WIDTH, .01, .1, "Native shell distance as a fraction of the authored ship radius"),
    ):
        if prop not in owner:
            owner[prop] = default
            owner.id_properties_ui(prop).update(min=0.0, soft_max=maximum,
                                                description=description)


def _field(member):
    name = f"Carbon Native Haze Field v1 {member.identity}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = nodes._new_group(name)
    tree.interface.new_socket(name="Heat", in_out="OUTPUT", socket_type="NodeSocketFloat")
    for label in ("LocalPosition", "WorldNormal", "VertexView", "SunDirection"):
        nodes._socket(tree, label, "NodeSocketVector", default=(0, 0, 0))
    for label in ("VertexDistance", "shipRadius", "time", "Noise", "AtlasAO", "AtlasCurvature", "Grunge"):
        nodes._socket(tree, label, "NodeSocketFloat", default=0)
    for constant in member.constants:
        for label, kind, default in frontier.constant_sockets(member, constant):
            nodes._socket(tree, label, kind, default=default)
    graph = frontier.Graph(tree, member)
    inputs = graph.input.outputs
    sun = graph.heat_sun(inputs["SunDirection"],
        tuple(graph.c("HeatShipRotation", i) for i in range(4)),
        tuple(graph.c("HeatShipRotationLagged", i) for i in range(4)))
    heat = graph.heat_field(inputs["LocalPosition"], inputs["VertexDistance"],
        inputs["WorldNormal"], inputs["VertexView"], sun, inputs["shipRadius"], inputs["time"],
        inputs["Noise"], inputs["AtlasAO"], inputs["AtlasCurvature"], inputs["Grunge"])
    graph.bind(heat, graph.output.inputs["Heat"])
    return tree


def material(area, member, resources, owner, radius):
    effect = area.get("effect") or {}
    result = bpy.data.materials.new("Native heat haze (approximation)")
    result.use_nodes = True
    result.node_tree.nodes.clear()
    graph = frontier.Graph(result.node_tree, member, False)
    group = graph.nodes.new("ShaderNodeGroup")
    group.node_tree = _field(member)
    for constant in effect.get("constParameters", []):
        name, values = constant.get("name"), constant.get("value") or []
        if name not in member.constants or not values:
            continue
        for lane, (label, kind, _) in enumerate(frontier.constant_sockets(member, name)):
            if kind == "NodeSocketColor":
                group.inputs[label].default_value = tuple(values[:3]) + (1.0,)
            elif lane < len(values):
                group.inputs[label].default_value = float(values[lane])
    try:
        frontier.wire_heat_inputs(member, effect, result, group, resources,
                                  radius=radius, sun_direction=(0, -1, 0))
    except ValueError:
        bpy.data.materials.remove(result)
        raise
    # Native adaptation: shader view inputs follow the active viewport as well
    # as the scene camera. These are not Carbon's vertex interpolants.
    geometry = graph.nodes.new("ShaderNodeNewGeometry")
    camera = graph.nodes.new("ShaderNodeCameraData")
    graph.bind(geometry.outputs["Normal"], group.inputs["WorldNormal"])
    graph.bind(geometry.outputs["Incoming"], group.inputs["VertexView"])
    graph.bind(camera.outputs["View Distance"], group.inputs["VertexDistance"])
    _drive(group.inputs["Growth"], owner, GROWTH)
    strength = graph.nodes.new("ShaderNodeValue")
    strength.label = "Native haze strength"
    _drive(strength.outputs[0], owner, STRENGTH)
    texcoord = graph.nodes.new("ShaderNodeTexCoord")
    noise = graph.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 8
    graph.bind(graph.vector("SCALE", texcoord.outputs["Object"], 1 / radius), noise.inputs["Vector"])
    graph.bind(graph.math("MULTIPLY", nodes.time_value(result.node_tree), .2), noise.inputs["W"])
    bump = graph.nodes.new("ShaderNodeBump")
    bump.inputs["Distance"].default_value = radius * .1
    graph.bind(noise.outputs["Fac"], bump.inputs["Height"])
    refraction = graph.nodes.new("ShaderNodeBsdfRefraction")
    refraction.inputs["Roughness"].default_value = 0
    graph.bind(bump.outputs["Normal"], refraction.inputs["Normal"])
    amount = graph.math("MULTIPLY", strength.outputs[0], graph.sat(group.outputs["Heat"]))
    graph.bind(graph.math("ADD", 1, graph.math("MULTIPLY", amount, .2)), refraction.inputs["IOR"])
    transparent = graph.nodes.new("ShaderNodeBsdfTransparent")
    mix = graph.nodes.new("ShaderNodeMixShader")
    # Native presentation choice: retain most of the unobstructed image.
    # Eevee screen tracing can miss the surface behind a bumped interface;
    # a fully refractive shell otherwise turns those misses into dark holes.
    graph.bind(graph.math("MINIMUM", amount, .25), mix.inputs[0])
    graph.bind(transparent.outputs[0], mix.inputs[1])
    graph.bind(refraction.outputs[0], mix.inputs[2])
    output = graph.nodes.new("ShaderNodeOutputMaterial")
    graph.bind(mix.outputs[0], output.inputs["Surface"])
    # Single optical interface. Automatic object thickness can skip the hull.
    output.inputs["Thickness"].default_value = 0
    result.surface_render_method = "DITHERED"
    result.use_raytrace_refraction = True
    result["carbon_native_heat_haze"] = True
    result["carbon_effect_path"] = effect.get("effectFilePath", "")
    result["carbon_effect_identity"] = member.identity
    result["carbon_source"] = "frontier"
    del result["carbon_fx_vertex_view"]
    return result


def attach(source, owner, area, member, resources, radius):
    """Derive a shell from the evaluated area without changing source geometry."""
    if member.target != "frontier" or member.name != "fxheatdistortionv5" or member.tier != "sm_depth":
        raise ValueError("Native haze requires Frontier fxheatdistortionv5 sm_depth")
    index = area.get("index")
    if not isinstance(index, int) or index < 0 or index >= len(source.data.materials):
        raise ValueError(f"Native haze index {index} is outside the source material slots")
    if radius is None or radius <= 0:
        raise ValueError("Native haze requires an authored ship radius")
    count = max(1, area.get("count") or 1)
    mat = material(area, member, resources, owner, radius)
    _controls(owner)
    shell = bpy.data.objects.new("Native heat haze", bpy.data.meshes.new("Native heat haze carrier"))
    source.users_collection[0].objects.link(shell)
    shell.parent = source
    shell.hide_select = True
    shell["carbon_native_heat_haze"] = True
    shell["carbon_sof_kind"] = "native_heat_haze"
    # The carrier intentionally has no material slots: it must not participate
    # in SOF slot editing. Set Material below owns the render-only shader.
    tree = bpy.data.node_groups.new("Native heat haze shell", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    graph = frontier.Graph(tree, None, False)
    obj = graph.nodes.new("GeometryNodeObjectInfo")
    obj.transform_space = "RELATIVE"
    obj.inputs["Object"].default_value = source
    obj.inputs["As Instance"].default_value = False
    indices = graph.nodes.new("GeometryNodeInputMaterialIndex")
    outside = graph.math("MAXIMUM", graph.math("LESS_THAN", indices.outputs[0], index),
                         graph.math("GREATER_THAN", indices.outputs[0], index + count - 1))
    strength = graph.nodes.new("ShaderNodeValue")
    _drive(strength.outputs[0], owner, STRENGTH)
    delete = graph.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "FACE"
    graph.bind(obj.outputs["Geometry"], delete.inputs["Geometry"])
    graph.bind(graph.math("MAXIMUM", outside, graph.math("LESS_THAN", strength.outputs[0], .000001)),
               delete.inputs["Selection"])
    width = graph.nodes.new("ShaderNodeValue")
    _drive(width.outputs[0], owner, WIDTH)
    normal = graph.nodes.new("GeometryNodeInputNormal")
    position = graph.nodes.new("GeometryNodeSetPosition")
    graph.bind(delete.outputs["Geometry"], position.inputs["Geometry"])
    # Keep a minimum non-coincident shell even if the user sets width to zero.
    distance = graph.math("MULTIPLY", radius, graph.math("MAXIMUM", width.outputs[0], .00001))
    graph.bind(graph.vector("SCALE", normal.outputs[0], distance), position.inputs["Offset"])
    assign = graph.nodes.new("GeometryNodeSetMaterial")
    assign.inputs["Material"].default_value = mat
    graph.bind(position.outputs["Geometry"], assign.inputs["Geometry"])
    output = graph.nodes.new("NodeGroupOutput")
    graph.bind(assign.outputs["Geometry"], output.inputs["Geometry"])
    modifier = shell.modifiers.new("Native heat haze shell", "NODES")
    modifier.node_group = tree
    return shell
