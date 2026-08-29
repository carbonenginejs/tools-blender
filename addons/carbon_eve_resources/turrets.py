"""Fitting turrets to a hull's turret locators.

A hull carries named hardpoints -- `locator_turret_1a` and its siblings, twelve
on a Celestis -- and a turret is a separate model that mounts on one. Both
halves already exist here: the locators are built with the ship, and a turret's
own `.black` names its geometry and its effect, which is a `quadv5` from the
same family the hull's own areas use.

So this is mostly plumbing, and deliberately so. Nothing about a weapon is
derived: the service's library names each one's `resPath`, and the `slot` says
what it mounts to. Another consumer tried working the slot out from the path
and got the extra-large turrets wrong -- 147 against the library's 72 -- which
is why neither is recomputed here.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import service_access
from .core import resindex, sof_fetch, weapons


#: Held because Blender does NOT keep a reference to the strings a dynamic
#: `items` callback returns -- letting them be collected shows up as mangled
#: labels, or a crash.
_ITEMS: list = []
_FAMILIES: list = []

#: What a fitted turret is marked with, so it can be found and cleared.
FITTED = "carbon_turret"


def _catalogue(context):
    return weapons.catalogue(service_access.client(context))


def family_items(self, context):
    global _FAMILIES

    found = weapons.families(_catalogue(context))
    # "ALL", not "": Blender warns that an empty identifier matches no enum
    # and falls back to index 0 on every redraw.
    _FAMILIES = [("ALL", "All", "Every turret in the library")]
    _FAMILIES += [(name, name.title(), f"{name} turrets") for name in found]
    return _FAMILIES


def turret_items(self, context):
    """Every turret the library lists, narrowed by family."""

    global _ITEMS

    rows = _catalogue(context)
    wanted = getattr(self, "family", "ALL")
    if wanted and wanted != "ALL":
        rows = [row for row in rows if row["family"] == wanted]
    if not rows:
        _ITEMS = [("", "No turrets loaded", "The service is unreachable")]
        return _ITEMS
    # Keyed by TYPE ID. 602 turrets share just 57 models, so keying by the
    # model path gives sixteen entries the same identifier -- and Blender then
    # answers every one of them with the first, so picking a weapon showed a
    # different weapon's name.
    _ITEMS = [(str(row["typeID"]), row["name"],
               f"{row['slot']} - {row['resPath'].rsplit('/', 1)[-1]}")
              for row in rows]
    return _ITEMS


class CARBON_TurretState(PropertyGroup):
    family: EnumProperty(
        name="Family",
        description="Narrow the list. Read off the model path, and used for "
                    "nothing else -- what a weapon IS comes from its slot",
        items=family_items,
    )
    turret: EnumProperty(
        name="Turret",
        description="Which turret to mount on every hardpoint",
        items=turret_items,
    )
    status: StringProperty(default="")


def _cache_root(context):
    from .addon import _cache_path, _prefs

    return _cache_path(_prefs(context))


def hardpoints(context):
    """The turret locators of the ACTIVE ship, or of the whole file.

    Whatever is selected decides which hull gets fitted; with nothing selected
    every hull in the file does, which is what a demo scene wants.
    """

    chosen = [obj for obj in context.selected_objects] or list(bpy.data.objects)
    roots = set()
    for obj in chosen:
        node = obj
        while node is not None:
            roots.add(node.name)
            node = node.parent

    found = [obj for obj in bpy.data.objects
             if obj.get("carbon_locator_kind") == "turret"]
    if not context.selected_objects:
        return found

    kept = []
    for locator in found:
        node = locator
        while node is not None:
            if node.name in roots:
                kept.append(locator)
                break
            node = node.parent
    return kept or found


def ship_faction(context) -> str:
    """The faction of the ship being fitted, from its own DNA.

    A slot defaults its faction to its parent's, and the parent's is the
    SECOND field of the hull's DNA -- `hull:faction:race`. Nothing else needs
    reading, and nothing is guessed: a hull whose DNA names no faction leaves
    its turrets in the colours they shipped with, which is what the engine
    does when either faction fails to resolve.
    """

    for locator in hardpoints(context):
        node = locator
        while node is not None:
            dna = str(getattr(getattr(node, "carbon_sof", None), "dna", "")
                      or node.get("carbon_sof_dna") or "")
            if dna:
                parts = dna.split(":")
                return parts[1] if len(parts) > 1 else ""
            node = node.parent
    return ""


def fetch_turret(client, res_path: str, cache_root, *, progress=None,
                 faction: str = ""):
    """The turret's document and its files. Runs on the JOB thread.

    No `bpy` and no scene changes in here: what comes back is handed to the
    main thread, which is the only place allowed to touch Blender data.
    """

    document = weapons.turret_document(client, res_path)
    if not document:
        raise RuntimeError(f"no turret document for {res_path}")

    geometry = str(document.get("geometryResPath") or "")
    if not geometry:
        raise RuntimeError(f"{res_path} names no geometry")

    build = str((client.request_json("GET", "/eve/latest/build")
                 or {}).get("build") or "")
    index = resindex.load(cache_root, build) if build else None

    wanted = [geometry]
    effect = document.get("turretEffect") or {}
    for resource in (effect.get("resources") or []):
        path = resource.get("resourcePath")
        if path and path not in wanted:
            wanted.append(path)

    resources = {}
    for path in wanted:
        if progress is not None:
            progress(f"Fetching {Path(path).name}")
        try:
            found = sof_fetch.fetch_resource(path, client, cache_root,
                                             build=build, index=index)
        except Exception as exc:
            print(f"[CarbonEngineJS SOF] turret resource {path}: {exc}")
            continue
        if found is not None:
            resources[path] = str(found)

    if geometry not in resources:
        raise RuntimeError(f"could not fetch {geometry}")

    # The turret takes the SHIP's faction, as the engine does: a slot defaults
    # its faction to the parent's, off the hull's own DNA.
    colours = {}
    if faction:
        from .core import sof_materials

        if progress is not None:
            progress(f"Reading {faction}")
        colours = faction_colours(sof_materials.faction(faction, client), client)
    return document, resources, colours


def clear_fitted(locators=None):
    """Removes the turrets this add-on fitted, and nothing else."""

    names = {obj.name for obj in locators} if locators is not None else None
    removed = 0
    for obj in list(bpy.data.objects):
        if not obj.get(FITTED):
            continue
        if names is not None and str(obj.get("carbon_turret_locator")) not in names:
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    return removed


def _turret_material(document, resources, name):
    """The turret's own material, through the quad family the hull uses."""

    from .quad import interface as quad_interface
    from .quad import materials as quad_materials

    effect = document.get("turretEffect") or {}
    if not effect.get("effectFilePath"):
        return None

    # A `.black` is not a SOF document, and its effect is not shaped like one.
    # `constParameters` arrives as a packed `black.structureList` -- a byte
    # blob with a count and a stride -- where the document carries a list of
    # named values, and `parameters` is null rather than empty. Iterating
    # either as the document's shape walks a dict's KEYS and fails on a string.
    #
    # So only what is genuinely the same is passed through. The turret's
    # constants stay unread; decoding that container is its own job.
    effect = {
        "_type": effect.get("_type"),
        "effectFilePath": effect.get("effectFilePath"),
        "resources": [entry for entry in (effect.get("resources") or [])
                      if isinstance(entry, dict)],
        "parameters": [],
        "constParameters": [],
        "options": [],
    }
    try:
        family = quad_interface.load_family()
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] turret family unavailable: {exc}")
        return None

    material, problem = quad_materials.build_area_material(
        {"name": name, "effect": effect}, family, resources, 0)
    if problem:
        print(f"  ! {problem}")
    return material


