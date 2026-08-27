"""Building a ship in Blender from a SOF bundle.

The object tree mirrors an `EveShip2`: a hull with its mesh areas, the decals
that belong to it, and the per-ship values every one of them reads. That
structure is the point rather than a convenience -- what gets exported is the
SOF hull and faction, so a scene that does not map back to one cannot be
exported at all.

Values are driven FROM the SOF. Editing the objects directly is possible and
unsupported: it cannot be exported, and the next SOF change overwrites it.

This lives in the add-on rather than in a script because the panel and the
command line must build the SAME ship. When they did not, two hulls in one
scene came out looking like different games -- one with the measured quad
shaders, one with an approximation the panel could reach.
"""

from __future__ import annotations

import json
import os

import bpy
import mathutils

from . import logos, placeholders
from .quad import decals as decal_module
from .quad import interface as quad_interface
from .quad import nodes
from .quad.materials import (build_area_material, ensure_projection,
                             fill_unbound_textures)

#: The area lists an `EveShip2` keeps, in the order they are drawn.
BATCHES = ("opaqueAreas", "transparentAreas", "additiveAreas", "distortionAreas")


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
    known_actions = set(bpy.data.actions)
    bpy.ops.import_scene.carbon_gr2(filepath=path)
    created = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    keep_actions([action for action in bpy.data.actions if action not in known_actions],
                 [o for o in bpy.data.objects if o not in before and o.type == "ARMATURE"])
    parent_to_armature(created)
    for mesh in created:
        store_rest_position(mesh)
    return created


#: The undeformed vertex position, stored as our own mesh attribute.
#:
#: Carbon projects patterns and decals from the RAW model position -- the
#: vertex before skinning -- which is what fixes a pattern to the surface so it
#: deforms with the hull. Blender's Texture Coordinate gives the DEFORMED
#: position instead, so a posed bone slides the hull through a pattern that
#: stays put in space.
#:
#: Blender's own `rest_position` would serve, but it is opt-in per mesh and the
#: property does not exist in Blender 5. Writing it ourselves is also closer to
#: the truth: this is the model position, which is what the shader means.
REST_POSITION = "carbon_rest_position"


def store_rest_position(mesh_object):
    """Records each vertex's undeformed position on the mesh.

    Called at import, when the vertices ARE the rest pose: no armature has been
    evaluated yet, so the coordinates on the mesh datablock are the model's own.
    """

    data = getattr(mesh_object, "data", None)
    if data is None or not hasattr(data, "attributes") or not data.vertices:
        return None
    if REST_POSITION in data.attributes:
        return data.attributes[REST_POSITION]

    attribute = data.attributes.new(REST_POSITION, "FLOAT_VECTOR", "POINT")
    flat = [component for vertex in data.vertices for component in vertex.co]
    attribute.data.foreach_set("vector", flat)
    return attribute


def parent_to_armature(meshes):
    """Puts each skinned mesh UNDER the armature that deforms it.

    The importer leaves them as SIBLINGS: the mesh carries an Armature modifier
    and neither object is parented to the other. Nothing looks wrong until the
    ship is moved -- then the mesh moves and the rig does not, the modifier
    deforms against a rig that is no longer where the mesh is, and the geometry
    flies off in a direction that looks like a broken rig rather than a broken
    parent.

    Parenting is the fix Blender expects: the armature is the root of a skinned
    object, and moving it moves the ship. The world transform is preserved, so
    nothing shifts at the moment of parenting.
    """

    parented = 0
    for mesh in meshes:
        armature = next((m.object for m in mesh.modifiers
                         if m.type == "ARMATURE" and m.object), None)
        if armature is None or mesh.parent is not None:
            continue
        mesh.parent = armature
        mesh.matrix_parent_inverse = armature.matrix_world.inverted()
        parented += 1
    if parented:
        print(f"  parented {parented} mesh(es) to their armature")
    return parented


