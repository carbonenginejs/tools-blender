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
import math
import os
from pathlib import Path

import bpy
import mathutils

from . import logos, placeholders, sof_enums, sof_faction_nodes
from .core import resfile, weapons
from .quad import decals as decal_module
from .quad import interface as quad_interface
from .quad import materials as quad_materials
from .quad import nodes
from .quad.materials import (build_area_material,
                             ensure_projection,
                             fill_unbound_textures)

#: The area lists an `EveShip2` keeps, in the order they are drawn.
BATCHES = ("opaqueAreas", "transparentAreas", "additiveAreas", "distortionAreas")


CARBON_DOCUMENT_SCHEMA = "carbon.document"


def expand_document(document):
    """Expands a `carbon.document` node graph into the tree this script walks.

    One shape: the CjsModel hydration spelling. `_type` names the class, `_id`
    identifies a node, and `{"_ref": id}` points at one.

    A second `{id, kind, fields}` / `{"$ref": id}` format used to arrive here
    too. It was tools-core's internal class-population form and is retired, so
    reading it is gone rather than kept "just in case" -- a reader that accepts
    a format nothing writes only hides the day something starts writing it
    again by accident.

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
        identity = node.get("_id")
        if identity is not None:
            nodes[identity] = node

    cache = {}

    def reference_of(value):
        """The id a value points at, or None."""

        return value.get("_ref")

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
        out = {"_type": node.get("_type")}
        cache[node_id] = out
        # The properties are on the node itself.
        fields = {key: value for key, value in node.items()
                  if key not in ("_type", "_id")}
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


def strip_storage_name(objects, path):
    """Takes the cached FILENAME back off the names an import made.

    The importer names everything `<file stem>_<shape>`, and our file stem is
    the resource's storage address -- sixteen hex, an underscore, thirty-two
    more. So a Legion's hull arrived called

        c9f99c58d3574e1b_12c0293aa9d0c653e45455b40be2061a_MDe3_TShape3

    and every decal, plane and banner borrowed that as its ship id, which made
    the outliner unreadable. The shape's own name is what a person recognises,
    and Blender adds `.001` itself when a second ship brings the same one.
    """

    import re

    stem = os.path.splitext(os.path.basename(str(path)))[0]
    prefix = re.compile(r"^" + re.escape(stem) + r"[._]?", re.IGNORECASE)
    for obj in objects:
        shortened = prefix.sub("", obj.name)
        if shortened and shortened != obj.name:
            obj.name = shortened
        if obj.data is not None and getattr(obj.data, "name", None):
            shortened = prefix.sub("", obj.data.name)
            if shortened:
                obj.data.name = shortened
    return objects


def import_geometry(path, label="", logical_path=""):
    # The importer ships inside this add-on now, so it is a sibling import
    # rather than a separate add-on someone had to install. Registering it here
    # when absent keeps script paths working, which may run the builder without
    # enabling anything. Re-registering an enabled importer appends its File >
    # Import callback a second time even though Blender rejects the duplicate
    # classes, which is how one CMF operator appeared twice in the menu.
    from . import gr2_importer

    operators = bpy.ops.import_scene
    if (not hasattr(operators, "carbon_gr2")
            or not hasattr(operators, "carbon_cmf")):
        gr2_importer.register()
    before = set(bpy.data.objects)
    known_actions = set(bpy.data.actions)
    format_hint = str(logical_path or path).replace("\\", "/").lower()
    if format_hint.endswith(".cmf"):
        bpy.ops.import_scene.carbon_cmf(filepath=str(path))
    else:
        bpy.ops.import_scene.carbon_gr2(filepath=str(path))
    created = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    keep_actions([action for action in bpy.data.actions if action not in known_actions],
                 [o for o in bpy.data.objects if o not in before and o.type == "ARMATURE"])
    fresh = [o for o in bpy.data.objects if o not in before]
    strip_storage_name(fresh, path)
    if label:
        # Suffixed like everything else in the ship, so the hull and its
        # skeleton say which ship they belong to instead of relying on
        # Blender's .001 -- which says only that a name was taken.
        for obj in fresh:
            remember_name(obj, obj.name, label)
            obj.name = unique_name(obj.name, label)
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

        # Override if present, else the fallback below -- never recomputed
        # from anything else, which is the rule the addressing contract sets.
        #
        # The fallback is EDGE, not REPEAT. REPEAT is the one mode that tiles a
        # pattern across a whole hull, and Carbon treats clamp as the pattern
        # norm: `CustomMaskClamps` is set from `addressUMode == 3` and the
        # shader lerps the pattern UV toward `clamp(uv, 0, 1)`.
        #
        # It IS a fallback and not a fact. The true answer is the effect
        # container's declared sampler default, which the measured family data
        # does not carry yet -- see
        # `/docs/contracts/webgl2-emulated-addressing.md`.
        sampler = overrides.get(f"PatternMask{index + 1}MapSampler", {})
        obj[prefix + "wrap"] = (
            address_to_mode.get(sampler.get("addressUMode",
                                            sampler.get("addressU", 3)), 1.0),
            address_to_mode.get(sampler.get("addressVMode",
                                            sampler.get("addressV", 3)), 1.0),
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
    label = ship_label(document)

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

        objects = import_geometry(local, label, path)
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
            # Which AREA this material is, and which area TYPE chose its
            # materials.
            #
            # The type used to be missing: `Tr2MeshArea` keeps name, index and
            # count and dropped it, so a later pass recovered it from the hull
            # record by matching -- which found seven of twenty-six on a
            # Legion. The runtime now annotates SOF-authored areas with
            # `_areaType`, the selector it actually used, and depth-area clones
            # inherit it. Where that is present it is the answer and no
            # matching is needed.
            authored = area.get("_areaType")
            if authored is not None:
                material["carbon_area_type"] = int(authored)
            material["carbon_area"] = str(area.get("name") or "")
            material["carbon_area_index"] = int(index)
            material["carbon_area_count"] = int(area.get("count") or 1)
            material["carbon_area_shader"] = str(
                area.get("effect", {}).get("effectFilePath", "")).rsplit("/", 1)[-1].lower()
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


#: How wide the MEANING is before the unique part of a name.
#:
#: Every name is the thing it means on the left, padded, then whatever makes it
#: unique on the right, so the unique column lines up and can be read down:
#:
#:     decalSets_____________________mde3_t3
#:     primary_______________________mde3_t3.decalSets
#:     Killmarks_____________________mde3_t3
#:
#: The meaning is the array name for a set collection, the visibility group for
#: a group, and the item's own name for an object. A name longer than the field
#: takes its own width rather than being cut, because a truncated name is worse
#: than a ragged column.
NAME_FIELD = 30


#: The set collections built for the ship in hand, by (kind, group).
#:
#: Kept because a NAME cannot be used to find them again. Two kinds each have a
#: "primary", Blender renames the second, and a lookup by name then misses it
#: and builds another -- which is how a single plane set once grew into
#: primary.001 and primary.002.
_SET_COLLECTIONS = {}


def reset_set_collections():
    _SET_COLLECTIONS.clear()



#: How many underscores separate the widest name from the unique part.
NAME_GAP = 3


def align_names(collection):
    """Re-pads every name in a ship so the unique parts line up.

    The padding target cannot be known while building -- it is the WIDEST name
    in the ship, and the widest name is only known once the last one exists. So
    names are written with a minimum gap and squared up here, from the meaning
    each thing carries as a property rather than by trying to read it back out
    of a name that already has underscores in it.
    """

    things = []

    def walk(node):
        meaning = node.get("carbon_meaning")
        unique = node.get("carbon_unique")
        if meaning and unique:
            things.append((node, str(meaning), str(unique)))
        for child in getattr(node, "children", []):
            walk(child)
        for obj in getattr(node, "objects", []):
            meaning = obj.get("carbon_meaning")
            unique = obj.get("carbon_unique")
            if meaning and unique:
                things.append((obj, str(meaning), str(unique)))

    walk(collection)
    if not things:
        return 0
    width = max(len(meaning) for _, meaning, _ in things) + NAME_GAP
    for node, meaning, unique in things:
        node.name = f"{meaning:_<{width}}{unique}"
    return width


def remember_name(node, meaning, unique):
    """Keeps the two halves of a name, so it can be re-padded later."""

    node["carbon_meaning"] = str(meaning)
    node["carbon_unique"] = str(unique)
    return node


def unique_name(meaning, unique):
    """`meaning_______unique`, padded so the unique parts line up.

    Blender's names are unique PER ID TYPE across the whole file and a
    collection does NOT scope them, so a second ship's `Killmarks` becomes
    `Killmarks.001` wherever it sits. Supplying the unique part ourselves is
    what keeps the meaning readable: the ship is on the right, and nothing is
    left to Blender to disambiguate.
    """

    if not unique:
        return str(meaning)
    # At least one underscore, always. A name as long as the field would
    # otherwise run straight into the unique part -- "LightStrip_rightmde3_t3"
    # -- which is unreadable and, worse, ambiguous about where one ends.
    width = max(NAME_FIELD, len(str(meaning)) + 1)
    return f"{meaning:_<{width}}{unique}"



def ship_label(document):
    """What to call this ship, and what everything in it is suffixed with.

    The hull from the DNA -- `mde3_t3` -- numbered when the scene already has
    one: `mde3_t3`, `mde3_t3_2`, `mde3_t3_3`. A name and a number, because the
    id appears on every decal, plane and banner in the outliner and has to be
    readable at a glance.
    """

    def taken(name):
        # An EMPTY collection does not count. A rebuild deletes the objects
        # and leaves the shell behind until it is pruned, and counting that
        # would make every reload of one ship a "second" one.
        found = bpy.data.collections.get(name)
        return found is not None and bool(found.objects or found.children)

    dna = str((document or {}).get("dna") or "")
    base = dna.split(":", 1)[0].strip().lower() or "ship"
    if not taken(base):
        return base
    number = 2
    while taken(f"{base}_{number}"):
        number += 1
    return f"{base}_{number}"


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
    root_name = unique_name("decalSets", root_parent.name)
    root = next((c for c in root_parent.children if c.name == root_name), None)
    if root is None:
        root = bpy.data.collections.new(root_name)
        remember_name(root, "decalSets", root_parent.name)
        root_parent.children.link(root)
    key = (root_parent.name, "decalSets", group)
    child = _SET_COLLECTIONS.get(key)
    if child is None or child.name not in bpy.data.collections:
        child = bpy.data.collections.new(unique_name(group, root_parent.name))
        remember_name(child, group, root_parent.name)
        root.children.link(child)
        _SET_COLLECTIONS[key] = child
    # The visibility group lives on the GROUP, not on each decal: it is what the
    # client switches a whole set on and off by, and a consumer editing it
    # should be editing one value rather than seventeen.
    if visibility_group and child.get("visibilityGroup") != visibility_group:
        child["visibilityGroup"] = visibility_group
    return child


def build_decals(document, hull, resources, family, decal_sets=None,
                 collection=None, faction_slots=None, faction_tree=None):
    """Copies each decal's hull triangles into its own mesh and shades it.

    A decal is a re-draw of part of the hull, not a surface of its own, so the
    triangles come from `staticIndexBuffers` indexing the hull's vertices. They
    are lifted very slightly along their normals because Carbon biases in depth
    instead -- adding 1e-5 to clip-space z -- and Blender has no equivalent that
    works the same way in both EEVEE and Cycles.
    """

    import bmesh
    import mathutils

    # Every name is its meaning then the ship, so two hulls of the same class
    # can sit in one scene without Blender renaming either of them.
    ship_name = collection.name if collection is not None else (hull.name if hull else "")

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

    # The hull's UVs, so a decal can read the hull's OWN maps where it sits.
    #
    # `decalv5` samples two sets: its own maps through the projection, and the
    # hull's normal, dirt and dust at the MESH UV -- the decal is a re-draw of
    # hull triangles and is lit as part of it. Copying only positions left the
    # second set unreachable, so the hull's normal never reached the decal.
    #
    # UVs live per LOOP, not per vertex, so the source polygon has to be found
    # again: a triangle's three vertex indices identify it.
    source_uv = source.uv_layers.active if source.uv_layers else None
    by_triangle = {}
    if source_uv is not None:
        for polygon in source.polygons:
            if len(polygon.vertices) < 3:
                continue
            by_triangle[frozenset(polygon.vertices)] = {
                vertex: source_uv.data[loop].uv.copy()
                for vertex, loop in zip(polygon.vertices, polygon.loop_indices)}

    for decal in found:
        if not decal.triangles:
            skipped["no triangles"] = skipped.get("no triangles", 0) + 1
            continue

        mesh = bpy.data.meshes.new(decal.name)
        bm = bmesh.new()
        uvs = bm.loops.layers.uv.new("UV0") if by_triangle else None
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
                face = bm.faces.new(verts)
                if uvs is not None:
                    corners = by_triangle.get(frozenset(triangle))
                    if corners:
                        for loop, index in zip(face.loops, triangle):
                            found_uv = corners.get(index)
                            if found_uv is not None:
                                loop[uvs].uv = found_uv
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

        obj = remember_name(
            bpy.data.objects.new(unique_name(decal.name, ship_name), mesh),
            decal.name, ship_name)
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
        mesh.materials.append(build_decal_material(
            decal, resources, obj, faction_slots, faction_tree))
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


def build_decal_material(decal, resources, obj=None, faction_slots=None,
                         faction_tree=None):
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
        image = quad_materials.load_texture(
            local, name=resfile.display_name(path), logical_path=path)
        if image is None:
            continue          # a 3D volume texture, or unreadable
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

    wire_decal_normals(decal, resources, mnodes, mlinks, principled, projection,
                       sampled)

    transparency = sampled.get("DecalTransparencyMap")
    albedo = sampled.get("DecalAlbedoMap")
    glow = sampled.get("DecalGlowMap")

    if albedo is not None:
        mlinks.new(albedo.outputs["Color"], principled.inputs["Base Color"])
    if "DecalRoughnessMap" in sampled:
        mlinks.new(sampled["DecalRoughnessMap"].outputs["Color"], principled.inputs["Roughness"])

    # Carbon's fresnel colour IS F0, and Principled expresses a dielectric's F0
    # as `0.08 * Specular IOR Level * Specular Tint` -- so the map is scaled by
    # 1/0.08 at full level to land F0 = colour, exactly as the hull's is.
    #
    # It was loaded and never connected. A decal whose colour lives in its F0
    # rather than its albedo then draws BLACK: the Amarr logo is gold metal,
    # and gold is a fresnel colour.
    if "DecalFresnelMap" in sampled:
        principled.inputs["Specular IOR Level"].default_value = 1.0
        f0 = mnodes.new("ShaderNodeVectorMath")
        f0.operation = "SCALE"
        f0.location = (-40, -160)
        f0.label = "F0 -> Specular Tint"
        f0.inputs["Scale"].default_value = 1.0 / 0.08
        mlinks.new(sampled["DecalFresnelMap"].outputs["Color"], f0.inputs[0])
        mlinks.new(f0.outputs[0], principled.inputs["Specular Tint"])

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
        # A decal names its glow by TYPE -- the hull record carries
        # `glowColorType`, and 11, 12, 15 and 17 on an Abaddon are fire, hull,
        # darkhull and killmark. The SOF endpoint resolves that before we see
        # it, so the slot is recovered from the value and the faction drives
        # it from then on.
        bind_faction_colour(mnodes, mlinks,
                            principled.inputs["Emission Color"], glow_colour,
                            faction_slots, faction_tree, (-200, 320),
                            {"_glowColorType": decal.glow_color_type}
                            if decal.glow_color_type >= 0 else None,
                            "_glowColorType")

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
        # One, and no driver. This used to be scaled by a preview-only
        # multiplier that was correct at 1 and that nobody needed to change --
        # so it was a knob, a per-ship property and a driver, all to multiply
        # by one.
        principled.inputs["Emission Strength"].default_value = 1.0


#: An interior cube, unwrapped so Blender can sample it by direction. The
#: bundle flattens a cube to a single face, which throws five sixths of the
#: interior away, so a prepared equirectangular image may sit beside it and
#: carry a `.source.json` recording what it came from -- a converted file
#: otherwise goes stale in silence.
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


def wire_decal_normals(decal, resources, mnodes, mlinks, principled, projection,
                       sampled):
    """A decal has TWO normals, and they live in different spaces.

    The HULL's normal map is read at the MESH UV. A decal is a re-draw of hull
    triangles and is lit as part of the hull, so it has to carry the same
    surface detail as the plate around it or it reads as a sticker on smooth
    metal. It is an ordinary tangent-space map.

    The DECAL's own normal map is read through the PROJECTION, and its vectors
    are in the DECAL's frame -- the one its position, rotation and scaling
    describe -- not the surface's. So it is decoded, turned by the decal's own
    rotation and handed over in OBJECT space. Feeding it as tangent-space would
    light its relief as though every decal faced the same way, which is right
    only for the one that happens to.

    Summed and renormalised, which is the ordinary way to carry two
    perturbations of one surface.
    """

    def texture(name, projected, y):
        path = decal.textures.get(name)
        local = resources.get(path) if path else None
        if not local or not os.path.exists(local):
            return None
        image = quad_materials.load_texture(
            local, name=resfile.display_name(path), logical_path=path)
        if image is None:
            return None
        image.colorspace_settings.name = "Non-Color"
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-1240, y)
        node.label = name
        if projected:
            node.extension = "CLIP"
            mlinks.new(projection.outputs["UV"], node.inputs["Vector"])
        else:
            uv = mnodes.new("ShaderNodeUVMap")
            uv.uv_map = "UV0"
            uv.location = (-1440, y)
            mlinks.new(uv.outputs["UV"], node.inputs["Vector"])
        return node

    hull = texture("NormalMap", False, -520)
    own = sampled.get("DecalNormalMap") or texture("DecalNormalMap", True, -860)

    vectors = []
    if hull is not None:
        node = mnodes.new("ShaderNodeNormalMap")
        node.location = (-860, -520)
        node.label = "hull normal, at the mesh UV"
        mlinks.new(hull.outputs["Color"], node.inputs["Color"])
        vectors.append(node.outputs["Normal"])

    if own is not None:
        # 0..1 back to a vector, which is what a normal map stores.
        decode = mnodes.new("ShaderNodeVectorMath")
        decode.operation = "MULTIPLY_ADD"
        decode.location = (-1040, -860)
        decode.label = "decode"
        decode.inputs[1].default_value = (2.0, 2.0, 2.0)
        decode.inputs[2].default_value = (-1.0, -1.0, -1.0)
        mlinks.new(own.outputs["Color"], decode.inputs[0])

        spin = mnodes.new("ShaderNodeAttribute")
        spin.attribute_type = "OBJECT"
        spin.attribute_name = "carbon_decal_rotation"
        spin.location = (-1040, -1020)

        turned = mnodes.new("ShaderNodeMapping")
        # NORMAL, not POINT: it rotates and renormalises and applies no
        # translation, which is what a direction needs.
        turned.vector_type = "NORMAL"
        turned.location = (-860, -860)
        turned.label = "into the decal's own frame"
        mlinks.new(decode.outputs[0], turned.inputs["Vector"])
        mlinks.new(spin.outputs["Vector"], turned.inputs["Rotation"])

        encode = mnodes.new("ShaderNodeVectorMath")
        encode.operation = "MULTIPLY_ADD"
        encode.location = (-700, -860)
        encode.label = "encode"
        encode.inputs[1].default_value = (0.5, 0.5, 0.5)
        encode.inputs[2].default_value = (0.5, 0.5, 0.5)
        mlinks.new(turned.outputs["Vector"], encode.inputs[0])

        node = mnodes.new("ShaderNodeNormalMap")
        node.space = "OBJECT"
        node.location = (-540, -860)
        node.label = "decal normal, in decal space"
        mlinks.new(encode.outputs[0], node.inputs["Color"])
        vectors.append(node.outputs["Normal"])

    if not vectors:
        return
    combined = vectors[0]
    if len(vectors) > 1:
        added = mnodes.new("ShaderNodeVectorMath")
        added.operation = "ADD"
        added.location = (-360, -680)
        added.label = "hull + decal"
        mlinks.new(vectors[0], added.inputs[0])
        mlinks.new(vectors[1], added.inputs[1])
        unit = mnodes.new("ShaderNodeVectorMath")
        unit.operation = "NORMALIZE"
        unit.location = (-220, -680)
        mlinks.new(added.outputs[0], unit.inputs[0])
        combined = unit.outputs[0]
    mlinks.new(combined, principled.inputs["Normal"])


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

            image = quad_materials.rename(
                bpy.data.images.load(equirect, check_existing=True),
                resfile.display_name(cube or "") + "_equirect")
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

    from .core import sof_resolution

    settings = obj.carbon_sof
    dna = str(document.get("dna") or "")
    if dna:
        # Every command, not just the components: the editor shows the DNA's
        # mesh, pattern, respathinsert and layout commands, and a ship built
        # from a DNA that carries them must arrive showing them rather than
        # blank fields someone has to fill in again. The DNA is kept verbatim
        # rather than recomposed, so nothing it holds can be lost on the way in.
        settings.read_dna(dna)

    # One group of four slots PER AREA TYPE, because that is how a faction
    # stores them: four material names keyed `areaType:slot`. A single group
    # for the whole ship cannot express a hull and its sails disagreeing about
    # slot 3, and pushing one group everywhere paints the hull's colours onto
    # the sails.
    #
    # The two pattern layers stay ship-wide -- the pattern branch of the
    # resolution chain never consults the area type.
    by_type = {}
    for target in sof_panels.ship_objects(obj):
        for slot in getattr(target, "material_slots", []):
            material = slot.material
            if material is None or not material.use_nodes:
                continue
            area_type = material.get("carbon_area_type", None)
            if area_type is None or int(area_type) < 0:
                continue
            group = next((n for n in material.node_tree.nodes
                          if n.bl_idname == "ShaderNodeGroup" and n.node_tree
                          and "Pattern" not in n.node_tree.name), None)
            if group is None:
                continue
            # First material of a type wins as the representative. Areas of one
            # type resolve identically by construction, so any of them answers.
            by_type.setdefault(int(area_type),
                               (group, int(material.get("carbon_blocked_materials", 0) or 0)))

    if not by_type:
        # No area types were recovered -- no hull record, most likely. One
        # ship-wide group is what we can honestly offer, and it is what the
        # tool did before area types were carried at all.
        group = next((n for slot in obj.material_slots if slot.material
                      and slot.material.use_nodes
                      for n in slot.material.node_tree.nodes
                      if n.bl_idname == "ShaderNodeGroup" and n.node_tree
                      and "Pattern" not in n.node_tree.name), None)
        if group is not None:
            by_type[-1] = (group, 0)

    wanted = [(index, False, area_type) for area_type in sorted(by_type)
              for index in (1, 2, 3, 4)]
    wanted += [(index, True, -1) for index in (1, 2)]
    for index, is_pattern, area_type in wanted:
        entry = settings.slot(index, is_pattern, area_type)
        if entry is None:
            entry = settings.materials.add()
            entry.index = index
            entry.is_pattern = is_pattern
            entry.area_type = area_type
        source = by_type.get(area_type) or next(iter(by_type.values()), None)
        if source is None:
            continue
        group, blocked = source
        entry.blocked = blocked
        sockets = sof_panels.PATTERN_SOCKETS if is_pattern else sof_panels.MATERIAL_SOCKETS
        # Read EVERY value before writing any of them. Assigning a field
        # fires the update that pushes the whole slot back into the
        # material, so reading and writing in the same pass overwrites the
        # sockets still to be read -- which silently gave every slot the
        # default gloss of 0.5 instead of the authored 0.774.
        found = {}
        for field, pattern in sockets.items():
            socket = group.inputs.get(pattern.format(index))
            if socket is None:
                continue
            found[field] = (float(socket.default_value) if field == "gloss"
                            else tuple(socket.default_value)[:3])
        # Filling FROM the SOF, so the colour updates must not read as a
        # person editing them and mark the slot custom.
        with sof_panels.applying():
            for field, value in found.items():
                setattr(entry, field, value)

    # The colours are in the slots now, and they no longer know where they came
    # from -- resolution happened upstream and threw the question away. The DNA
    # still knows, so read it back out before anyone looks at the panel.
    settings.stamp_sources()

    # Point each area's shader at the MATERIAL its slot names, rather than
    # leaving the values as loose constants nobody can trace. A material shared
    # by two areas becomes one node group, so editing it later reaches both
    # without anything walking the ship.
    bound = settings.bind_materials(obj)
    if bound["bound"]:
        print(f"  materials: {bound['bound']} slot(s) bound to "
              f"{bound['materials']} material group(s)")
    if bound["missing"]:
        # Named but unfetchable: those areas still show what they were built
        # with, which is true, and saying so beats a silent black area.
        print(f"  materials: could not fetch {', '.join(bound['missing'])}")

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


def ship_armature(hull, collection=None):
    """The armature an attachment should ride, or None.

    The hull's own parent when it is skinned -- `parent_to_armature` puts a
    skinned mesh under its rig. But an UNSKINNED hull still has a skeleton;
    it simply is not its parent, and looking only there meant every
    attachment on such a hull stored its bone index and bound to nothing.
    Measured: a Legion bound 78 sprites of 78, an Abaddon 0 of 11.

    So the ship's own collection is the fallback. One armature is the normal
    case; where a hull brings several, the first is the one the attachments
    were authored against.
    """

    if hull is not None and hull.parent is not None             and hull.parent.type == "ARMATURE":
        return hull.parent
    for candidate in getattr(collection, "objects", ()):
        if candidate.type == "ARMATURE":
            return candidate
    for candidate in bpy.data.objects:
        if candidate.type == "ARMATURE":
            return candidate
    return None


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

    # Through the MODEL's own bone order, not Blender's.
    #
    # Blender re-sorts bones by hierarchy on leaving edit mode, so
    # `data.bones[i]` is not bone `i` of the model: one of nineteen positions
    # agrees on a Celestis. The importer records the real order, and reading
    # it is the difference between an attachment on its own bone and one on
    # somebody else's -- which looks fine at rest and flies off the moment the
    # hull animates.
    order = armature.get("carbon_bone_order")
    bones = armature.data.bones
    bone = None
    if order is not None and 0 <= bone_index < len(order):
        bone = bones.get(str(order[bone_index]))
    if bone is None:
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


def attach_to_named_bone(obj, armature, name: str):
    """Parents an object to the bone of the SAME NAME, if there is one.

    This is how a hardpoint follows a moving hull. A locator carries no bone
    index -- the named ones are just a name and a matrix -- and the engine
    resolves them by NAME: `EveLocator2.GetTransform` multiplies in the bone
    called the same thing when one is bound.

    Most hulls have no such bone and nothing happens. The ones that do are the
    ones that move: a Kronos has seven of its seventeen turret locators as
    bones, which are exactly the hardpoints that deploy when it sieges.
    """

    if armature is None or not name:
        return False
    bone = armature.data.bones.get(str(name))
    if bone is None:
        return False

    obj["carbon_bone_name"] = bone.name
    world = obj.matrix_world.copy()
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone.name
    # Bone parenting is relative to the bone's TAIL, and the object has not
    # been evaluated since it was placed, so the inverse is computed here
    # rather than left for Blender to work out later.
    obj.matrix_parent_inverse = (
        armature.matrix_world @ bone.matrix_local
        @ mathutils.Matrix.Translation((0.0, bone.length, 0.0))).inverted()
    obj.matrix_world = world
    return True


#: How much of its authored size a sprite dot is drawn at, by DEFAULT.
#:
#: The preference of the same name overrides it, and zero there draws none.
#:
#: EVE's sprites are camera-facing billboards that grow with distance, so
#: `minScale` is a screen-space number rather than a radius in the world.
#: Taken literally it makes beach balls. A tenth reads as a light on a hull,
#: which is what the thing IS -- and the authored scales stay on the object,
#: so nothing is lost by drawing it smaller.
SPRITE_SIZE = 0.047

#: The name the falloff node carries, so the preference can find it in every
#: sprite material without walking the graph looking for something plausible.
SPRITE_FALLOFF_NODE = "carbon sprite falloff"

#: The rim falloff, as an exponent.
#:
#: Lower means MORE visible glow, which is the opposite way round to the
#: intuition. The core blows out wherever t^falloff * brightness exceeds one,
#: and the halo is what the curve leaves between there and the silhouette:
#:
#:   6 against 12   core at t>0.66, but the tail is 0.05 at 40% of the radius
#:                  -- arithmetically a gradient, visually a crisp dot
#:   4 against 10   core at t>0.56, tail 0.26 at 40% -- a halo you can see
#:
#: Too low and the saturated part covers most of the sphere and the fade gets
#: squeezed against the silhouette, which is a hard edge.
SPRITE_FALLOFF = 2.5

#: How hard the centre burns, in emission strength.
#:
#: Deliberately far above one. The core then clips to white and reads as SOLID
#: while the curve below it does the halo -- which is how a point of light is
#: drawn. Clamping to one instead gave a flat disc with a hard edge, and on a
#: low-poly sphere that is a hexagon.
SPRITE_BRIGHTNESS = 6.0

#: One sphere, shared by every sprite in the file. A sprite is a glowing DOT
#: and a sphere reads as one from any direction -- which a flat quad does not,
#: since EVE's are camera-facing billboards and Blender has no such thing
#: without a constraint per object or a geometry-node tree over the lot.
SPRITE_MESH = "CarbonSprite dot"


def sprite_mesh():
    """The shared dot geometry, made once."""

    mesh = bpy.data.meshes.get(SPRITE_MESH)
    if mesh is not None:
        return mesh

    import bmesh

    mesh = bpy.data.meshes.new(SPRITE_MESH)
    working = bmesh.new()
    # Subdivision 2 is 162 vertices. One was 42 and its silhouette read as a
    # hexagon at any size worth seeing, which no amount of shading fixes.
    # Two hundred of these is 32k vertices, which is nothing next to a hull.
    bmesh.ops.create_icosphere(working, subdivisions=2, radius=1.0)
    working.to_mesh(mesh)
    working.free()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    # One empty slot, so each OBJECT can carry its own material over the
    # shared mesh. Without a slot here there is nothing for an object to
    # override, and every sprite in the file would wear the last set's colour.
    mesh.materials.append(None)
    return mesh


def bind_faction_colour(nodes_of, links_of, socket, colour, faction_slots,
                        faction_tree, location=(0, 0), item=None, key=""):
    """Points one colour socket at the faction, or sets it as a literal.

    The whole faction idea in one place: if this colour IS one of the
    faction's slots, the socket reads the faction group and follows it when
    somebody edits it. If it is not, the value is written in and stands alone.

    Returns the slot it bound to, or None.
    """

    value = tuple(float(v) for v in (list(colour or ()) + [1.0])[:4])
    slot = sof_faction_nodes.slot_for(item, key, faction_slots, value)
    if slot is not None and faction_tree is not None:
        node = nodes_of.new("ShaderNodeGroup")
        node.node_tree = faction_tree
        node.location = location
        source = node.outputs.get(slot)
        if source is not None:
            links_of.new(source, socket)
            return slot
        nodes_of.remove(node)
    socket.default_value = value[:3] + (1.0,)
    return None


def faction_colour_material(faction_tree, slot, fallback=(1.0, 1.0, 1.0, 1.0)):
    """An additive glow that READS one of the faction's colour slots.

    One material per slot, not per set and not per object: everything naming
    `primary` shares this, so changing that colour on the faction changes all
    of them at once. That is the whole point of the faction being a source.

    Without a faction to read -- an unknown one, or a colour that matches no
    slot -- the colour is baked in and the material stands alone.
    """

    name = f"{sof_faction_nodes.PREFIX} {slot}" if faction_tree is not None         else "SofFaction literal"
    material = bpy.data.materials.new(unique_name(name, ""))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    # Strength from the OBJECT's alpha times a soft falloff, so a blink drives
    # one sprite without touching the others that share this material.
    info = tree.nodes.new("ShaderNodeObjectInfo")
    info.location = (-300, -220)
    tree.links.new(sprite_falloff(tree, info.outputs["Alpha"]),
                   emission.inputs["Strength"])

    source = None
    if faction_tree is not None and slot in {s.name for s in faction_tree.interface.items_tree if getattr(s, "in_out", "") == "OUTPUT"}:
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = faction_tree
        node.location = (-300, 0)
        source = node.outputs.get(slot)
    if source is not None:
        tree.links.new(source, emission.inputs["Color"])
        material["carbon_faction_slot"] = str(slot)
    else:
        emission.inputs["Color"].default_value = tuple(fallback[:3]) + (1.0,)

    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (0, 180)
    add = tree.nodes.new("ShaderNodeAddShader")
    add.location = (200, 60)
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    return material


def sprite_falloff(tree, strength_socket):
    """Fades a sprite out towards its rim, so a ball reads as a light.

    `blinkinglightspool.fx` carries no texture: the shader draws the dot, and
    what it draws is bright in the middle and gone at the edge. A sphere lit
    flat is a solid ball, which is what made these look like beads on a hull.

    Blender's Layer Weight `Facing` is 0 looking straight at the surface and 1
    at the silhouette -- the opposite way round to what the name suggests, and
    using it directly lit the RIM and left the middle dark, which reads as a
    bubble rather than a light. So it is inverted first, then squared to tighten
    the centre.

    No image to invent, and no camera-facing constraint per object, which is
    the other way to get a billboard and costs one constraint on each of two
    hundred sprites.
    """

    weight = tree.nodes.new("ShaderNodeLayerWeight")
    weight.location = (-700, 180)
    weight.inputs["Blend"].default_value = 0.5

    middle = tree.nodes.new("ShaderNodeMath")
    middle.operation = "SUBTRACT"
    middle.location = (-520, 180)
    middle.label = "centre, not rim"
    middle.inputs[0].default_value = 1.0
    tree.links.new(weight.outputs["Facing"], middle.inputs[1])

    # The curve, NOT clamped. Clamping made a flat disc with a hard edge; an
    # exponent keeps a smooth falloff all the way to the silhouette, and the
    # brightness below overdrives the middle so it clips to white and reads as
    # solid. Solid centre, soft glow, one curve.
    squared = tree.nodes.new("ShaderNodeMath")
    squared.operation = "POWER"
    squared.location = (-340, 180)
    squared.label = SPRITE_FALLOFF_NODE
    squared.name = SPRITE_FALLOFF_NODE
    squared.inputs[1].default_value = SPRITE_FALLOFF
    tree.links.new(middle.outputs[0], squared.inputs[0])

    lit = tree.nodes.new("ShaderNodeMath")
    lit.operation = "MULTIPLY"
    lit.location = (-160, 180)
    lit.label = "x blink"
    tree.links.new(squared.outputs[0], lit.inputs[0])
    tree.links.new(strength_socket, lit.inputs[1])

    bright = tree.nodes.new("ShaderNodeMath")
    bright.operation = "MULTIPLY"
    bright.location = (0, 180)
    bright.label = "brightness"
    bright.inputs[1].default_value = SPRITE_BRIGHTNESS
    tree.links.new(lit.outputs[0], bright.inputs[0])
    return bright.outputs[0]


def drive_blink(obj, rate, phase):
    """Makes one sprite blink, on the object rather than in its material.

    Every sprite naming the same faction colour SHARES a material, so the
    blink cannot live there -- it would blink all of them together. The
    object's own colour alpha is free and the material reads it, so each
    sprite carries its own rhythm and they still share one material.

    `blinkRate` is in cycles per second and `blinkPhase` offsets the cycle,
    which is what stops a row of lights pulsing in unison. A rate of zero is a
    light that is simply on, and gets no driver at all.
    """

    if rate <= 0.0:
        return False
    fps = float(getattr(bpy.context.scene.render, "fps", 24) or 24)
    obj.driver_remove("color", 3)
    driver = obj.driver_add("color", 3).driver
    driver.type = "SCRIPTED"
    # A square wave: EVE's blinking lights are on or off, not a sine fade.
    # Off is 0.15 rather than 0 so an unlit sprite is still findable in the
    # viewport instead of vanishing.
    driver.expression = (
        f"1.0 if ((frame / {fps:g} * {rate:g} + {phase:g}) % 1.0) < 0.5 "
        f"else 0.15")
    return True


def sprite_material(set_index, effect=None, resources=None):
    """One material for a whole sprite set, tinted PER OBJECT.

    Every sprite in a set is the same glow in a different colour, so the
    colour comes from each object rather than from the material: an Object
    Info node reads it, and the set shares one material instead of carrying
    sixty-seven of them.

    `blinkinglightspool.fx` has no texture at all -- the shader draws the dot
    -- so there is nothing to sample here. Additive, like every other glow on
    the ship.
    """

    material = bpy.data.materials.new(unique_name(f"sprite_{set_index}", ""))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)

    info = tree.nodes.new("ShaderNodeObjectInfo")
    info.location = (-300, 0)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    tree.links.new(info.outputs["Color"], emission.inputs["Color"])
    # Strength from the object's ALPHA, which is what the blink drives, faded
    # towards the rim so the sphere reads as a glow rather than a bead.
    tree.links.new(sprite_falloff(tree, info.outputs["Alpha"]),
                   emission.inputs["Strength"])

    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (0, 180)
    add = tree.nodes.new("ShaderNodeAddShader")
    add.location = (200, 60)
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    material["carbon_effect"] = str((effect or {}).get("effectFilePath") or "")
    return material


#: The sphere a haze is drawn as, shared by every one in the file.
HAZE_MESH = "CarbonHaze sphere"

#: The names the haze nodes carry, so a preference can find them in every haze
#: material without walking the graph looking for something plausible.
HAZE_FALLOFF_NODE = "carbon haze falloff"
HAZE_DENSITY_NODE = "carbon haze density"

#: How dense a haze is, per unit of its authored alpha.
#:
#: This is the DIAL's default, not a factor it multiplies: the preference
#: replaces this number outright, and a fresh build reads the preference. They
#: have to be the same value or the first nudge of the dial jumps the look --
#: which it did, from a built 4.0 to a dial that read 2.5.
#:
#: The density fed to Blender is this over the cloud's own RADIUS, because a
#: volume's density is per unit of path length: the same number through a
#: cloud two metres across and one a hundred and twenty metres across are two
#: completely different clouds. Dividing by the radius makes the authored
#: alpha mean what it should -- how much is absorbed passing THROUGH -- and
#: makes the look independent of the importer's scale, which is what broke
#: when hulls stopped arriving at a hundredth of their real size.
HAZE_DENSITY = 0.7

#: How quickly a haze thins towards its shell, as an exponent on the distance
#: from its centre.
#:
#: Without this the density is UNIFORM inside the ellipsoid, so the cloud stops
#: dead at the mesh surface -- a hard-edged blob, which is not what haze does.
#: Higher keeps it near the middle; lower spreads it to the shell.
HAZE_FALLOFF = 8.0

#: How much a haze glows in the dark, THROUGH the whole cloud.
#:
#: Small on purpose. At one the emission carried the whole look and the cloud
#: read as a lamp rather than as air.
#:
#: Through the cloud, not per metre of it -- the material divides by the
#: radius. Emission accumulates along the ray just as absorption does, so a
#: flat strength makes a big haze hundreds of times brighter than a small one
#: for the same authored number.
#:
#: Cut by four fifths with the density, on the operator's call. BOTH terms had
#: to move: a volume scatters by its density and emits by its strength, and
#: dropping only one leaves the other carrying the brightness it was asked to
#: give up.
HAZE_EMISSION = 0.01


def haze_mesh():
    """The shared ellipsoid, made once and scaled per haze."""

    mesh = bpy.data.meshes.get(HAZE_MESH)
    if mesh is not None:
        return mesh

    import bmesh

    mesh = bpy.data.meshes.new(HAZE_MESH)
    working = bmesh.new()
    # Smoother than a sprite: this one is metres across rather than a dot, so
    # its silhouette is read directly.
    bmesh.ops.create_icosphere(working, subdivisions=3, radius=1.0)
    working.to_mesh(mesh)
    working.free()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.materials.append(None)
    return mesh


def haze_material(faction_tree, slot, colour, radius=1.0,
                  thickness=None, falloff=None):
    """A haze: a VOLUME, not a surface.

    `hazespherical.fx` carries no texture and no geometry beyond a sphere --
    what it draws is a soft-edged cloud, and the honest Blender equivalent is
    a volume rather than a shell with a clever falloff. A volume needs no
    trick to fade at its edges and looks the same from every direction, which
    is the whole difficulty of the sprite dots solved for free.

    The density comes from the authored ALPHA over the cloud's own RADIUS.
    A volume's density is per unit of path length, so alpha alone describes a
    different cloud at every size -- and at real hull scale, where a haze is a
    hundred metres rather than one, it describes an opaque one. The authored
    value is kept on the object either way.
    """

    name = f"{sof_faction_nodes.PREFIX} haze {slot}" if slot else "SofHaze"
    material = bpy.data.materials.new(unique_name(name, ""))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)

    volume = tree.nodes.new("ShaderNodeVolumePrincipled")
    volume.location = (0, 0)
    alpha = float(colour[3]) if len(colour) > 3 else 0.1
    # Per metre of the path through it, not per cloud.
    span = max(float(radius), 1e-6)
    alpha /= span
    emission = HAZE_EMISSION / span
    # The dials, or the built-in defaults when nothing passes them.
    #
    # `thickness`, not `density`: the density NODE is built below under that
    # name, and a parameter sharing it was rebound to the node before it was
    # read -- so the multiply became alpha times a node and every haze on the
    # ship failed to build, silently, with the rest of the hull fine.
    thickness = HAZE_DENSITY if thickness is None else float(thickness)
    falloff = HAZE_FALLOFF if falloff is None else float(falloff)

    # Density that THINS towards the shell. The distance from the object's own
    # centre is 0 in the middle and 1 at the surface, because the shared mesh
    # is a unit sphere -- so one minus that, raised to a power, is a cloud that
    # fades out instead of ending at the mesh.
    where = tree.nodes.new("ShaderNodeTexCoord")
    where.location = (-900, -260)
    span = tree.nodes.new("ShaderNodeVectorMath")
    span.operation = "LENGTH"
    span.location = (-720, -260)
    tree.links.new(where.outputs["Object"], span.inputs[0])

    inward = tree.nodes.new("ShaderNodeMath")
    inward.operation = "SUBTRACT"
    inward.location = (-540, -260)
    inward.label = "centre, not shell"
    inward.use_clamp = True
    inward.inputs[0].default_value = 1.0
    tree.links.new(span.outputs["Value"], inward.inputs[1])

    curve = tree.nodes.new("ShaderNodeMath")
    curve.operation = "POWER"
    curve.location = (-360, -260)
    curve.label = HAZE_FALLOFF_NODE
    curve.name = HAZE_FALLOFF_NODE
    curve.inputs[1].default_value = falloff
    tree.links.new(inward.outputs[0], curve.inputs[0])

    density = tree.nodes.new("ShaderNodeMath")
    density.operation = "MULTIPLY"
    density.location = (-180, -260)
    density.label = HAZE_DENSITY_NODE
    density.name = HAZE_DENSITY_NODE
    density.inputs[1].default_value = alpha * thickness
    tree.links.new(curve.outputs[0], density.inputs[0])
    tree.links.new(density.outputs[0], volume.inputs["Density"])

    # A little emission so a haze is visible in the dark, but not so much that
    # it reads as a lamp instead of as air.
    if "Emission Strength" in volume.inputs:
        # Per METRE of the path through it, exactly as the density is.
        #
        # This is the one that kept the haze bright. A volume's emission
        # accumulates along the ray, so a flat strength through a cloud 250
        # metres across arrives 250 times over -- which swamped the density
        # cut entirely and left a solid glowing blob under the hull. Dividing
        # by the radius makes the number mean what it should: how much the
        # whole cloud emits, not how much each metre of it does.
        volume.inputs["Emission Strength"].default_value = emission * thickness
    material["carbon_haze_emission"] = emission

    source = None
    if faction_tree is not None and slot:
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = faction_tree
        node.location = (-300, 0)
        source = node.outputs.get(slot)
    if source is not None:
        tree.links.new(source, volume.inputs["Color"])
        if "Emission Color" in volume.inputs:
            tree.links.new(source, volume.inputs["Emission Color"])
        material["carbon_faction_slot"] = str(slot)
    else:
        flat = tuple(float(v) for v in colour[:3]) + (1.0,)
        volume.inputs["Color"].default_value = flat
        if "Emission Color" in volume.inputs:
            volume.inputs["Emission Color"].default_value = flat

    tree.links.new(volume.outputs[0], output.inputs["Volume"])
    # The authored alpha, kept so a density preference can rescale against it
    # rather than replacing what the SOF said.
    material["carbon_haze_alpha"] = alpha
    return material


def build_haze_sets(document, hull, collection, hull_sets=None,
                    faction_slots=None, faction_tree=None,
                    thickness=None, falloff=None):
    """The soft clouds a hull sits in: one ellipsoid per haze.

    Rare -- of eight hulls checked only a Dominix had any, and it has five --
    but they are what makes a ship look like it is venting rather than sitting
    in a vacuum.

    Each haze is placed AND SCALED by its own transform, so the shared sphere
    becomes the ellipsoid the SOF describes: 119 by 93 by 162 on that hull,
    which is metres across rather than a dot.
    """

    armature = ship_armature(hull, collection)
    mesh = None
    built, lights = [], []
    hidden = 0
    for set_index, haze_set in enumerate(find_typed(document, "EveHazeSet")):
        hazes = haze_set.get("hazes") or haze_set.get("items") or []
        if not hazes:
            continue
        source = ((hull_sets or [])[set_index]
                  if set_index < len(hull_sets or []) else {})
        group = attachment_collection(
            collection, "hazeSets",
            str(source.get("visibilityGroupName") or "primary"),
            source.get("visibilityGroup"))
        if mesh is None:
            mesh = haze_mesh()

        for index, item in enumerate(hazes):
            obj = remember_name(bpy.data.objects.new(
                unique_name(f"haze_{set_index}_{index}", collection.name), mesh),
                f"haze_{set_index}_{index}", collection.name)
            group.objects.link(obj)
            obj.matrix_world = item_matrix(item, hull)

            colour = tuple(float(v) for v in
                           (item.get("color") or (1.0, 1.0, 1.0, 1.0)))
            slot = sof_faction_nodes.slot_for(item, "_colorType",
                                              faction_slots, colour)
            if obj.material_slots:
                obj.material_slots[0].link = "OBJECT"
                # Its own size in WORLD units, which is what the density has
                # to be divided by. The mesh is a unit sphere, so the scale
                # in the matrix is the radius.
                extent = obj.matrix_world.to_scale()
                radius = (abs(extent.x) + abs(extent.y) + abs(extent.z)) / 3.0
                obj.material_slots[0].material = haze_material(
                    faction_tree, slot, colour, radius, thickness, falloff)
            if slot:
                obj["carbon_faction_slot"] = slot

            obj["carbon_haze_color"] = colour
            # Four numbers nobody here has names for. Kept as authored rather
            # than guessed at: whatever they mean, an export needs them.
            obj["carbon_haze_data"] = tuple(
                float(v) for v in (item.get("hazeData") or (0.0, 0.0, 0.0, 0.0)))
            # The same rule the planes follow: a zero colour is not drawn.
            if not any(colour[:3]):
                obj.hide_viewport = True
                obj.hide_render = True
                obj["carbon_display"] = False
                hidden += 1
            attach_to_bone(obj, armature, item.get("boneIndex"))
            stamp_identity(obj, f"haze_{index}", "haze",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)

        for order, light in enumerate(haze_set.get("lights") or []):
            obj = attachment_light(
                light, unique_name(f"haze_{set_index}_light_{order}",
                                   collection.name), hull, armature)
            group.objects.link(obj)
            lights.append(obj)

    if built or lights:
        print(f"  built {len(built)} haze(s) and {len(lights)} haze light(s) "
              f"from {len(find_typed(document, 'EveHazeSet'))} set(s)"
              + (f"; {hidden} hidden" if hidden else ""))
    return built + lights


def build_sprite_sets(document, hull, collection, hull_sets=None,
                      faction_slots=None, faction_tree=None, size=None):
    """The blinking lights: a glowing dot at each authored position.

    Every hull has them and they are the most numerous thing on it -- a Legion
    carries seventy-eight in one set. So they share one mesh and one material
    per set, and differ by object: position, colour, and the scale the dot is
    drawn at.

    `minScale` is what the sprite is drawn at up close and `maxScale` how far
    it grows with distance, which is a screen-space trick Blender has no
    equivalent for. The dot is built at minScale and maxScale is kept on the
    object, so nothing is lost and nothing is invented.
    """

    # Zero means draw none of them: a set that is off costs nothing to build
    # and nothing to look at.
    size = SPRITE_SIZE if size is None else float(size)
    if size <= 0.0:
        return []

    armature = ship_armature(hull, collection)
    mesh = None
    built, lights = [], []
    blinking = 0
    # One material per faction colour SLOT, shared across every set: a hull
    # names four or five of them between all its sprites, so this is a handful
    # of materials rather than one per set or one per sprite.
    by_slot = {}
    for set_index, sprite_set in enumerate(find_typed(document, "EveSpriteSet")):
        sprites = sprite_set.get("sprites") or []
        if not sprites:
            continue
        source = (hull_sets or [])[set_index] if set_index < len(hull_sets or []) else {}
        group = attachment_collection(
            collection, "spriteSets",
            str(source.get("visibilityGroupName") or "primary"),
            source.get("visibilityGroup"))
        material = sprite_material(set_index, sprite_set.get("effect"))
        if mesh is None:
            mesh = sprite_mesh()

        for index, item in enumerate(sprites):
            obj = remember_name(bpy.data.objects.new(
                unique_name(f"sprite_{set_index}_{index}", collection.name), mesh),
                f"sprite_{set_index}_{index}", collection.name)
            group.objects.link(obj)
            # THROUGH THE HULL, like every other attachment. Placing a sprite
            # at its authored position directly puts it in SOF units, and the
            # importer scales a hull by 0.01 -- so they sat a hundred times too
            # far out at a hundred times the size, which reads as "the lights
            # are wrong" rather than "the sprites missed a transform".
            scale = float(item.get("minScale") or 1.0) * size
            local = mathutils.Matrix.LocRotScale(
                mathutils.Vector(tuple(float(v) for v in
                                       (item.get("position") or (0.0, 0.0, 0.0)))[:3]),
                mathutils.Quaternion((1.0, 0.0, 0.0, 0.0)),
                mathutils.Vector((scale, scale, scale)))
            obj.matrix_world = (hull.matrix_world @ local) if hull is not None else local

            # The colour is the FACTION's, not the hull's. It arrives already
            # resolved from the faction's colour set, which is checkable: the
            # same hull under two factions gives two colours, and ab1_t1 is
            # (0.761, 0.518, 0.271) under amarrbase and (1.0, 0.302, 0.0)
            # under empire_gallente. So it is read from the item and never
            # looked up against the hull.
            colour = tuple(float(v) for v in (item.get("color") or (1.0, 1.0, 1.0, 1.0)))
            # The object's OWN colour, which the set's one material reads
            # through an Object Info node. Sixty-seven sprites, one material.
            obj.color = (colour + (1.0, 1.0, 1.0, 1.0))[:4]
            # Which of the faction's colours this sprite named. Everything
            # naming the same slot shares one material and follows the faction
            # when it is edited; a colour that matches no slot keeps its own.
            slot = sof_faction_nodes.slot_for(item, "_colorType",
                                              faction_slots, colour)
            if slot is not None:
                if slot not in by_slot:
                    by_slot[slot] = faction_colour_material(
                        faction_tree, slot, colour)
                wearing = by_slot[slot]
                obj["carbon_faction_slot"] = slot
            else:
                wearing = material

            # The material rides the OBJECT, not the shared mesh.
            if obj.material_slots:
                obj.material_slots[0].link = "OBJECT"
                obj.material_slots[0].material = wearing
            obj["carbon_sprite_color"] = colour
            obj["carbon_sprite_min_scale"] = scale
            # What it was BUILT with, so the preference can resize it later as
            # a ratio rather than having to rebuild the ship.
            obj["carbon_sprite_size"] = size
            obj["carbon_sprite_max_scale"] = float(item.get("maxScale") or scale)
            rate = float(item.get("blinkRate") or 0.0)
            phase = float(item.get("blinkPhase") or 0.0)
            obj["carbon_sprite_blink_rate"] = rate
            obj["carbon_sprite_blink_phase"] = phase
            if drive_blink(obj, rate, phase):
                blinking += 1
            obj["carbon_sprite_falloff"] = float(item.get("falloff") or 0.0)
            attach_to_bone(obj, armature, item.get("boneIndex"))
            stamp_identity(obj, f"sprite_{index}", "sprite",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)

        for order, light in enumerate(sprite_set.get("lights") or []):
            obj = attachment_light(
                light, unique_name(f"sprite_{set_index}_light_{order}",
                                   collection.name), hull, armature)
            group.objects.link(obj)
            lights.append(obj)

    if built or lights:
        print(f"  built {len(built)} sprite(s) and {len(lights)} sprite light(s) "
              f"from {len(find_typed(document, 'EveSpriteSet'))} set(s)"
              + (f"; {blinking} blink" if blinking else ""))
    return built + lights


def attachment_light(entry, name, hull, armature, owner=None):
    """One attachment's light. The same shape wherever it appears.

    Sprites, planes, haze and spotlights all wrap the same `lightData`: a
    position and rotation, a colour, a radius and an inner radius, a
    brightness, and noise terms. So this is written once and used by each of
    them rather than copied per attachment type.

    `entry` is the WRAPPER -- what carries `blinkRate`, `blinkPhase`,
    `fadeType`, `saturation`, `lightProfilePath` and the `index` of the
    attachment it belongs to. Every one of those is kept on the object: none
    of them has a Blender equivalent, and losing them would make the ship
    unexportable.

    Returns the object, unlinked: the caller decides which collection it
    belongs in.
    """

    data = entry.get("lightData") or {}
    lamp = bpy.data.lights.new(name, "POINT")
    # radius is the reach, innerRadius the falloff start; Blender has one
    # size, so the inner radius is what the lamp's own radius becomes.
    #
    # In the HULL'S units, not a hardcoded hundredth. The importer scales a
    # hull by 0.01 by default, and writing that number here meant a radius
    # that was only right while nobody changed the setting -- and silently
    # wrong afterwards, in a way that reads as "the lights are too big".
    unit = hull.matrix_world.to_scale().x if hull is not None else 1.0
    lamp.shadow_soft_size = float(data.get("innerRadius") or 0.0) * abs(unit)
    colour = tuple(data.get("color") or (0.0, 0.0, 0.0, 0.0))
    lamp.color = tuple(colour[:3]) or (1.0, 1.0, 1.0)
    lamp.energy = float(data.get("brightness") or 1.0)

    obj = bpy.data.objects.new(name, lamp)
    world = item_matrix(data, hull)
    obj.matrix_world = world
    obj["carbon_light_radius"] = float(data.get("radius") or 0.0)
    obj["carbon_light_inner_radius"] = float(data.get("innerRadius") or 0.0)
    obj["carbon_light_flags"] = int(data.get("flags") or 0)
    for key in ("noiseAmplitude", "noiseFrequency", "noiseOctaves"):
        if key in data:
            obj[f"carbon_light_{key}"] = float(data.get(key) or 0.0)
    for key in ("blinkRate", "blinkPhase", "saturation"):
        if key in entry:
            obj[f"carbon_light_{key}"] = float(entry.get(key) or 0.0)
    if entry.get("fadeType") is not None:
        obj["carbon_light_fade_type"] = int(entry.get("fadeType") or 0)
    if entry.get("lightProfilePath"):
        obj["carbon_light_profile"] = str(entry.get("lightProfilePath"))
    if entry.get("index") is not None:
        obj["carbon_light_index"] = int(entry.get("index") or 0)

    if owner is not None:
        obj.parent = owner
        obj.matrix_parent_inverse = owner.matrix_world.inverted()
        obj.matrix_world = world
    else:
        attach_to_bone(obj, armature, data.get("boneIndex"))
    return obj


def build_plane_sets(document, hull, collection, hull_sets=None,
                     resources=None):
    """One quad per `EvePlaneSetItem`, placed and coloured as the set says.

    A plane set is the ship's glowing panels and light strips: additive quads
    at a transform, each with its own colour and blink data. They are rigid, so
    each rides its bone rather than being skinned.
    """

    armature = ship_armature(hull, collection)
    built, lights = [], []
    hidden = 0
    for set_index, plane_set in enumerate(find_typed(document, "EvePlaneSet")):
        effect = plane_set.get("effect") or {}
        planes = plane_set.get("planes") or []
        if not planes:
            continue
        source = (hull_sets or [])[set_index] if set_index < len(hull_sets or []) else {}
        group = attachment_collection(
            collection, "planeSets",
            str(source.get("visibilityGroupName") or "primary"),
            source.get("visibilityGroup"))
        planes_built = []
        for index, item in enumerate(planes):
            mesh = quad_mesh(f"planeset{set_index}_{index}")
            obj = remember_name(bpy.data.objects.new(
                unique_name(f"plane_{set_index}_{index}", collection.name), mesh),
                f"plane_{set_index}_{index}", collection.name)
            group.objects.link(obj)

            obj.matrix_world = item_matrix(item, hull)

            colour = tuple(item.get("color") or (1.0, 1.0, 1.0, 1.0))
            # A ZERO colour means the item is not drawn. Carbon sets
            # display=false for it once the faction colour is in
            # (`EveSOFData`: `if (isZeroColor(item.color)) item.display =
            # false`), which is why an Abaddon's planes are (0,0,0,1): they
            # are off, not black. Drawing them was what made plane sets look
            # like solid slabs.
            #
            # Hidden rather than skipped: the item is still part of the SOF and
            # has to survive being exported.
            if not any(float(v) for v in colour[:3]):
                obj.hide_viewport = True
                obj.hide_render = True
                obj["carbon_display"] = False
                hidden += 1
            obj["carbon_plane_color"] = colour
            obj["carbon_plane_blink"] = tuple(item.get("blinkData") or (1.0, 0.0, 1.0, 0.0))
            obj["carbon_plane_effect"] = str(effect.get("effectFilePath") or "")
            # Kept on the object as authored: the two layers each carry their
            # own transform and scroll, which is what makes one strip crawl
            # while the next shimmers off the same two maps.
            for key in ("layer1Transform", "layer2Transform",
                        "layer1Scroll", "layer2Scroll"):
                obj[f"carbon_plane_{key}"] = tuple(
                    float(v) for v in (item.get(key) or (0.0, 0.0, 0.0, 0.0)))
            obj["carbon_plane_mask_atlas"] = int(item.get("maskAtlasID") or 0)
            obj.data.materials.append(
                plane_material(item, set_index, index, effect, resources, hull))
            attach_to_bone(obj, armature, item.get("boneIndex"))
            stamp_identity(obj, f"plane_{index}", "plane",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)
            planes_built.append(obj)

        # A set has NO light, one, or several -- an Abaddon has four for
        # twelve planes -- so nothing here pairs them off by position the way
        # banners do. The wrapper names the plane it belongs to in `index`,
        # and a light naming a plane that is not there stays loose in the set
        # rather than being attached to whatever happened to be next.
        for order, light in enumerate(plane_set.get("lights") or []):
            wanted = light.get("index")
            owner = (planes_built[int(wanted)]
                     if isinstance(wanted, (int, float))
                     and 0 <= int(wanted) < len(planes_built) else None)
            obj = attachment_light(
                light,
                unique_name(f"{(owner.get('carbon_sof_name') or 'plane')}_light"
                            if owner else f"plane_{set_index}_light_{order}",
                            collection.name),
                hull, armature, owner)
            group.objects.link(obj)
            lights.append(obj)
    if built or lights:
        print(f"  built {len(built)} plane(s) and {len(lights)} plane light(s) "
              f"from {len(find_typed(document, 'EvePlaneSet'))} set(s)")
        if hidden:
            print(f"    {hidden} plane(s) hidden: a zero colour means the "
                  f"item is not drawn")
    return built + lights


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

    root_name = unique_name(kind, parent.name)
    root = next((c for c in parent.children if c.name == root_name), None)
    if root is None:
        root = bpy.data.collections.new(root_name)
        remember_name(root, kind, parent.name)
        parent.children.link(root)
    if not group:
        return root
    # The kind is part of what makes a group unique: every kind has a
    # "primary", and Blender would rename the second one and then never find it
    # again -- which produced a fresh primary.001, .002 per set.
    # The group carries only its own name and the ship: the parent collection
    # already says which kind it is, so repeating it reads as noise.
    key = (parent.name, kind, group)
    child = _SET_COLLECTIONS.get(key)
    if child is None or child.name not in bpy.data.collections:
        child = bpy.data.collections.new(unique_name(group, parent.name))
        remember_name(child, group, parent.name)
        root.children.link(child)
        _SET_COLLECTIONS[key] = child
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


def plane_material(item, set_index, index, effect=None, resources=None,
                   ship_object=None):
    """A glow panel: two scrolling layers through a mask, ADDED to the scene.

    The same shape as a banner, which is measured from `banner.fx`, minus the
    logo -- `planeglow.fx` carries Layer1Map, Layer2Map and MaskMap and nothing
    else to draw:

        uv1    = uv * layer1Transform.xy + layer1Transform.zw + layer1Scroll.xy * t
        uv2    = uv * layer2Transform.xy + layer2Transform.zw + layer2Scroll.xy * t
        colour = Mask * Layer2 * Layer1 * Color
        alpha  = Layer1.a * Layer2.a * Mask.a

    The difference from a banner is WHERE the numbers come from: a banner's
    transforms are constants on the effect, shared by the set, while a plane
    carries its own -- which is what lets one strip crawl and the next one
    shimmer while both read the same two maps.

    This was a flat emission of the plane's authored colour before, which is
    why the panels looked like solid geometry: that colour is (0, 0, 0, 1) on
    every plane of an Abaddon, so the quads rendered as black slabs. Black is
    the honest answer for an unlit panel once the blend is additive -- it adds
    nothing, and the light comes from the maps.
    """

    material = bpy.data.materials.new(unique_name(f"plane_{set_index}_{index}", ""))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (760, 0)

    def four(name, fallback):
        value = list(item.get(name) or fallback)
        return [float(v) for v in (value + fallback)[:4]]

    layer1_transform = four("layer1Transform", [1.0, 1.0, 0.0, 0.0])
    layer2_transform = four("layer2Transform", [1.0, 1.0, 0.0, 0.0])
    layer1_scroll = four("layer1Scroll", [0.0, 0.0, 0.0, 0.0])
    layer2_scroll = four("layer2Scroll", [0.0, 0.0, 0.0, 0.0])
    tint = four("color", [1.0, 1.0, 1.0, 1.0])

    time_socket = nodes.time_value(tree, (-1500, 300))

    def scrolled(transform, rate, row):
        """One layer's UV: scaled, offset, and carried along by time."""

        coordinate = tree.nodes.new("ShaderNodeUVMap")
        coordinate.location = (-1500, row)
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (-1300, row)
        mapping.inputs["Scale"].default_value = (transform[0], transform[1], 1.0)
        mapping.inputs["Location"].default_value = (transform[2], transform[3], 0.0)
        tree.links.new(coordinate.outputs["UV"], mapping.inputs["Vector"])

        offset = tree.nodes.new("ShaderNodeVectorMath")
        offset.operation = "SCALE"
        offset.location = (-1120, row - 140)
        offset.inputs[0].default_value = (rate[0], rate[1], 0.0)
        tree.links.new(time_socket, offset.inputs["Scale"])

        moved = tree.nodes.new("ShaderNodeVectorMath")
        moved.operation = "ADD"
        moved.location = (-940, row)
        tree.links.new(mapping.outputs["Vector"], moved.inputs[0])
        tree.links.new(offset.outputs[0], moved.inputs[1])
        return moved.outputs[0]

    def sample(name, vector, row):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.location = (-720, row)
        node.label = name
        node.extension = "REPEAT"
        image = _effect_image(name, effect, resources)
        if image is not None:
            node.image = image
        if vector is not None:
            tree.links.new(vector, node.inputs["Vector"])
        return node

    layer1 = sample("Layer1Map", scrolled(layer1_transform, layer1_scroll, 520), 520)
    layer2 = sample("Layer2Map", scrolled(layer2_transform, layer2_scroll, 200), 200)
    mask = sample("MaskMap", None, -140)

    def multiply(a, b, row, label):
        node = tree.nodes.new("ShaderNodeVectorMath")
        node.operation = "MULTIPLY"
        node.location = (-260, row)
        node.label = label
        tree.links.new(a, node.inputs[0])
        tree.links.new(b, node.inputs[1])
        return node.outputs[0]

    colour = multiply(layer1.outputs["Color"], layer2.outputs["Color"],
                      340, "layer1 x layer2")
    colour = multiply(mask.outputs["Color"], colour, -20, "x mask")

    tinted = tree.nodes.new("ShaderNodeVectorMath")
    tinted.operation = "MULTIPLY"
    tinted.location = (-60, -20)
    tinted.label = "x Color"
    tinted.inputs[1].default_value = tuple(tint[:3])
    tree.links.new(colour, tinted.inputs[0])

    alpha = tree.nodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.location = (-60, -280)
    tree.links.new(layer1.outputs["Alpha"], alpha.inputs[0])
    tree.links.new(layer2.outputs["Alpha"], alpha.inputs[1])
    with_mask = tree.nodes.new("ShaderNodeMath")
    with_mask.operation = "MULTIPLY"
    with_mask.location = (100, -280)
    with_mask.label = "alpha"
    tree.links.new(alpha.outputs[0], with_mask.inputs[0])
    tree.links.new(mask.outputs["Alpha"], with_mask.inputs[1])

    premultiplied = tree.nodes.new("ShaderNodeVectorMath")
    premultiplied.operation = "SCALE"
    premultiplied.location = (260, -20)
    premultiplied.label = "colour x alpha"
    tree.links.new(tinted.outputs[0], premultiplied.inputs[0])
    tree.links.new(with_mask.outputs[0], premultiplied.inputs["Scale"])

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (440, -20)
    tree.links.new(premultiplied.outputs[0], emission.inputs["Color"])

    # Added, not blended: a glow panel puts light on top of what is behind it.
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (440, 200)
    add = tree.nodes.new("ShaderNodeAddShader")
    add.location = (600, 60)
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    return material


