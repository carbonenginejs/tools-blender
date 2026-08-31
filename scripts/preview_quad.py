"""Builds a quad material on a mesh and renders it, to see the group working.

This is a development preview, not part of the add-on. It generates the node
group from the measured interface, wires a folder of EVE textures into it by
filename suffix, frames the object and renders.

Run with Blender's own Python::

    blender --background --factory-startup --python scripts/preview_quad.py -- \\
        --textures <dir> [--mesh <file.blend>] [--object <name>]
        [--member quadv5.fx] [--out <file.blend>] [--render <file.png>]

Two things this exists to demonstrate, because both were wrong at some point:

* the four material layers are selected by `MaterialMap`, so a hull with four
  distinct layer colours shows its regions rather than one flat tint;
* the normal map is two-channel with an implicit Z, and is Non-Color -- loading
  it as sRGB is invisible until the lighting looks subtly flat.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import bpy
import mathutils


ADDONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addons")
if ADDONS not in sys.path:
    sys.path.insert(0, ADDONS)

from carbon_eve_resources.quad import load_family, nodes  # noqa: E402


#: EVE's texture filename suffixes. A convention, not something the container
#: states, so it is kept here rather than in the add-on.
SUFFIXES = {
    "_a": "AlbedoMap",
    "_d": "DirtMap",
    "_g": "GlowMap",
    "_m": "MaterialMap",
    "_n": "NormalMap",
    "_p3": "PaintMaskMap",
    "_p": "PaintMaskMap",
    "_r": "RoughnessMap",
}

EXTENSIONS = (".png", ".dds", ".tga")

#: Distinct enough to show which region each material layer owns.
#: The glow map is a few tiny window strips and Carbon relies on the client's
#: bloom, so the faithful pow(glow, 2.4) is far below anything visible at 1.0.
DEMO_EMISSION_STRENGTH = 12.0

DEMO_COLORS = {
    "Mtl1DiffuseColor": (0.05, 0.12, 0.30, 1.0),
    "Mtl2DiffuseColor": (0.55, 0.45, 0.20, 1.0),
    "Mtl3DiffuseColor": (0.60, 0.60, 0.62, 1.0),
    "Mtl4DiffuseColor": (0.25, 0.05, 0.05, 1.0),
}


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--textures", required=True)
    parser.add_argument("--noise", default="")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--mesh", default="")
    parser.add_argument("--object", default="")
    parser.add_argument("--member", default="quadv5.fx")
    parser.add_argument("--sof", default="",
                        help="A carbon.document JSON from tools-core, to drive the "
                             "material from a real ship's authored values")
    parser.add_argument("--out", default="")
    parser.add_argument("--environment", default="",
                        help="Equirectangular nebula for the world environment")
    parser.add_argument("--sun-strength", type=float, default=SUN_SCALE[0],
                        help="Scales the star's relative intensity into Blender sun energy; "
                             "0 leaves the environment as the only light")
    parser.add_argument("--render", default="")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def sof_effect(path, member_name):
    """Finds the Tr2Effect for one family member in a SOF document.

    Blender never composes SOF itself. This preview reads a `carbon.document`
    obtained from the same hosted service as the installed add-on.

    The constants live under `constParameters`, not `parameters`; the latter
    exists and is empty, which reads as "this ship has no material values".
    """

    import json

    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)

    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") == "Tr2Effect" and member_name in str(node.get("effectFilePath", "")):
                found.append(node)
            for value in node.values():
                walk(value)

    walk(document)
    return found[0] if found else None


def texture_prefix(directory, given):
    if given:
        return given
    for entry in sorted(os.listdir(directory)):
        stem, extension = os.path.splitext(entry)
        if extension.lower() in EXTENSIONS and "_" in stem:
            return stem.rsplit("_", 1)[0] + "_"
    return ""


def find(directory, prefix, suffix):
    for extension in EXTENSIONS:
        candidate = os.path.join(directory, f"{prefix.rstrip('_')}{suffix}{extension}")
        if os.path.exists(candidate):
            return candidate
    return None


def build(args):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    hull = None
    if args.mesh:
        with bpy.data.libraries.load(args.mesh, link=False) as (src, dst):
            wanted = [args.object] if args.object else list(src.objects)
            dst.objects = [name for name in src.objects if name in wanted]
        for obj in dst.objects:
            if obj is None:
                continue
            bpy.context.collection.objects.link(obj)
            if obj.type == "MESH" and hull is None:
                hull = obj
    if hull is None:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0)
        hull = bpy.context.active_object

    member = load_family().member(args.member)
    if member is None:
        raise SystemExit(f"Unknown family member: {args.member}")
    tree = nodes.build_group(member)

    material = bpy.data.materials.new(f"Carbon {member.name}")
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    group = mnodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.location = (0, 0)
    mlinks.new(group.outputs["BSDF"], output.inputs["Surface"])

    prefix = texture_prefix(args.textures, args.prefix)
    row = 900
    for suffix, socket in SUFFIXES.items():
        if socket not in group.inputs:
            continue
        path = find(args.textures, prefix, suffix)
        if not path:
            continue
        image = bpy.data.images.load(path, check_existing=True)
        # Carbon states this: only AlbedoMap is sRGB among authored textures.
        image.colorspace_settings.name = (
            "sRGB" if member.annotation(socket).srgb else "Non-Color"
        )
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-600, row)
        node.label = socket
        mlinks.new(node.outputs["Color"], group.inputs[socket])
        print(f"  {os.path.basename(path)} -> {socket}"
              f" ({image.colorspace_settings.name})")
        row -= 300

    if args.noise and "DustNoiseMap" in group.inputs and os.path.exists(args.noise):
        image = bpy.data.images.load(args.noise, check_existing=True)
        image.colorspace_settings.name = "Non-Color"
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-600, row)
        node.label = "DustNoiseMap"
        coord = mnodes.new("ShaderNodeTexCoord")
        coord.location = (-1100, row)
        mapping = mnodes.new("ShaderNodeMapping")
        mapping.location = (-900, row)
        # The container declares this scale; it is the same 20 that appears as a
        # literal in the emitted GLSL.
        scale = member.annotation("DustNoiseMap").uv_scale
        mapping.inputs["Scale"].default_value = (scale, scale, scale)
        mlinks.new(coord.outputs["UV"], mapping.inputs["Vector"])
        mlinks.new(mapping.outputs["Vector"], node.inputs["Vector"])
        mlinks.new(node.outputs["Color"], group.inputs["DustNoiseMap"])
        if "DustNoiseAlpha" in group.inputs:
            mlinks.new(node.outputs["Alpha"], group.inputs["DustNoiseAlpha"])
        print(f"  {os.path.basename(args.noise)} -> DustNoiseMap (UV x{scale:g})")

    fill_unbound_textures(member, group, mnodes, mlinks, row)

    if "previewGlowScale" in group.inputs:
        group.inputs["previewGlowScale"].default_value = DEMO_EMISSION_STRENGTH

    effect = sof_effect(args.sof, member.name) if args.sof else None
    if effect is None:
        for name, colour in DEMO_COLORS.items():
            if name in group.inputs:
                group.inputs[name].default_value = colour
        print("  using demo colours; pass --sof for a ship's authored values")
    else:
        applied = 0
        for constant in effect.get("constParameters", []):
            name, value = constant.get("name"), constant.get("value") or []
            # Some constants are exposed under a clearer socket name, so a
            # document's Carbon name has to be translated or it stops applying
            # without saying so.
            socket = group.inputs.get(nodes.socket_name(name))
            if socket is None or not value:
                continue
            if socket.type == "RGBA":
                socket.default_value = tuple(value[:3]) + (1.0,)
            else:
                # Only .x is read; the remaining lanes are padding.
                socket.default_value = float(value[0])
            applied += 1
        options = ", ".join(f"{o['name']}={o['value']}" for o in effect.get("options", []))
        print(f"  applied {applied} authored constants from {os.path.basename(args.sof)}")
        print(f"  the ship's own options: {options}")

    hull.data.materials.clear()
    hull.data.materials.append(material)
    return hull


BLACK_IMAGE = "carbon_black"


def black_image():
    """A shared 1x1 black image, for texture slots with nothing authored.

    A SOF document with no SKIN resolves its pattern masks to
    `res:/texture/global/black.dds`, so black is EVE's own neutral rather than a
    stand-in: a mask of zero covers nothing. Generated rather than downloaded so
    the preview works offline.
    """

    existing = bpy.data.images.get(BLACK_IMAGE)
    if existing:
        return existing
    image = bpy.data.images.new(BLACK_IMAGE, width=1, height=1, alpha=True)
    image.generated_color = (0.0, 0.0, 0.0, 1.0)
    image.pixels = [0.0, 0.0, 0.0, 1.0]
    image.colorspace_settings.name = "Non-Color"
    return image


def fill_unbound_textures(member, group, mnodes, mlinks, row):
    """Gives every unfilled texture slot a labelled black image node.

    The socket defaults are already black, so this changes nothing visually. It
    makes the material self-describing instead: every map the shader binds is
    present as a node a user can point at a file, which is the same reason the
    add-on already creates unconnected nodes for Carbon-only maps.

    Colourspace comes from Carbon's `Tr2sRGB` annotation, so the pattern masks
    -- which do not carry it -- land as Non-Color, as do all the other masks.
    """

    filled = []
    for texture in member.textures:
        socket = group.inputs.get(texture)
        if socket is None or socket.is_linked:
            continue
        node = mnodes.new("ShaderNodeTexImage")
        node.image = black_image()
        node.location = (-600, row)
        node.label = texture
        # Per-node, so pointing this at a real file keeps the right space.
        node.image.colorspace_settings.name = (
            "sRGB" if member.annotation(texture).srgb else "Non-Color"
        )
        mlinks.new(node.outputs["Color"], socket)
        filled.append(texture)
        row -= 300
    if filled:
        print(f"  black (Non-Color) for unauthored: {', '.join(filled)}")


def frame(hull):
    bpy.context.view_layer.update()
    corners = [hull.matrix_world @ mathutils.Vector(c) for c in hull.bound_box]
    centre = sum(corners, mathutils.Vector((0, 0, 0))) / len(corners)
    radius = max((c - centre).length for c in corners) or 1.0

    camera_data = bpy.data.cameras.new("Camera")
    # EVE hulls are hundreds to thousands of units long. The default 1000-unit
    # far clip puts a battleship entirely behind the far plane and renders an
    # empty frame that looks exactly like a broken material.
    camera_data.clip_start = radius * 0.01
    camera_data.clip_end = radius * 20.0
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    camera.location = centre + mathutils.Vector((radius * 1.6, -radius * 1.9, radius * 0.9))
    camera.rotation_euler = (centre - camera.location).normalized().to_track_quat("-Z", "Y").to_euler()

    light_data = bpy.data.lights.new("Sun", type="SUN")
    light_data.energy = 4.0
    SUN_LIGHT.append(light_data)
    light = bpy.data.objects.new("Sun", light_data)
    light.rotation_euler = (math.radians(55), 0.0, math.radians(35))
    bpy.context.collection.objects.link(light)

    image, manifest = read_environment(ENVIRONMENT[0] if ENVIRONMENT else "")
    set_world(image, scene_settings=(manifest or {}).get("scene"))
    if manifest:
        apply_sun(manifest)
    set_viewport_clipping(radius)
    add_glare()


#: Set by a caller to use an EVE nebula as the world environment.
ENVIRONMENT = []

#: The scene's sun, so a system's star can recolour it after framing.
SUN_LIGHT = []

#: How much of EVE's relative star intensity to give Blender's sun.
#:
#: EVE's `intensity` is a relative luminosity number, not the irradiance in
#: W/m2 that Blender's sun strength means, so it cannot be used raw -- only the
#: RATIO between two stars is meaningful. Kept deliberately low because the
#: environment probe carries most of a quad surface's light: a sun strong
#: enough to be the main light blows out the material and loses the nebula,
#: which is what an earlier 3.0 here did.
SUN_SCALE = [0.4]


def apply_sun(manifest):
    """Colours the sun from the system's own star.

    tools-core derives both from the star record, so this only applies them:
    colour from the blackbody temperature, intensity from the luminosity curve.
    Blender's sun strength is irradiance in W/m2 rather than EVE's relative
    number, so the intensity scales a sensible default rather than being used
    raw -- the ratio between two stars is meaningful, the absolute value is not.
    """

    sun = (manifest or {}).get("sun") or {}
    colour = sun.get("color") or [1.0, 1.0, 1.0]
    intensity = float(sun.get("intensity") or 1.0)
    for data in SUN_LIGHT:
        data.color = tuple(colour[:3])
        data.energy = SUN_SCALE[0] * intensity

    # sunDirection is the direction light TRAVELS -- GetPerFrameSunDirection
    # negates it to get the shader's Sun.DirWorld, which points at the light.
    # EVE is Y-up and Blender Z-up, the same quarter turn the GR2 importer
    # applies, so (x, y, z) becomes (x, -z, y).
    travel = sun.get("travel")
    if travel and len(travel) >= 3:
        import mathutils
        direction = mathutils.Vector((travel[0], -travel[2], travel[1])).normalized()
        for data in SUN_LIGHT:
            for obj in bpy.data.objects:
                if obj.data is data:
                    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        print(f"  sun travels {tuple(round(v, 3) for v in direction)}")
    system = (manifest or {}).get("system") or {}
    print(f"  sun: {system.get('name', '?')} {sun.get('star') or ''} "
          f"colour {[round(c, 3) for c in colour[:3]]} intensity {intensity:.3f} "
          f"-> energy {SUN_SCALE[0] * intensity:.2f}")


def read_environment(path):
    """Resolves --environment to an image and the system's sun, if given a dir.

    An environment directory may contain an `environment.hdr` beside an
    `environment.json` holding the star's colour and intensity. Accepting
    either the directory or the image keeps the common case one argument.
    """

    import json
    import os

    if not path:
        return "", None
    if os.path.isdir(path):
        manifest_path = os.path.join(path, "environment.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            image = os.path.join(path, manifest.get("environment", "environment.hdr"))
            return image, manifest
        for name in ("environment.hdr", "environment.png"):
            candidate = os.path.join(path, name)
            if os.path.exists(candidate):
                return candidate, None
        return "", None
    return path, None


def set_world(environment_path="", strength=1.0, scene_settings=None):
    """The scene's environment, from an EVE nebula when one is given.

    This is where most of a hull's light comes from. Carbon samples its
    environment cube twice -- once along the reflection vector for specular and
    once along the normal for irradiance -- and scales both by
    `ReflectionIntensity`; that is split-sum image-based lighting, and Blender
    already does it. So the nebula is handed over as a world texture and Cycles
    or EEVEE light the surface with it, rather than the probe being
    reimplemented in nodes.

    Without one, dark materials with high gloss and bright fresnel -- which
    describes most of an EVE hull -- have nothing to reflect and read as flat
    and far too dark.
    """

    import os

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    tree = world.node_tree
    background = tree.nodes["Background"]
    background.inputs[1].default_value = strength

    if environment_path and os.path.exists(environment_path):
        image = bpy.data.images.load(environment_path, check_existing=True)
        image.colorspace_settings.name = "Linear Rec.709"
        texture = tree.nodes.new("ShaderNodeTexEnvironment")
        texture.image = image
        texture.location = (-300, 0)
        # A Mapping node so the nebula can be turned without re-exporting it:
        # EVE is Y-up and Blender Z-up, and which way a nebula faces is a scene
        # decision rather than a property of the cube.
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (-500, 0)
        coordinate = tree.nodes.new("ShaderNodeTexCoord")
        coordinate.location = (-700, 0)
        tree.links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], background.inputs[0])

        # Carbon scales the visible nebula and the environment samples by
        # DIFFERENT amounts -- nebulaIntensity for the backdrop and
        # reflectionIntensity, the shader's cb2[14].w, for both probe taps. One
        # Blender world serves both, so Is Camera Ray picks which applies:
        # what the camera sees directly against what surfaces reflect.
        settings = scene_settings or {}
        nebula = float(settings.get("nebulaIntensity") or strength)
        reflection = float(settings.get("reflectionIntensity") or strength)
        if nebula != reflection:
            path = tree.nodes.new("ShaderNodeLightPath")
            path.location = (-500, 300)
            pick = tree.nodes.new("ShaderNodeMix")
            pick.data_type = "FLOAT"
            pick.location = (-300, 300)
            pick.label = "camera vs reflected"
            tree.links.new(path.outputs["Is Camera Ray"], pick.inputs["Factor"])
            pick.inputs[2].default_value = reflection
            pick.inputs[3].default_value = nebula
            tree.links.new(pick.outputs[0], background.inputs[1])
            print(f"  intensity: nebula {nebula:g} seen, {reflection:g} reflected")
        else:
            background.inputs[1].default_value = nebula
        print(f"  world environment: {os.path.basename(environment_path)}")
    else:
        background.inputs[0].default_value = (0.02, 0.025, 0.04, 1.0)

    bpy.context.scene.world = world


#: EVE hulls run from tens of units to well over a thousand, and a station is
#: larger again. Blender's 1000-unit default viewport far clip hides most of a
#: battleship the moment you orbit out.
VIEWPORT_CLIP_END = 10000.0


def set_viewport_clipping(radius=0.0):
    """Opens up the 3D viewport's clipping range in the saved file.

    This is a SEPARATE setting from the camera's, and fixing only the camera
    leaves renders correct while the interactive viewport still clips -- which
    reads as a broken import rather than a view setting.
    """

    far = max(VIEWPORT_CLIP_END, radius * 20.0)
    near = max(far / 1e6, 0.1)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.clip_start = near
                    space.clip_end = far
    print(f"  viewport clipping {near:g} to {far:g}")


def add_glare():
    """Blooms the emissive windows in the compositor.

    Carbon's glow is a handful of very small, very bright window strips -- the
    glow map's mean is under 0.01 -- and the client blooms them. EEVEE has no
    bloom setting any more, so without a compositor glare the emission is
    present, correct, and effectively invisible against a lit hull. That looks
    exactly like a disconnected glow, and was reported as one.
    """

    scene = bpy.context.scene

    # Blender 5.0 moved compositing to a node group on the scene, and the Glare
    # node's settings became input sockets rather than properties. Both differ
    # from 4.x, so this is written against 5.0 and skipped elsewhere.
    if not hasattr(scene, "compositing_node_group"):
        print("  no compositor glare: this Blender predates compositing_node_group")
        return

    group = bpy.data.node_groups.new("Preview compositing", "CompositorNodeTree")
    scene.compositing_node_group = group

    # The render arrives through a Render Layers node inside the group, not
    # through the group's own input. Wiring a bare group input instead yields
    # its default -- white -- and blows the whole frame out, which looks like a
    # runaway glare rather than an unconnected image.
    group.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    render = group.nodes.new("CompositorNodeRLayers")
    render.location = (-300, 0)
    # 5.0 has no Composite node inside the group; it ends at a Group Output.
    node_out = group.nodes.new("NodeGroupOutput")
    node_out.location = (300, 0)
    glare = group.nodes.new("CompositorNodeGlare")
    glare.location = (0, 0)

    # These are menu sockets taking the display string, not an enum identifier,
    # and a wrong value fails silently and leaves the default -- which is
    # Streaks, at a size that smears the frame to white. So report rather than
    # swallow.
    for socket, setting in (("Type", "Bloom"), ("Quality", "High"),
                            ("Threshold", 0.4), ("Strength", 0.6), ("Size", 0.7)):
        if socket not in glare.inputs:
            print(f"  glare has no {socket!r} socket")
            continue
        try:
            glare.inputs[socket].default_value = setting
        except (TypeError, AttributeError) as error:
            print(f"  glare {socket}={setting!r} rejected: {error}")

    group.links.new(render.outputs["Image"], glare.inputs["Image"])
    group.links.new(glare.outputs["Image"], node_out.inputs[0])
    print("  compositor glare added (bloom)")


def main():
    args = parse_args(sys.argv)
    hull = build(args)
    frame(hull)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 600

    if args.out:
        bpy.ops.wm.save_as_mainfile(filepath=args.out)
        print("saved", args.out)
    if args.render:
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
        print("rendered", args.render)


if __name__ == "__main__":
    main()
