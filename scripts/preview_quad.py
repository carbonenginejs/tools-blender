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
    parser.add_argument("--render", default="")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def sof_effect(path, member_name):
    """Finds the Tr2Effect for one family member in a SOF document.

    Blender never composes SOF itself -- `tools-core` does that and emits the
    document, which this only reads. Fetch one with::

        curl "http://127.0.0.1:5510/eve/<build>/sof/dna/<dna>" -o ship.json

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

    if "EmissionStrength" in group.inputs:
        group.inputs["EmissionStrength"].default_value = DEMO_EMISSION_STRENGTH

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
            socket = group.inputs.get(name)
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
    light = bpy.data.objects.new("Sun", light_data)
    light.rotation_euler = (math.radians(55), 0.0, math.radians(35))
    bpy.context.collection.objects.link(light)

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.025, 0.04, 1.0)
    bpy.context.scene.world = world
    add_glare()


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
