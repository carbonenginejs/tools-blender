"""The four tools, as their own tab in the 3D view sidebar.

Blender gives an add-on three places to put a menu, and they are not
interchangeable:

- the **Properties editor**, tied to whatever is selected. Right for editing one
  object's values, which is where the SOF panels already live.
- the **3D view sidebar** (the N panel), under a tab of our own. Right for tools
  a person reaches for while working -- they stay put as the selection changes.
- a true **menu**, appended to an existing one. Right for commands, wrong for
  anything with state to show.

The four tools want the sidebar: they are used while looking at a ship, and
three of the four are not about the selected object at all.

Each panel here shows what the tool owns and nothing else. What edits a value
edits the SOF; the scene follows.
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from . import sof_panels, sof_resolution

CATEGORY = "Carbon"


def _ship_of(context):
    """The ship the selection belongs to, or None.

    A consumer clicks a decal far more often than the hull, so walking up to the
    ship is what makes the panels usable at all -- without it every tool would
    read as empty for most selections.
    """

    obj = context.object
    while obj is not None:
        if getattr(obj, "carbon_sof", None) is not None and obj.carbon_sof.dna:
            return obj
        obj = obj.parent
    for candidate in bpy.data.objects:
        settings = getattr(candidate, "carbon_sof", None)
        if settings is not None and settings.dna:
            return candidate
    return None


class CARBON_PT_sidebar_dna(Panel):
    """Composing a DNA, and loading a ship from one."""

    bl_label = "SOF DNA Builder"
    bl_idname = "CARBON_PT_sidebar_dna"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        ship = _ship_of(context)

        if ship is not None:
            row = layout.row()
            row.enabled = False
            row.prop(ship.carbon_sof, "dna", text="Loaded")

        if state is None:
            layout.label(text="Resource browser is not registered", icon="ERROR")
            return
        layout.prop(state, "dna", text="DNA")
        build = layout.row()
        build.enabled = not state.busy
        build.operator("carbon.eve_resource_build_sof_dna", icon="PLAY")
        if state.status:
            layout.label(text=state.status, icon="INFO")


class CARBON_PT_sidebar_sof(Panel):
    """The SOF a ship is built from."""

    bl_label = "SOF Editor"
    bl_idname = "CARBON_PT_sidebar_sof"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        ship = _ship_of(context)
        if ship is None:
            layout.label(text="No ship in the scene", icon="INFO")
            return

        settings = ship.carbon_sof
        layout.label(text=ship.name.split("_")[0], icon="OUTLINER_OB_ARMATURE")
        for field in ("hull", "faction", "race", "pattern"):
            layout.prop(settings, field)
        row = layout.row()
        row.enabled = False
        row.prop(settings, "dna", text="DNA")
        # Two different jobs, and confusing them wastes a rebuild. `Apply`
        # pushes what is already here down to the children; `Rebuild` asks
        # tools-core what the edited SOF actually resolves to, which is the
        # only way a faction change can reach the scene at all.
        buttons = layout.row(align=True)
        buttons.operator("carbon.sof_apply", text="Apply", icon="FILE_REFRESH")
        buttons.operator("carbon.sof_rebuild", text="Rebuild", icon="PLAY")


class CARBON_PT_sidebar_materials(Panel):
    """The faction's materials, which every area of the hull reads."""

    bl_label = "Materials"
    bl_parent_id = "CARBON_PT_sidebar_sof"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        ship = _ship_of(context)
        if ship is None:
            return
        # The MATERIAL is what a consumer picks; the colours are what it holds.
        # Leading with the colours would suggest they are the thing being
        # chosen, which is a material editor's model rather than the SOF's.
        #
        # Grouped by AREA TYPE, because that is how a faction stores them:
        # four material names keyed `areaType:slot`. The hull and the sails are
        # separate groups even when they resolve to the same colours today.
        settings = ship.carbon_sof
        for area_type in settings.area_types():
            column = layout.column(align=True)
            name = (sof_resolution.area_type_name(area_type) if area_type >= 0
                    else "whole ship")
            header = column.row()
            header.label(text=name.upper(), icon="MATERIAL")
            for entry in settings.materials:
                if entry.is_pattern or entry.area_type != area_type:
                    continue
                _draw_slot(column, ship, entry)

        patterns = [entry for entry in settings.materials if entry.is_pattern]
        if patterns:
            column = layout.column(align=True)
            # Ship-wide on purpose: the pattern branch of the resolution chain
            # never consults the area type, so these are the same on every area
            # whose shader asks for them.
            column.label(text="PATTERN (whole ship)", icon="TEXTURE")
            for entry in patterns:
                _draw_slot(column, ship, entry)


