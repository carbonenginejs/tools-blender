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


CARBON_DOCUMENT_SCHEMA = "carbon.document"


def expand_document(document):
    """Expands a `carbon.document` node graph into the tree this script walks.

    TWO shapes arrive here, and they are different formats rather than one
    format written two ways:

    - HYDRATION data, the CjsModel spelling: `_type` names the class, `_id`
      identifies a node, and `{"_ref": id}` points at one. This is what a
      dehydrated object graph looks like and what a reader should expect.
    - a CLASS-POPULATION format, which is what the bundle at hand carries:
      `{id, kind, fields}` with `{"$ref": id}`. It describes how to build the
      objects rather than an object graph that has been saved.

    Both are node graphs with shared references, so both expand the same way,
    and this reads either. Do not treat the second as a variant spelling of the
    first when writing anything back.

    Either way the deduplication is the point: a hull's effect is referenced by
    every area that uses it rather than copied. Sharing is PRESERVED rather than
    unrolled -- an expanded node is cached by id and handed out again, so two
    areas that referenced one effect still see one object. The cache entry is
    placed BEFORE the fields are walked, which is what stops a cycle, and the
    graph has them: a child can point back at its parent.
    """

    if not isinstance(document, dict) or document.get("schema") != CARBON_DOCUMENT_SCHEMA:
        return document

    nodes = {}
    for node in document.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        identity = node.get("_id", node.get("id"))
        if identity is not None:
            nodes[identity] = node

    cache = {}

    def reference_of(value):
        """The id a value points at, under either spelling, or None."""

        if "_ref" in value:
            return value["_ref"]
        if "$ref" in value:
            return value["$ref"]
        return None

    def convert(value):
        if isinstance(value, dict):
            identity = reference_of(value)
            if identity is not None:
                return expand(identity)
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    def expand(node_id):
        if node_id in cache:
            return cache[node_id]
        node = nodes.get(node_id)
        if node is None:
            return None
        out = {"_type": node.get("_type", node.get("kind"))}
        cache[node_id] = out
        fields = node.get("fields")
        if not isinstance(fields, dict):
            # Under the contract the properties are on the node itself.
            fields = {key: value for key, value in node.items()
                      if key not in ("_type", "_id", "id", "kind")}
        for key, value in fields.items():
            out[key] = convert(value)
        return out

    for root in document.get("roots") or []:
        reference = (root or {}).get("ref") if isinstance(root, dict) else None
        identity = None
        if isinstance(reference, dict):
            identity = reference_of(reference)
        elif isinstance(root, dict):
            identity = reference_of(root)
        if identity is not None:
            return expand(identity)
    raise SystemExit("carbon.document has no root reference")


def load_manifest(directory):
    """The `res:/` -> local file map, from either bundle shape.

    Two exist and they are not interchangeable. A hand-made bundle carries a
    flat `manifest.json` of ABSOLUTE paths; a bundle built by tools-core carries
    `bundle.json`, whose paths are RELATIVE to the bundle and nested by resource
    path. Reading only the first is why a tools-core bundle appeared to be
    missing its textures when every one of them was on disk.
    """

    flat = os.path.join(directory, "manifest.json")
    if os.path.exists(flat):
        with open(flat, encoding="utf-8") as handle:
            return json.load(handle)

    built = os.path.join(directory, "bundle.json")
    if not os.path.exists(built):
        raise SystemExit(f"{directory} has neither manifest.json nor bundle.json")
    with open(built, encoding="utf-8") as handle:
        bundle = json.load(handle)
    resources = {}
    for logical, relative in (bundle.get("resources") or {}).items():
        local = os.path.normpath(os.path.join(directory, relative))
        if os.path.exists(local):
            resources[logical] = local
    return resources


def load_document(path):
    """Reads a SOF document, whichever shape it is written in."""

    with open(path, encoding="utf-8") as handle:
        return expand_document(json.load(handle))


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


