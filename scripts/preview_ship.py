"""Assembles a whole ship from a SOF document: geometry, areas and materials.

Where `preview_quad.py` puts one material on one mesh, this builds what a real
hull needs -- one material per `Tr2MeshArea`, each with its own authored
constants and textures, routed onto the geometry's index groups.

Run with Blender's own Python::

    blender --background --factory-startup --python scripts/preview_ship.py -- \\
        --sof ship.json --resources <dir> [--out x.blend] [--render x.png]

`--resources` is a directory holding the geometry and textures the document
references, plus a `manifest.json` mapping each `res:/` path to a local file.

Three things this exists to demonstrate, each of which is easy to get wrong:

* one hull uses SEVERAL family members at once -- a Legion is `quadv5`,
  `quadheatv5` and `quadsailsv5` together -- so a single material cannot
  describe it;
* two areas can share a member and still differ, so the node GROUP is shared
  while the MATERIAL is per area;
* area `index`/`count` address the geometry's index groups, which the GR2
  importer has already turned into material slots in the same order.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ADDONS = os.path.join(os.path.dirname(HERE), "addons")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ADDONS not in sys.path:
    sys.path.insert(0, ADDONS)

from carbon_eve_resources.quad import decals as decal_module, load_family, nodes  # noqa: E402
import preview_quad  # noqa: E402

BATCHES = ("opaqueAreas", "transparentAreas", "additiveAreas", "distortionAreas")


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sof", required=True)
    parser.add_argument("--resources", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--environment", default="",
                        help="Equirectangular nebula for the world environment")
    parser.add_argument("--sun-strength", type=float, default=None,
                        help="Scales the star's intensity into Blender sun energy; "
                             "0 leaves the environment as the only light")
    parser.add_argument("--render", default="")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def load_document(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def find_meshes(document):
    """Every Tr2Mesh in the document, in the order it appears."""

    meshes = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") in ("Tr2Mesh", "Tr2InstancedMesh"):
                meshes.append(node)
            for value in node.values():
                walk(value)

    walk(document)
    return meshes


def import_geometry(path):
    import io_scene_carbon_gr2

    try:
        io_scene_carbon_gr2.register()
    except Exception:
        pass  # already registered
    before = set(bpy.data.objects)
    bpy.ops.import_scene.carbon_gr2(filepath=path)
    created = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    return created


def find_custom_masks(document):
    """The ship's pattern projections, in order."""

    masks = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") == "EveCustomMask":
                masks.append(node)
            for value in node.values():
                walk(value)

    walk(document)
    return masks


def apply_custom_masks(obj, masks, effects):
    """Writes the ship's projections onto the object as custom properties.

    They belong to the SHIP, not to a material: every area shares the same two
    projections, which is where Carbon puts them too -- the per-object constant
    buffer. Materials read them back through Attribute nodes, so one edit here
    reaches every area at once.

    Wrap mode comes from the effect's `Tr2SamplerOverride`, not from the mask's
    `clampU`/`clampV`: those are a boolean that cannot tell EDGE from BORDER.
    """

    import mathutils

    address_to_mode = {1: 0.0, 3: 1.0, 4: 2.0}  # REPEAT, EDGE, BORDER

    overrides = {}
    for effect in effects:
        for override in effect.get("samplerOverrides", []):
            overrides[override.get("name", "")] = override

    for index, mask in enumerate(masks[:2]):
        prefix = f"carbon_mask{index}_"
        obj[prefix + "position"] = tuple(mask.get("position") or (0.0, 0.0, 0.0))
        obj[prefix + "scaling"] = tuple(mask.get("scaling") or (1.0, 1.0, 1.0))

        # Stored as euler so it drops straight into a Mapping node; a
        # four-component read would depend on the Attribute node's Alpha
        # carrying w, which is not established.
        x, y, z, w = tuple(mask.get("rotation") or (0.0, 0.0, 0.0, 1.0))
        euler = mathutils.Quaternion((w, x, y, z)).to_euler()
        obj[prefix + "rotation"] = (euler.x, euler.y, euler.z)

        obj[prefix + "mirrored"] = 1.0 if mask.get("isMirrored") else 0.0

        sampler = overrides.get(f"PatternMask{index + 1}MapSampler", {})
        obj[prefix + "wrap"] = (
            address_to_mode.get(sampler.get("addressUMode", sampler.get("addressU", 1)), 0.0),
            address_to_mode.get(sampler.get("addressVMode", sampler.get("addressV", 1)), 0.0),
            0.0,
        )

        targets = tuple(mask.get("targetMaterials") or (1.0, 1.0, 1.0, 1.0))
        obj[prefix + "targets"] = tuple(targets[:3]) + (1.0,)
        obj[prefix + "target4"] = float(targets[3]) if len(targets) > 3 else 1.0
        obj[prefix + "material"] = float(mask.get("materialIndex", 0))
        # V only. D3D texture space has V increasing downward and Blender's
        # increases upward, so the projected V needs the usual 1 - v; U needs
        # nothing, which is what makes this a convention rather than a fudge.
        # Established by testing all four combinations against a client render.
        if prefix + "flip" not in obj.keys():
            obj[prefix + "flip"] = (0.0, 1.0, 0.0)

        print(f"  mask {index}: wrap={obj[prefix + 'wrap'][:2]} "
              f"mirrored={obj[prefix + 'mirrored']:g} "
              f"targets={targets} materialIndex={mask.get('materialIndex')}")