#: The state an idle ship sits in, by the names GR2 files actually use.
#:
#: A hull names its own: gb2_t2 has NormalLoop, StartSiege, SiegeLoop and
#: EndSiege; others carry Normal, Warp, Normal2Warp and Warp2Normal. So this is
#: a preference order and not a lookup -- anything unmatched falls back to the
#: first action the file provided.
IDLE_ACTIONS = ("NormalLoop", "Normal", "Idle")


def keep_actions(actions, armatures):
    """Keeps imported animations, and puts one on the armature.

    The GR2 importer creates an Action per animation but assigns NONE of them,
    so every one has zero users -- and Blender PURGES zero-user data when the
    file is saved. Import by hand and they are there; build a ship and save it
    and they are gone, which is exactly how it looked: actions in a live
    session, an empty dope sheet in the saved file.

    So each gets a fake user, and the armature is given the idle one, because a
    dope sheet with no action assigned shows nothing at all and reads as an
    import that failed.
    """

    for action in actions:
        action.use_fake_user = True

    if not actions or not armatures:
        return actions

    def rank(action):
        name = action.name.rsplit(".", 1)[-1]
        for position, wanted in enumerate(IDLE_ACTIONS):
            if name == wanted:
                return position
        return len(IDLE_ACTIONS)

    idle = sorted(actions, key=rank)[0]
    for armature in armatures:
        if armature.animation_data is None:
            armature.animation_data_create()
        if armature.animation_data.action is None:
            armature.animation_data.action = idle
    print(f"  kept {len(actions)} action(s); armature set to {idle.name}")
    return actions


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


def assemble(document_path, resources_directory, *, clear=True,
             document=None, resources=None, family=None):
    """Imports the geometry and shades every mesh area of a document.

    The document, resource map and shader family can be passed in when the
    caller already has them, so a whole build reads each ONCE.
    """

    document = load_document(document_path) if document is None else document
    resources = load_manifest(resources_directory) if resources is None else resources
    family = quad_interface.load_family() if family is None else family

    if clear:
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


#: How wide the KIND field is before a set's own name.
#:
#: Padded so the names line up as columns in the outliner, which is the whole
#: reason for the underscores:
#:
#:     decalSets______primary
#:     planeSets______primary
#:     bannerSets_____primary
#:
#: Fifteen is the longest kind plus a gap. A longer kind simply takes its own
#: width rather than being cut, because a truncated name is worse than a ragged
#: column.
KIND_FIELD = 15


def collection_name(kind, group):
    """A set's collection name: the kind, padded, then the set's own name.

    Blender's IDs of one type share ONE namespace, so a second ship's
    `decalSets______primary` becomes `.001`. That is accepted rather than
    engineered around: the collection sits inside its ship, and the
    `visibilityGroup` property carries the meaning, so the suffix costs nothing
    a reader needs.
    """

    return f"{kind:_<{KIND_FIELD}}{group}" if group else kind



def ship_collection(name):
    """The collection that IS the ship, created on demand.

    One ship, one collection, everything of it inside: the armature that roots
    it, the hull, and the decals beneath. That is the `EveShip2` tree in
    Blender's own terms, and it is what makes two ships in one scene separable
    -- an exporter walks the collection and finds a ship rather than a scene
    that happens to contain one.
    """

    scene = bpy.context.scene.collection
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in scene.children:
        scene.children.link(collection)
    return collection