def wire_heat_shimmer(member, effect, group, mnodes, mlinks, resources):
    """Displaces the glow lookup by the heat shimmer, for heat members.

    The chain has to run through the material because each step needs a texture
    sampled between groups: noise UVs, sample the noise twice, work out the
    displacement, then sample the GLOW map at the displaced coordinate. A group
    cannot feed a texture that feeds itself back.

    Heat scales the glow map rather than adding a texture of its own, so this
    replaces the glow the quad group would otherwise sample at a plain UV.
    """

    if "HeatGlowNoiseMap" not in member.textures or "GlowMap" not in group.inputs:
        return

    noise_path = effect.get("resources") or []
    noise = next((r.get("resourcePath") for r in noise_path
                  if r.get("name") == "HeatGlowNoiseMap"), None)
    glow = next((r.get("resourcePath") for r in noise_path
                 if r.get("name") == "GlowMap"), None)
    noise_local, glow_local = resources.get(noise or ""), resources.get(glow or "")
    if not noise_local or not glow_local:
        return
    if not (os.path.exists(noise_local) and os.path.exists(glow_local)):
        return

    lanes = {}
    for constant in effect.get("constParameters", []):
        name = str(constant.get("name", ""))
        if "HeatGlowData" in name:
            lanes[name] = tuple(constant.get("value") or (0.0, 0.0, 1.0, 0.0))

    material_map = next((n for n in mnodes
                         if n.bl_idname == "ShaderNodeTexImage" and n.label == "MaterialMap"), None)
    if material_map is None:
        return

    separate = mnodes.new("ShaderNodeSeparateColor")
    separate.location = (-1500, 600)
    mlinks.new(material_map.outputs["Color"], separate.inputs[0])

    uv_group = mnodes.new("ShaderNodeGroup")
    uv_group.node_tree = nodes.build_heat_uv_group()
    uv_group.location = (-1300, 500)
    mlinks.new(separate.outputs["Red"], uv_group.inputs["MaterialMap"])

    displace = mnodes.new("ShaderNodeGroup")
    displace.node_tree = nodes.build_heat_displace_group()
    displace.location = (-700, 500)
    mlinks.new(separate.outputs["Red"], displace.inputs["MaterialMap"])

    # Carbon's own component names, so the lanes land where they belong.
    for layer in range(1, 5):
        value = lanes.get(f"Mtl{layer}HeatGlowData")
        if not value:
            continue
        for socket, index in (("Shimmer speed", 1), ("Shimmer size", 2)):
            key = f"Mtl{layer}HeatGlow {socket}"
            if key in uv_group.inputs:
                uv_group.inputs[key].default_value = float(value[index])
        for socket, index in (("Shimmer strength", 3), ("boosterGain influence", 0)):
            key = f"Mtl{layer}HeatGlow {socket}"
            if key in displace.inputs:
                displace.inputs[key].default_value = float(value[index])

    noise_image = bpy.data.images.load(noise_local, check_existing=True)
    noise_image.colorspace_settings.name = "Non-Color"
    for index in (1, 2):
        node = mnodes.new("ShaderNodeTexImage")
        node.image = noise_image
        node.location = (-1000, 700 - index * 260)
        node.label = f"HeatGlowNoiseMap {index}"
        mlinks.new(uv_group.outputs[f"Noise UV {index}"], node.inputs["Vector"])
        mlinks.new(node.outputs["Color"], displace.inputs[f"Noise {index}"])

    glow_node = next((n for n in mnodes
                      if n.bl_idname == "ShaderNodeTexImage" and n.label == "GlowMap"), None)
    if glow_node is None:
        return
    mlinks.new(displace.outputs["Glow UV"], glow_node.inputs["Vector"])
    print("  heat shimmer wired (glow sampled at a displaced UV)")


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

    wire_heat_shimmer(member, effect, group, mnodes, mlinks, resources)
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

    return material, None


def assemble(args):
    document = load_document(args.sof)
    resources = load_manifest(args.resources)
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


def apply_ship_globals(objects, overrides=None):
    """Writes the per-ship values every material reads.

    Age, activation, booster gain, emission strength and the kill count are one
    per object in Carbon, so a hull's areas and all its decals must agree. They
    live on the OBJECT and are read with Attribute nodes, which means the decals
    need their own copy: an Attribute node reads the object being SHADED, and a
    decal is its own object even though it is parented to the hull.
    """

    values = {name: default for name, (_, default) in nodes.SHIP_PROPERTIES.items()}
    values.update(overrides or {})
    limits = {
        "carbon_ship_age_weeks": dict(min=0.0, max=10000.0, step=100, precision=1,
                                      description="Converted to shipData.z (dirtLevel); dirt saturates by about a year"),
        "carbon_ship_activation_strength": dict(min=0.0, max=1.0, step=5, precision=3,
                                       description="shipData.y: scales every glow on the ship"),
        "carbon_ship_booster_gain": dict(min=0.0, max=1.0, step=5, precision=3,
                                         description="shipData.x: opens the heat gate; heat is fully on by 0.02"),
        "carbon_preview_glow_scale": dict(min=0.0, max=1000.0, step=100, precision=2,
                                         description="Preview only, not a Carbon value: EVE blooms its glows and Blender does not"),
        "carbon_ship_kill_count": dict(min=0.0, max=999.0, step=100, precision=0,
                                       description="displayData.x: whole kills, drawn as tally marks"),
    }
    for obj in objects:
        for name, (prop, _) in nodes.SHIP_PROPERTIES.items():
            obj[prop] = float(values[name])
            # Give each one a sane range and description, so the panel reads as
            # a control rather than as a raw custom property.
            if hasattr(obj, "id_properties_ui"):
                obj.id_properties_ui(prop).update(**limits.get(prop, {}))
    listed = ", ".join(f"{name} {values[name]:g}" for name in sorted(values))
    print(f"  ship values on {len(objects)} object(s): {listed}")


