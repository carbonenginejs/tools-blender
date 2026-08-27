"""The SOF a ship is built from, as panels that drive the scene.

The flow this serves, in the operator's own order:

    a type id, or a name and a skin
        -> a DNA
        -> a ship
        -> change a component
        -> the change flows to every child object

So the SOF is the SOURCE and the scene is the RESULT, not the other way round.
That matters beyond convenience: what gets exported is the SOF hull and faction,
never the built `EveShip2`, so the authored values have to live somewhere that
survives a rebuild -- on the ship object, in SOF's own shape.

Editing any field here pushes it into every material of the hull AND of its
children, because a decal is its own object and would otherwise keep the value
it was built with.

The type-id and skin lookups are a deliberate SEAM: resolving either to a hull
and a faction is `tools-core`'s job, and this holds the input and the answer.
Everything below works without it.
"""

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import sof_resolution
from .quad import interface as quad_interface


#: Where a material slot's values land in the shader, by slot index.
#:
#: Carbon's own names. Slots 1-4 are the hull's materials; the two PATTERN slots
#: are what a SKIN paints over them.
MATERIAL_SOCKETS = {
    "diffuse": "Mtl{}DiffuseColor",
    "gloss": "Mtl{}Gloss",
    "fresnel": "Mtl{}FresnelColor",
}
PATTERN_SOCKETS = {
    "diffuse": "PMtl{}DiffuseColor",
    "gloss": "PMtl{}Gloss",
    "fresnel": "PMtl{}FresnelColor",
}


def ship_objects(obj):
    """A hull and everything parented to it, which is what a change must reach.

    A decal is its own object with its own material, so a value pushed only to
    the hull leaves every decal showing what it was built with.
    """

    if obj is None:
        return []
    found = [obj]
    for child in bpy.data.objects:
        if child.parent is obj and child not in found:
            found.append(child)
    return found


def push_to_materials(obj, values, *, area_type=None, blocked_slot=None):
    """Writes `{socket name: value}` into the quad groups this change belongs to.

    Sockets are addressed by NAME, never by index: which sockets a member binds
    depends on which quad it is, so `quadsailsv5` and `quadv5` do not agree on
    position. A name a material does not have is skipped rather than guessed at.

    `area_type` restricts the write to areas of one type, which is what a
    faction material actually is: the faction stores four material names PER
    AREA TYPE, so writing one set into every material paints a hull's colours
    onto its sails. Left as None the write reaches everything, which is right
    only for values that genuinely are ship-wide.

    `blocked_slot` is the 1-based slot a DNA override is being written for. An
    area whose `blockedMaterials` vetoes that slot keeps the faction's material
    and must be skipped -- mde3_t3's sails block slots 3 and 4, so a skin
    repaints its hull and booster but only half its sails.
    """

    written = 0
    seen = set()
    for target in ship_objects(obj):
        for slot in getattr(target, "material_slots", []):
            material = slot.material
            if material is None or not material.use_nodes or material.name in seen:
                continue
            seen.add(material.name)
            if area_type is not None:
                found = material.get("carbon_area_type", None)
                # An area nobody could identify is left alone rather than
                # swept up by every edit: painting it with a type it may not
                # have is worse than leaving it as it was built.
                if found is None or int(found) != int(area_type):
                    continue
            if blocked_slot is not None and sof_resolution.is_blocked(
                    material.get("carbon_blocked_materials", 0), blocked_slot):
                continue
            for node in material.node_tree.nodes:
                if node.bl_idname != "ShaderNodeGroup" or not node.node_tree:
                    continue
                for name, value in values.items():
                    socket = node.inputs.get(quad_interface.socket_name(name))
                    if socket is None:
                        continue
                    if socket.type == "VALUE":
                        socket.default_value = float(value)
                    else:
                        socket.default_value = tuple(value)[:len(socket.default_value)]
                    written += 1
    return written


