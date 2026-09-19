"""Blender evaluation of Carbon's rigid indexed position and tangent frame.

Frontier 3512930 skinned_quadv5.sm_depth VS reads one palette index. Generic
vector attributes are not deformed by Blender's Armature modifier, so four
weighted helper points recover the evaluated palette's affine columns.
"""
import bpy
from mathutils import Matrix, Vector


def attach_rigid_frame(obj):
    """Replace this mesh's Armature modifier with the qualified rigid path."""
    existing = next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                     and m.node_group.get("carbon_rigid_frame")), None)
    if existing is not None:
        return existing
    arm_modifier = next((m for m in obj.modifiers if m.type == "ARMATURE" and m.object), None)
    if arm_modifier is None:
        raise ValueError("Rigid shader has no imported armature")
    arm = arm_modifier.object
    order = list(arm.get("carbon_bone_order", []))
    inverse = list(arm.get("carbon_inverse_bind", []))
    indices = obj.data.attributes.get("carbon_rigid_bone")
    if not order or len(inverse) != 16 * len(order):
        raise ValueError("Rigid shader needs the authored inverse-bind matrices")
    if indices is None or any(v.value < 0 or v.value >= len(order) for v in indices.data):
        raise ValueError("Rigid shader has an unresolved first-lane bone binding")
    if any(name not in arm.data.bones for name in order):
        raise ValueError("Rigid shader palette contains a missing Blender bone")
    channels = [attribute.name for attribute in obj.data.attributes if attribute.name.startswith(("gr2_tangent", "gr2_binormal", "gr2_normal"))
                and attribute.data_type == "FLOAT_VECTOR" and attribute.domain == "POINT"]
    if not all(obj.data.attributes.get("gr2_" + name) for name in ("tangent", "binormal", "normal")):
        raise ValueError("Rigid shader needs an imported tangent frame")

    helper = arm.get("carbon_rigid_helper")
    if helper is None:
        vertices = []
        for index, name in enumerate(order):
            flat = inverse[index * 16:(index + 1) * 16]
            # Carbon stores row-vector matrices; Blender uses columns.
            bind = Matrix([flat[row * 4:row * 4 + 4] for row in range(4)]).transposed()
            correction = arm.data.bones[name].matrix_local @ bind
            vertices.extend(correction @ Vector(point) for point in ((0,0,0), (1,0,0), (0,1,0), (0,0,1)))
        data = bpy.data.meshes.new("Carbon rigid palette")
        data.from_pydata(vertices, [], [])
        helper = bpy.data.objects.new("Carbon rigid palette", data)
        (arm.users_collection[0] if arm.users_collection else bpy.context.scene.collection).objects.link(helper)
        helper.parent = arm
        helper.matrix_parent_inverse = Matrix.Identity(4)
        helper.matrix_basis = Matrix.Identity(4)
        for index, name in enumerate(order):
            helper.vertex_groups.new(name=name).add(list(range(index * 4, index * 4 + 4)), 1, "REPLACE")
        modifier = helper.modifiers.new("Carbon palette pose", "ARMATURE")
        modifier.object = arm
        modifier.use_deform_preserve_volume = False
        helper.hide_render = True
        helper.hide_set(True)
        helper["carbon_rigid_palette"] = True
        arm["carbon_rigid_helper"] = helper

    from ..quad.frontier import Graph
    tree = bpy.data.node_groups.new("Carbon rigid frame " + obj.name, "GeometryNodeTree")
    tree["carbon_rigid_frame"] = True
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    g = Graph(tree, None)
    geometry = g.input.outputs["Geometry"]
    info = g.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "RELATIVE"
    info.inputs["Object"].default_value = helper
    info.inputs["As Instance"].default_value = False
    index = g.nodes.new("GeometryNodeInputNamedAttribute")
    index.data_type = "INT"
    index.inputs["Name"].default_value = "carbon_rigid_bone"
    position = g.nodes.new("GeometryNodeInputPosition").outputs["Position"]
    points = []
    for offset in range(4):
        sample = g.nodes.new("GeometryNodeSampleIndex")
        sample.data_type, sample.domain, sample.clamp = "FLOAT_VECTOR", "POINT", False
        g.bind(info.outputs["Geometry"], sample.inputs["Geometry"])
        g.bind(position, sample.inputs["Value"])
        g.bind(g.math("ADD", g.math("MULTIPLY", index.outputs["Attribute"], 4), offset), sample.inputs["Index"])
        points.append(sample.outputs["Value"])
    columns = [g.vector("SUBTRACT", point, points[0]) for point in points[1:]]

    def transform(value):
        return g.weighted(g.split(value), columns, vector=True)

    # Capture incoming position before writing output. Shape keys have already
    # evaluated here; using a static rest-position attribute would erase them.
    capture = g.nodes.new("GeometryNodeCaptureAttribute")
    capture.domain = "POINT"
    capture.capture_items.new("VECTOR", "Position")
    g.bind(geometry, capture.inputs["Geometry"])
    g.bind(position, capture.inputs["Position"])
    geometry = capture.outputs["Geometry"]
    for name in channels:
        attribute = obj.data.attributes[name]
        values = [0.0] * (len(attribute.data) * 3)
        attribute.data.foreach_get("vector", values)
        rest_name = "carbon_rest_" + name
        rest = obj.data.attributes.get(rest_name) or obj.data.attributes.new(name=rest_name, type="FLOAT_VECTOR", domain="POINT")
        rest.data.foreach_set("vector", values)
        source = g.nodes.new("GeometryNodeInputNamedAttribute")
        source.data_type = "FLOAT_VECTOR"
        source.inputs["Name"].default_value = rest_name
        store = g.nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type, store.domain = "FLOAT_VECTOR", "POINT"
        store.inputs["Name"].default_value = name
        g.bind(geometry, store.inputs["Geometry"])
        g.bind(transform(source.outputs["Attribute"]), store.inputs["Value"])
        geometry = store.outputs["Geometry"]
    set_position = g.nodes.new("GeometryNodeSetPosition")
    g.bind(geometry, set_position.inputs["Geometry"])
    g.bind(g.vector("ADD", points[0], transform(capture.outputs["Position"])), set_position.inputs["Position"])
    g.bind(set_position.outputs["Geometry"], g.output.inputs["Geometry"])
    target_index = list(obj.modifiers).index(arm_modifier)
    modifier = obj.modifiers.new("Carbon rigid frame", "NODES")
    modifier.node_group = tree
    obj.modifiers.move(len(obj.modifiers) - 1, target_index)
    obj.modifiers.remove(arm_modifier)
    return modifier
