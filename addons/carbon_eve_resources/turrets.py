"""Fitting weapons to a hull's authored weapon locators.

A hull carries named hardpoints -- `locator_turret_1a` and its siblings, twelve
on a Celestis -- and a turret is a separate model that mounts on one. Both
halves already exist here: the locators are built with the ship, and a turret's
own `.black` names its geometry and its effect, which is a `quadv5` from the
same family the hull's own areas use.

So this is mostly plumbing, and deliberately so. The service's library names
each weapon's `resPath` and natural `slot`; the shared catalogue also applies
the engine rule that an XL hardpoint accepts every XL weapon.
"""

from __future__ import annotations

from pathlib import Path
import re

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import service_access, sof_panels
import mathutils

from . import ship as ship_module
from .core import resindex, sof_fetch, weapons
from .ship import unique_name


#: Held because Blender does NOT keep a reference to the strings a dynamic
#: `items` callback returns -- letting them be collected shows up as mangled
#: labels, or a crash.

#: What a fitted turret is marked with, so it can be found and cleared.
FITTED = "carbon_turret"



#: What a locator's authored NAME says it takes. Runtime locators have no type
#: enum: the name is the category signal. The shared catalogue owns both this
#: prefix mapping and the compatibility rule used by filtering.
KINDS = weapons.WEAPON_KINDS

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


def _weapon_names(slot):
    """Every weapon name for one slot, for the search field."""

    def names(context=None):
        try:
            source = sof_panels._catalog_source(context)
        except Exception:
            return []
        rows = weapons.catalogue(service_access.client(context), slots=(slot,), **source.sde())
        return [f"{row['name']} [{row['typeID']}]" for row in rows]

    return names


def _search_for(slot):
    """A `search=` callback over one slot's weapon names."""

    return sof_panels.name_search(_weapon_names(slot))


class CARBON_TurretState(PropertyGroup):
    # One chooser per kind rather than one with a filter: the panels are shown
    # side by side, so a single shared chooser would make picking a missile
    # change what the turret panel says it will fit.
    #
    # SEARCHED, not enumerated. An EnumProperty draws every item at once, and
    # there are 784 weapons - the turret list alone is unusable as a dropdown.
    # A search field asks for what was typed and offers matches, which is the
    # only workable shape at this size.
    #
    # It holds the weapon's NAME. Names are unique across the catalogue (784
    # types, no duplicates), so a name resolves to exactly one typeID - and
    # typeID stays what gets fitted, because 602 turrets share 57 models and
    # keying by model path made picking one weapon fit another. Several names
    # sharing a model is fine and expected; they are different weapons.
    __annotations__ = {
        kind: StringProperty(name=label, default="",
                             search=_search_for(slot),
                             description=f"Which weapon to mount on a "
                                         f"{label.lower()} hardpoint; type "
                                         f"{sof_panels.SEARCH_AFTER} letters "
                                         f"to search")
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
    source = sof_panels._catalog_source(context)
    def same_source(obj):
        while obj is not None:
            settings = getattr(obj, "carbon_sof", None)
            if settings is not None and settings.source_target:
                return (settings.source_target == source.target
                        and settings.resource_build == source.resource_build)
            obj = obj.parent
        return source.target == "eve"
    found = [obj for obj in bpy.data.objects
             if obj.get("carbon_locator_kind") in wanted and same_source(obj)]
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


def dna_of(locator) -> str:
    """The owning ship's DNA, retaining race and material overrides per hull."""

    node = locator
    while node is not None:
        dna = str(getattr(getattr(node, "carbon_sof", None), "dna", "")
                  or node.get("carbon_sof_dna") or "")
        if dna:
            return dna
        node = node.parent
    return ""


def faction_of(locator) -> str:
    parts = dna_of(locator).split(":")
    return parts[1] if len(parts) > 1 else ""


def factions_for(locators) -> list:
    """Distinct ship DNAs for resolving turret parameters independently."""

    return sorted({dna_of(locator) for locator in locators if dna_of(locator)})


def fetch_turret(client, res_path: str, cache_root, *, progress=None,
                 factions=(), source=None, resfiles_root=None):
    """The turret's document and its files. Runs on the JOB thread.

    No `bpy` and no scene changes in here: what comes back is handed to the
    main thread, which is the only place allowed to touch Blender data.
    """

    from .core.source import resolve
    source = source or resolve(client, cache_root=cache_root)
    document = weapons.turret_document(client, res_path, **source.resources())
    if not document:
        raise RuntimeError(f"no turret document for {res_path}")

    geometry = str(document.get("geometryResPath") or "")
    if not geometry:
        raise RuntimeError(f"{res_path} names no geometry")

    build = source.resource_build
    index = resindex.load(cache_root, build) if source.target == "eve" else None

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
                                             **source.resources(), index=index, resfiles_root=resfiles_root)
        except Exception as exc:
            print(f"[CarbonEngineJS SOF] turret resource {path}: {exc}")
            continue
        if found is not None:
            resources[path] = str(found)

    if geometry not in resources:
        raise RuntimeError(f"could not fetch {geometry}")

    # The turret takes the SHIP's faction, as the engine does: a slot defaults
    # its faction to the parent's, off the hull's own DNA.
    from .core import sof_materials

    from .core import turret_materials, sof_resolution
    colours = {}
    if factions:
        generic = client.request_json("GET", f"/{source.target}/{source.resource_build}/res/dx9/model/spaceobjectfactory/generic.black?format=json")["object"]
        for dna in factions:
            parsed = sof_resolution.parse(dna)
            faction = sof_materials.faction(parsed.faction, client, **source.resources())
            race = client.request_json("GET", f"/{source.target}/{source.resource_build}/sof/races/{parsed.race}")
            hull = client.request_json("GET", f"/{source.target}/{source.resource_build}/sof/hulls/{parsed.hull}")
            colours[dna] = turret_materials.resolve(effect, generic, faction,
                lambda name: sof_materials.material(name, client, **source.resources()),
                race=race, dna=dna, sof6=bool(hull.get("sof6", False)))
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