def move_to_collection(obj, collection):
    """Puts an object in one collection and no other."""

    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def decal_collection(group, visibility_group="", parent=None):
    """`<ship> > decals > <set>`, created on demand.

    The tree matters beyond tidiness: an exporter walks collections, so the
    structure here is the structure that leaves. The set is the named group a
    consumer recognises, and it carries the visibility group the client
    switches the whole set by.
    """

    root_parent = parent or bpy.context.scene.collection
    root_name = "decalSets"
    root = next((c for c in root_parent.children if c.name.split(".")[0] == root_name), None)
    if root is None:
        root = bpy.data.collections.new(root_name)
        root_parent.children.link(root)
    child_name = collection_name("decalSets", group)
    child = next((c for c in root.children if c.name.split(".")[0] == child_name), None)
    if child is None:
        child = bpy.data.collections.new(child_name)
        root.children.link(child)
    # The visibility group lives on the GROUP, not on each decal: it is what the
    # client switches a whole set on and off by, and a consumer editing it
    # should be editing one value rather than seventeen.
    if visibility_group and child.get("visibilityGroup") != visibility_group:
        child["visibilityGroup"] = visibility_group
    return child


def build_decals(document, hull, resources, family, decal_sets=None, collection=None):
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
    # A built decal carries neither its name nor its visibility group: EveSOF
    # copies the transform, the bone and the effect onto it and leaves the set
    # behind. Both come from the hull record, matched by transform rather than
    # by index -- see decal_module.name_decals for why index would misname them.
    if decal_sets:
        found = decal_module.name_decals(found, decal_sets)
        named = sum(1 for decal in found if decal.sof_name)
        print(f"  named {named}/{len(found)} decal(s) from the hull's decal sets")
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
        decal_collection(decal.group, decal.visibility_group, collection).objects.link(obj)
        stamp_identity(obj, decal.sof_name, "decal", decal.visibility_group)
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
        # A decal projects from the model position too, and its triangles are
        # copies of the hull's -- so it needs its own copy of the rest pose
        # before anything deforms it.
        store_rest_position(obj)
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

    from . import sof_panels
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


#: A plane faces +Z in its own space -- ccpwgl's `PlaneNormal` is [0, 0, 1, 0]
#: -- so the quad lies in local XY and the item's scaling sizes it.
#:
#: The corner OFFSETS themselves live in a vertex constant buffer the engine
#: fills (planeglow reads `cb0[cornerIndex]`), which we have not measured, so
#: the unit quad here is the obvious one and wants checking against a client
#: render before it is trusted.
PLANE_CORNERS = ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0))


def stamp_identity(obj, name, kind, group=""):
    """Records what a thing IS, apart from what it is called.

    Blender's names are unique PER ID TYPE across the whole file, and a
    collection does not scope them: a second ship's `Killmarks` becomes
    `Killmarks.001` no matter where it sits. So a display name cannot be relied
    on to carry meaning -- it is already `.001` the moment two ships are open.

    The SOF's own name goes on the object as a property instead. An exporter
    reads that and is unaffected by however Blender chose to spell the name.
    """

    if name:
        obj["carbon_sof_name"] = str(name)
    obj["carbon_sof_kind"] = str(kind)
    if group:
        obj["carbon_visibility_group"] = str(group)
    return obj