def ensure_projection(mnodes):
    """One projection-group node per material, reusing the shared group."""

    for node in mnodes:
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree                 and node.node_tree.name == nodes.PROJECTION_GROUP:
            return node
    tree = bpy.data.node_groups.get(nodes.PROJECTION_GROUP) or nodes.build_projection_group()
    node = mnodes.new("ShaderNodeGroup")
    node.node_tree = tree
    node.location = (-1400, 400)
    return node


def build_area_material(area, family, resources, index):
    """One material for one mesh area, from its own effect."""

    effect = area.get("effect") or {}
    shader = str(effect.get("effectFilePath", ""))
    member = family.member(shader)
    if member is None:
        return None, f"{area.get('name')}: no measured member for {shader.rsplit('/', 1)[-1]}"

    tree = nodes.build_group(member)
    material = bpy.data.materials.new(f"{index:02d} {area.get('name') or member.name}")
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    group = mnodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.location = (0, 0)
    mlinks.new(group.outputs["BSDF"], output.inputs["Surface"])

    row = 900
    for resource in effect.get("resources", []):
        name, path = resource.get("name"), resource.get("resourcePath")
        socket = group.inputs.get(name)
        local = resources.get(path)
        if socket is None or not local or not os.path.exists(local):
            continue
        image = bpy.data.images.load(local, check_existing=True)
        image.colorspace_settings.name = (
            "sRGB" if member.annotation(name).srgb else "Non-Color"
        )
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-700, row)
        node.label = name
        mlinks.new(node.outputs["Color"], socket)

        # Pattern masks are sampled with projected coordinates from the shared
        # projection group, and the per-axis wrapping is done there, so the
        # image node must not wrap on its own.
        # The sails detail texture is looked up with a scaled and rotated UV0,
        # not a projection, so it gets its own small transform group fed from
        # this area's own SailsDetailData. Two areas of one hull share the
        # texture and differ only in the rotation.
        if name == "SailsDetailMap":
            node.extension = "REPEAT"
            data = next((c.get("value") for c in effect.get("constParameters", [])
                         if c.get("name") == "SailsDetailData"), None)
            sails = mnodes.new("ShaderNodeGroup")
            sails.node_tree = nodes.build_sails_group()
            sails.location = (-1000, row)
            if data:
                sails.inputs["Tiling"].default_value = float(data[0])
                sails.inputs["Rotation"].default_value = float(data[1])
                print(f"  sails uv: tiling {data[0]:g}, rotation {data[1]:g} rad")
            mlinks.new(sails.outputs["UV"], node.inputs["Vector"])

        pattern_index = {"PatternMask1Map": 1, "PatternMask2Map": 2}.get(name)
        if pattern_index is not None:
            node.extension = "EXTEND"
            projection = ensure_projection(mnodes)
            mlinks.new(projection.outputs[f"UV {pattern_index}"], node.inputs["Vector"])
            coverage = group.inputs.get(f"Pattern{pattern_index}Coverage")
            if coverage is not None:
                mlinks.new(projection.outputs[f"Coverage {pattern_index}"], coverage)

        scale = member.annotation(name).uv_scale
        if scale != 1.0:
            coord = mnodes.new("ShaderNodeTexCoord")
            coord.location = (-1200, row)
            mapping = mnodes.new("ShaderNodeMapping")
            mapping.location = (-1000, row)
            mapping.inputs["Scale"].default_value = (scale, scale, scale)
            mlinks.new(coord.outputs["UV"], mapping.inputs["Vector"])
            mlinks.new(mapping.outputs["Vector"], node.inputs["Vector"])
        if name == "DustNoiseMap" and nodes.DUST_ALPHA in group.inputs:
            mlinks.new(node.outputs["Alpha"], group.inputs[nodes.DUST_ALPHA])
        row -= 300

    preview_quad.fill_unbound_textures(member, group, mnodes, mlinks, row)

    for constant in effect.get("constParameters", []):
        name, value = constant.get("name"), constant.get("value") or []
        socket = group.inputs.get(nodes.socket_name(name))
        if socket is None or not value:
            continue
        if socket.type == "RGBA":
            socket.default_value = tuple(value[:3]) + (1.0,)
        else:
            socket.default_value = float(value[0])

    if "EmissionStrength" in group.inputs:
        group.inputs["EmissionStrength"].default_value = preview_quad.DEMO_EMISSION_STRENGTH

    return material, None