#: The TYPE of banner: which one this is, and so which picture belongs on it.
#:
#: GENERATED, not typed out. The enum lives in the runtime's own
#: `EveSOFDataHullBanner.Usage` and again in ccpwgl, so a copy here would be a
#: third that drifts in silence -- and it already did: a hand-written
#: three-entry table named this hull's VERTICAL_BANNER "3".
#:
#: Regenerate with `python scripts/generate_sof_enums.py`.
BANNER_USAGES = tuple(sof_enums.names("bannerUsage"))



def build_banner_sets(document, hull, collection, hull_sets=None, owners=None,
                      cache_directory="", resources=None, overrides=None):
    """Banners and the lights that shine on them.

    A banner is the quad a logo is drawn on, and a banner set carries its own
    lights -- the fittings that light the logo rather than scene lighting.

    Which logo belongs on a set is the SET's key, one of the twenty-four
    usages. mde3_t3 has two sets: two ALLIANCE banners and two CORPORATION
    banners, and no CEO or vertical banner at all.

    The image is an EXTERNAL parameter, so a set with nothing to show is not
    built.
    """

    armature = ship_armature(hull, collection)
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

        # The USAGE is the SET's key, not the item's `reference`.
        #
        # An item's reference is its index within the set's own list; the set is
        # what says which of the twenty-four usages it serves. Reading the item
        # instead gave this hull one banner of each of four different usages
        # when it actually has TWO alliance logos and TWO corporation logos --
        # so three of them went looking for owners that were never asked for and
        # came out blank.
        usage = banner_set.get("key")
        slot = BANNER_USAGES[usage] if isinstance(usage, int)             and 0 <= usage < len(BANNER_USAGES) else str(usage or "")
        # No image, no banner. A banner exists to carry a picture: one with
        # nothing resolved is an invisible quad that can still be selected,
        # exported and puzzled over.
        artwork = None
        if owners and cache_directory:
            try:
                artwork = logos.banner_logo(slot, owners, cache_directory)
            except logos.LogoError as error:
                print(f"  ! {error}")
        if artwork is None and placeholders.banner_placeholder(slot) is None:
            print(f"  no image for the {slot or 'unnamed'} banner set; not building its "
                  f"{len(banners)} banner(s)")
            continue

        banners_built = []
        for index, item in enumerate(banners):
            mesh = quad_mesh(f"bannerset{set_index}_{index}")
            obj = remember_name(bpy.data.objects.new(
                unique_name(f"banner_{slot or set_index}_{index}", collection.name), mesh),
                f"banner_{slot or set_index}", collection.name)
            group.objects.link(obj)
            obj.matrix_world = item_matrix(item, hull)
            obj["carbon_banner_usage"] = int(usage or 0)
            obj["carbon_banner_reference"] = int(item.get("reference") or 0)
            obj["carbon_banner_type"] = slot
            # angleX and angleY tilt the banner about its own axes; kept as
            # authored so nothing is lost, and not yet applied.
            obj["carbon_banner_angles"] = (float(item.get("angleX") or 0.0),
                                           float(item.get("angleY") or 0.0))
            obj.data.materials.append(
                banner_material(slot, set_index, index, owners, cache_directory,
                                banner_set.get("effect"), resources, hull,
                                overrides))
            attach_to_bone(obj, armature, item.get("bone"))
            stamp_identity(obj, slot, "banner",
                           str(source.get("visibilityGroupName") or "primary"))
            built.append(obj)
            banners_built.append(obj)

        for index, light in enumerate(banner_set.get("lights") or []):
            # The light belongs to ONE banner: the hull record pairs them, and
            # the built radii are the banner's own scaling times the set's
            # multipliers -- 12.566 by 0.3 gives the 3.77 inner radius exactly.
            # So it hangs off its banner rather than sitting loose in the set,
            # and moving the banner takes its light along.
            owner = banners_built[index] if index < len(banners_built) else None
            obj = attachment_light(
                light,
                unique_name(f"{(owner.get('carbon_sof_name') or 'banner')}_light"
                            if owner else f"banner_light_{index}",
                            collection.name),
                hull, armature, owner)
            group.objects.link(obj)
            lights.append(obj)

    if built or lights:
        print(f"  built {len(built)} banner(s) and {len(lights)} banner light(s)")
        zero = sum(1 for light in lights if max(light.data.color) == 0.0)
        if zero:
            print(f"    ! {zero} banner light(s) have a colour of zero in the document")
    return built + lights


