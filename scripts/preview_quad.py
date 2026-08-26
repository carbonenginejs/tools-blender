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
    parser.add_argument("--out", default="")
    parser.add_argument("--render", default="")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


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

    for name, colour in DEMO_COLORS.items():
        if name in group.inputs:
            group.inputs[name].default_value = colour

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