def assemble(args):
    document = load_document(args.sof)
    with open(os.path.join(args.resources, "manifest.json"), encoding="utf-8") as handle:
        resources = json.load(handle)
    family = load_family()

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    meshes = find_meshes(document)
    print(f"document has {len(meshes)} mesh(es)")

    primary = None
    warnings = []
    for mesh in meshes:
        path = mesh.get("geometryResPath")
        local = resources.get(path)
        if not local or not os.path.exists(local):
            warnings.append(f"geometry not downloaded: {path}")
            continue

        objects = import_geometry(local)
        if not objects:
            warnings.append(f"{path}: importer created no mesh")
            continue
        target = objects[0]
        if primary is None:
            primary = target
        effects = [a.get("effect") or {} for b in BATCHES for a in (mesh.get(b) or [])]
        masks = find_custom_masks(document)
        if masks:
            apply_custom_masks(target, masks, effects)
        print(f"\n{os.path.basename(path)} -> {target.name}, "
              f"{len(target.data.materials)} index group(s)")

        # Areas address index groups, and the importer made one slot per group
        # in the same order, so the slot index is the area index.
        areas = [(batch, area) for batch in BATCHES for area in (mesh.get(batch) or [])]
        slots = len(target.data.materials)
        for batch, area in areas:
            index = area.get("index")
            material, problem = build_area_material(area, family, resources, index or 0)
            if problem:
                warnings.append(problem)
                continue
            if not isinstance(index, int) or index >= slots:
                warnings.append(f"{area.get('name')}: index {index} outside {slots} slots")
                continue
            for offset in range(max(1, area.get("count") or 1)):
                if index + offset < slots:
                    target.data.materials[index + offset] = material
            fx = str(area.get("effect", {}).get("effectFilePath", "")).rsplit("/", 1)[-1]
            print(f"  [{batch[:-5]:11}] slot {index} <- {material.name}   ({fx})")

    for warning in warnings:
        print(f"  ! {warning}")
    return primary