def drive_ship_sockets(objects, source):
    """Points every material's per-ship sockets at ONE object's properties.

    The values are read through drivers rather than Attribute nodes because
    EEVEE delivers only eight object attributes per material and silently
    returns zero beyond that -- the pattern masks alone ask for sixteen. A
    decal's driver targets the HULL, so a hull and everything on it stay in
    step no matter which object is being shaded.
    """

    driven = materials = 0
    seen = set()
    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            material = slot.material
            if material is None or material.name in seen or not material.use_nodes:
                continue
            seen.add(material.name)
            for node in material.node_tree.nodes:
                if node.bl_idname != "ShaderNodeGroup" or not node.node_tree:
                    continue
                count = nodes.drive_ship_values(node, source)
                count += nodes.drive_mask_values(node, source)
                if count:
                    driven += count
                    materials += 1
    print(f"  drove {driven} per-ship socket(s) across {materials} material group(s)")


def decal_collection(group):
    """`decals > {group}`, created on demand.

    The tree matters beyond tidiness: an exporter walks collections, so the
    structure here is the structure that leaves. Grouping by the SOF's
    visibility group is what lets a consumer turn a whole set on or off the way
    the client does.
    """

    scene = bpy.context.scene.collection
    root = bpy.data.collections.get("decals")
    if root is None:
        root = bpy.data.collections.new("decals")
        scene.children.link(root)
    child = bpy.data.collections.get(group)
    if child is None:
        child = bpy.data.collections.new(group)
        root.children.link(child)
    elif child.name not in root.children:
        root.children.link(child)
    return child


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
        bm.verts.index_update()
        order = {source: vertex.index for source, vertex in made.items()}
        bm.to_mesh(mesh)
        bm.free()

        if not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            skipped["no faces"] = skipped.get("no faces", 0) + 1
            continue

        obj = bpy.data.objects.new(decal.name, mesh)
        decal_collection(decal.group).objects.link(obj)
        obj.matrix_world = hull.matrix_world
        obj.parent = hull
        obj.matrix_parent_inverse = hull.matrix_world.inverted()

        # The decal's transform lives on the decal object, so one projection
        # group serves every decal on every hull.
        # The projection is the decal's own transform on the RAW model
        # position, and the bone does not enter it. parentBoneMatrix comes from
        # JointMat -- the animated SKINNING matrices, not the rest pose -- and
        # it moves the geometry, not the projection. At rest a skinning matrix
        # is identity, so a static hull needs nothing from the bone.
        #
        # Composing the bone's REST matrix here instead threw all nine
        # bone-parented decals off the hull entirely while the eight without a
        # bone stayed correct -- a clean bisect that named this as the fault.
        # parent_bone is kept on the object for when animation matters.
        x, y, z, w = decal.rotation
        obj["carbon_decal_position"] = tuple(decal.position)
        obj["carbon_decal_scaling"] = tuple(decal.scaling)
        obj["carbon_decal_rotation"] = tuple(mathutils.Quaternion((w, x, y, z)).to_euler())
        obj["carbon_decal_bone"] = decal.parent_bone
        if decal.parent_bone >= 0:
            boned += 1

        skin_like_hull(obj, hull, order)
        mesh.materials.append(build_decal_material(decal, resources, obj))
        built.append(obj)

    for reason, count in skipped.items():
        print(f"  ! {count} decal(s) skipped: {reason}")
    print(f"  built {len(built)} decal object(s); {boned} are skinned to a bone, identity at rest")
    return built