def own_banner_image(slot, overrides):
    """A picture the person chose for this banner slot, or None.

    Loaded by PATH, so the same file used by two ships is one image in the
    file. A path that will not open is reported and treated as absent: a
    banner falling back to the fetched logo is recoverable, a banner silently
    left blank is not.
    """

    path = str((overrides or {}).get(slot) or "").strip()
    if not path:
        return None
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except (RuntimeError, OSError) as exc:
        print(f"  ! {slot} banner image {path}: {exc}")
        return None
    image.colorspace_settings.name = "sRGB"
    return image


def banner_material(slot, set_index, index, owners=None, cache_directory="",
                    effect=None, resources=None, ship_object=None,
                    overrides=None):
    """A banner: a logo behind two SCROLLING layers and a mask, ADDED to the scene.

    Measured from `banner.fx` rather than guessed:

        uv1    = uv * Layer1Transform.xy + Layer1Transform.zw + LayerScroll.xy * t
        uv2    = uv * Layer2Transform.xy + Layer2Transform.zw + LayerScroll.zw * t
        rim    = (1 - |dot(view, normal)|)^2 + 1
        colour = Mask * Layer2 * Layer1 * (Image * rim) * Color
        alpha  = Layer2.a * Layer1.a * Image.a
        out    = colour * alpha, with the target's own alpha written as ZERO

    That last line matters: writing alpha zero is ADDITIVE blending, not the
    alpha blend this had before. A banner adds light to whatever is behind it
    rather than covering it.

    `LayerScroll` is (10, 0, 1, -0.3) here: the first layer races sideways while
    the second drifts slowly the other way, which is what makes a hologram look
    alive. Both are driven by frame time.

    The maps are the hull's own -- hologram noise, pulse and interlace.
    """

    material = bpy.data.materials.new(unique_name(f"banner_{slot or set_index}", ""))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (760, 0)

    constants = {str(c.get("name")): list(c.get("value") or [])
                 for c in ((effect or {}).get("constParameters") or [])}
    layer1_transform = constants.get("Layer1Transform", [1.0, 1.0, 0.0, 0.0])
    layer2_transform = constants.get("Layer2Transform", [1.0, 1.0, 0.0, 0.0])
    scroll = constants.get("LayerScroll", [0.0, 0.0, 0.0, 0.0])
    tint = constants.get("Color", [1.0, 1.0, 1.0, 1.0])

    # The SAME clock the heat shimmer uses. Writing a second one here got the
    # expression right and the VARIABLE wrong -- `frame / fps` needs `fps`
    # declared as a driver variable, and without it the expression fails
    # silently and every layer sits still at time zero.
    time_socket = nodes.time_value(tree, (-1500, 300))

    def scrolled(transform, rate, row):
        """One layer's UV: scaled, offset, and carried along by time."""

        coordinate = tree.nodes.new("ShaderNodeUVMap")
        coordinate.location = (-1500, row)
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (-1300, row)
        mapping.inputs["Scale"].default_value = (float(transform[0]), float(transform[1]), 1.0)
        mapping.inputs["Location"].default_value = (float(transform[2]), float(transform[3]), 0.0)
        tree.links.new(coordinate.outputs["UV"], mapping.inputs["Vector"])

        offset = tree.nodes.new("ShaderNodeVectorMath")
        offset.operation = "SCALE"
        offset.location = (-1120, row - 140)
        offset.inputs[0].default_value = (float(rate[0]), float(rate[1]), 0.0)
        tree.links.new(time_socket, offset.inputs["Scale"])

        moved = tree.nodes.new("ShaderNodeVectorMath")
        moved.operation = "ADD"
        moved.location = (-940, row)
        tree.links.new(mapping.outputs["Vector"], moved.inputs[0])
        tree.links.new(offset.outputs[0], moved.inputs[1])
        return moved.outputs[0]

    def sample(name, vector, row):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.location = (-720, row)
        node.label = name
        node.extension = "REPEAT"
        image = _effect_image(name, effect, resources)
        if image is not None:
            node.image = image
        if vector is not None:
            tree.links.new(vector, node.inputs["Vector"])
        return node

    layer1 = sample("Layer1Map", scrolled(layer1_transform, scroll[:2], 520), 520)
    layer2 = sample("Layer2Map", scrolled(layer2_transform, scroll[2:4], 200), 200)
    mask = sample("MaskMap", None, -140, )

    # Somebody's own picture first, then the one fetched for this ship's
    # owner, then the labelled placeholder. An override that cannot be opened
    # says so and falls through rather than leaving the banner blank.
    logo = own_banner_image(slot, overrides)
    if logo is None and owners and cache_directory:
        try:
            logo = logos.banner_logo(slot, owners, cache_directory)
        except logos.LogoError as error:
            print(f"  ! {error}")
    if logo is not None:
        material["carbon_logo"] = logo.name
    logo = logo or placeholders.banner_placeholder(slot)

    image_node = tree.nodes.new("ShaderNodeTexImage")
    image_node.location = (-720, -440)
    image_node.label = "ImageMap"
    image_node.extension = "CLIP"
    if logo is not None:
        image_node.image = logo

    # rim = (1 - |dot(view, normal)|)^2 + 1, brighter edge on as the shader has it.
    weight = tree.nodes.new("ShaderNodeLayerWeight")
    weight.location = (-720, 700)
    squared = tree.nodes.new("ShaderNodeMath")
    squared.operation = "MULTIPLY"
    squared.location = (-540, 700)
    tree.links.new(weight.outputs["Facing"], squared.inputs[0])
    tree.links.new(weight.outputs["Facing"], squared.inputs[1])
    rim = tree.nodes.new("ShaderNodeMath")
    rim.operation = "ADD"
    rim.location = (-380, 700)
    rim.label = "rim"
    rim.inputs[1].default_value = 1.0
    tree.links.new(squared.outputs[0], rim.inputs[0])

    lit = tree.nodes.new("ShaderNodeVectorMath")
    lit.operation = "SCALE"
    lit.location = (-460, -440)
    lit.label = "logo x rim"
    tree.links.new(image_node.outputs["Color"], lit.inputs[0])
    tree.links.new(rim.outputs[0], lit.inputs["Scale"])

    def multiply(a, b, row, label):
        node = tree.nodes.new("ShaderNodeVectorMath")
        node.operation = "MULTIPLY"
        node.location = (-260, row)
        node.label = label
        tree.links.new(a, node.inputs[0])
        tree.links.new(b, node.inputs[1])
        return node.outputs[0]

    colour = multiply(layer1.outputs["Color"], lit.outputs[0], 340, "layer1 x logo")
    colour = multiply(layer2.outputs["Color"], colour, 160, "x layer2")
    colour = multiply(mask.outputs["Color"], colour, -20, "x mask")

    tinted = tree.nodes.new("ShaderNodeVectorMath")
    tinted.operation = "MULTIPLY"
    tinted.location = (-60, -20)
    tinted.label = "x Color"
    tinted.inputs[1].default_value = tuple(float(v) for v in tint[:3])
    tree.links.new(colour, tinted.inputs[0])

    # alpha = Layer1.a * Layer2.a * Image.a, and the colour is premultiplied by
    # it exactly as the shader does before writing.
    alpha = tree.nodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.location = (-60, -280)
    tree.links.new(layer1.outputs["Alpha"], alpha.inputs[0])
    tree.links.new(layer2.outputs["Alpha"], alpha.inputs[1])
    with_image = tree.nodes.new("ShaderNodeMath")
    with_image.operation = "MULTIPLY"
    with_image.location = (100, -280)
    with_image.label = "alpha"
    tree.links.new(alpha.outputs[0], with_image.inputs[0])
    tree.links.new(image_node.outputs["Alpha"], with_image.inputs[1])

    premultiplied = tree.nodes.new("ShaderNodeVectorMath")
    premultiplied.operation = "SCALE"
    premultiplied.location = (260, -20)
    premultiplied.label = "colour x alpha"
    tree.links.new(tinted.outputs[0], premultiplied.inputs[0])
    tree.links.new(with_image.outputs[0], premultiplied.inputs["Scale"])

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (440, -20)
    tree.links.new(premultiplied.outputs[0], emission.inputs["Color"])

    # The shader's last multiply is by cb3[12].y, a per-object value. That slot
    # is activationStrength for the quad family, and a banner dimming with the
    # ship it belongs to is the behaviour that makes sense -- so it is driven
    # from the same per-ship value everything else reads. At its default of one
    # it changes nothing, which is why this is structure rather than a knob.
    if ship_object is not None:
        strength = emission.inputs["Strength"]
        index = list(emission.inputs).index(strength)
        path = f'nodes["{emission.name}"].inputs[{index}].default_value'
        tree.driver_remove(path)
        driver = tree.driver_add(path).driver
        driver.type = "SCRIPTED"
        variable = driver.variables.new()
        variable.name = "v"
        variable.targets[0].id_type = "OBJECT"
        variable.targets[0].id = ship_object
        # Named through SHIP_PROPERTIES, not spelled out. This was one of the
        # only driver targets written as a literal, so renaming a ship property
        # would have broken the banners alone, silently, at driver-evaluation
        # time rather than with an exception.
        variable.targets[0].data_path = '["%s"]' % nodes.SHIP_PROPERTIES["activationStrength"][0]
        driver.expression = "v"

    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (440, 160)
    add = tree.nodes.new("ShaderNodeAddShader")
    add.location = (600, 60)
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], output.inputs["Surface"])

    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    material.use_backface_culling = False
    return material