def hide_non_geometry():
    """Keeps armatures and empties out of the render.

    The GR2 importer brings in a hull's skeleton, and a battleship's armature
    is large enough to sit in front of the geometry from most angles. That
    reads as the material being wrong -- it cost one diagnosis here, chasing a
    pattern offset that turned out to be bone shapes over the hull.
    """

    hidden = 0
    for obj in bpy.data.objects:
        if obj.type in {"ARMATURE", "EMPTY"}:
            obj.hide_viewport = True
            obj.hide_render = True
            hidden += 1
    if hidden:
        print(f"  hid {hidden} non-geometry object(s) from the render")


def build_decals(document, hull, resources, family):
    """Copies each decal's hull triangles into its own mesh and shades it.

    A decal is a re-draw of part of the hull, not a surface of its own, so the
    triangles come from `staticIndexBuffers` indexing the hull's vertices. They
    are lifted very slightly along their normals because Carbon biases in depth
    instead -- adding 1e-5 to clip-space z -- and Blender has no equivalent that
    works the same way in both EEVEE and Cycles.
    """

    import bmesh
    import mathutils

    found = decal_module.read_decals(document)
    if not found:
        return []
    print("")
    print(f"{decal_module.summarise(found)}")

    corners = [hull.matrix_world @ mathutils.Vector(c) for c in hull.bound_box]
    centre = sum(corners, mathutils.Vector((0, 0, 0))) / 8
    radius = max((c - centre).length for c in corners) or 1.0
    lift = radius * decal_module.DECAL_LIFT_FRACTION / max(hull.scale.x, 1e-9)

    source = hull.data
    built = []
    skipped = {}
    boned = 0

    for decal in found:
        if not decal.triangles:
            skipped["no triangles"] = skipped.get("no triangles", 0) + 1
            continue

        mesh = bpy.data.meshes.new(decal.name)
        bm = bmesh.new()
        made = {}
        for triangle in decal.triangles:
            try:
                verts = []
                for index in triangle:
                    if index not in made:
                        vertex = source.vertices[index]
                        offset = mathutils.Vector(vertex.normal) * lift
                        made[index] = bm.verts.new(mathutils.Vector(vertex.co) + offset)
                    verts.append(made[index])
                bm.faces.new(verts)
            except (IndexError, ValueError):
                # A repeated triangle, or an index past the hull -- neither is
                # worth losing the rest of the decal over.
                continue
        bm.to_mesh(mesh)
        bm.free()

        if not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            skipped["no faces"] = skipped.get("no faces", 0) + 1
            continue

        obj = bpy.data.objects.new(decal.name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.matrix_world = hull.matrix_world
        obj.parent = hull
        obj.matrix_parent_inverse = hull.matrix_world.inverted()

        # The decal's transform lives on the decal object, so one projection
        # group serves every decal on every hull.
        # A decal's transform may be relative to a BONE rather than the hull:
        # nine of a Legion's seventeen are, and the vertex stage composes
        # worldMatrix with parentBoneMatrix. Ignoring that leaves those decals
        # somewhere else entirely -- often the far side of the hull, which reads
        # as one showing through from underneath.
        x, y, z, w = decal.rotation
        matrix = mathutils.Matrix.LocRotScale(
            mathutils.Vector(decal.position),
            mathutils.Quaternion((w, x, y, z)),
            mathutils.Vector(decal.scaling),
        )
        bone = bone_rest_matrix(hull, decal.parent_bone)
        if bone is not None:
            matrix = bone @ matrix
            boned += 1
        position, rotation, scaling = matrix.decompose()
        obj["carbon_decal_position"] = tuple(position)
        obj["carbon_decal_scaling"] = tuple(scaling)
        obj["carbon_decal_rotation"] = tuple(rotation.to_euler())

        mesh.materials.append(build_decal_material(decal, resources))
        built.append(obj)

    for reason, count in skipped.items():
        print(f"  ! {count} decal(s) skipped: {reason}")
    print(f"  built {len(built)} decal object(s), {boned} placed on a bone")
    return built


def bone_rest_matrix(hull, index):
    """The rest matrix of one bone, by the index a decal names.

    Decal bone indices address the GR2 skeleton, which the importer builds in
    the same order, so the index selects directly. Returns None when there is no
    armature or the index is out of range -- a decal is better placed in hull
    space than not at all.
    """

    if index is None or index < 0:
        return None
    armature = next((child for child in hull.children if child.type == "ARMATURE"), None)
    if armature is None:
        armature = next((obj for obj in bpy.data.objects if obj.type == "ARMATURE"), None)
    if armature is None:
        return None
    bones = armature.data.bones
    if index >= len(bones):
        return None
    return bones[index].matrix_local


def build_decal_material(decal, resources):
    """One decal's material, sampling its maps through the projection."""

    material = bpy.data.materials.new(decal.name)
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (500, 0)

    projection = mnodes.new("ShaderNodeGroup")
    projection.node_tree = nodes.build_decal_projection_group()
    projection.location = (-900, 200)

    principled = mnodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (200, 0)
    principled.inputs["Metallic"].default_value = 0.0

    row = 400
    sampled = {}
    for name, path in sorted(decal.textures.items()):
        if not name.startswith("Decal"):
            continue  # the hull's own maps; decalv5 reads them at the mesh UV
        local = resources.get(path)
        if not local or not os.path.exists(local):
            continue
        image = bpy.data.images.load(local, check_existing=True)
        image.colorspace_settings.name = (
            "sRGB" if decal_module.DECAL_TEXTURES.get(name) else "Non-Color"
        )
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-600, row)
        node.label = name
        # Every decal map clamps to a black border, so a decal covers nothing
        # outside its own projection. CLIP is exactly that.
        node.extension = "CLIP"
        mlinks.new(projection.outputs["UV"], node.inputs["Vector"])
        sampled[name] = node
        row -= 300

    transparency = sampled.get("DecalTransparencyMap")
    albedo = sampled.get("DecalAlbedoMap")
    glow = sampled.get("DecalGlowMap")

    if albedo is not None:
        mlinks.new(albedo.outputs["Color"], principled.inputs["Base Color"])
    if "DecalRoughnessMap" in sampled:
        mlinks.new(sampled["DecalRoughnessMap"].outputs["Color"], principled.inputs["Roughness"])

    glow_colour = decal.constants.get("DecalGlowColor")
    intensity = decal.constants.get("DecalIntensityData")
    if glow is not None:
        strength = mnodes.new("ShaderNodeVectorMath")
        strength.operation = "SCALE"
        strength.location = (-200, 200)
        mlinks.new(glow.outputs["Color"], strength.inputs[0])
        strength.inputs["Scale"].default_value = float(intensity[0]) if intensity else 1.0
        mlinks.new(strength.outputs[0], principled.inputs["Emission Color"])
        principled.inputs["Emission Strength"].default_value = 1.0
    elif glow_colour:
        principled.inputs["Emission Color"].default_value = tuple(glow_colour[:3]) + (1.0,)

    if transparency is not None:
        mlinks.new(transparency.outputs["Color"], principled.inputs["Alpha"])

    # The shader alpha-blends with ZWRITEENABLE off: decals are depth-TESTED
    # against the hull but never depth-WRITING. That is exactly the case where
    # EEVEE draws the far side of a transparent surface through the near one,
    # so backfaces are culled and the transparent back is hidden.
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    if hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    material.use_backface_culling = True
    if hasattr(material, "show_transparent_back"):
        material.show_transparent_back = False

    mlinks.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def main():
    args = parse_args(sys.argv)
    primary = assemble(args)
    hide_non_geometry()
    if primary is None:
        raise SystemExit("no geometry was assembled")

    build_decals(load_document(args.sof), primary,
                 json.load(open(os.path.join(args.resources, "manifest.json"), encoding="utf-8")),
                 load_family())
    preview_quad.ENVIRONMENT[:] = [args.environment] if args.environment else []
    if args.sun_strength is not None:
        preview_quad.SUN_SCALE[0] = args.sun_strength
    preview_quad.frame(primary)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 600

    if args.out:
        bpy.ops.wm.save_as_mainfile(filepath=args.out)
        print("saved", args.out)
    if args.render:
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
        print("rendered", args.render)


if __name__ == "__main__":
    main()
