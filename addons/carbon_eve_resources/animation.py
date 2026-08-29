"""Choosing which of a hull's animations plays.

A hull ships several and the loader picks `NormalLoop`, which is the right
default and is also the one that never moves: measured across the file, every
`*Loop` is a HELD POSE -- 150 curves and zero of them varying on a Kronos,
510 and zero on a Legion. The motion is in the TRANSITIONS. `StartSiege` and
`EndSiege` animate over 250 frames; a Legion's `Defensive2Sniper` varies 91 of
its 510 curves.

So "the hull will not animate" is usually the loader having done exactly what
it should. This is the switch.

It also sets the scene's frame range to the action's own. They do not match by
default -- the actions start at frame 0 and Blender starts at 1 -- so the first
frame of every transition was being skipped.
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, Panel, PropertyGroup


#: Held because Blender does not keep a reference to the strings a dynamic
#: `items` callback returns.
_ITEMS: list = []


def armature_of(context):
    """The armature of whatever is selected, or the only one in the file."""

    found = []
    for obj in (context.selected_objects or []):
        node = obj
        while node is not None:
            if node.type == "ARMATURE":
                found.append(node)
            for child in node.children:
                if child.type == "ARMATURE":
                    found.append(child)
            node = node.parent

    if not found:
        found = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    # The one with the most bones: a hull's rig against the shield sphere's
    # single bone, which is also an armature and never what anybody means.
    return max(found, key=lambda obj: len(obj.pose.bones), default=None)


def actions_for(armature):
    """Every action that belongs to this armature's own model.

    They are named `<model>.<Action>`, so the model is the part before the
    last dot and that is what groups them. Actions from another hull in the
    same file are not offered.
    """

    if armature is None:
        return []
    current = getattr(getattr(armature, "animation_data", None), "action", None)
    stem = current.name.rsplit(".", 1)[0] if current and "." in current.name else ""
    if not stem:
        return sorted(bpy.data.actions, key=lambda act: act.name)
    return sorted((act for act in bpy.data.actions
                   if act.name.rsplit(".", 1)[0] == stem),
                  key=lambda act: act.name)


def key_range(action):
    """`(first, last)` over every curve in the action, or None.

    Blender 5's actions are LAYERED: the curves live in a channelbag under a
    slot, and `Action.fcurves` does not exist any more. Reading it is an
    AttributeError, not an empty list.
    """

    keys = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for slot in getattr(action, "slots", []):
                bag = strip.channelbag(slot)
                if bag is None:
                    continue
                for curve in bag.fcurves:
                    for point in curve.keyframe_points:
                        keys.append(point.co[0])
    if not keys:
        return None
    return int(min(keys)), int(max(keys))


def moves(action) -> bool:
    """Whether anything in the action actually changes.

    A held pose has keys like any other action; what it does not have is a
    curve whose value differs between them.
    """

    span = key_range(action)
    if span is None:
        return False
    first, last = span
    if last <= first:
        return False
    frames = [first + (last - first) * step / 6.0 for step in range(7)]
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for slot in getattr(action, "slots", []):
                bag = strip.channelbag(slot)
                if bag is None:
                    continue
                for curve in bag.fcurves:
                    seen = {round(curve.evaluate(frame), 6) for frame in frames}
                    if len(seen) > 1:
                        return True
    return False


def action_items(self, context):
    global _ITEMS

    found = actions_for(armature_of(context))
    if not found:
        _ITEMS = [("", "No animations", "This hull carries none")]
        return _ITEMS

    _ITEMS = []
    for act in found:
        name = act.name.rsplit(".", 1)[-1]
        span = key_range(act)
        note = f"{span[0]}-{span[1]}" if span else "no keys"
        still = "" if moves(act) else "  (held pose)"
        _ITEMS.append((act.name, f"{name}{still}", f"frames {note}"))
    return _ITEMS


def _chosen(self, context):
    if self.action:
        bpy.ops.carbon.play_animation("EXEC_DEFAULT", action=self.action)


class CARBON_AnimationState(PropertyGroup):
    action: EnumProperty(
        name="Animation",
        description="Which of the hull's animations to pose it with. The "
                    "loops are held poses; the transitions are what move",
        items=action_items,
        update=_chosen,
    )


class CARBON_OT_play_animation(Operator):
    """Assign this animation to the hull and match the scene's frame range"""

    bl_idname = "carbon.play_animation"
    bl_label = "Use Animation"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def execute(self, context):
        armature = armature_of(context)
        act = bpy.data.actions.get(self.action)
        if armature is None or act is None:
            self.report({"ERROR"}, "No hull rig to animate")
            return {"CANCELLED"}

        if armature.animation_data is None:
            armature.animation_data_create()
        armature.animation_data.action = act
        # Blender 5 needs the SLOT as well as the action: an action assigned
        # without one evaluates to nothing at all.
        slots = list(getattr(act, "slots", []))
        if slots:
            armature.animation_data.action_slot = slots[0]

        span = key_range(act)
        if span is not None:
            # The actions start at frame 0 and Blender starts at 1, so without
            # this the first frame of every transition is skipped.
            context.scene.frame_start, context.scene.frame_end = span
            context.scene.frame_set(span[0])

        name = act.name.rsplit(".", 1)[-1]
        if not moves(act):
            self.report({"INFO"}, f"{name} is a held pose; it does not move")
        else:
            self.report({"INFO"}, f"{name}: frames {span[0]}-{span[1]}")
        return {"FINISHED"}


class CARBON_PT_sidebar_animation(Panel):
    """Which animation the hull is posed with."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarbonEngineJS"
    bl_label = "Animation"
    bl_idname = "CARBON_PT_sidebar_animation"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return armature_of(context) is not None

    def draw(self, context):
        layout = self.layout
        state = getattr(context.window_manager, "carbon_eve_animation", None)
        if state is None:
            layout.label(text="Not registered")
            return

        armature = armature_of(context)
        found = actions_for(armature)
        moving = sum(1 for act in found if moves(act))
        layout.label(text=f"{len(found)} animation(s), {moving} that move",
                     icon="ARMATURE_DATA")
        layout.prop(state, "action", text="")
        current = getattr(armature.animation_data, "action", None)
        if current is not None:
            span = key_range(current)
            layout.label(text=f"{current.name.rsplit('.', 1)[-1]}"
                              + (f"  {span[0]}-{span[1]}" if span else ""),
                         icon="PLAY")


CLASSES = (CARBON_AnimationState, CARBON_OT_play_animation,
           CARBON_PT_sidebar_animation)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.carbon_eve_animation = bpy.props.PointerProperty(
        type=CARBON_AnimationState)


def unregister():
    del bpy.types.WindowManager.carbon_eve_animation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