def _draw_slot(layout, ship, entry):
    """One material slot: what it is, where it came from, what it holds."""

    box = layout.box()
    header = box.row(align=True)
    # A slot the faction supplied looks identical to one the DNA asked for
    # once the colours are resolved, so the panel has to say which -- only
    # one of the two is the ship's own and survives a rebuild.
    from_dna = entry.source == sof_resolution.SOURCE_DNA
    label = ("Pattern " if entry.is_pattern else "Material ") + str(entry.index)
    header.label(text=label, icon="DECORATE_KEYFRAME" if from_dna else "DECORATE")
    name = header.row(align=True)
    name.alert = entry.material == sof_panels.CUSTOM_MATERIAL
    name.prop(entry, "material", text="")
    if entry.material == sof_panels.CUSTOM_MATERIAL:
        header.operator("carbon.sof_export_material", text="", icon="EXPORT"
                        ).slot = f"{entry.index}:{int(entry.is_pattern)}:{entry.area_type}"
    if not entry.is_pattern and sof_resolution.is_blocked(entry.blocked, entry.index):
        # Worth saying, because it is the one case where a slot ignores the
        # skin: the hull author blocked it on this area.
        box.label(text="this area blocks skin overrides here", icon="LOCKED")
    elif not entry.material:
        box.label(text="supplied by the faction " + (ship.carbon_sof.faction or "?"))
    values = box.row(align=True)
    values.prop(entry, "diffuse", text="")
    values.prop(entry, "fresnel", text="")
    values.prop(entry, "gloss", text="")


class CARBON_PT_sidebar_attributes(Panel):
    """What a hull shares across everything on it."""

    bl_label = "Attribute Editor"
    bl_idname = "CARBON_PT_sidebar_attributes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    #: The per-ship values, and what to call them. Carbon's own names, except
    #: the two marked preview -- those have no Carbon counterpart.
    VALUES = (
        ("carbon_ship_age_weeks", "Age in weeks"),
        ("carbon_ship_booster_gain", "boosterGain"),
        ("carbon_ship_activation_strength", "activationStrength"),
        ("carbon_ship_kill_count", "killCount"),
        ("carbon_preview_glow_scale", "Glow scale (preview)"),
        ("carbon_preview_banner_scale", "Banner scale (preview)"),
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        ship = _ship_of(context)
        if ship is None:
            layout.label(text="No ship in the scene", icon="INFO")
            return
        for name, label in self.VALUES:
            if name in ship.keys():
                layout.prop(ship, f'["{name}"]', text=label)
        layout.label(text="Drives every material and decal on this ship", icon="DRIVER")


class CARBON_PT_sidebar_types(Panel):
    """Items and skins."""

    bl_label = "Type Browser"
    bl_idname = "CARBON_PT_sidebar_types"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        ship = _ship_of(context)
        if ship is not None:
            layout.prop(ship.carbon_sof, "type_id")
            layout.prop(ship.carbon_sof, "skin")
        # Honest about what is not built yet, rather than showing a control that
        # does nothing: tools-core has no route that LISTS types, so a browser
        # has to be built on the skin library's name index.
        layout.label(text="Lookup is not wired yet", icon="INFO")


CLASSES = (
    CARBON_PT_sidebar_dna,
    CARBON_PT_sidebar_sof,
    CARBON_PT_sidebar_materials,
    CARBON_PT_sidebar_attributes,
    CARBON_PT_sidebar_types,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