def slot_materials(obj, entry):
    """The area materials one slot governs.

    A hull material belongs to one AREA TYPE and must not reach the others; a
    pattern layer is ship-wide, because the pattern branch of the resolution
    chain never consults the area type; and a DNA override skips any area whose
    `blockedMaterials` vetoes its slot.
    """

    wanted = None if entry.is_pattern or entry.area_type < 0 else entry.area_type
    blocked = (entry.index if not entry.is_pattern
               and entry.source == sof_resolution.SOURCE_DNA else None)
    found = []
    seen = set()
    for target in ship_objects(obj):
        for slot in getattr(target, "material_slots", []):
            material = slot.material
            if material is None or not material.use_nodes or material.name in seen:
                continue
            seen.add(material.name)
            if wanted is not None:
                area_type = material.get("carbon_area_type", None)
                # An area nobody could identify is left alone rather than swept
                # up by every edit.
                if area_type is None or int(area_type) != int(wanted):
                    continue
            if blocked is not None and sof_resolution.is_blocked(
                    material.get("carbon_blocked_materials", 0), blocked):
                continue
            found.append(material)
    return found


def bound_group_for(obj, entry):
    """The material group this slot is reading, if it has been bound."""

    from . import sof_material_nodes

    for material in slot_materials(obj, entry):
        tree = sof_material_nodes.bound_group(material, entry.index,
                                              is_pattern=entry.is_pattern)
        if tree is not None:
            return tree
    return None


def _material_update(self, context):
    """Writes this slot's values into the MATERIAL the slot is reading.

    Nothing is pushed into shaders any more. Every area that names this
    material is linked to one node group, so setting the group's values is the
    whole update -- the areas are reading it, not holding copies of it.
    """

    from . import sof_material_nodes

    obj = getattr(self, "id_data", None)
    if not isinstance(obj, bpy.types.Object):
        return
    tree = bound_group_for(obj, self)
    if tree is None:
        return
    sof_material_nodes._fill(tree, {
        "diffuse": tuple(self.diffuse),
        "fresnel": tuple(self.fresnel),
        "gloss": self.gloss,
    })


#: Set while a slot is being filled from the SOF, so the update callbacks know
#: the change did not come from a person.
_APPLYING = {"depth": 0}


class applying:
    """Marks values as arriving FROM the SOF rather than from a consumer.

    Without it, filling a slot from a faction record trips every colour's update
    and marks the slot custom -- so a freshly loaded ship would claim every
    material had been edited by hand.
    """

    def __enter__(self):
        _APPLYING["depth"] += 1
        return self

    def __exit__(self, *_):
        _APPLYING["depth"] -= 1
        return False


def _colour_edited(self, context):
    """A colour changed. If a person did it, the slot is no longer the material.

    In SOF a colour is not a value someone picks -- it belongs to a named
    MATERIAL, and the slot names which one. Editing a colour directly therefore
    means the slot has left that material behind, and saying so is what keeps
    the material name honest rather than a stale label on values it no longer
    describes.
    """

    if _APPLYING["depth"] == 0 and self.material and self.material != CUSTOM_MATERIAL:
        # A shared material is shared on purpose -- mde3_t3's hull and sails
        # both read `black_deadstar_coated` -- so editing it in place would
        # repaint every slot that names it. Going custom takes a private copy
        # and leaves the original alone for everyone else.
        _privatise(self, context)
        with applying():
            self.material = CUSTOM_MATERIAL
    _material_update(self, context)


def _privatise(entry, context):
    """Gives one slot its own copy of the material it is reading."""

    from . import sof_material_nodes

    obj = getattr(entry, "id_data", None)
    if not isinstance(obj, bpy.types.Object):
        return
    tree = bound_group_for(obj, entry)
    if tree is None:
        return
    owner = f"{obj.name} {'P' if entry.is_pattern else ''}{entry.index}"
    private = sof_material_nodes.make_private(tree, owner)
    for material in slot_materials(obj, entry):
        sof_material_nodes.bind_slot(material, entry.index, private,
                                     is_pattern=entry.is_pattern)