def skin_like_hull(obj, hull, vertex_map):
    """Makes a decal deform with the hull it was copied from.

    A decal's vertices ARE hull vertices, so its weights are the hull's. Copying
    the groups and adding the same armature modifier makes the decal follow
    every deformation exactly, which is also the right answer to the bone
    question: the shader takes parentBoneMatrix from JointMat, the SKINNING
    matrices, so in Blender the armature does that work rather than the
    projection.

    Parenting alone only carries the object transform, so an animated hull
    would deform out from under its decals.
    """

    if not hull.vertex_groups:
        return

    armature = next((m.object for m in hull.modifiers
                     if m.type == "ARMATURE" and m.object), None)
    if armature is None:
        return

    groups = {}
    for group in hull.vertex_groups:
        groups[group.index] = obj.vertex_groups.new(name=group.name)

    for source_index, target_index in vertex_map.items():
        for entry in hull.data.vertices[source_index].groups:
            target = groups.get(entry.group)
            if target is not None and entry.weight:
                target.add([target_index], entry.weight, "REPLACE")

    modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature


def build_decal_material(decal, resources, obj=None):
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

    if decal.shader == "decalholev5.fx":
        wire_hull_breach(decal, principled, sampled, projection, mnodes, mlinks,
                         glow_colour, resources)
    elif decal.shader == "decalcounterv5.fx" and transparency is not None:
        wire_kill_counter(decal, principled, transparency, projection,
                          mnodes, mlinks, glow_colour, intensity, obj)
    elif transparency is not None:
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

    # A decal that drives the surface itself -- the emissive ones -- has already
    # connected it, and relinking the BSDF here would silently overwrite that.
    if not output.inputs["Surface"].is_linked:
        mlinks.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def wire_kill_counter(decal, principled, transparency, projection, mnodes, mlinks,
                      glow_colour, intensity, obj=None):
    """Draws a kill counter as tally marks driven by the ship's count.

    The counter decal carries no number of its own: the count arrives per
    OBJECT, and the shader turns it into nine columns by three rows of marks,
    lighting as many in each row as that row's decimal digit and discarding the
    rest. The mark texture is sampled nine times across, which is why it repeats
    here where every other decal map clips.
    """

    counter = mnodes.new("ShaderNodeGroup")
    counter.node_tree = nodes.build_kill_counter_group()
    counter.location = (-400, -200)
    mlinks.new(projection.outputs["UV"], counter.inputs["UV"])

    transparency.extension = "REPEAT"
    mlinks.new(counter.outputs["Mark UV"], transparency.inputs["Vector"])

    # alpha = coverage * (mark * intensity)^2 -- the square is the shader's own,
    # applied after the scaling, and it is what keeps the marks hard-edged.
    scaled = mnodes.new("ShaderNodeMath")
    scaled.operation = "MULTIPLY"
    scaled.location = (-200, -300)
    scaled.label = "mark x intensity"
    mlinks.new(transparency.outputs["Color"], scaled.inputs[0])
    scaled.inputs[1].default_value = float(intensity[0]) if intensity else 1.0

    squared = mnodes.new("ShaderNodeMath")
    squared.operation = "MULTIPLY"
    squared.location = (-60, -300)
    squared.label = "squared"
    mlinks.new(scaled.outputs[0], squared.inputs[0])
    mlinks.new(scaled.outputs[0], squared.inputs[1])

    alpha = mnodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.location = (80, -300)
    alpha.label = "discarded where no mark"
    mlinks.new(squared.outputs[0], alpha.inputs[0])
    mlinks.new(counter.outputs["Coverage"], alpha.inputs[1])
    mlinks.new(alpha.outputs[0], principled.inputs["Alpha"])

    # The counter GLOWS rather than reflecting, and it glows at the ship's own
    # emission strength -- the shader scales it by the same per-object value the
    # hull glows use, so a counter lit at 1.0 reads as paint, not as a light.
    if glow_colour:
        principled.inputs["Emission Color"].default_value = tuple(glow_colour[:3]) + (1.0,)
        principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        strength = principled.inputs["Emission Strength"]
        strength.default_value = nodes.SHIP_PROPERTIES["previewGlowScale"][1]
        if obj is None:
            return
        index = list(principled.inputs).index(strength)
        path = f'nodes["{principled.name}"].inputs[{index}].default_value'
        principled.id_data.driver_remove(path)
        driver = principled.id_data.driver_add(path).driver
        driver.type = "SCRIPTED"
        variable = driver.variables.new()
        variable.name = "v"
        variable.targets[0].id_type = "OBJECT"
        variable.targets[0].id = obj
        variable.targets[0].data_path = f'["{nodes.SHIP_PROPERTIES["previewGlowScale"][0]}"]'
        driver.expression = "v"