def quad_mesh(name):
    """A unit quad WITH a UV map.

    The UVs are the point: without them an image samples one corner texel for
    the whole face, and since a logo is drawn on black that texel is black --
    so a banner whose alpha comes from luminance vanishes completely. It looks
    exactly like a banner that was never built.
    """

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(PLANE_CORNERS), [], [(0, 1, 2, 3)])
    mesh.update()
    uvs = mesh.uv_layers.new(name="UV0")
    # In the order PLANE_CORNERS gives: bottom left, bottom right, top right,
    # top left.
    for index, coordinate in enumerate(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
        uvs.data[index].uv = coordinate
    return mesh


def find_typed(document, wanted):
    """Every node of a `_type`, in document order."""

    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") == wanted:
                found.append(node)
            for value in node.values():
                walk(value)

    walk(document)
    return found


def attach_to_bone(obj, armature, bone_index):
    """Parents an object to a bone, keeping where it already is.

    An attachment is RIGID -- it rides its bone rather than being deformed by
    several -- so it is bone-parented rather than skinned, which is the opposite
    of how a decal follows the hull.

    The index is stored either way. Bone order is the model's, and if that ever
    disagrees with the armature's the property is what makes it visible instead
    of leaving an attachment silently on the wrong bone.
    """

    obj["carbon_bone_index"] = int(bone_index)
    if armature is None or bone_index is None or bone_index < 0:
        return False
    bones = armature.data.bones
    if bone_index >= len(bones):
        return False
    bone = bones[bone_index]
    obj["carbon_bone_name"] = bone.name
    world = obj.matrix_world.copy()
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone.name
    # Bone parenting is relative to the bone's TAIL, and the object has not been
    # evaluated since it was placed, so the inverse is computed rather than
    # left to Blender to work out later.
    obj.matrix_parent_inverse = (armature.matrix_world @ bone.matrix_local
                                 @ mathutils.Matrix.Translation((0.0, bone.length, 0.0))).inverted()
    obj.matrix_world = world
    return True


def build_plane_sets(document, hull, collection, hull_sets=None):
    """One quad per `EvePlaneSetItem`, placed and coloured as the set says.

    A plane set is the ship's glowing panels and light strips: additive quads
    at a transform, each with its own colour and blink data. They are rigid, so
    each rides its bone rather than being skinned.
    """

    armature = hull.parent if hull is not None and hull.parent is not None         and hull.parent.type == "ARMATURE" else None
    built = []
    for set_index, plane_set in enumerate(find_typed(document, "EvePlaneSet")):
        effect = (plane_set.get("effect") or {}).get("effectFilePath", "")
        planes = plane_set.get("planes") or []
        if not planes:
            continue
        source = (hull_sets or [])[set_index] if set_index < len(hull_sets or []) else {}
        group = attachment_collection(
            collection, "planeSets",
            str(source.get("visibilityGroupName") or "primary"),
            source.get("visibilityGroup"))
        for index, item in enumerate(planes):
            mesh = quad_mesh(f"planeset{set_index}_{index}")
            obj = bpy.data.objects.new(f"plane_{set_index}_{index}", mesh)
            group.objects.link(obj)

            obj.matrix_world = item_matrix(item, hull)

            colour = tuple(item.get("color") or (1.0, 1.0, 1.0, 1.0))
            obj["carbon_plane_color"] = colour
            obj["carbon_plane_blink"] = tuple(item.get("blinkData") or (1.0, 0.0, 1.0, 0.0))
            obj["carbon_plane_effect"] = str(effect)
            obj.data.materials.append(plane_material(colour, set_index, index))
            attach_to_bone(obj, armature, item.get("boneIndex"))
            stamp_identity(obj, f"plane_{index}", "plane",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)
    if built:
        print(f"  built {len(built)} plane(s) from {len(find_typed(document, 'EvePlaneSet'))} set(s)")
    return built


def attachment_collection(parent, kind, group="", visibility_hash=None):
    """`<ship> > <kind> > <group>`, the shape every attachment set shares.

    This follows the SOF rather than the built ship. An `EveShip2` keeps its
    attachments loose in one list; the SOF keeps them in named sets under a
    visibility group, and the SOF is what gets exported -- so the SOF's shape is
    the one worth having in the scene.

    An attachment set is named by its VISIBILITY GROUP: that is the identity a
    hull gives it, and what the client switches the whole set by. The group
    carries it as a property so a consumer edits one value rather than one per
    item, exactly as the decal sets do.

    The hull record spells it twice, as a string and as an fnv1 hash. Both are
    kept: the string is what a person reads, the hash is what the engine
    compares.
    """

    root_name = kind
    root = next((c for c in parent.children if c.name.split(".")[0] == root_name), None)
    if root is None:
        root = bpy.data.collections.new(root_name)
        parent.children.link(root)
    if not group:
        return root
    child_name = collection_name(kind, group)
    child = next((c for c in root.children if c.name.split(".")[0] == child_name), None)
    if child is None:
        child = bpy.data.collections.new(child_name)
        root.children.link(child)
    if child.get("visibilityGroup") != group:
        child["visibilityGroup"] = group
    # As a STRING. The hash is fnv1 and unsigned 32-bit -- 4181794693 for
    # "primary" -- and Blender's integer properties are signed, so storing it as
    # a number raises rather than wrapping.
    if visibility_hash is not None:
        text = str(int(visibility_hash))
        if child.get("visibilityGroupHash") != text:
            child["visibilityGroupHash"] = text
    return child


def plane_material(colour, set_index, index):
    """An additive glow of the plane's own colour.

    `planeglow` layers two scrolling maps through a mask and is NOT measured, so
    this is the item's authored colour emitted flat -- the placement and the
    colour are honest, the texturing is not there yet.
    """

    material = bpy.data.materials.new(f"plane {set_index}.{index}")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-200, 0)
    emission.inputs["Color"].default_value = tuple(colour[:3]) + (1.0,)
    emission.inputs["Strength"].default_value = 1.0
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, 160)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (0, 0)
    mix.inputs["Fac"].default_value = float(colour[3]) if len(colour) > 3 else 1.0
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    return material