def _turret_material(document, resources, name, target="eve"):
    """The turret's own material, through the quad family the hull uses."""

    from .quad import interface as quad_interface
    from .quad import materials as quad_materials

    effect = document.get("turretEffect") or {}
    if not effect.get("effectFilePath"):
        return None

    # Typed packed values are decoded once by the service's shared Black reader.
    effect = dict(effect)
    for field in ("resources", "parameters", "constParameters", "options"):
        value = effect.get(field)
        if isinstance(value, dict) and value.get("count", 0):
            raise RuntimeError(f"Service did not decode turret {field}; update tools-core")
        effect[field] = value if isinstance(value, list) else []
    try:
        family = quad_interface.load_family(target=target)
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] turret family unavailable: {exc}")
        return None

    material, problem = quad_materials.build_area_material(
        {"name": name, "effect": effect}, family, resources, 0)
    if problem:
        print(f"  ! {problem}")
    return material


def self_name(name: str) -> str:
    """A weapon's name as an object name: no spaces, no punctuation."""

    return "".join(ch if ch.isalnum() else "_" for ch in str(name)).strip("_")


def turret_mount_matrix(locator, hull):
    """Cancel authored locator scale while retaining the hull's world transform."""
    # Carbon: EveTurretSet.cpp SetLocalTransform (1771-1779) removes locator
    # scale before UpdateTransform (1520) composes local * ship (row vectors).
    # Blender uses column vectors: ship @ normalized locator. The holder stays
    # parented to the locator so animated hardpoint movement is retained.
    parent = hull.matrix_world if hull is not None else mathutils.Matrix.Identity(4)
    local = parent.inverted() @ locator.matrix_world
    unscaled = local.copy()
    for axis in range(3):
        column = local.col[axis].to_3d()
        if column.length == 0.0:
            raise ValueError("Cannot fit a turret to a zero-scale locator")
        column.normalize()
        for row in range(3):
            unscaled[row][axis] = column[row]
    return local.inverted() @ unscaled


