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

from . import sof_panels

CATEGORY = "CarbonEngineJS"


def ships_in_file():
    """Every ship in the blend, in name order."""

    return [obj for obj in bpy.data.objects
            if getattr(obj, "carbon_sof", None) is not None and obj.carbon_sof.dna]


def _ship_of(context):
    """The ship the SELECTION belongs to, or None.

    A consumer clicks a decal far more often than the hull, so walking up to
    the ship is what makes the panels usable at all -- without it every tool
    would read as empty for most selections.

    What it must NOT do is fall back to whichever ship happens to come first in
    the file. It used to, and with two ships open every edit landed on the same
    one however carefully the other was selected -- an attribute editor that
    changes a ship the person is not touching.

    The one honest exception is a file holding exactly one ship: then there is
    nothing to be ambiguous about, and requiring a selection would only be
    pedantry.
    """

    obj = context.object if context else None
    while obj is not None:
        if getattr(obj, "carbon_sof", None) is not None and obj.carbon_sof.dna:
            return obj
        obj = obj.parent

    ships = ships_in_file()
    return ships[0] if len(ships) == 1 else None


def _no_ship(layout, context):
    """Says which of the two reasons there is no ship to show."""

    if len(ships_in_file()) > 1:
        # Different problem, different remedy: there IS a ship, and the panel
        # is refusing to guess which.
        layout.label(text="Select part of a ship", icon="RESTRICT_SELECT_ON")
    else:
        layout.label(text="No ship in the scene", icon="INFO")


class CARBON_PT_sidebar_about(Panel):
    """The add-on itself, and the licence its use depends on.

    First in the tab because accepting the terms gates everything below it:
    without acceptance the tools cannot fetch anything, and a person who has
    not accepted needs to be told that before they wonder why nothing loads.
    """

    bl_label = "CarbonEngineJS"
    bl_idname = "CARBON_PT_sidebar_about"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_order = 0

    def draw(self, context):
        from . import addon

        # One row, two states. The terms, the revision and the acceptance date
        # are all in Preferences already; repeating them here made a panel that
        # is read once into the tallest thing in the tab.
        prefs = addon._prefs(context)
        accepted = addon._creator_terms_accepted(prefs)
        row = self.layout.row(align=True)
        # First person, because it is the person's statement and not a label
        # on a document: ticking it IS the acceptance.
        row.label(text="I accept the EVE Online Creator License",
                  icon="CHECKBOX_HLT" if accepted else "CHECKBOX_DEHLT")
        # The terms themselves, one click away rather than quoted at length.
        row.operator(addon.EVE_RESOURCE_OT_open_creator_terms.bl_idname,
                     text="", icon="URL")
        # Revoke lives in Preferences: destructive, and it sat beside a link.
        if not accepted:
            row.operator(addon.EVE_RESOURCE_OT_accept_creator_terms.bl_idname,
                         text="Accept")

        # The cache is content-addressed, so an updated file lands beside the
        # one it replaces. Prune drops what no kept build references; Clear
        # drops every payload.
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        if state is None:
            return
        cache = self.layout.row(align=True)
        cache.label(text=state.cache_summary, icon="DISK_DRIVE")
        cache.operator(addon.EVE_RESOURCE_OT_refresh_cache_stats.bl_idname,
                       text="", icon="FILE_REFRESH")
        cache.operator(addon.EVE_RESOURCE_OT_prune_cache.bl_idname,
                       text="", icon="SORTTIME")
        cache.operator(addon.EVE_RESOURCE_OT_clear_cache.bl_idname,
                       text="", icon="TRASH")

        # Colour space, here rather than only in Preferences: it is a
        # WORKING choice. Blender's AgX default desaturates EVE's colours, and
        # a person comparing against the game flips this while comparing.
        space = self.layout.row(align=True)
        space.label(text="Colour space")
        space.prop(prefs, "view_transform_mode", expand=True)

        # Sprite size, beside the colour space for the same reason: it is a
        # judgement somebody makes while looking at a hull, and it applies on
        # the spot rather than at the next load. Zero draws none.
        sprites = self.layout.row(align=True)
        sprites.prop(prefs, "sprite_scale")
        sprites.prop(prefs, "sprite_glow")

        # Banners: a tick and a path each. Somebody's own artwork, instead of
        # the logo fetched for whoever owns the ship -- which is what a person
        # wants when the ship belongs to nobody yet, or when they are showing
        # a design rather than a character's actual corp.
        banners = self.layout.box()
        banners.label(text="Banners")
        for switch, field in (("use_corp_banner", "corp_banner"),
                              ("use_alliance_banner", "alliance_banner")):
            row = banners.row(align=True)
            row.prop(prefs, switch)
            path = row.row(align=True)
            # The path stays visible when the tick is off: turning it off must
            # not lose what somebody typed.
            path.enabled = getattr(prefs, switch)
            path.prop(prefs, field, text="")

        # The local folders are in Preferences, not here. They are set up
        # once and then left alone, and two path fields in a sidebar is a lot
        # of room for something nobody touches twice.

        # Handing the ship over: model, blend and textures in one folder.
        from . import export as export_module

        self.layout.operator(export_module.CARBON_OT_save_standalone.bl_idname,
                             icon="PACKAGE")