#: What a banner's `reference` names -- `EveSOFDataHullBanner.Usage`, verbatim.
#:
#: Twenty-four of them, not three. A hull carries more than the alliance, corp
#: and CEO slots people think of: vertical and horizontal banners, the target
#: and current system's, publicity posters and five recruitment panels. mde3_t3
#: alone uses a VERTICAL_BANNER, which a three-entry table would have named "3".
BANNER_REFERENCES = (
    "alliance_logo", "corp_logo", "ceo_portrait",
    "vertical_banner", "horizontal_banner",
    "target_system_alliance_logo", "target_system_vertical_banner",
    "target_system_horizontal_banner",
    "target_system_info_0", "target_system_info_1", "target_system_info_2",
    "target_system_info_3", "target_system_info_4", "target_system_status",
    "current_system_alliance_logo", "current_system_vertical_banner",
    "current_system_horizontal_banner",
    "publicity_poster", "publicity_portrait",
    "recruitment_information_0", "recruitment_information_1",
    "recruitment_information_2", "recruitment_information_3",
    "recruitment_information_4",
)


def build_banner_sets(document, hull, collection, hull_sets=None, owners=None,
                      cache_directory=""):
    """Banners and the lights that shine on them.

    A banner is the quad a player's alliance, corporation or CEO logo is drawn
    on, and a banner set carries its own lights -- the fittings that light the
    logo rather than scene lighting.

    The logo itself is an EXTERNAL parameter: which image lands here depends on
    who owns the ship, so the slot is recorded and left empty rather than filled
    with a guess.
    """

    armature = hull.parent if hull is not None and hull.parent is not None         and hull.parent.type == "ARMATURE" else None
    built, lights = [], []
    for set_index, banner_set in enumerate(find_typed(document, "EveBannerSet")):
        banners = banner_set.get("banners") or []
        if not banners:
            continue
        source = (hull_sets or [])[set_index] if set_index < len(hull_sets or []) else {}
        group = attachment_collection(
            collection, "bannerSets",
            str(source.get("visibilityGroupName") or "primary"),
            source.get("visibilityGroup"))

        banners_built = []
        for index, item in enumerate(banners):
            reference = item.get("reference")
            slot = BANNER_REFERENCES[reference] if isinstance(reference, int)                 and 0 <= reference < len(BANNER_REFERENCES) else str(reference or "")
            mesh = quad_mesh(f"bannerset{set_index}_{index}")
            obj = bpy.data.objects.new(f"banner_{slot or set_index}_{index}", mesh)
            group.objects.link(obj)
            obj.matrix_world = item_matrix(item, hull)
            obj["carbon_banner_reference"] = int(reference or 0)
            obj["carbon_banner_slot"] = slot
            # angleX and angleY tilt the banner about its own axes; kept as
            # authored so nothing is lost, and not yet applied.
            obj["carbon_banner_angles"] = (float(item.get("angleX") or 0.0),
                                           float(item.get("angleY") or 0.0))
            obj.data.materials.append(
                banner_material(slot, set_index, index, owners, cache_directory))
            attach_to_bone(obj, armature, item.get("bone"))
            stamp_identity(obj, slot, "banner",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)
            banners_built.append(obj)

        for index, light in enumerate(banner_set.get("lights") or []):
            data = light.get("lightData") or {}
            lamp = bpy.data.lights.new(f"bannerlight{set_index}_{index}", "POINT")
            # radius is the reach, innerRadius the falloff start; Blender has one
            # size, so the inner radius is what the lamp's own radius becomes.
            lamp.shadow_soft_size = float(data.get("innerRadius") or 0.0) * 0.01
            colour = tuple(data.get("color") or (0.0, 0.0, 0.0, 0.0))
            lamp.color = tuple(colour[:3]) or (1.0, 1.0, 1.0)
            lamp.energy = float(data.get("brightness") or 1.0)
            # The light belongs to ONE banner: the hull record pairs them, and
            # the built radii are the banner's own scaling times the set's
            # multipliers -- 12.566 by 0.3 gives the 3.77 inner radius exactly.
            # So it hangs off its banner rather than sitting loose in the set,
            # and moving the banner takes its light along.
            owner = banners_built[index] if index < len(banners_built) else None
            obj = bpy.data.objects.new(
                f"{owner.name}_light" if owner else f"banner_light_{set_index}_{index}", lamp)
            group.objects.link(obj)
            world = item_matrix(data, hull)
            obj.matrix_world = world
            obj["carbon_light_radius"] = float(data.get("radius") or 0.0)
            obj["carbon_light_inner_radius"] = float(data.get("innerRadius") or 0.0)
            obj["carbon_light_flags"] = int(data.get("flags") or 0)
            if owner is not None:
                obj.parent = owner
                obj.matrix_parent_inverse = owner.matrix_world.inverted()
                obj.matrix_world = world
            else:
                attach_to_bone(obj, armature, data.get("boneIndex"))
            lights.append(obj)

    if built or lights:
        print(f"  built {len(built)} banner(s) and {len(lights)} banner light(s)")
        zero = sum(1 for light in lights if max(light.data.color) == 0.0)
        if zero:
            print(f"    ! {zero} banner light(s) have a colour of zero in the document")
    return built + lights