def fit(context, document, resources, res_path: str, name: str):
    """Places one turret on every hardpoint. MAIN thread only."""

    geometry = str(document.get("geometryResPath") or "")
    local = resources.get(geometry)
    if not local:
        raise RuntimeError(f"no local file for {geometry}")

    locators = hardpoints(context)
    if not locators:
        raise RuntimeError("this ship has no turret locators")

    before = set(bpy.data.objects)
    bpy.ops.import_scene.carbon_gr2(filepath=str(local))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        raise RuntimeError(f"{Path(local).name} imported nothing")

    material = _turret_material(document, resources, name)
    roots = [obj for obj in imported if obj.parent is None] or imported[:1]

    fitted = []
    for order, locator in enumerate(locators):
        # The FIRST hardpoint keeps the imported objects; the rest get copies,
        # so one import serves the whole ship.
        if order == 0:
            copies = imported
        else:
            copies = []
            mapping = {}
            for obj in imported:
                clone = obj.copy()
                if obj.data is not None:
                    clone.data = obj.data       # one mesh, many turrets
                mapping[obj] = clone
                copies.append(clone)
            for obj, clone in mapping.items():
                clone.parent = mapping.get(obj.parent, None)
            for collection in locator.users_collection:
                for clone in copies:
                    collection.objects.link(clone)

        tops = [obj for obj in copies if obj.parent is None] or copies[:1]
        for top in tops:
            top.parent = locator
            top.matrix_parent_inverse = locator.matrix_world.inverted()
            top.matrix_world = locator.matrix_world

        for obj in copies:
            obj[FITTED] = res_path
            obj["carbon_turret_locator"] = locator.name
            obj["carbon_turret_name"] = name
            if material is not None and obj.type == "MESH":
                obj.data.materials.clear()
                obj.data.materials.append(material)
        fitted.extend(copies)

    # The first import landed wherever the importer put it; put it with its
    # locator like every copy.
    for obj in imported:
        if obj.parent is None:
            obj.parent = locators[0]

    print(f"  fitted {name} to {len(locators)} hardpoint(s), "
          f"{len(fitted)} object(s)")
    return len(locators), material


