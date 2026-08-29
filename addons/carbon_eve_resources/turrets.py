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
import re

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import service_access
from . import ship as ship_module
from .core import resindex, sof_fetch, weapons


#: Held because Blender does NOT keep a reference to the strings a dynamic
#: `items` callback returns -- letting them be collected shows up as mangled
#: labels, or a crash.
_ITEMS: dict = {}

#: What a fitted turret is marked with, so it can be found and cleared.
FITTED = "carbon_turret"



#: What a locator's NAME says it takes, and which weapons fit it.
#:
#: The prefixes are `EveLocator2.Type` -- the engine reads a hardpoint's kind
#: off its own name and nothing else, so `locator_xl_3a` takes an extra-large
#: turret because of what it is CALLED. The slot on the right is the weapons
#: library's own field, which is why a hull only offers what it actually has.
KINDS = (
    ("turret", "turrets", "Turrets"),
    ("xl", "xlTurrets", "XL Turrets"),
    ("launcher", "launchers", "Missiles"),
    ("atomic", "atomics", "Atomics"),
    ("chain", "chains", "Chains"),
)

KIND_SLOT = {kind: slot for kind, slot, _label in KINDS}

#: `locator_turret_1a` -- the bay is the NUMBER and the letter is one muzzle.
#:
#: This is the engine's own grouping: it strips the trailing letter to get a
#: prefix and binds one turret set per prefix, so 1a and 1b are two barrels of
#: bay ONE rather than two hardpoints. Fitting per locator gives a Celestis
#: twelve independent guns where it has six.
BAY = re.compile(r"^locator_[a-z0-9]+_(\d+)[a-z]*$", re.IGNORECASE)


def bay_of(locator) -> str:
    """Which hardpoint a locator is a muzzle of, as its number."""

    found = BAY.match(str(locator.get("carbon_locator_name")
                          or locator.name.split("___")[0] or ""))
    return found.group(1) if found else ""


def _catalogue(context):
    return weapons.catalogue(service_access.client(context))


def _items_for(slot):
    """An items callback for one weapon slot, holding its own strings."""

    def items(self, context):
        rows = [row for row in weapons.catalogue(
            service_access.client(context), slots=(slot,))]
        if not rows:
            _ITEMS[slot] = [("", "None available", "")]
            return _ITEMS[slot]
        # Keyed by TYPE ID. 602 turrets share just 57 models, so keying by the
        # model path gives sixteen entries the same identifier -- and Blender
        # answers every one of them with the first, so picking a weapon showed
        # a different weapon's name.
        _ITEMS[slot] = [(str(row["typeID"]), row["name"],
                         f"{row['resPath'].rsplit('/', 1)[-1]}")
                        for row in rows]
        return _ITEMS[slot]

    return items


class CARBON_TurretState(PropertyGroup):
    # One chooser per kind rather than one with a filter: the panels are shown
    # side by side, so a single shared chooser would make picking a missile
    # change what the turret panel says it will fit.
    __annotations__ = {
        kind: EnumProperty(name=label, items=_items_for(slot),
                           description=f"Which weapon to mount on a "
                                       f"{label.lower()} hardpoint")
        for kind, slot, label in KINDS
    }
    __annotations__["status"] = StringProperty(default="")


def _cache_root(context):
    from .addon import _cache_path, _prefs

    return _cache_path(_prefs(context))


def weapon_locators(context, kind=""):
    """The weapon locators of the ACTIVE ship, or of the whole file.

    Whatever is selected decides which hull gets fitted; with nothing selected
    every hull in the file does, which is what a demo scene wants.
    """

    wanted = {kind} if kind else set(KIND_SLOT)
    found = [obj for obj in bpy.data.objects
             if obj.get("carbon_locator_kind") in wanted]
    if not context.selected_objects:
        return found

    roots = set()
    for obj in context.selected_objects:
        node = obj
        while node is not None:
            roots.add(node.name)
            node = node.parent

    kept = []
    for locator in found:
        node = locator
        while node is not None:
            if node.name in roots:
                kept.append(locator)
                break
            node = node.parent
    return kept or found


def hardpoints(context, kind=""):
    """`[(kind, bay, [locators])]` -- what a person actually fits.

    A hardpoint is a BAY, and a bay is one or more muzzles: `locator_turret_1a`
    and `_1b` are two barrels of bay one. The engine groups them the same way,
    by stripping the trailing letter to get a prefix and binding one turret set
    to each.
    """

    bays = {}
    for locator in weapon_locators(context, kind):
        key = (str(locator.get("carbon_locator_kind") or ""), bay_of(locator))
        bays.setdefault(key, []).append(locator)

    def order(item):
        (found_kind, bay), _ = item
        return (found_kind, int(bay) if bay.isdigit() else 0)

    return [(found_kind, bay, sorted(group, key=lambda o: o.name))
            for (found_kind, bay), group in sorted(bays.items(), key=order)]