def banner_material(slot, set_index, index, owners=None, cache_directory=""):
    """A banner's surface: its placeholder if it has one, black if it does not.

    BLACK IS TRANSPARENT. Alpha comes from the image's luminance rather than
    from an alpha channel, so a placeholder is a bright outline floating where
    the banner is instead of a black rectangle stuck to the hull -- and a slot
    with no placeholder disappears entirely, which is the honest look for a
    banner nobody has filled.
    """

    material = bpy.data.materials.new(f"banner {slot or set_index}.{index}")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-200, -60)
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, 120)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (0, 0)
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs["Surface"])

    # A real logo when we know whose ship this is; the placeholder otherwise.
    image = None
    if owners and cache_directory:
        try:
            image = logos.banner_logo(slot, owners, cache_directory)
        except logos.LogoError as error:
            print(f"  ! {error}")
    if image is not None:
        material["carbon_logo"] = image.name
    image = image or placeholders.banner_placeholder(slot)
    if image is None:
        # No placeholder: black, and therefore invisible.
        emission.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        mix.inputs["Fac"].default_value = 0.0
    else:
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.location = (-560, 0)
        texture.label = "placeholder"
        texture.extension = "CLIP"
        luminance = tree.nodes.new("ShaderNodeRGBToBW")
        luminance.location = (-360, -160)
        tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
        # BLACK IS TRANSPARENT, for a real logo as much as a placeholder: the
        # image server draws logos on black, so luminance is the alpha whether
        # or not the PNG carries one.
        tree.links.new(texture.outputs["Color"], luminance.inputs["Color"])
        tree.links.new(luminance.outputs["Val"], mix.inputs["Fac"])
        emission.inputs["Strength"].default_value = 2.0

    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    material.use_backface_culling = False
    return material