#: What a slot is called once its colours no longer match any named material.
CUSTOM_MATERIAL = "custom"

class CARBON_SOF_Name(PropertyGroup):
    """One catalog entry. A `name` is all a search list needs."""


def catalog_items(context=None):
    """Fills the window's material list, once, and returns how many it holds.

    The catalog lives on the WINDOW MANAGER rather than in the blend: it is
    1149 names that belong to a tools-core build, not to this file, and saving
    a copy into every .blend would make each one carry a stale snapshot of a
    catalog it does not own.
    """

    from . import service_access, sof_materials

    window = (context or bpy.context).window_manager
    items = window.carbon_sof_materials
    if len(items):
        return len(items)
    for name in sof_materials.catalog(service_access.client(context)):
        items.add().name = name
    return len(items)


def _material_named(self, context):
    """The slot was pointed at a different material, so it takes its values.

    Fetched rather than derived: a material is a bag of vec4 parameters and
    only tools-core has them.

    A name arriving FROM the SOF is skipped -- it already came with its values,
    and re-fetching would be a round trip to be told what we were just told.
    """

    if _APPLYING["depth"] != 0:
        return
    from . import service_access, sof_material_nodes, sof_materials

    obj = getattr(self, "id_data", None)
    chosen = str(self.material or "")
    if not isinstance(obj, bpy.types.Object) or not chosen or chosen == CUSTOM_MATERIAL:
        return
    values = sof_materials.material_values(
        sof_materials.material(chosen, service_access.client(context)))
    if not values:
        # Said plainly rather than leaving the old colours under a new name,
        # which would be a slot lying about what it holds.
        print(f"[CarbonEngineJS SOF] {chosen}: no parameters were fetched; "
              "the colours below still belong to the previous material")
        return

    # REBIND to the chosen material. Writing the new values into the group the
    # slot was already reading would repaint every other slot sharing it --
    # naming the sails' slot 1 differently changed the hull, because both read
    # `black_deadstar_coated` -- and would leave that group holding one
    # material's values under another material's name.
    tree = sof_material_nodes.material_group(chosen, values)
    for material in slot_materials(obj, self):
        sof_material_nodes.bind_slot(material, self.index, tree,
                                     is_pattern=self.is_pattern)
    with applying():
        for field, value in values.items():
            setattr(self, field, value)


class CARBON_SOF_Material(PropertyGroup):
    """One material slot: the NAME of a SOF material, and the values it carries.

    The name is the thing a consumer chooses. The colours below it are what that
    material holds -- shown because they are useful to see, and editable at the
    cost of the slot becoming `custom`, which is the honest description of
    values that no longer belong to any named material.

    EVE's colours are HDR -- authored above one, three times normal is common --
    so these carry a soft maximum rather than a hard clamp.
    """

    index: IntProperty(default=1, min=1, max=4)
    is_pattern: BoolProperty(default=False)
    #: The material this slot uses, BY NAME.
    #:
    #: A string, deliberately, and not an enum: an EnumProperty with a dynamic
    #: items callback stores the chosen INDEX, so every slot whose index was
    #: never set displayed item 0 -- the catalog's first material -- and an
    #: index into a 1149-entry list is meaningless the moment the catalog
    #: changes. The name is the thing worth keeping.
    material: StringProperty(
        name="Material", default="", update=_material_named,
        description="The SOF material this slot uses. Pick one to fill the "
                    "slot with its values; editing a colour below leaves that "
                    "material and marks the slot custom")
    #: WHERE the value came from, which the value itself no longer knows. By
    #: the time colours reach a shader they are four numbers in a constant
    #: buffer that cannot tell an override from a default, so a consumer
    #: looking at a red hull has no way to see whether the DNA asked for red or
    #: the faction simply is red -- and only one of those is exportable.
    source: StringProperty(
        name="From", default=sof_resolution.SOURCE_FACTION,
        description="Whether this slot was named by the DNA or supplied by the faction")
    #: Which AREA TYPE this slot governs, or -1 for a value that is genuinely
    #: ship-wide. A faction holds four material names per area type, so the
    #: same slot number is a different material on a hull area than on a sails
    #: area -- one panel per ship cannot say that, and pushing one set of
    #: values everywhere paints the hull's colours onto the sails.
    area_type: IntProperty(default=-1)
    #: The area's `blockedMaterials` mask, so a DNA override knows which areas
    #: refuse it. Authored on the hull area, and the only place a skin can be
    #: overruled per slot.
    blocked: IntProperty(default=0)
    diffuse: FloatVectorProperty(
        name="Diffuse", subtype="COLOR", size=3, min=0.0, soft_max=1.0,
        default=(0.0, 0.0, 0.0), update=_colour_edited,
        description="Authored above 1 for HDR; 3x normal is common")
    fresnel: FloatVectorProperty(
        name="Fresnel", subtype="COLOR", size=3, min=0.0, soft_max=1.0,
        default=(0.04, 0.04, 0.04), update=_colour_edited,
        description="F0, the reflectance looking straight on")
    gloss: FloatProperty(
        name="Gloss", default=0.5, min=0.0, max=1.0, update=_colour_edited)