#: An interior cube, unwrapped so Blender can sample it by direction. The
#: bundle flattens a cube to a single face, which throws five sixths of the
#: interior away, so the equirect is built beside it by
#: `scripts/prepare_cube_texture.mjs` and carries a `.source.json` recording
#: what it came from -- a converted file otherwise goes stale in silence.
EQUIRECT_SUFFIX = "_equirect.png"


def emissive_surface(mnodes, mlinks, output):
    """An emissive, alpha-blended surface: what the glow decals actually are.

    `decalholev5`, `decalglowv5` and `decalcounterv5` all write a colour and an
    alpha and nothing else -- no diffuse, no specular, no normal. Running that
    through a Principled BSDF adds a specular reflection the shader has no term
    for, and against a bright nebula that sheen reads as a flat GREY panel
    sitting where the decal is. An Emission mixed with Transparent has no such
    term.

    Returns (emission node, mix node); the caller sets the colour and the
    factor.
    """

    transparent = mnodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (200, 120)
    emission = mnodes.new("ShaderNodeEmission")
    emission.location = (200, -60)
    mix = mnodes.new("ShaderNodeMixShader")
    mix.location = (380, 0)
    mlinks.new(transparent.outputs[0], mix.inputs[1])
    mlinks.new(emission.outputs[0], mix.inputs[2])
    mlinks.new(mix.outputs[0], output.inputs["Surface"])
    return emission, mix


def wire_hull_breach(decal, principled, sampled, projection, mnodes, mlinks,
                     glow_colour, resources):
    """A breach that reads as a hole rather than as a sticker.

    decalholev5 fakes depth: it rays the view through a UNIT SPHERE in decal
    space and samples the interior cube along where the ray LEAVES it, so the
    inside shifts as the camera moves.

        colour = DecalGlowColor * mix(holeMap.x, interior.a, holeMap.w)
        alpha  = DecalTransparencyMap.a

    The interior lives in the cube's ALPHA channel, not its colour.
    """

    hole = sampled.get("DecalHoleMap")
    transparency = sampled.get("DecalTransparencyMap")
    if transparency is not None:
        mlinks.new(transparency.outputs["Alpha"], principled.inputs["Alpha"])

    interior = None
    cube = decal.textures.get("DecalInsideCubeMap")
    local = resources.get(cube or "")
    if local:
        equirect = os.path.splitext(local)[0] + EQUIRECT_SUFFIX
        if os.path.exists(equirect):
            ray = mnodes.new("ShaderNodeGroup")
            ray.node_tree = nodes.build_hole_group()
            ray.location = (-400, -400)
            mlinks.new(projection.outputs["Position"], ray.inputs["Position"])
            mlinks.new(projection.outputs["View"], ray.inputs["View"])

            image = bpy.data.images.load(equirect, check_existing=True)
            image.colorspace_settings.name = "Non-Color"
            interior = mnodes.new("ShaderNodeTexEnvironment")
            interior.image = image
            interior.location = (-200, -400)
            interior.label = "DecalInsideCubeMap"
            # The interior is a few small, hard-edged lights on a dark ground.
            # Linear filtering over an unwrapped 128-pixel cube smears those
            # into a speckle; Closest keeps them as the windows they are.
            interior.interpolation = "Closest"
            mlinks.new(ray.outputs["Direction"], interior.inputs["Vector"])

            # A ray that misses the sphere is discarded, so it contributes no
            # interior rather than a smeared edge texel.
            if transparency is not None:
                gated = mnodes.new("ShaderNodeMath")
                gated.operation = "MULTIPLY"
                gated.location = (-60, -240)
                gated.label = "discard where the ray misses"
                mlinks.new(transparency.outputs["Alpha"], gated.inputs[0])
                mlinks.new(ray.outputs["Hit"], gated.inputs[1])
                mlinks.new(gated.outputs[0], principled.inputs["Alpha"])

    # colour = glow * mix(rim, interior, blend)
    mixed = mnodes.new("ShaderNodeMix")
    mixed.data_type = "FLOAT"
    mixed.location = (100, -400)
    mixed.label = "rim -> interior"
    if hole is not None:
        mlinks.new(hole.outputs["Color"], mixed.inputs["A"])
        mlinks.new(hole.outputs["Alpha"], mixed.inputs["Factor"])
    else:
        mixed.inputs["Factor"].default_value = 1.0
    if interior is not None:
        # The interior is the cube's ALPHA. Environment Texture has no alpha
        # output, so the converter writes that channel into RGB as well and
        # this reads it from Color.
        mlinks.new(interior.outputs["Color"], mixed.inputs["B"])

    colour = mnodes.new("ShaderNodeVectorMath")
    colour.operation = "SCALE"
    colour.location = (260, -400)
    colour.inputs[0].default_value = tuple(glow_colour[:3]) if glow_colour else (1.0, 1.0, 1.0)
    mlinks.new(mixed.outputs["Result"], colour.inputs["Scale"])

    # No BSDF: the shader writes a colour and an alpha, and nothing else.
    output = next(n for n in mnodes if n.bl_idname == "ShaderNodeOutputMaterial")
    emission, mix = emissive_surface(mnodes, mlinks, output)
    mlinks.new(colour.outputs[0], emission.inputs["Color"])
    for link in list(principled.outputs[0].links):
        mlinks.remove(link)
    if principled.inputs["Alpha"].is_linked:
        mlinks.new(principled.inputs["Alpha"].links[0].from_socket, mix.inputs["Fac"])