#: Which area type a turret takes its materials from.
#:
#: Zero, which is primary. Carbon asks the faction for `GetAreaType(0)` on both
#: the hull and the turret and uses that one alone -- a turret has no areas of
#: its own to match up.
TURRET_AREA_TYPE = 0

#: What the glow is multiplied by after it is resolved.
#:
#: Half. The engine dims a turret's glow relative to the hull's deliberately,
#: so a hardpoint does not out-shine the ship it sits on.
GLOW_DIM = 0.5


def faction_colours(faction_record, client):
    """The four materials and the glow a faction gives a turret.

    This is `SetupTurretMaterial`'s rule, and its shape is worth stating
    because it is not obvious: a turret does not carry the ship's colours, it
    carries the ship's faction's MATERIAL NAMES, resolved to values.

    The names come from the parent faction's area type 0, through the reroute
    the faction publishes -- `materialUsageList` says which authored slot each
    of the shader's four material slots actually reads, and it is not the
    identity: Gallente base swaps the first two.

    Runs on the JOB thread: it fetches, and touches nothing in the scene.
    """

    from .core import sof_materials

    if not faction_record:
        return {}

    names = sof_materials.faction_material_names(faction_record)
    usage = faction_record.get("materialUsageList") or [0, 1, 2, 3]
    found = {"materials": {}, "glow": None}

    for slot in range(1, 5):
        try:
            source = int(usage[slot - 1]) + 1
        except (IndexError, TypeError, ValueError):
            source = slot
        name = sof_materials.material_name_for(names, TURRET_AREA_TYPE, source)
        if not name:
            continue
        values = sof_materials.material_values(
            sof_materials.material(name, client))
        if values:
            found["materials"][slot] = {"name": name, **values}

    # The glow is named by INDEX into the faction's colour types, and the
    # colour set answers by NAME -- so the enum is the bridge between them.
    table = ((faction_record.get("areaMaterials") or {}).get("glowColor")
             or {})
    index = table.get(f"{TURRET_AREA_TYPE}:GeneralGlowColor")
    colours = ((faction_record.get("colorSet") or {}).get("colors") or {})
    if index is not None:
        from . import sof_faction_nodes

        types = sof_faction_nodes.COLOUR_TYPES
        if 0 <= int(index) < len(types):
            value = colours.get(types[int(index)])
            if value:
                found["glow"] = tuple(float(v) for v in value[:3])
    return found