def kinds_present(context):
    """The weapon kinds this hull has, in the order KINDS lists them."""

    found = {kind for kind, _bay, _group in hardpoints(context)}
    return [row for row in KINDS if row[0] in found]


def ship_of(locator):
    """The hull object a locator belongs to, or None.

    The one carrying the per-ship values -- age, activation, booster gain,
    kill count. Walking up from the locator finds it whichever collection the
    turret ends up in.
    """

    # The ROOT first, then the same anchor rule the ship build used -- the
    # biggest mesh under it. Every object of a ship carries the per-ship
    # properties, but only one is the DRIVER SOURCE, and picking a different
    # one silently decouples the turret: its dirt would then answer to an
    # object nothing else writes to.
    node = locator
    root = None
    while node is not None:
        root = node
        node = node.parent
    if root is None:
        return None

    def descendants(obj):
        yield obj
        for child in obj.children:
            yield from descendants(child)

    return ship_module.ship_anchor(list(descendants(root)))


def ship_faction(context) -> str:
    """The faction of the ship being fitted, from its own DNA.

    A slot defaults its faction to its parent's, and the parent's is the
    SECOND field of the hull's DNA -- `hull:faction:race`. Nothing else needs
    reading, and nothing is guessed: a hull whose DNA names no faction leaves
    its turrets in the colours they shipped with, which is what the engine
    does when either faction fails to resolve.
    """

    for _kind, _bay, group in hardpoints(context):
      for locator in group:
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
    strip_patterns(material)
    return material


def strip_patterns(material):
    """A turret has no pattern. Say so, rather than relying on the mask.

    A turret's shader IS the hull's -- the same `quadv5` group, the same maps
    -- minus the pattern layers: nothing paints a SKIN onto a gun. The mask
    textures come back unauthored and are filled with black, which makes the
    pattern blend contribute nothing already, but the pattern MATERIALS were
    then left at white while a hull's sit at black.

    Zeroing both the materials and the targets makes it true by construction
    rather than by the mask happening to be black -- and the hull's pattern
    targets are driven onto this material along with its dirt, so "happens to
    be" is doing real work there.
    """

    if material is None:
        return 0
    group = next((node for node in material.node_tree.nodes
                  if node.type == "GROUP"), None)
    if group is None:
        return 0

    cleared = 0
    for socket in group.inputs:
        name = socket.name
        if not (name.startswith("PMtl") or "attern" in name):
            continue
        try:
            if hasattr(socket.default_value, "__len__"):
                socket.default_value = tuple(
                    0.0 for _ in socket.default_value[:-1]) + (1.0,)
            else:
                socket.default_value = 0.0
        except (TypeError, AttributeError):
            continue
        cleared += 1
    return cleared


def fit(context, document, resources, res_path: str, name: str,
        locators=None):
    """Places one turret on every hardpoint. MAIN thread only."""

    geometry = str(document.get("geometryResPath") or "")
    local = resources.get(geometry)
    if not local:
        raise RuntimeError(f"no local file for {geometry}")

    if locators is None:
        locators = [locator for _kind, _bay, group in hardpoints(context)
                    for locator in group]
    if not locators:
        raise RuntimeError("this ship has no hardpoints of that kind")

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

        # A turret's DIRT is the ship's. It is bolted to the hull, so a dirty
        # ship has dirty guns and moving the ship's age has to move both --
        # the values are driven from the HULL rather than copied, exactly as a
        # decal's are, so they cannot drift apart.
        hull = ship_of(locator)
        if hull is not None:
            values = {key: hull[key] for key, (_, _default)
                      in ship_module.nodes.SHIP_PROPERTIES.items()
                      if hull.get(key) is not None}
            ship_module.apply_ship_globals(copies, values)
            ship_module.drive_ship_sockets(copies, hull)
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
        record = sof_materials.material(name, client)
        values = sof_materials.material_values(record)
        # `AssignParameters` assigns EVERY parameter a material carries, not a
        # chosen three, and `DustDiffuseColor` is one of them -- the colour the
        # material goes when it is dirty. Left out, a dirty turret goes white
        # while the hull beside it goes brown.
        dust = ((record or {}).get("parameters") or {}).get("DustDiffuseColor")
        if dust:
            values = dict(values, dust=tuple(float(v) for v in dust[:3]))
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
                              ("dust", f"Mtl{slot}DustDiffuseColor"),
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
    bl_label = "Fit"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="turret", options={"HIDDEN"})
    #: One bay, or empty for every bay of this kind.
    bay: StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        from . import addon

        state = context.window_manager.carbon_eve_turrets
        chosen_id = getattr(state, self.kind, "")
        if not chosen_id:
            self.report({"ERROR"}, "Choose a weapon first")
            return {"CANCELLED"}

        client = service_access.client(context)
        if client is None:
            self.report({"ERROR"}, "The CarbonEngineJS service is unreachable")
            return {"CANCELLED"}

        wanted = [(kind, bay, group) for kind, bay, group
                  in hardpoints(context, self.kind)
                  if not self.bay or bay == self.bay]
        if not wanted:
            self.report({"ERROR"}, "No hardpoints; load a ship first")
            return {"CANCELLED"}
        locators = [locator for _kind, _bay, group in wanted
                    for locator in group]

        chosen = next((row for row in weapons.catalogue(
                           client, slots=(KIND_SLOT.get(self.kind),))
                       if str(row["typeID"]) == chosen_id), None)
        if chosen is None:
            self.report({"ERROR"}, "That turret is no longer listed")
            return {"CANCELLED"}
        res_path, name = chosen["resPath"], chosen["name"]
        cache_root = _cache_root(context)

        faction = ship_faction(context)

        def work():
            document, resources, colours = fetch_turret(
                client, res_path, cache_root, progress=addon._set_progress,
                faction=faction)
            return name, res_path, document, resources, colours, locators

        try:
            addon._launch_job(context, "turrets", work, f"Fetching {name}")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def finish_job(context, result) -> str:
    """Applies a fetched turret. MAIN thread only."""

    name, res_path, document, resources, colours, locators = result
    state = context.window_manager.carbon_eve_turrets
    clear_fitted(locators)
    count, material = fit(context, document, resources, res_path, name,
                          locators)
    written = apply_faction_colours(material, colours)
    state.status = (f"{name} on {count} hardpoint(s)"
                    + (f", {written} faction value(s)" if written else ""))
    return state.status


