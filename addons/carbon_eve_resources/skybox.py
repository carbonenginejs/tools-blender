"""The nebula behind the ship, as Blender's world.

Most of what lights a quad surface comes from the environment, so a hull lit by
a flat grey reads as far too dark however right its materials are. This puts
the real sky behind it.

The choice offered is the REGION rather than the system. A nebula belongs to
the region -- every system in The Forge sees the same sky -- so 114 named
regions say everything 8490 systems would, and the service confirms it by
reporting which region a system's nebula came from.

Three conversions stand between the cube and the world, and all of them are in
`dds/environment.py`: BC6H, which Blender does not read; cube to
equirectangular, which the Environment Texture node wants; and float to
Radiance `.hdr`, because the values run well above one and a PNG would clip
exactly the bright detail a sky is for. That work runs in a child process and
is cached by content, so a region costs about twenty seconds once and nothing
ever again.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import service_access
from .core import nebula, resindex, sof_fetch


#: The enum's items, held because Blender does NOT keep a reference to the
#: strings a dynamic `items` callback returns. Letting them be collected shows
#: up as mangled labels, or a crash.
_ITEMS: list = []

#: The node names, so a rebuild replaces its own work rather than stacking.
ENVIRONMENT_NODE = "CarbonNebula"


def region_items(self, context):
    """Every region that has a sky, by name."""

    global _ITEMS
    client = service_access.client(context)
    found = nebula.regions(client)
    if not found:
        _ITEMS = [("", "No regions loaded", "The service is unreachable")]
        return _ITEMS
    _ITEMS = [(str(identifier), name, f"Nebula {nebula_id}")
              for identifier, name, nebula_id in found]
    return _ITEMS


def _region_update(self, context):
    """Picking a region IS the request. There is nothing else to confirm.

    The button stays, for rebuilding after a cache clear, but choosing from
    the list and then having to press something else was read -- correctly --
    as the choice not working.
    """

    if self.region:
        bpy.ops.carbon.apply_skybox("INVOKE_DEFAULT")


def _strength_update(self, context):
    """Live, so the brightness can be dialled without rebuilding the sky.

    A NAMED function, not a lambda. A lambda written in the class body reports
    its frame as `<string>` and cannot see this module's globals, so it raises
    NameError on every change.
    """

    world = context.scene.world if context and context.scene else None
    tree = getattr(world, "node_tree", None)
    if tree is None:
        return
    for node in tree.nodes:
        if node.type == "BACKGROUND":
            node.inputs["Strength"].default_value = self.strength


class CARBON_SkyboxState(PropertyGroup):
    region: EnumProperty(
        name="Region",
        description="Whose sky to put behind the ship. Every system in a "
                    "region shares one nebula",
        items=region_items,
        update=_region_update,
    )
    strength: FloatProperty(
        name="Strength",
        description="How brightly the nebula lights the scene. The cube is "
                    "authored below one and the client applies the scene's "
                    "own nebulaIntensity on top",
        default=1.0, min=0.0, soft_max=10.0,
        update=_strength_update,
    )
    use_sun: BoolProperty(
        name="Sun",
        description="Add a sun lamp aimed out of the nebula's brightest "
                    "point. The nebula names no sun direction, so this is "
                    "read from the sky itself",
        default=True,
    )
    status: StringProperty(default="")


def _cache_root(context):
    from .addon import _cache_path, _prefs

    return _cache_path(_prefs(context))


def apply_world(scene, image, strength: float):
    """Points the scene's world at one equirectangular image.

    Its own world, named for the add-on, so an artist's existing world is left
    alone and a second region replaces the first rather than adding to it.
    """

    world = scene.world
    if world is None or not world.name.startswith("CarbonNebula"):
        world = bpy.data.worlds.get("CarbonNebula") or bpy.data.worlds.new("CarbonNebula")
        scene.world = world

    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (300, 0)
    background = tree.nodes.new("ShaderNodeBackground")
    background.location = (100, 0)
    background.inputs["Strength"].default_value = strength

    texture = tree.nodes.new("ShaderNodeTexEnvironment")
    texture.name = texture.label = ENVIRONMENT_NODE
    texture.location = (-200, 0)
    texture.image = image
    # An HDR carries light, not colour: it must not be pushed through a
    # transfer curve on the way in.
    if image is not None:
        try:
            image.colorspace_settings.name = "Linear Rec.709"
        except TypeError:                # older colour configurations
            image.colorspace_settings.name = "Linear"

    tree.links.new(texture.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    return world


def build_environment(client, nebula_id: int, cache_root, *, progress=None):
    """The nebula's cube, fetched and converted. Returns the `.hdr` path.

    Runs on the JOB thread: no `bpy` and no scene changes in here, only the
    fetch and the child process. What comes back is applied on the main thread
    by `finish_job`.
    """

    from .dds import reader as dds_reader, worker

    path = nebula.cube_path(client, nebula_id)
    if not path:
        raise RuntimeError("that region names no nebula cube")

    if progress is not None:
        progress(f"Fetching {Path(path).name}")
    build = str((client.request_json("GET", "/eve/latest/build")
                 or {}).get("build") or "")
    index = resindex.load(cache_root, build) if build else None
    source = sof_fetch.fetch_resource(path, client, cache_root, build=build,
                                      index=index)

    # Beside the cube in our cache, addressed by the cube's CONTENT, so the
    # regions that share a nebula share the conversion too.
    destination = dds_reader.derived_path(source, ".hdr")
    if destination.is_file() and destination.stat().st_size > 0:
        return destination, path

    if not worker.convert_environment(source, destination, progress=progress):
        # The child could not run. Do it here rather than refuse: this thread
        # holds the GIL while it works, so the window will stutter, but the
        # artist gets their sky.
        from .dds import environment as cube

        cube.convert_file(source, destination)
    return destination, path


#: The sun lamp this add-on owns, so it is replaced rather than duplicated.
SUN_OBJECT = "CarbonSun"

#: How strong the key light is, in watts per square metre.
#:
#: A hull is metres across now that the importer no longer shrinks it by a
#: hundredth, so this is a real irradiance rather than a number that only
#: worked at one scale.
SUN_STRENGTH = 4.0

#: How wide the sun is on the sky, in degrees. EVE's suns are hard-edged;
#: about a degree gives a shadow with an edge rather than a smear.
SUN_ANGLE = 1.0


def apply_sun(scene, direction, colour):
    """Points a sun lamp the way the sky says the light comes from.

    `direction` is the direction the light TRAVELS, which is what Blender's
    sun points along -- its -Z axis. A lamp is created if this add-on has not
    made one; an artist's own lights are left alone.
    """

    import mathutils

    lamp = bpy.data.lights.get(SUN_OBJECT)
    if lamp is None:
        lamp = bpy.data.lights.new(SUN_OBJECT, "SUN")
    lamp.color = tuple(colour[:3])
    lamp.energy = SUN_STRENGTH
    lamp.angle = math.radians(SUN_ANGLE)

    obj = bpy.data.objects.get(SUN_OBJECT)
    if obj is None or obj.data is not lamp:
        obj = bpy.data.objects.new(SUN_OBJECT, lamp)
    if obj.name not in scene.collection.objects:
        try:
            scene.collection.objects.link(obj)
        except RuntimeError:
            pass                         # already linked somewhere in the scene

    travel = mathutils.Vector(direction)
    if travel.length == 0.0:
        travel = mathutils.Vector((0.0, 0.0, -1.0))
    # A sun shines down its own -Z, so the rotation is the one that takes -Z
    # onto the direction of travel.
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = travel.normalized().to_track_quat("-Z", "Y")
    return obj


def finish_job(context, result) -> str:
    """Applies a finished conversion. MAIN thread only."""

    from .dds import environment as cube

    name, (destination, path) = result
    state = context.window_manager.carbon_eve_skybox
    image = bpy.data.images.get(destination.name)
    if image is None:
        image = bpy.data.images.load(str(destination))
    apply_world(context.scene, image, state.strength)

    sun = cube.read_sun(destination)
    if sun is not None and state.use_sun:
        apply_sun(context.scene, *sun)

    state.status = f"{name}: {Path(path).name}"
    return f"Nebula set to {name}"


class CARBON_OT_apply_skybox(Operator):
    """Fetch the region's nebula and put it behind the ship"""

    bl_idname = "carbon.apply_skybox"
    bl_label = "Set Nebula"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import addon

        state = context.window_manager.carbon_eve_skybox
        if not state.region:
            self.report({"ERROR"}, "Choose a region first")
            return {"CANCELLED"}

        client = service_access.client(context)
        if client is None:
            self.report({"ERROR"}, "The CarbonEngineJS service is unreachable")
            return {"CANCELLED"}

        chosen = int(state.region)
        row = next((row for row in nebula.regions(client) if row[0] == chosen),
                   None)
        if row is None:
            self.report({"ERROR"}, "That region is no longer listed")
            return {"CANCELLED"}
        name, nebula_id = row[1], row[2]

        cache_root = _cache_root(context)

        def work():
            return name, build_environment(client, nebula_id, cache_root,
                                           progress=addon._set_progress)

        try:
            # The same background job the ships use. A nebula is six faces of
            # block decoding, over a minute of it, and doing that in front of
            # the artist is what made picking a region look like it did
            # nothing at all.
            addon._launch_job(context, "skybox", work, f"Building {name}'s sky")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class CARBON_PT_sidebar_skybox(Panel):
    """The nebula picker, in the add-on's own tab."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarbonEngineJS"
    bl_label = "Nebula"
    bl_idname = "CARBON_PT_sidebar_skybox"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        state = getattr(context.window_manager, "carbon_eve_skybox", None)
        if state is None:
            layout.label(text="Not registered")
            return

        layout.prop(state, "region")
        layout.prop(state, "strength")
        layout.prop(state, "use_sun")
        layout.operator(CARBON_OT_apply_skybox.bl_idname, icon="WORLD")
        if state.status:
            layout.label(text=state.status, icon="CHECKMARK")


CLASSES = (CARBON_SkyboxState, CARBON_OT_apply_skybox, CARBON_PT_sidebar_skybox)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.carbon_eve_skybox = bpy.props.PointerProperty(
        type=CARBON_SkyboxState)


def unregister():
    del bpy.types.WindowManager.carbon_eve_skybox
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