def _component_update(self, context):
    """A component changed, so the DNA that names the ship changes with it."""

    self.dna = self.compose_dna()


class CARBON_SOF_Settings(PropertyGroup):
    """The SOF a ship is built from: hull, faction, race, pattern.

    These are the AUTHORED inputs. A built `EveShip2` is the result of resolving
    them and is not what gets exported, so they are kept here rather than read
    back out of the scene.
    """

    hull: StringProperty(
        name="Hull", default="", update=_component_update,
        description="The SOF hull, for example mde3_t3")
    faction: StringProperty(
        name="Faction", default="", update=_component_update,
        description="The SOF faction, which supplies the materials and the logos")
    race: StringProperty(
        name="Race", default="", update=_component_update)
    pattern: StringProperty(
        name="Pattern", default="", update=_component_update,
        description="The SKIN's pattern, if the ship wears one")
    dna: StringProperty(
        name="DNA", default="",
        description="hull:faction:race, and a pattern when there is one")

    #: What a consumer types instead of a DNA. Resolving either to a hull and a
    #: faction is tools-core's job; this holds the input and the answer.
    type_id: IntProperty(
        name="Type ID", default=0, min=0,
        description="Resolved to a hull and faction by tools-core")
    skin: StringProperty(
        name="Skin", default="",
        description="A SKIN name, resolved to a pattern and faction")

    materials: CollectionProperty(type=CARBON_SOF_Material)

    def compose_dna(self) -> str:
        """The DNA these components and material slots describe.

        Composed through `sof_resolution`, which writes the runtime's own
        grammar and sorts the commands the way `EveSOFDNA` does -- so a DNA
        built here can be pasted straight into the loader, and two edits that
        reach the same ship produce the same text.

        Only slots the DNA OWNS are written. A slot showing the faction's
        material is not an override, and writing it out would freeze today's
        faction colour into the DNA -- the ship would stop following its
        faction the moment anyone rebuilt it.
        """

        if not any((self.hull, self.faction, self.race)):
            return ""
        materials = []
        for index in range(1, sof_resolution.MATERIAL_SLOTS + 1):
            entry = self.slot(index)
            owned = (entry is not None
                     and entry.source == sof_resolution.SOURCE_DNA
                     and entry.material
                     and entry.material != CUSTOM_MATERIAL)
            materials.append(entry.material if owned else sof_resolution.NONE)

        dna = sof_resolution.compose([self.hull], self.faction, self.race)
        if self.pattern:
            parts = [part for part in str(self.pattern).split(";") if part]
            if parts:
                dna = sof_resolution.with_pattern(dna, parts[0], parts[1:])
        return sof_resolution.with_materials(dna, materials)

    def bind_materials(self, obj) -> dict:
        """Points every area shader at the material groups its slots name.

        This is the SOF's whole job on the scene side: name the material, and
        let the shader read it. Nothing writes colours into a shader here.

        A material's values are fetched once and land in its group; the areas
        link to that group. A material shared by two areas is therefore one
        datablock, and editing it later reaches both without anything walking
        the ship.
        """

        from . import service_access, sof_material_nodes, sof_materials

        client = service_access.client()
        report = {"bound": 0, "materials": 0, "missing": []}
        for entry in self.materials:
            name = str(entry.material or "")
            if not name or name == CUSTOM_MATERIAL:
                continue
            values = sof_materials.material_values(
                sof_materials.material(name, client))
            if not values:
                # Named but unfetchable. Binding an empty group would paint the
                # area black, which is worse than leaving it as it was built.
                if name not in report["missing"]:
                    report["missing"].append(name)
                continue
            tree = sof_material_nodes.material_group(name, values)
            report["materials"] += 1
            for material in slot_materials(obj, entry):
                if sof_material_nodes.bind_slot(material, entry.index, tree,
                                                is_pattern=entry.is_pattern):
                    report["bound"] += 1
        return report

    def stamp_sources(self) -> None:
        """Records, per slot, whether the DNA named it or the faction gave it.

        Called after a build, when the slots hold resolved colours and nothing
        else. The colours cannot answer this -- resolution has already happened
        and thrown the question away -- so the DNA is read for it instead.
        """

        try:
            parsed = sof_resolution.parse(self.dna)
        except sof_resolution.DnaError:
            return

        # The faction's own table, keyed `areaType:slot`. tools-core serves it
        # already flattened, so naming a faction-supplied slot is a lookup.
        from . import service_access, sof_materials
        names = sof_materials.faction_material_names(
            sof_materials.faction(self.faction, service_access.client()))

        # Per area, not per ship. Two areas can disagree about the same slot
        # number -- an area that blocks a slot keeps the faction's material
        # while its neighbours take the skin's -- so asking once for the whole
        # ship would answer for whichever area happened to be built first.
        for entry in self.materials:
            sources = sof_resolution.area_slot_sources(
                parsed,
                entry.area_type if entry.area_type >= 0 else sof_resolution.TYPE_PRIMARY,
                entry.blocked,
            )
            found = next((source for source in sources
                          if source.index == entry.index
                          and source.is_pattern == entry.is_pattern), None)
            if found is None:
                continue
            with applying():
                entry.source = found.source
                if found.material:
                    entry.material = found.material
                elif names:
                    # A faction-sourced slot has a name too -- the faction's --
                    # and leaving it blank made every such slot look nameless
                    # when it is simply named somewhere else.
                    entry.material = sof_materials.material_name_for(
                        names,
                        entry.area_type if entry.area_type >= 0
                        else sof_resolution.TYPE_PRIMARY,
                        entry.index)


    def slot(self, index, pattern=False, area_type=-1):
        """One slot, keyed by area type as well as number.

        The area type is part of the key because the same slot NUMBER is a
        different material on a hull area than on a sails area. Looking one up
        by number alone returns whichever area happened to be built first,
        which is how a hull's colours end up on the sails.
        """

        for entry in self.materials:
            if (entry.index == index and entry.is_pattern == pattern
                    and entry.area_type == area_type):
                return entry
        return None

    def area_types(self) -> tuple:
        """The area types this ship actually has slots for, in order."""

        found = []
        for entry in self.materials:
            if not entry.is_pattern and entry.area_type not in found:
                found.append(entry.area_type)
        return tuple(sorted(found))