def item_matrix(item, hull):
    """An attachment's world transform, from a position/rotation/scaling triple.

    Built explicitly because a freshly created object has not been evaluated:
    reading matrix_world back at this point gives identity, which puts every
    attachment at its bone rather than where the document says.
    """

    x, y, z, w = tuple(item.get("rotation") or (0.0, 0.0, 0.0, 1.0))
    local = mathutils.Matrix.LocRotScale(
        mathutils.Vector(tuple(item.get("position") or (0.0, 0.0, 0.0))),
        mathutils.Quaternion((w, x, y, z)),
        mathutils.Vector(tuple(item.get("scaling") or (1.0, 1.0, 1.0))))
    return (hull.matrix_world @ local) if hull is not None else local


def build_ship(document_path, resources_directory, *, clear=True,
               globals_overrides=None, decal_sets=None, hull_record=None,
               owners=None, cache_directory=""):
    """Builds a whole ship: geometry, areas, decals, and the SOF that drives it.

    ONE call, because the panel and the command line must produce the same
    ship. When each assembled its own way the decals came through on one path
    and not the other, which is not a difference a consumer should have to know
    about.

    Returns the hull object, or None when the document assembled no geometry.
    """

    # Read ONCE. Expanding the document twice produced two separate object
    # trees for the same ship: the areas were shaded from one and the decals
    # built from the other, so a value corrected in one graph was invisible to
    # the other -- a whole class of bug for no benefit.
    document = load_document(document_path)
    resources = load_manifest(resources_directory)
    family = quad_interface.load_family()

    existing = set(bpy.data.objects)
    primary = assemble(document_path, resources_directory, clear=clear,
                       document=document, resources=resources, family=family)
    if primary is None:
        return None

    # One collection per ship, named for the hull, holding EVERYTHING of the
    # ship: the armature that roots it, every mesh including the secondary ones
    # like the shield sphere, and the decals below. Membership is by what the
    # build CREATED rather than by parenting, because a secondary mesh brings
    # its own armature and is parented to neither the hull nor its rig.
    root = primary
    while root.parent is not None:
        root = root.parent
    collection = ship_collection(root.name.replace("_Armature", "") or "ship")
    for obj in bpy.data.objects:
        if obj not in existing:
            move_to_collection(obj, collection)

    decal_objects = build_decals(document, primary, resources, family, decal_sets,
                                 collection)
    plane_objects = build_plane_sets(document, primary, collection,
                                     (hull_record or {}).get("planeSets"))
    banner_objects = build_banner_sets(document, primary, collection,
                                       (hull_record or {}).get("bannerSets"),
                                       owners, cache_directory)

    # Every object of the ship reads the same per-ship values, and a decal is
    # its own object, so the values are written to all of them and the material
    # sockets are driven from the hull.
    ship = [primary] + list(decal_objects) + list(plane_objects) + list(banner_objects)
    apply_ship_globals(ship, globals_overrides)
    drive_ship_sockets(ship, primary)
    populate_sof(primary, document, family)
    prune_empty_collections()
    return primary


def prune_empty_collections():
    """Removes the empty collections an import leaves behind.

    The GR2 importer makes a collection per file; once the ship has been
    gathered into its own, those are empty shells that make the outliner read
    as though the ship were in several places at once.
    """

    removed = 0
    for collection in list(bpy.data.collections):
        if collection.objects or collection.children or collection.users > 1:
            continue
        bpy.data.collections.remove(collection)
        removed += 1
    return removed