def populate_sof(obj, document, family):
    """Fills the ship's SOF panels from the document it was built from.

    The panels ARE the SOF once the ship is in Blender -- they are what gets
    edited and what will get exported -- so a freshly built ship must arrive
    with them filled in rather than blank. Anything left blank here reads as
    "this ship has no faction", which is a lie about the data.

    The DNA is the authority for the components, because a built document keeps
    it verbatim; the materials come from what the areas actually resolved to.
    """

    from carbon_eve_resources import sof_panels
    try:
        sof_panels.register()
    except Exception:
        pass                      # already registered, which is fine

    settings = obj.carbon_sof
    dna = str(document.get("dna") or "")
    if dna:
        # hull:faction:race, with an optional trailing :pattern?...
        head, _, tail = dna.partition(":pattern?")
        parts = head.split(":")
        settings.hull = parts[0] if len(parts) > 0 else ""
        settings.faction = parts[1] if len(parts) > 1 else ""
        settings.race = parts[2] if len(parts) > 2 else ""
        settings.pattern = tail

    # The material slots, read back from what the areas resolved to. The hull
    # material is the honest source: every area shares the faction's colours.
    wanted = [(index, False) for index in (1, 2, 3, 4)] + [(index, True) for index in (1, 2)]
    for index, is_pattern in wanted:
        if settings.slot(index, is_pattern) is None:
            entry = settings.materials.add()
            entry.index = index
            entry.is_pattern = is_pattern

    material = next((slot.material for slot in obj.material_slots
                     if slot.material and slot.material.use_nodes), None)
    group = None
    if material is not None:
        group = next((n for n in material.node_tree.nodes
                      if n.bl_idname == "ShaderNodeGroup" and n.node_tree
                      and "Pattern" not in n.node_tree.name), None)
    if group is not None:
        for entry in settings.materials:
            sockets = sof_panels.PATTERN_SOCKETS if entry.is_pattern else sof_panels.MATERIAL_SOCKETS
            # Read EVERY value before writing any of them. Assigning a field
            # fires the update that pushes the whole slot back into the
            # material, so reading and writing in the same pass overwrites the
            # sockets still to be read -- which silently gave every slot the
            # default gloss of 0.5 instead of the authored 0.774.
            found = {}
            for field, pattern in sockets.items():
                socket = group.inputs.get(pattern.format(entry.index))
                if socket is None:
                    continue
                found[field] = (float(socket.default_value) if field == "gloss"
                                else tuple(socket.default_value)[:3])
            for field, value in found.items():
                setattr(entry, field, value)

    print(f"  SOF: {settings.dna or '(no dna in the document)'}")


def main():
    args = parse_args(sys.argv)
    primary = assemble(args)
    hide_non_geometry()
    if primary is None:
        raise SystemExit("no geometry was assembled")

    decal_objects = build_decals(
        load_document(args.sof), primary,
        load_manifest(args.resources),
        load_family())
    ship_objects = [primary] + list(decal_objects)
    apply_ship_globals(ship_objects,
                       {"previewGlowScale": preview_quad.DEMO_EMISSION_STRENGTH})
    drive_ship_sockets(ship_objects, primary)
    populate_sof(primary, load_document(args.sof), load_family())
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