class CARBON_SOF_OT_apply(Operator):
    """Pushes every value to the ship, for when a rebuild has drifted."""

    bl_idname = "carbon.sof_apply"
    bl_label = "Apply to Ship"
    bl_description = "Write every SOF value into this hull and all its children"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        while obj is not None and not getattr(obj, "carbon_sof", None).dna:
            obj = obj.parent
        if obj is None:
            self.report({"ERROR"}, "No ship to apply to")
            return {"CANCELLED"}

        settings = obj.carbon_sof
        settings.stamp_sources()
        report = settings.bind_materials(obj)
        message = "Bound %d slot(s) to %d material(s)" % (report["bound"],
                                                          report["materials"])
        if report["missing"]:
            # Named but unfetchable, and said out loud: those areas are still
            # showing whatever they were built with.
            self.report({"WARNING"},
                        message + "; could not fetch " + ", ".join(report["missing"]))
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class CARBON_SOF_OT_rebuild(Operator):
    """The SOF Builder: resolve the edited SOF, and rebuild what hangs off it.

    A hull does not know its faction's colours, so changing the faction cannot
    be pushed straight into the scene the way a colour can -- there is nothing
    in Blender that knows what the new faction resolves to. Resolution belongs
    to tools-core, which owns the rules (the DNA's materials win unless `none`,
    then the faction per area, then the primary area), so this recomposes the
    DNA from what was edited and asks for the answer rather than guessing it.

    Reimplementing the resolution here would be a second copy of those rules,
    drifting quietly while looking right -- the expensive kind of wrong.
    """

    bl_idname = "carbon.sof_rebuild"
    bl_label = "Rebuild from SOF"
    bl_description = ("Recompose the DNA from these components and rebuild the "
                      "ship, so every child follows the change")
    bl_options = {"REGISTER"}

    def execute(self, context):
        obj = context.object
        while obj is not None and not getattr(obj, "carbon_sof", None).dna:
            obj = obj.parent
        if obj is None:
            self.report({"ERROR"}, "No ship to rebuild")
            return {"CANCELLED"}

        settings = obj.carbon_sof
        dna = settings.compose_dna()
        if not dna:
            self.report({"ERROR"}, "Fill in at least the hull, faction and race")
            return {"CANCELLED"}
        settings.dna = dna

        state = getattr(context.window_manager, "carbon_eve_resources", None)
        if state is None:
            self.report({"ERROR"}, "Resource browser is not registered")
            return {"CANCELLED"}
        state.dna = dna
        self.report({"INFO"}, f"Rebuilding {dna}")
        return bpy.ops.carbon.eve_resource_build_sof_dna(refresh=True)