class CARBON_OT_clear_turrets(Operator):
    """Remove the turrets this add-on fitted"""

    bl_idname = "carbon.clear_turrets"
    bl_label = "Clear"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="", options={"HIDDEN"})
    bay: StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        if self.kind:
            locators = [locator for kind, bay, group
                        in hardpoints(context, self.kind)
                        if not self.bay or bay == self.bay
                        for locator in group]
            removed = clear_fitted(locators)
        else:
            removed = clear_fitted()
        context.window_manager.carbon_eve_turrets.status = (
            f"removed {removed} object(s)" if removed else "nothing fitted")
        return {"FINISHED"}


def _draw_hardpoints(self, context):
    """One panel's body: the chooser, fit-all, and a row per bay."""

    layout = self.layout
    state = getattr(context.window_manager, "carbon_eve_turrets", None)
    if state is None:
        layout.label(text="Not registered")
        return

    bays = hardpoints(context, self.KIND)
    layout.prop(state, self.KIND, text="")

    row = layout.row(align=True)
    fit = row.operator(CARBON_OT_fit_turrets.bl_idname, text="Fit All",
                       icon="TOOL_SETTINGS")
    fit.kind, fit.bay = self.KIND, ""
    clear = row.operator(CARBON_OT_clear_turrets.bl_idname, text="",
                         icon="X")
    clear.kind, clear.bay = self.KIND, ""

    for kind, bay, group in bays:
        line = layout.row(align=True)
        # What is on this bay already, if anything. Read off the fitted
        # objects rather than remembered separately, so the panel cannot
        # disagree with the scene.
        fitted = next((obj.get("carbon_turret_name") for obj in bpy.data.objects
                       if obj.get(FITTED)
                       and obj.get("carbon_turret_locator")
                       in {locator.name for locator in group}), None)
        line.label(text=f"{bay}: {fitted or '-'}"
                        + (f"  ({len(group)})" if len(group) > 1 else ""),
                   icon="EMPTY_ARROWS")
        one = line.operator(CARBON_OT_fit_turrets.bl_idname, text="",
                            icon="IMPORT")
        one.kind, one.bay = kind, bay
        drop = line.operator(CARBON_OT_clear_turrets.bl_idname, text="",
                             icon="X")
        drop.kind, drop.bay = kind, bay

    if state.status:
        layout.label(text=state.status, icon="CHECKMARK")


def _make_panel(kind: str, label: str):
    """One panel per weapon kind, shown only when the hull has that kind.

    A hull offers what its LOCATORS say it offers -- a cruiser with no
    `locator_xl_*` has no extra-large hardpoints, and a panel for them would
    be a control that cannot do anything.
    """

    def poll(cls, context):
        return bool(hardpoints(context, cls.KIND))

    return type(
        f"CARBON_PT_sidebar_hardpoints_{kind}",
        (Panel,),
        {
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "UI",
            "bl_category": "CarbonEngineJS",
            "bl_label": f"Hardpoints - {label}",
            "bl_idname": f"CARBON_PT_hardpoints_{kind}",
            "bl_options": {"DEFAULT_CLOSED"},
            "KIND": kind,
            "poll": classmethod(poll),
            "draw": _draw_hardpoints,
        })


PANELS = tuple(_make_panel(kind, label) for kind, _slot, label in KINDS)

CLASSES = ((CARBON_TurretState, CARBON_OT_fit_turrets,
            CARBON_OT_clear_turrets) + PANELS)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.carbon_eve_turrets = bpy.props.PointerProperty(
        type=CARBON_TurretState)


def unregister():
    del bpy.types.WindowManager.carbon_eve_turrets
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