def _effect_image(name, effect, resources):
    """One of an effect's own maps, out of the bundle.

    Shared by banners and plane sets: both name their maps the same way, and
    both read them out of the same resource map.
    """

    path = next((r.get("resourcePath") for r in ((effect or {}).get("resources") or [])
                 if r.get("name") == name), None)
    local = (resources or {}).get(str(path or ""))
    if not local or not os.path.exists(str(local)):
        return None
    image = quad_materials.rename(
        bpy.data.images.load(str(local), check_existing=True),
        resfile.display_name(str(path or "")), str(path or ""))
    image.colorspace_settings.name = "Non-Color"
    return image

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
               owners=None, cache_directory="", banner_images=None,
               faction_record=None, booster_record=None, sprite_size=None,
               haze_density=None, haze_falloff=None):
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

    reset_set_collections()
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
    collection = ship_collection(ship_label(document))
    for obj in bpy.data.objects:
        if obj not in existing:
            move_to_collection(obj, collection)

    # The faction's colours, as one source every child reads. Built before the
    # attachments so they can bind to it as they are made.
    faction_slots = sof_faction_nodes.colour_slots(faction_record)
    faction_tree = None
    if faction_slots:
        # The DNA names the faction in its second field, which is the name a
        # person recognises and the one they typed.
        parts = str(document.get("dna") or "").split(":")
        faction_name = parts[1] if len(parts) > 1 and parts[1] else "faction"
        faction_tree = sof_faction_nodes.faction_group(faction_name,
                                                       faction_slots)

    decal_objects = build_decals(document, primary, resources, family, decal_sets,
                                 collection, faction_slots, faction_tree)
    plane_objects = build_plane_sets(document, primary, collection,
                                     (hull_record or {}).get("planeSets"),
                                     resources)
    haze_objects = build_haze_sets(document, primary, collection,
                                   (hull_record or {}).get("hazeSets"),
                                   faction_slots, faction_tree,
                                   haze_density, haze_falloff)
    sprite_objects = build_sprite_sets(document, primary, collection,
                                       (hull_record or {}).get("spriteSets"),
                                       faction_slots, faction_tree, sprite_size)
    banner_objects = build_banner_sets(document, primary, collection,
                                       (hull_record or {}).get("bannerSets"),
                                       owners, cache_directory, resources,
                                       banner_images)
    locator_objects = build_locators(document, primary, collection)
    # Boosters are NOT built. `build_boosters` works and is kept, but what it
    # makes is the wrong thing -- see the note above it. Turning it back on is
    # one line, and should not happen until the flame is sliced.
    booster_objects = ()

    # Every object of the ship reads the same per-ship values, and a decal is
    # its own object, so the values are written to all of them and the material
    # sockets are driven from the hull.
    ship = ([primary] + list(decal_objects) + list(plane_objects)
            + list(sprite_objects) + list(haze_objects)
            + list(banner_objects) + list(booster_objects))
    apply_ship_globals(ship, globals_overrides)
    drive_ship_sockets(ship, primary)

    # Recover each material's AREA TYPE from the hull record before the SOF
    # panels are filled. The built document dropped it -- a `Tr2MeshArea` keeps
    # name, index and count and not the type that chose its materials -- and
    # without it every material looks alike, so an edit meant for the hull
    # reaches the sails too.
    from .core import sof_areas
    stamped = sof_areas.stamp_ship(ship, hull_record)
    if stamped["materials"]:
        types = ", ".join(f"{name} x{count}"
                          for name, count in sorted(stamped["types"].items()))
        print(f"  areas: {stamped['matched']}/{stamped['materials']} identified"
              f"{' (' + types + ')' if types else ''}")
        if stamped["unmatched"]:
            # Said out loud: an unidentified area is one no material edit will
            # reach, which is better than one every edit reaches by mistake.
            print(f"  areas: {len(stamped['unmatched'])} not identified, "
                  f"left as built")

    populate_sof(primary, document, family)
    root = parent_to_root(ship, collection, document)
    prune_empty_collections()
    align_names(collection)
    return primary