def fit(context, document, resources, res_path: str, name: str,
        locators=None, colours=None, source=None):
    """Places one turret on every hardpoint. MAIN thread only."""

    target = source.target if source else "eve"
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
    ship_module.import_geometry(local, name, logical_path=geometry)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        raise RuntimeError(f"{Path(local).name} imported nothing")

    # Materials contain drivers targeting their owning hull. Share across
    # that hull's hardpoints, never across separate ships with identical DNA.
    colours = colours or {}
    by_faction = {}

    def material_for(locator):
        faction = dna_of(locator)
        key = (ship_of(locator), faction)
        if key not in by_faction:
            from .core import turret_materials
            values = colours.get(faction) or {}
            made = _turret_material(turret_materials.apply(document, values), resources, name, target)
            written = len(values)
            if made is not None:
                made.name = f"{made.name} {faction}" if faction else made.name
                made["carbon_turret_faction"] = faction
            if faction:
                print(f"    {faction}: {written} faction value(s)")
            by_faction[key] = made
        return by_faction[key]

    # The model's OWN base transform, taken off its first root before anything
    # is reparented.
    #
    # The importer gives every imported object the same EVE-to-Blender
    # rotation, and a locator already carries that rotation because it was
    # built through the hull -- so parenting the model to a locator as
    # imported applies it TWICE, and the turrets come out on the wrong angles.
    # Expressing each root relative to this base removes it exactly once and
    # keeps whatever the roots' offsets are relative to each other.
    tops = [obj for obj in imported if obj.parent is None] or imported[:1]
    base = tops[0].matrix_world.copy()
    try:
        unbase = base.inverted()
    except ValueError:                   # a singular matrix cannot be undone
        unbase = mathutils.Matrix.Identity(4)
    placement = {obj: (unbase @ obj.matrix_world.copy()) for obj in tops}

    fitted = []
    for order, locator in enumerate(locators):
        # The FIRST hardpoint keeps the imported objects; the rest get copies,
        # so one import serves the whole ship.
        if order == 0:
            copies, origin = imported, {}
        else:
            copies, mapping = [], {}
            origin = {}
            for obj in imported:
                clone = obj.copy()
                if obj.data is not None:
                    clone.data = obj.data       # one mesh, many turrets
                mapping[obj] = clone
                origin[clone] = obj
                copies.append(clone)
            for obj, clone in mapping.items():
                # Object.copy retains modifier targets; each turret must be
                # deformed by its own copied rig, not the first hardpoint's.
                for modifier in clone.modifiers:
                    if modifier.type == "ARMATURE" and modifier.object in mapping:
                        modifier.object = mapping[modifier.object]
                if obj.parent in mapping:
                    # Keep the model's OWN hierarchy, and its own local
                    # transform with it.
                    local = obj.matrix_local.copy()
                    clone.parent = mapping[obj.parent]
                    clone.matrix_parent_inverse.identity()
                    clone.matrix_local = local
            for collection in locator.users_collection:
                for clone in copies:
                    collection.objects.link(clone)

        # A CONTAINER per bay, and the model goes under it untouched.
        #
        # Every root used to be slammed onto the locator's own transform, which
        # is right only if a turret has exactly one root. A real turret has
        # several -- barrels, a mount, an armature -- laid out relative to each
        # other, and forcing them all to one transform collapses that layout:
        # the barrels keep the locator's Z and lose their own X and Y, so half
        # of them end up above the hull and half below.
        #
        # Parenting to an empty AT the locator moves the whole assembly and
        # changes nothing inside it.
        holder = bpy.data.objects.new(
            unique_name(f"{self_name(name)}_{locator.name}", ""), None)
        holder.empty_display_type = "PLAIN_AXES"
        holder.empty_display_size = 0.5
        holder.hide_render = True
        for collection in locator.users_collection:
            collection.objects.link(holder)
        holder.parent = locator
        holder.matrix_parent_inverse.identity()
        holder.matrix_local = turret_mount_matrix(locator, ship_of(locator))

        for obj in copies:
            if obj.parent is not None and obj.parent in copies:
                continue
            source = obj if order == 0 else origin.get(obj, obj)
            obj.parent = holder
            obj.matrix_parent_inverse.identity()
            obj.matrix_local = placement.get(
                source, mathutils.Matrix.Identity(4))

        copies = copies + [holder]
        material = material_for(locator)
        for obj in copies:
            obj[FITTED] = res_path
            obj["carbon_turret_locator"] = locator.name
            obj["carbon_turret_name"] = name
            if material is not None and obj.type == "MESH":
                if not obj.data.materials:
                    obj.data.materials.append(material)
                # Geometry is shared across hardpoints; material bindings are
                # per object so another hull cannot repaint existing turrets.
                for slot in obj.material_slots:
                    slot.link = "OBJECT"
                    slot.material = material

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

    print(f"  fitted {name} to {len(locators)} hardpoint(s), "
          f"{len(fitted)} object(s), {len(by_faction)} faction(s)")
    return len(locators), by_faction


class CARBON_OT_fit_turrets(Operator):
    """Mount the chosen weapon on every matching hardpoint of the ship"""

    bl_idname = "carbon.fit_turrets"
    bl_label = "Fit"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="turret", options={"HIDDEN"})
    #: One bay, or empty for every bay of this kind.
    bay: StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        from . import addon

        source = sof_panels._catalog_source(context)
        state = context.window_manager.carbon_eve_turrets
        chosen_name = str(getattr(state, self.kind, "") or "").strip()
        if not chosen_name:
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

        rows = weapons.catalogue(client, slots=(KIND_SLOT.get(self.kind),), **source.sde())
        matches = [row for row in rows
                   if chosen_name.casefold() in (str(row["name"]).casefold(),
                       f"{row['name']} [{row['typeID']}]".casefold())]
        chosen = matches[0] if len(matches) == 1 else None
        if chosen is None:
            self.report({"ERROR"},
                        f"No weapon named \"{chosen_name}\" for this hardpoint")
            return {"CANCELLED"}
        res_path, name = chosen["resPath"], chosen["name"]
        cache_root = _cache_root(context)
        factions = factions_for(locators)
        from .core.source import resfiles_directory
        resfiles_root = resfiles_directory(addon._prefs(context), source.target)
        if resfiles_root:
            resfiles_root = bpy.path.abspath(resfiles_root)

        def work():
            document, resources, colours = fetch_turret(
                client, res_path, cache_root, progress=addon._set_progress,
                factions=factions, source=source, resfiles_root=resfiles_root)
            return name, res_path, document, resources, colours, locators, source

        try:
            addon._launch_job(context, "turrets", work, f"Fetching {name}")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def finish_job(context, result) -> str:
    """Applies a fetched turret. MAIN thread only."""

    name, res_path, document, resources, colours, locators, source = result
    state = context.window_manager.carbon_eve_turrets
    # Fetching runs in the background; the picker may now belong to another source.
    if source != sof_panels._catalog_source(context):
        state.status = "Source changed; fetched weapon was not fitted"
        return state.status
    clear_fitted(locators)
    count, by_faction = fit(context, document, resources, res_path, name,
                            locators, colours, source)
    painted = [f for f in by_faction if f]
    state.status = (f"{name} on {count} hardpoint(s)"
                    + (f", {len(painted)} faction(s)" if painted else ""))
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