def apply_faction_colours(material, colours):
    """Puts a faction's materials and glow onto a fitted turret. MAIN thread.

    A turret whose faction does not resolve keeps the colours it shipped with,
    which is what the engine does too -- `SetupTurretMaterial` returns early
    unless BOTH the hull's faction and the turret's resolve.
    """

    if material is None or not colours:
        return 0

    group = next((node for node in material.node_tree.nodes
                  if node.type == "GROUP"), None)
    if group is None:
        return 0

    written = 0
    for slot, values in (colours.get("materials") or {}).items():
        for field, socket in (("diffuse", f"Mtl{slot}DiffuseColor"),
                              ("fresnel", f"Mtl{slot}FresnelColor"),
                              ("gloss", f"Mtl{slot}Gloss")):
            value = values.get(field)
            target = group.inputs.get(socket)
            if value is None or target is None:
                continue
            if field == "gloss":
                # Gloss is a vec4 in the SOF and x is the only part read. The
                # socket may be either -- a float on some members, a vector on
                # others -- so which one it is decides, not which one it
                # usually is.
                if hasattr(target.default_value, "__len__"):
                    current = list(target.default_value)
                    current[0] = float(value)
                    target.default_value = current
                else:
                    target.default_value = float(value)
            else:
                target.default_value = tuple(value[:3]) + (1.0,)
            written += 1
        material[f"carbon_turret_material{slot}"] = values.get("name", "")

    glow = colours.get("glow")
    target = group.inputs.get("GeneralGlowColor")
    if glow is not None and target is not None:
        target.default_value = tuple(channel * GLOW_DIM
                                     for channel in glow) + (1.0,)
        written += 1
    return written


class CARBON_OT_fit_turrets(Operator):
    """Mount the chosen turret on every hardpoint of the selected ship"""

    bl_idname = "carbon.fit_turrets"
    bl_label = "Fit Turrets"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import addon

        state = context.window_manager.carbon_eve_turrets
        if not state.turret:
            self.report({"ERROR"}, "Choose a turret first")
            return {"CANCELLED"}

        client = service_access.client(context)
        if client is None:
            self.report({"ERROR"}, "The CarbonEngineJS service is unreachable")
            return {"CANCELLED"}

        if not hardpoints(context):
            self.report({"ERROR"}, "No turret locators; load a ship first")
            return {"CANCELLED"}

        chosen = next((row for row in _catalogue(context)
                       if str(row["typeID"]) == state.turret), None)
        if chosen is None:
            self.report({"ERROR"}, "That turret is no longer listed")
            return {"CANCELLED"}
        res_path, name = chosen["resPath"], chosen["name"]
        cache_root = _cache_root(context)

        faction = ship_faction(context)

        def work():
            return (name, res_path) + fetch_turret(
                client, res_path, cache_root, progress=addon._set_progress,
                faction=faction)

        try:
            addon._launch_job(context, "turrets", work, f"Fetching {name}")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def finish_job(context, result) -> str:
    """Applies a fetched turret. MAIN thread only."""

    name, res_path, document, resources, colours = result
    state = context.window_manager.carbon_eve_turrets
    clear_fitted(hardpoints(context))
    count, material = fit(context, document, resources, res_path, name)
    written = apply_faction_colours(material, colours)
    state.status = (f"{name} on {count} hardpoint(s)"
                    + (f", {written} faction value(s)" if written else ""))
    return state.status


class CARBON_OT_clear_turrets(Operator):
    """Remove the turrets this add-on fitted"""

    bl_idname = "carbon.clear_turrets"
    bl_label = "Clear"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = clear_fitted()
        context.window_manager.carbon_eve_turrets.status = (
            f"removed {removed} object(s)" if removed else "nothing fitted")
        return {"FINISHED"}


class CARBON_PT_sidebar_turrets(Panel):
    """The turret fitter."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarbonEngineJS"
    bl_label = "Turrets"
    bl_idname = "CARBON_PT_sidebar_turrets"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        state = getattr(context.window_manager, "carbon_eve_turrets", None)
        if state is None:
            layout.label(text="Not registered")
            return

        points = len(hardpoints(context))
        layout.label(text=f"{points} hardpoint(s)"
                          + ("" if context.selected_objects else ", whole file"),
                     icon="EMPTY_ARROWS")
        layout.prop(state, "family")
        layout.prop(state, "turret")
        row = layout.row(align=True)
        row.operator(CARBON_OT_fit_turrets.bl_idname, icon="TOOL_SETTINGS")
        row.operator(CARBON_OT_clear_turrets.bl_idname, icon="X")
        if state.status:
            layout.label(text=state.status, icon="CHECKMARK")


CLASSES = (CARBON_TurretState, CARBON_OT_fit_turrets, CARBON_OT_clear_turrets,
           CARBON_PT_sidebar_turrets)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.carbon_eve_turrets = bpy.props.PointerProperty(
        type=CARBON_TurretState)


def unregister():
    del bpy.types.WindowManager.carbon_eve_turrets
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