def ship_anchor(objects):
    """The object the whole ship hangs off: the hull.

    Grabbing a ship in the viewport means clicking its hull, and a leaf cannot
    take its siblings with it. So the hull IS the parent of everything else,
    and the empty above it is a handle rather than the only way to move it.

    The biggest mesh, rather than the first: import order belongs to the GR2
    loader, and a banner plane would otherwise be able to win.

    This holds even when the hull is SKINNED, which inverts Blender's usual
    mesh-under-armature. That costs nothing, measured rather than assumed:
    reparenting a Legion moved its evaluated geometry by 0.0000, and moving the
    hull by 100 then moved the banners, the rig and the deformed geometry by
    exactly 100 each -- no double transform, and no dependency cycle. The
    deform is relative, so as long as mesh and rig move together it does not
    care which of them is the parent.
    """

    meshes = [obj for obj in objects
              if obj.type == "MESH" and obj.data is not None]
    if not meshes:
        return None
    return max(meshes, key=lambda obj: len(obj.data.vertices))


def parent_to_root(objects, collection, document):
    """Gives the ship one object to move, and hangs everything under it.

    A built ship has thirteen unparented objects -- the hull, its armature,
    every banner, every plane -- so dragging the hull moved the hull and left
    the rest behind.

    Parented with an identity inverse because the root sits at the origin,
    which is where the ship was built. Anything that already has a parent keeps
    it: a banner's light belongs to its banner, not to the root.
    """

    dna = str(document.get("dna") or "ship")
    root = bpy.data.objects.new(f"{dna.split(':')[0]}   {dna}", None)
    root.empty_display_type = "ARROWS"
    root.empty_display_size = 20.0
    collection.objects.link(root)
    stamp_identity(root, dna, "ship")

    # Everything in the ship's COLLECTION, not just the objects the builder
    # returned: the armatures and any secondary mesh are in neither that list
    # nor anyone's parent chain, and they were the ones left behind.
    wanted = list(objects)
    pending = [collection]
    while pending:
        found = pending.pop()
        wanted.extend(found.objects)
        pending.extend(found.children)

    seen, unique = set(), []
    for obj in wanted:
        if obj is root or obj.name in seen:
            continue
        seen.add(obj.name)
        unique.append(obj)

    # Everything hangs off the hull, and the hull off the root, so moving
    # EITHER moves the ship. Parenting to the root alone left the banners and
    # the skeleton behind whenever a person grabbed the hull itself, which is
    # the thing they can actually click.
    anchor = ship_anchor(unique)
    if anchor is not None and anchor.parent is not None:
        # The hull arrives under its own armature. Lifted out, world transform
        # preserved, so the rig can hang off it a moment later.
        keep = anchor.matrix_world.copy()
        anchor.parent = None
        anchor.matrix_world = keep
    adopted = 0
    for obj in unique:
        if obj.parent is not None or obj is anchor:
            continue
        obj.parent = anchor if anchor is not None else root
        # The general form rather than identity: an anchor with a transform of
        # its own would otherwise apply it to every child a second time.
        obj.matrix_parent_inverse = (anchor.matrix_world.inverted()
                                     if anchor is not None
                                     else mathutils.Matrix.Identity(4))
        adopted += 1
    if anchor is not None:
        anchor.parent = root
        anchor.matrix_parent_inverse.identity()

    # The SOF settings live here too, so selecting the root and editing the SOF
    # works the same as selecting any part of the ship.
    settings = getattr(root, "carbon_sof", None)
    if settings is not None and dna:
        settings.read_dna(dna)
    print(f"  root: {adopted} object(s) under "
          f"{anchor.name if anchor else root.name}, and that under {root.name}")
    return root


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