class CARBON_SOF_OT_export_material(Operator):
    """Writes a custom material out, so an edit can leave Blender.

    A slot goes `custom` the moment a colour is edited, and a custom material
    exists nowhere but this blend. Exporting it is what turns a local edit into
    something a SOF can carry -- until it is written down it is a change nobody
    else can see, which is the same trap as editing an object instead of its
    SOF.
    """

    bl_idname = "carbon.sof_export_material"
    bl_label = "Export Custom Material"
    bl_description = "Write this slot's values to a JSON file beside the blend"
    bl_options = {"REGISTER"}

    slot: StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        import json
        import os

        obj = context.object
        while obj is not None and not getattr(obj, "carbon_sof", None).dna:
            obj = obj.parent
        if obj is None:
            self.report({"ERROR"}, "No ship to export from")
            return {"CANCELLED"}

        # `index:isPattern:areaType`. The area type is part of the key: the
        # same slot number is a different material on a hull area than on a
        # sails area, so exporting by number alone would write out whichever
        # area happened to be built first.
        parts = (self.slot.split(":") + ["", ""])[:3]
        entry = obj.carbon_sof.slot(int(parts[0] or 1), parts[1] == "1",
                                    int(parts[2] or -1))
        if entry is None:
            self.report({"ERROR"}, "That slot is not there")
            return {"CANCELLED"}

        settings = obj.carbon_sof
        document = {
            "schema": "carbon.sof-material",
            "version": 1,
            "name": f"{settings.hull or 'hull'}_{'pattern' if entry.is_pattern else 'material'}{entry.index}",
            "from": {"hull": settings.hull, "faction": settings.faction,
                     "dna": settings.dna},
            "values": {
                "diffuse": [round(v, 6) for v in entry.diffuse],
                "fresnel": [round(v, 6) for v in entry.fresnel],
                "gloss": round(entry.gloss, 6),
            },
        }

        directory = os.path.dirname(bpy.data.filepath) or bpy.app.tempdir
        path = os.path.join(directory, document["name"] + ".sof-material.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write(chr(10))
        self.report({"INFO"}, f"Wrote {os.path.basename(path)}")
        print(f"  wrote {path}")
        return {"FINISHED"}


class CARBON_SOF_OT_ensure_slots(Operator):
    """Creates the four material slots and the two pattern slots."""

    bl_idname = "carbon.sof_ensure_slots"
    bl_label = "Add Material Slots"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.object.carbon_sof
        wanted = [(index, False) for index in (1, 2, 3, 4)]
        wanted += [(index, True) for index in (1, 2)]
        for index, is_pattern in wanted:
            if settings.slot(index, is_pattern) is None:
                entry = settings.materials.add()
                entry.index = index
                entry.is_pattern = is_pattern
        return {"FINISHED"}


class CARBON_PT_sof(Panel):
    """The root: what this ship IS, in SOF's terms."""

    bl_label = "Carbon SOF"
    bl_idname = "CARBON_PT_sof"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        settings = context.object.carbon_sof
        layout = self.layout
        layout.use_property_split = True
        row = layout.row()
        row.enabled = False
        row.prop(settings, "dna")
        layout.operator(CARBON_SOF_OT_apply.bl_idname, icon="FILE_REFRESH")


class CARBON_PT_sof_source(Panel):
    """What a consumer types instead of a DNA."""

    bl_label = "Source"
    bl_parent_id = "CARBON_PT_sof"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        settings = context.object.carbon_sof
        layout = self.layout
        layout.use_property_split = True
        layout.prop(settings, "type_id")
        layout.prop(settings, "skin")
        layout.label(text="Resolved by tools-core", icon="INFO")


class CARBON_PT_sof_components(Panel):
    """Hull, faction and race -- the three a DNA is made of."""

    bl_label = "Components"
    bl_parent_id = "CARBON_PT_sof"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        settings = context.object.carbon_sof
        layout = self.layout
        layout.use_property_split = True
        layout.prop(settings, "hull")
        layout.prop(settings, "faction")
        layout.prop(settings, "race")
        layout.prop(settings, "pattern")


def draw_slot(layout, settings, entry, *, compact=False):
    """One material slot: what it is, where it came from, what it holds.

    Shared by both panels on purpose. The same slot NUMBER appears once per
    area type, so a label of "Material 1" alone renders the groups as
    indistinguishable duplicates -- which is exactly what they are not.
    """

    box = layout.box()
    header = box.row(align=True)
    # A slot the faction supplied looks identical to one the DNA asked for
    # once the colours are resolved, so the panel has to say which -- only one
    # of the two is the ship's own and survives a rebuild.
    from_dna = entry.source == sof_resolution.SOURCE_DNA
    label = ("Pattern " if entry.is_pattern else "Material ") + str(entry.index)
    header.label(text=label, icon="DECORATE_KEYFRAME" if from_dna else "DECORATE")
    name = header.row(align=True)
    name.alert = entry.material == CUSTOM_MATERIAL
    # A search field rather than a menu: 1149 materials is not a list anyone
    # scrolls, and the value stays the NAME, so a slot showing `custom` or a
    # material from a build we cannot reach still displays what it holds
    # instead of snapping to whatever happens to be first.
    if catalog_items():
        name.prop_search(entry, "material", bpy.context.window_manager,
                         "carbon_sof_materials", text="", icon="MATERIAL")
    else:
        name.prop(entry, "material", text="")
    if entry.material == CUSTOM_MATERIAL:
        header.operator("carbon.sof_export_material", text="", icon="EXPORT"
                        ).slot = f"{entry.index}:{int(entry.is_pattern)}:{entry.area_type}"
    if not entry.is_pattern and sof_resolution.is_blocked(entry.blocked, entry.index):
        # The one case where a slot ignores a skin: the hull author blocked it
        # on this area.
        box.label(text="this area blocks skin overrides here", icon="LOCKED")
    elif not entry.material:
        box.label(text="supplied by the faction " + (settings.faction or "?"))
    if compact:
        values = box.row(align=True)
        values.prop(entry, "diffuse", text="")
        values.prop(entry, "fresnel", text="")
        values.prop(entry, "gloss", text="")
    else:
        box.use_property_split = True
        box.prop(entry, "diffuse")
        box.prop(entry, "fresnel")
        box.prop(entry, "gloss")


def draw_material_groups(layout, settings, *, compact=False):
    """Every hull material slot, grouped by the AREA TYPE that owns it.

    The grouping is the point. Four slots per area type is what a faction
    actually stores, so a hull and its sails legitimately both have a
    "Material 3" holding different materials. Listing them flat makes that read
    as a duplicate rather than as two different things with the same number.
    """

    for area_type in settings.area_types():
        name = (sof_resolution.area_type_name(area_type) if area_type >= 0
                else "whole ship")
        column = layout.column(align=True)
        column.label(text=name.upper(), icon="MATERIAL")
        for entry in settings.materials:
            if entry.is_pattern or entry.area_type != area_type:
                continue
            draw_slot(column, settings, entry, compact=compact)


def draw_pattern_group(layout, settings, *, compact=False):
    """The two pattern layers, which are ship-wide on purpose.

    The pattern branch of the resolution chain never consults the area type, so
    these are the same on every area whose shader asks for them.
    """

    patterns = [entry for entry in settings.materials if entry.is_pattern]
    if not patterns:
        return
    column = layout.column(align=True)
    column.label(text="PATTERN (whole ship)", icon="TEXTURE")
    for entry in patterns:
        draw_slot(column, settings, entry, compact=compact)


class CARBON_PT_sof_materials(Panel):
    """The faction's materials, which every area of the hull reads."""

    bl_label = "Materials"
    bl_parent_id = "CARBON_PT_sof"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        settings = context.object.carbon_sof
        layout = self.layout
        if not settings.materials:
            layout.operator(CARBON_SOF_OT_ensure_slots.bl_idname, icon="ADD")
            return
        draw_material_groups(layout, settings)


class CARBON_PT_sof_patterns(Panel):
    """The two materials a SKIN's pattern paints over the hull."""

    bl_label = "Pattern Materials"
    bl_parent_id = "CARBON_PT_sof"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        settings = context.object.carbon_sof
        layout = self.layout
        draw_pattern_group(layout, settings)


CLASSES = (
    CARBON_SOF_Name,
    CARBON_SOF_Material,
    CARBON_SOF_Settings,
    CARBON_SOF_OT_apply,
    CARBON_SOF_OT_rebuild,
    CARBON_SOF_OT_export_material,
    CARBON_SOF_OT_ensure_slots,
    CARBON_PT_sof,
    CARBON_PT_sof_source,
    CARBON_PT_sof_components,
    CARBON_PT_sof_materials,
    CARBON_PT_sof_patterns,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.carbon_sof = PointerProperty(type=CARBON_SOF_Settings)
    # On the window manager, not the blend: the catalog belongs to a tools-core
    # build, and a copy saved into a file would go stale in it.
    bpy.types.WindowManager.carbon_sof_materials = CollectionProperty(type=CARBON_SOF_Name)


def unregister():
    if hasattr(bpy.types.WindowManager, "carbon_sof_materials"):
        del bpy.types.WindowManager.carbon_sof_materials
    if hasattr(bpy.types.Object, "carbon_sof"):
        del bpy.types.Object.carbon_sof
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
