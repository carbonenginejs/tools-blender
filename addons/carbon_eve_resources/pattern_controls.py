"""A per-hull panel for the SKIN pattern projections.

The quad shader reads a ship's two pattern projections from custom properties on
the OBJECT, through Attribute nodes, so every one of its area materials sees the
same values. That is the right place for the data -- Carbon keeps it in the
per-object constant buffer too -- but raw custom properties are a poor thing to
edit: a wrap mode shows as a bare float, a flip as a third vector component, and
nothing says which is which.

This gives them a real interface. Each control writes straight back to the
custom property the shader reads, so the panel is a view onto that one source of
truth rather than a second copy of it.

Values come from the SOF document when a ship is assembled; this is for
adjusting them afterwards, and for hulls posed by hand.
"""

from __future__ import annotations

import bpy

from .quad.nodes import MASK_PROPERTIES

MASK_COUNT = 2

#: Projection types as `EveSOFDataPatternLayer` authors them, in the order
#: `ToAddressMode` maps them: 0 REPEAT, 1 EDGE, 2 BORDER.
WRAP_ITEMS = (
    ("0", "Repeat", "Tile the projection"),
    ("1", "Clamp to edge", "Sample the edge outside the projection"),
    ("2", "Clamp to border", "Cover nothing outside the projection"),
)


def _property(obj, mask, key):
    return MASK_PROPERTIES[key].format(mask)


def _read(obj, mask, key, default):
    name = _property(obj, mask, key)
    if name not in obj.keys():
        return default
    value = obj[name]
    try:
        return tuple(value)
    except TypeError:
        return value


def _write(obj, mask, key, value):
    obj[_property(obj, mask, key)] = value
    # A custom property changing does not by itself redraw the shading, so the
    # viewport keeps showing the old projection until something else touches it.
    obj.update_tag()
    for area in getattr(bpy.context.screen, "areas", ()):
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _vector_getter(mask, key, index, default=0.0):
    def getter(self):
        return _read(self.id_data, mask, key, (default,) * 3)[index]
    return getter


def _vector_setter(mask, key, index):
    def setter(self, value):
        current = list(_read(self.id_data, mask, key, (0.0, 0.0, 0.0)))
        while len(current) < 3:
            current.append(0.0)
        current[index] = value
        _write(self.id_data, mask, key, tuple(current))
    return setter


def _make_mask_group(mask: int):
    """Builds one mask's PropertyGroup, bound to that mask's properties."""

    annotations = {}

    def vector(name, key, subtype, description):
        annotations[name] = bpy.props.FloatVectorProperty(
            name=name.replace("_", " ").title(), size=3, subtype=subtype,
            description=description,
            get=lambda self: _read(self.id_data, mask, key, (0.0, 0.0, 0.0))[:3],
            set=lambda self, value: _write(self.id_data, mask, key, tuple(value)),
        )

    vector("position", "position", "TRANSLATION", "Projection centre, in the hull's own space")
    vector("rotation", "rotation", "EULER", "Projection orientation")
    vector("scaling", "scaling", "XYZ", "Projection box size; the box maps to the texture once")

    annotations["mirrored"] = bpy.props.BoolProperty(
        name="Mirrored",
        description="Fold the projection about the hull's x = 0 plane",
        get=lambda self: bool(_read(self.id_data, mask, "mirrored", 0.0)),
        set=lambda self, value: _write(self.id_data, mask, "mirrored", 1.0 if value else 0.0),
    )

    for index, axis in enumerate("UV"):
        annotations[f"wrap_{axis.lower()}"] = bpy.props.EnumProperty(
            name=f"Wrap {axis}", items=WRAP_ITEMS,
            description=f"How the projection behaves outside the box along {axis}",
            get=(lambda i: lambda self: int(_read(self.id_data, mask, "wrap", (0.0,) * 3)[i]))(index),
            set=(lambda i: lambda self, value: _vector_setter(mask, "wrap", i)(self, float(value)))(index),
        )
        annotations[f"flip_{axis.lower()}"] = bpy.props.BoolProperty(
            name=f"Flip {axis}",
            description=("Mirror the sampled texture along this axis. V is flipped by "
                         "default: D3D texture space runs V downward and Blender's runs up"),
            get=(lambda i: lambda self: bool(_read(self.id_data, mask, "flip", (0.0,) * 3)[i]))(index),
            set=(lambda i: lambda self, value: _vector_setter(mask, "flip", i)(self, 1.0 if value else 0.0))(index),
        )

    for layer in range(4):
        key = "targets" if layer < 3 else "target4"
        annotations[f"target_{layer + 1}"] = bpy.props.BoolProperty(
            name=f"Material {layer + 1}",
            description=f"Let this projection paint over material layer {layer + 1}",
            get=(lambda l, k: lambda self: bool(
                _read(self.id_data, mask, k, (1.0,) * 4)[l] if k == "targets"
                else _read(self.id_data, mask, k, 1.0)))(layer if layer < 3 else 0, key),
            set=(lambda l, k: lambda self, value: (
                _vector_setter(mask, k, l)(self, 1.0 if value else 0.0) if k == "targets"
                else _write(self.id_data, mask, k, 1.0 if value else 0.0)))(layer if layer < 3 else 0, key),
        )

    return type(
        f"CarbonPatternMask{mask}",
        (bpy.types.PropertyGroup,),
        {"__annotations__": annotations},
    )