# --- Locators, and the boosters that sit on them --------------------------

#: How big a locator empty is drawn, as a fraction of the hull's radius.
#:
#: A locator has no size of its own -- it is a place, not a thing -- so this is
#: only about being able to see one and click it. Proportional rather than
#: fixed, because hulls run from frigates to titans.
LOCATOR_SIZE = 0.012

#: The kinds drawn by default. The rest are built but hidden: a cruiser has 271
#: locators and 122 of them are `vds_ambient`, which would bury the outliner in
#: points nobody asked for.
LOCATOR_SHOWN = (
    *(kind for kind, _slot, _label in weapons.WEAPON_KINDS),
    "booster", "cargobay", "camera",
)


def locator_matrix(locator, hull):
    """One locator's world transform, whichever form it arrived in.

    A named locator carries a 4x4 laid out ROW-major with the translation in
    the last row, which is the transpose of what Blender wants. A set entry
    carries a position, a quaternion the SOF calls `direction`, and a scale.
    """

    if locator.transform is not None:
        rows = locator.transform
        local = mathutils.Matrix((rows[0:4], rows[4:8], rows[8:12],
                                  rows[12:16])).transposed()
    else:
        x, y, z, w = locator.rotation
        local = mathutils.Matrix.LocRotScale(
            mathutils.Vector(locator.position),
            mathutils.Quaternion((w, x, y, z)),
            mathutils.Vector(locator.scaling))
    return (hull.matrix_world @ local) if hull is not None else local