class CARBON_PT_sidebar_dna(Panel):
    """Composing a DNA, and loading a ship from it.

    The DNA string and the parts below it are two views of one thing, and each
    fills the other.

    Not a SOF editor: this names SOF records, it does not edit them.
    """

    bl_label = "SOF DNA Builder"
    bl_idname = "CARBON_PT_sidebar_dna"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        # No animation decorators; none of this is keyframed.
        layout.use_property_decorate = False
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        ship = _ship_of(context)
        # The loaded ship's settings, else the scene's scratch SOF so a DNA
        # can be composed before anything is loaded.
        settings = ship.carbon_sof if ship is not None else context.scene.carbon_sof

        # Name, then the ids it resolves to, then the DNA. Each fills the
        # ones below, and any can be filled in directly.
        sof_panels.draw_name_search(layout, settings, "ship_name",
                                    kind="ships", text="Ship",
                                    icon="OUTLINER_OB_MESH")
        ids = layout.row(align=True)
        ids.prop(settings, "type_id", text="Type")
        ids.prop(settings, "skin_id", text="Skin")
        layout.prop(settings, "dna", text="DNA")
        required = layout.column(align=True)
        for field, kind, icon in (("hull", "hulls", "MESH_DATA"),
                                  ("faction", "factions", "COMMUNITY"),
                                  ("race", "races", "WORLD")):
            sof_panels.draw_name_search(required, settings, field, kind=kind,
                                        text=field.title(), icon=icon)

        _command(layout, settings, "use_mesh", "carbon_cmd_mesh",
                 [f"mesh_material{index}" for index in (1, 2, 3, 4)])
        _command(layout, settings, "use_pattern", "carbon_cmd_pattern",
                 ["pattern", "pattern_material5", "pattern_material6"])
        _command(layout, settings, "use_respath", "carbon_cmd_respath",
                 ["respath_insert"])
        _command(layout, settings, "use_layout", "carbon_cmd_layout",
                 ["layout_names"])

        # Load builds the DNA into a ship; Refresh re-resolves the materials
        # of the ship already here. The arrow forces a fresh bundle.
        buttons = layout.row(align=True)
        load = buttons.operator("carbon.eve_resource_build_sof_dna",
                                text="Load Ship", icon="IMPORT")
        load.dna = settings.dna
        load.refresh = False
        again = buttons.operator("carbon.eve_resource_build_sof_dna",
                                 text="", icon="FILE_REFRESH")
        again.dna = settings.dna
        again.refresh = True
        actions = layout.row(align=True)
        actions.operator("carbon.sof_apply", text="Refresh Materials",
                         icon="MATERIAL")
        actions.operator("carbon.sof_select_ship", text="", icon="RESTRICT_SELECT_OFF")

        if state is None:
            layout.label(text="Resource browser is not registered", icon="ERROR")
            return
        # Progress WHILE running, and the outcome after -- an error most of
        # all. Showing the status only while busy meant every failure was
        # written and then immediately hidden, so a load that did not work
        # said nothing at all about why.
        if state.status:
            failed = state.status.lower().startswith("error")
            row = layout.row()
            row.alert = failed
            row.label(text=state.status,
                      icon="ERROR" if failed else
                      ("SORTTIME" if state.busy else "CHECKMARK"))
        if state.busy:
            layout.operator("carbon.eve_resource_cancel", icon="X")


def _command(layout, settings, toggle, idname, fields):
    """One optional DNA command: a switch, and its arguments beneath it.

    The switch is the command's PRESENCE: off means absent from the DNA, which
    is not the same as arguments that are all `none`.
    """

    header, body = _panel(layout, idname)
    if header is None:
        body = layout.column(align=True)
    target = header if header is not None else body
    # Left-aligned: a property split floats the checkbox mid-panel.
    target.use_property_split = False
    target.prop(settings, toggle)
    if body is None:
        return
    body.enabled = bool(getattr(settings, toggle))
    body.use_property_split = True
    body.use_property_decorate = False
    for field in fields:
        if field.endswith(("material1", "material2", "material3", "material4",
                           "material5", "material6")):
            sof_panels.draw_name_search(body, settings, field, kind="materials",
                                        text=field.split("_")[-1].title(),
                                        icon="MATERIAL")
        elif field == "pattern":
            sof_panels.draw_name_search(body, settings, field, kind="patterns",
                                        text="Pattern", icon="TEXTURE")
        else:
            # respathinsert has no catalog route on tools-core -- what is legit
            # is per hull, and depends on which texture files actually exist --
            # and layouts are a `;` separated list. Both stay text.
            body.prop(settings, field)


def _panel(layout, idname):
    """`(header, body)` where the UI folds, `(None, None)` where it does not."""

    maker = getattr(layout, "panel", None)
    if maker is None:
        return None, None
    return maker(idname, default_closed=True)


class CARBON_PT_sidebar_attributes(Panel):
    """What a hull shares across everything on it."""

    bl_label = "Attribute Editor"
    bl_idname = "CARBON_PT_sidebar_attributes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    #: The per-ship values, and what to call them. Carbon's own names, and
    #: only Carbon's values: two preview-only multipliers used to sit here,
    #: both correct at 1 and neither worth a control.
    VALUES = (
        ("carbon_ship_age_weeks", "Age in weeks"),
        ("carbon_ship_booster_gain", "boosterGain"),
        ("carbon_ship_activation_strength", "activationStrength"),
        ("carbon_ship_kill_count", "killCount"),
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        ship = _ship_of(context)
        if ship is None:
            _no_ship(layout, context)
            return
        for name, label in self.VALUES:
            if name in ship.keys():
                layout.prop(ship, f'["{name}"]', text=label)


CLASSES = (
    CARBON_PT_sidebar_about,
    CARBON_PT_sidebar_dna,
    CARBON_PT_sidebar_attributes,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