MASK_GROUPS = [_make_mask_group(mask) for mask in range(MASK_COUNT)]


class CARBON_PT_pattern_projections(bpy.types.Panel):
    """Per-hull SKIN pattern controls."""

    bl_label = "Carbon Pattern Projections"
    bl_idname = "CARBON_PT_pattern_projections"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None:
            return False
        return any(
            MASK_PROPERTIES["position"].format(mask) in obj.keys()
            for mask in range(MASK_COUNT)
        )

    def draw(self, context):
        layout = self.layout
        obj = context.object
        layout.label(text="Shared by every area material on this hull", icon="INFO")

        for mask in range(MASK_COUNT):
            if MASK_PROPERTIES["position"].format(mask) not in obj.keys():
                continue
            settings = getattr(obj, f"carbon_pattern_{mask}", None)
            if settings is None:
                continue

            box = layout.box()
            box.label(text=f"Pattern Material {mask + 1}")

            column = box.column(align=True)
            column.prop(settings, "position")
            column.prop(settings, "rotation")
            column.prop(settings, "scaling")

            row = box.row(align=True)
            row.prop(settings, "wrap_u")
            row.prop(settings, "wrap_v")

            row = box.row(align=True)
            row.prop(settings, "flip_u", toggle=True)
            row.prop(settings, "flip_v", toggle=True)
            row.prop(settings, "mirrored", toggle=True)

            targets = box.row(align=True)
            targets.label(text="Paints over:")
            for layer in range(4):
                targets.prop(settings, f"target_{layer + 1}", toggle=True)



#: The per-ship values, in the order they belong on a panel, with the label the
#: operator should see rather than the shader's own spelling.
SHIP_VALUES = (
    ("carbon_ship_age_weeks", "Age (weeks)"),
    ("carbon_ship_activation", "Activation"),
    ("carbon_ship_booster_gain", "Booster gain"),
    ("carbon_ship_emission_strength", "Emission strength"),
    ("carbon_ship_kill_count", "Kill count"),
)


class CARBON_PT_ship_values(bpy.types.Panel):
    """The values Carbon holds once per SHIP, not once per material.

    These have to be edited HERE. Every material carries a matching socket, but
    those sockets are DRIVEN from these properties, so a number typed into the
    shader editor is overwritten on the next frame and looks like the feature is
    broken -- which is exactly how it looked when dirt stopped responding.

    They live on the object because Carbon holds them per object: one age, one
    activation, one kill count for a hull and every decal on it.
    """

    bl_label = "Carbon Ship Values"
    bl_idname = "CARBON_PT_ship_values"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and any(name in obj.keys() for name, _ in SHIP_VALUES)

    def draw(self, context):
        obj = context.object
        layout = self.layout
        layout.use_property_split = True
        for name, label in SHIP_VALUES:
            if name in obj.keys():
                layout.prop(obj, f'["{name}"]', text=label)
        layout.label(text="Drives every material and decal on this ship",
                     icon="DRIVER")


CLASSES = tuple(MASK_GROUPS) + (CARBON_PT_pattern_projections,
                               CARBON_PT_ship_values)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for mask, group in enumerate(MASK_GROUPS):
        setattr(
            bpy.types.Object,
            f"carbon_pattern_{mask}",
            bpy.props.PointerProperty(type=group),
        )


def unregister():
    for mask in range(len(MASK_GROUPS)):
        name = f"carbon_pattern_{mask}"
        if hasattr(bpy.types.Object, name):
            delattr(bpy.types.Object, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
