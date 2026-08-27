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


def push_to_materials(obj, values):
    """Writes `{socket name: value}` into every quad group the ship uses.

    Sockets are addressed by NAME, never by index: which sockets a member binds
    depends on which quad it is, so `quadsailsv5` and `quadv5` do not agree on
    position. A name a material does not have is skipped rather than guessed at.
    """

    written = 0
    seen = set()
    for target in ship_objects(obj):
        for slot in getattr(target, "material_slots", []):
            material = slot.material
            if material is None or not material.use_nodes or material.name in seen:
                continue
            seen.add(material.name)
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


def _material_update(self, context):
    """Pushes one slot's values to the ship it belongs to."""

    obj = getattr(self, "id_data", None)
    if not isinstance(obj, bpy.types.Object):
        return
    sockets = PATTERN_SOCKETS if self.is_pattern else MATERIAL_SOCKETS
    push_to_materials(obj, {
        sockets["diffuse"].format(self.index): tuple(self.diffuse) + (1.0,),
        sockets["gloss"].format(self.index): self.gloss,
        sockets["fresnel"].format(self.index): tuple(self.fresnel) + (1.0,),
    })


class CARBON_SOF_Material(PropertyGroup):
    """One material slot, as the FACTION authors it.

    EVE's colours are HDR -- authored above one, three times normal is common --
    so these carry a soft maximum rather than a hard clamp.
    """

    index: IntProperty(default=1, min=1, max=4)
    is_pattern: BoolProperty(default=False)
    diffuse: FloatVectorProperty(
        name="Diffuse", subtype="COLOR", size=3, min=0.0, soft_max=1.0,
        default=(0.0, 0.0, 0.0), update=_material_update,
        description="Authored above 1 for HDR; 3x normal is common")
    fresnel: FloatVectorProperty(
        name="Fresnel", subtype="COLOR", size=3, min=0.0, soft_max=1.0,
        default=(0.04, 0.04, 0.04), update=_material_update,
        description="F0, the reflectance looking straight on")
    gloss: FloatProperty(
        name="Gloss", default=0.5, min=0.0, max=1.0, update=_material_update)


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
        """`hull:faction:race`, with the pattern appended when there is one.

        The spelling a consumer already uses, so a DNA built here can be pasted
        straight into the loader.
        """

        parts = [self.hull, self.faction, self.race]
        if not any(parts):
            return ""
        dna = ":".join(part or "" for part in parts)
        if self.pattern:
            dna = dna + ":pattern?" + self.pattern
        return dna

    def slot(self, index, pattern=False):
        for entry in self.materials:
            if entry.index == index and entry.is_pattern == pattern:
                return entry
        return None


class CARBON_SOF_OT_apply(Operator):
    """Pushes every value to the ship, for when a rebuild has drifted."""

    bl_idname = "carbon.sof_apply"
    bl_label = "Apply to Ship"
    bl_description = "Write every SOF value into this hull and all its children"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        settings = obj.carbon_sof
        values = {}
        for entry in settings.materials:
            sockets = PATTERN_SOCKETS if entry.is_pattern else MATERIAL_SOCKETS
            values[sockets["diffuse"].format(entry.index)] = tuple(entry.diffuse) + (1.0,)
            values[sockets["gloss"].format(entry.index)] = entry.gloss
            values[sockets["fresnel"].format(entry.index)] = tuple(entry.fresnel) + (1.0,)
        written = push_to_materials(obj, values)
        objects = len(ship_objects(obj))
        self.report({"INFO"}, "Wrote %d socket(s) across %d object(s)" % (written, objects))
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
        for entry in settings.materials:
            if entry.is_pattern:
                continue
            box = layout.box()
            box.label(text="Material %d" % entry.index)
            box.use_property_split = True
            box.prop(entry, "diffuse")
            box.prop(entry, "fresnel")
            box.prop(entry, "gloss")


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
        for entry in settings.materials:
            if not entry.is_pattern:
                continue
            box = layout.box()
            box.label(text="Pattern Material %d" % entry.index)
            box.use_property_split = True
            box.prop(entry, "diffuse")
            box.prop(entry, "fresnel")
            box.prop(entry, "gloss")


CLASSES = (
    CARBON_SOF_Material,
    CARBON_SOF_Settings,
    CARBON_SOF_OT_apply,
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


def unregister():
    if hasattr(bpy.types.Object, "carbon_sof"):
        del bpy.types.Object.carbon_sof
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