def build_locators(document, hull, collection, found=None):
    """An empty per locator, grouped by kind.

    These are the places everything else bolts onto -- turrets, boosters,
    docking lights, the camera's look-at -- so they are built once, generically,
    rather than each consumer reading the document its own way. An artist can
    see them, snap to them, and parent their own work to them.

    Never rendered: a locator is a place, not a thing.
    """

    from .core import locators as locator_module

    found = locator_module.locators(document) if found is None else found
    if not found:
        return []

    armature = ship_armature(hull, collection)
    unit = abs(hull.matrix_world.to_scale().x) if hull is not None else 1.0
    radius = float(document.get("boundingSphereRadius") or 0.0) or 100.0
    size = max(radius * unit * LOCATOR_SIZE, 1e-4)

    built = []
    for locator in found:
        obj = remember_name(
            bpy.data.objects.new(
                unique_name(locator.name, collection.name), None),
            locator.name, collection.name)
        obj.empty_display_type = ("ARROWS" if locator.kind in LOCATOR_SHOWN
                                  else "PLAIN_AXES")
        obj.empty_display_size = size
        obj.hide_render = True

        group = attachment_collection(collection, "locators", locator.kind)
        group.objects.link(obj)
        obj.matrix_world = locator_matrix(locator, hull)
        attach_to_bone(obj, armature, locator.bone_index)

        # A named locator has no bone INDEX, so it is resolved by NAME -- the
        # engine's own rule, and what makes a hardpoint ride a moving hull.
        if locator.bone_index < 0:
            attach_to_named_bone(obj, armature, locator.name)

        obj["carbon_locator_kind"] = locator.kind
        obj["carbon_locator_set"] = locator.set_name
        obj["carbon_locator_index"] = locator.index
        stamp_identity(obj, locator.name, "locator", locator.kind)
        if locator.kind not in LOCATOR_SHOWN:
            obj.hide_viewport = True
        built.append(obj)

    counted = locator_module.kinds(found)
    summary = ", ".join(f"{kind} x{count}" for kind, count
                        in sorted(counted.items(), key=lambda row: -row[1]))
    print(f"  built {len(built)} locator(s): {summary}")
    return built


#: Boosters are built by the code below, and the ship does NOT call it.
#:
#: What it makes is a lofted tube: the exhaust outline carried aft and tapered.
#: That is the right SHAPE and the wrong THING. Carbon draws a booster as a
#: stack of parallel slices through the flame's volume -- `| | | | | |` seen
#: edge-on -- each one sampling the shape mask, the gradient and the noise, so
#: the flame reads as glowing gas rather than as a surface. A solid skin cannot
#: look like that however it is coloured, which is why this one does not.
#:
#: Everything else here stands and is worth keeping: the atlas gives each ship
#: its own exhaust outline, the flame colour is `shape0.color` at HDR and not
#: the nozzle's tiny `glowColor`, the gradients run bright-to-nothing along the
#: length, and none of those textures are in the built document -- they come
#: from the race record. Rebuild the geometry as slices; keep the rest.
#:
#: (Operator, on seeing the lofted version: "imagine multiple slices of
#: geometry | | | | | |". Removed rather than left drawing something wrong.)

#: The flame mesh, one per atlas cell, shared by every booster using it.
BOOSTER_MESH = "CarbonBooster flame"

#: How many stations along the flame, and how many points around it.
#:
#: The cross-section comes from a 32x32 mask, so 32 points around it is one
#: per texel and more would be inventing detail the mask does not have.
BOOSTER_RINGS = 14
BOOSTER_SIDES = 32

#: How far in the flame tapers by its far end, as a fraction of the nozzle.
#:
#: Not to nothing: the gradient's alpha is still 0.3 at the end, so the flame
#: is fading rather than closing, and a mesh that pinches to a point puts a
#: hard vertex where the texture wants a soft tail.
BOOSTER_TAIL = 0.12

#: How the taper runs. Below one it holds its width and then narrows late,
#: which is what a jet does; at one it is a straight cone.
BOOSTER_TAPER = 0.55

#: How bright the flame is, per unit of the authored shape colour.
#:
#: The shape colours are authored ABOVE one -- 8.7, 15, 11.9 on a Gallente
#: cruiser -- because they are HDR emission, so this stays at one and the
#: authored value does the work.
BOOSTER_GLOW = 1.0

#: The node the per-ship `boosterGain` drives, so the throttle is live.
BOOSTER_GAIN_NODE = "carbon booster gain"


def booster_profile(image, cell: int, count: int, sides: int = BOOSTER_SIDES):
    """The outline of one exhaust, as a radius per angle.

    The shape atlas is the per-ship part of a booster: each cell is a 32x32
    picture of the exhaust OPENING -- a petal, an oval, a rectangle -- and the
    flame is that outline carried aft. Reading it as geometry rather than as a
    texture means the silhouette is real, so it can be seen from any angle,
    selected, and exported.

    Returns `sides` radii in 0..1, or None when the cell cannot be read.
    """

    width, height = image.size
    if not width or not height or count <= 0:
        return None
    band = height // count
    if band <= 0:
        return None

    pixels = tuple(image.pixels)
    if len(pixels) < width * height * 4:
        return None

    # Blender's first row is the BOTTOM of the image; the atlas counts cells
    # from the top, which is the order the SOF's indices use.
    top = height - 1 - cell * band
    centre = (width - 1) / 2.0, (band - 1) / 2.0
    reach = min(centre) or 1.0

    def lit(x: int, y: int) -> bool:
        row = top - y
        if row < 0 or row >= height or x < 0 or x >= width:
            return False
        return pixels[(row * width + x) * 4] > 0.5

    # Every lit texel binned by ANGLE, keeping the furthest.
    #
    # Not a ray march outward: several cells are an OUTLINE with a dark middle
    # rather than a filled shape, and a ray stepping along one angle slips
    # between the outline's texels and reports a radius of zero. Binning every
    # texel cannot miss one.
    radii = [0.0] * sides
    for y in range(band):
        for x in range(width):
            if not lit(x, y):
                continue
            dx, dy = x - centre[0], y - centre[1]
            distance = math.hypot(dx, dy)
            if distance <= 0.0:
                continue
            step = int((math.atan2(dy, dx) % (2.0 * math.pi))
                       / (2.0 * math.pi) * sides) % sides
            radii[step] = max(radii[step], distance)

    # A bin with nothing in it takes the mean of its neighbours, so a thin
    # outline does not leave a notch in the silhouette.
    for step in range(sides):
        if radii[step] > 0.0:
            continue
        before = next((radii[(step - k) % sides] for k in range(1, sides)
                       if radii[(step - k) % sides] > 0.0), 0.0)
        after = next((radii[(step + k) % sides] for k in range(1, sides)
                      if radii[(step + k) % sides] > 0.0), 0.0)
        radii[step] = (before + after) / 2.0 if before or after else 0.0
    radii = [value / reach for value in radii]

    if not any(radii):
        return None
    # Normalised so the widest point of any exhaust is the radius the item's
    # own transform asks for.
    widest = max(radii)
    return [value / widest for value in radii]


def booster_mesh(profile=None, key: str = ""):
    """A flame: the exhaust outline carried aft and tapering in.

    Measured rather than assumed: across five hulls of four races every booster
    transform is pure scale and translation -- the mean cosine between the
    locator's Z and the ship's is exactly 1.000 -- and every booster sits at
    the stern. So the flame runs aft along -Z, and the transform's Z scale is
    its LENGTH while X and Y are its radius.

    Without a profile it falls back to a circle, which is what a booster looked
    like before the mask was read: right in size and placement, wrong in shape.
    """

    name = f"{BOOSTER_MESH} {key}" if key else BOOSTER_MESH
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        return mesh

    sides = len(profile) if profile else BOOSTER_SIDES
    if not profile:
        profile = [1.0] * sides

    vertices, faces = [], []
    for ring in range(BOOSTER_RINGS):
        along = ring / (BOOSTER_RINGS - 1)
        taper = 1.0 - (1.0 - BOOSTER_TAIL) * (along ** (1.0 / BOOSTER_TAPER))
        for step in range(sides):
            angle = 2.0 * math.pi * step / sides
            radius = profile[step] * taper
            vertices.append((radius * math.cos(angle),
                             radius * math.sin(angle), -along))

    for ring in range(BOOSTER_RINGS - 1):
        base, following = ring * sides, (ring + 1) * sides
        for step in range(sides):
            nxt = (step + 1) % sides
            faces.append((base + step, base + nxt,
                          following + nxt, following + step))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.materials.append(None)
    return mesh


def booster_material(booster, record, resources, layer: int = 0):
    """The flame's colour: a gradient along its length, in HDR.

    Two things the SOF separates and a first attempt conflates. `glowColor` is
    the little sprite at the NOZZLE and is authored near 0.02; the FLAME's
    colour is `shape0.color`, authored above one -- 8.7, 15, 11.9 on a Gallente
    cruiser -- because it is emission and meant to blow out. Using the glow
    colour for the flame gives a dim teal cone, which is what it did.

    The gradient carries the rest: bright at the nozzle, gone within a quarter
    of the length, with an alpha that keeps tailing off to 0.3 at the end.
    """

    material = bpy.data.materials.new(unique_name("SofBooster", ""))
    material.use_nodes = True
    material.blend_method = "BLEND"
    material.show_transparent_back = False
    tree = material.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (700, 0)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (380, 80)

    shape = (record or {}).get(f"shape{layer}") or {}
    colour = tuple(float(v) for v in
                   (shape.get("color")
                    or booster.get("glowColor") or (1.0, 1.0, 1.0, 1.0)))[:3]

    # How far along the flame, 0 at the nozzle and 1 at the tail. The mesh runs
    # from z=0 to z=-1, so the object coordinate IS the distance.
    where = nodes.new("ShaderNodeTexCoord")
    where.location = (-900, -100)
    parts = nodes.new("ShaderNodeSeparateXYZ")
    parts.location = (-720, -100)
    links.new(where.outputs["Object"], parts.inputs[0])

    along = nodes.new("ShaderNodeMath")
    along.operation = "MULTIPLY"
    along.location = (-560, -100)
    along.label = "0 at the nozzle, 1 at the tail"
    along.inputs[1].default_value = -1.0
    links.new(parts.outputs["Z"], along.inputs[0])

    gradient = _booster_gradient(record, resources, layer)
    tint = nodes.new("ShaderNodeVectorMath")
    tint.operation = "MULTIPLY"
    tint.location = (200, 80)
    tint.label = "shape colour"
    tint.inputs[1].default_value = colour

    if gradient is not None:
        place = nodes.new("ShaderNodeCombineXYZ")
        place.location = (-380, -100)
        links.new(along.outputs[0], place.inputs["X"])
        place.inputs["Y"].default_value = 0.5

        sampled = nodes.new("ShaderNodeTexImage")
        sampled.location = (-200, -60)
        sampled.image = gradient
        sampled.extension = "EXTEND"
        sampled.interpolation = "Linear"
        sampled.label = "gradient along the flame"
        links.new(place.outputs[0], sampled.inputs["Vector"])
        links.new(sampled.outputs["Color"], tint.inputs[0])
        fade = sampled.outputs["Alpha"]
    else:
        # No gradient to read: a smooth fall-off, so the flame still ends.
        tint.inputs[0].default_value = (1.0, 1.0, 1.0)
        curve = nodes.new("ShaderNodeMath")
        curve.operation = "SUBTRACT"
        curve.location = (-200, -200)
        curve.use_clamp = True
        curve.inputs[0].default_value = 1.0
        links.new(along.outputs[0], curve.inputs[1])
        fade = curve.outputs[0]

    links.new(tint.outputs[0], emission.inputs["Color"])

    gain = nodes.new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    gain.location = (200, -160)
    gain.name = gain.label = BOOSTER_GAIN_NODE
    gain.inputs[1].default_value = BOOSTER_GLOW
    links.new(fade, gain.inputs[0])
    emission.inputs["Strength"].default_value = 1.0

    # The gain drives the ALPHA, so throttling one down fades it out rather
    # than leaving a shape stuck to the hull.
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (380, -160)
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (540, 0)
    links.new(gain.outputs[0], mix.inputs["Fac"])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs["Surface"])

    material["carbon_booster_shape_color"] = colour
    return material


def _booster_image(path, resources):
    """One booster texture as a Blender image, or None.

    These are not in the built document -- it names the shader and leaves every
    texture path null -- so they are fetched from the RACE record alongside the
    ship and arrive in the same map as everything else.
    """

    if not path or not resources:
        return None
    found = resources.get(path) or resources.get(str(path).lower())
    if not found:
        return None
    existing = bpy.data.images.get(Path(found).name)
    if existing is not None:
        return existing
    try:
        image = bpy.data.images.load(str(found))
    except RuntimeError:
        return None
    image["carbon_res_path"] = path
    return image


def _booster_gradient(record, resources, layer: int):
    return _booster_image((record or {}).get(f"gradient{layer}ResPath"),
                          resources)


def build_boosters(document, hull, collection, record=None, resources=None):
    """The engine flames, one per booster item.

    Three sources, because the SOF splits them: the built document has the
    ITEMS -- where each engine is, how big, which atlas cell -- the race record
    has what a flame LOOKS like, and the atlas itself has the shape of each
    exhaust. The document names the shader and leaves all four of its texture
    paths null, so without the race record there is nothing to draw.
    """

    booster = document.get("boosters") or {}
    items = booster.get("items") or []
    if not items:
        return []

    record = record or {}
    armature = ship_armature(hull, collection)
    group = attachment_collection(collection, "boosters")
    unit = abs(hull.matrix_world.to_scale().x) if hull is not None else 1.0

    atlas = _booster_image(record.get("shapeAtlasResPath"), resources)
    count = int(record.get("shapeAtlasCount") or 0)
    shapes = {}

    built, lights = [], []
    for index, item in enumerate(items):
        cell = int(item.get("atlasIndex0") or 0)
        if cell not in shapes:
            profile = (booster_profile(atlas, cell, count)
                       if atlas is not None and count else None)
            shapes[cell] = booster_mesh(profile, f"{cell}" if atlas else "")
        mesh = shapes[cell]

        obj = remember_name(bpy.data.objects.new(
            unique_name(f"booster_{index}", collection.name), mesh),
            f"booster_{index}", collection.name)
        group.objects.link(obj)

        rows = tuple(float(v) for v in (item.get("transform") or ()))
        if len(rows) == 16:
            local = mathutils.Matrix((rows[0:4], rows[4:8], rows[8:12],
                                      rows[12:16])).transposed()
            obj.matrix_world = ((hull.matrix_world @ local)
                                if hull is not None else local)

        if obj.material_slots:
            obj.material_slots[0].link = "OBJECT"
            obj.material_slots[0].material = booster_material(
                booster, record, resources, 0)

        # Kept as authored: the second atlas cell and the trail belong to
        # layers this does not draw, and an export needs them.
        obj["carbon_booster_atlas"] = (int(item.get("atlasIndex0") or 0),
                                       int(item.get("atlasIndex1") or 0))
        obj["carbon_booster_has_trail"] = bool(item.get("hasTrail"))
        obj["carbon_booster_functionality"] = tuple(
            float(v) for v in (item.get("functionality") or (0, 0, 0, 0)))
        obj["carbon_booster_light_scale"] = float(item.get("lightScale") or 1.0)
        stamp_identity(obj, f"booster_{index}", "booster")
        built.append(obj)

        # One light per booster, from the SET's light: every booster on a hull
        # shares its colour and radius, and only the scale is per item.
        colour = tuple(float(v) for v in
                       (booster.get("lightColor") or (0, 0, 0, 0)))
        reach = float(booster.get("lightRadius") or 0.0)
        if any(colour[:3]) and reach > 0.0:
            lamp = bpy.data.lights.new(
                unique_name(f"booster_{index}_light", collection.name), "POINT")
            lamp.color = colour[:3]
            lamp.shadow_soft_size = (reach * float(item.get("lightScale") or 1.0)
                                     * unit)
            lamp.energy = reach * unit
            light = remember_name(bpy.data.objects.new(lamp.name, lamp),
                                  f"booster_{index}_light", collection.name)
            light.matrix_world = obj.matrix_world
            group.objects.link(light)
            attach_to_bone(light, armature, -1)
            light["carbon_booster_light_scale"] = float(
                item.get("lightScale") or 1.0)
            lights.append(light)

    trails = sum(1 for item in items if item.get("hasTrail"))
    shape = (f"{len(shapes)} exhaust shape(s) from the atlas"
             if atlas is not None else "NO shape atlas: round fallback")
    print(f"  built {len(built)} booster(s) and {len(lights)} booster light(s);"
          f" {shape}"
          + ("; always on" if booster.get("alwaysOn") else "")
          + (f"; {trails} trail(s) authored, not drawn" if trails else ""))
    return built + lights
