"""Assembles a whole ship from a SOF document: geometry, areas and materials.

Where `preview_quad.py` puts one material on one mesh, this builds what a real
hull needs -- one material per `Tr2MeshArea`, each with its own authored
constants and textures, routed onto the geometry's index groups.

Run with Blender's own Python::

    blender --background --factory-startup --python scripts/preview_ship.py -- \\
        --sof ship.json --resources <dir> [--out x.blend] [--render x.png]

`--resources` is a directory holding the geometry and textures the document
references, plus a `manifest.json` mapping each `res:/` path to a local file.

Three things this exists to demonstrate, each of which is easy to get wrong:

* one hull uses SEVERAL family members at once -- a Legion is `quadv5`,
  `quadheatv5` and `quadsailsv5` together -- so a single material cannot
  describe it;
* two areas can share a member and still differ, so the node GROUP is shared
  while the MATERIAL is per area;
* area `index`/`count` address the geometry's index groups, which the GR2
  importer has already turned into material slots in the same order.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ADDONS = os.path.join(os.path.dirname(HERE), "addons")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ADDONS not in sys.path:
    sys.path.insert(0, ADDONS)

from carbon_eve_resources.quad import load_family, nodes  # noqa: E402
import preview_quad  # noqa: E402

BATCHES = ("opaqueAreas", "transparentAreas", "additiveAreas", "distortionAreas")


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sof", required=True)
    parser.add_argument("--resources", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--render", default="")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def load_document(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def find_meshes(document):
    """Every Tr2Mesh in the document, in the order it appears."""

    meshes = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") in ("Tr2Mesh", "Tr2InstancedMesh"):
                meshes.append(node)
            for value in node.values():
                walk(value)

    walk(document)
    return meshes


def import_geometry(path):
    import io_scene_carbon_gr2

    try:
        io_scene_carbon_gr2.register()
    except Exception:
        pass  # already registered
    before = set(bpy.data.objects)
    bpy.ops.import_scene.carbon_gr2(filepath=path)
    created = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    return created


def build_area_material(area, family, resources, index):
    """One material for one mesh area, from its own effect."""

    effect = area.get("effect") or {}
    shader = str(effect.get("effectFilePath", ""))
    member = family.member(shader)
    if member is None:
        return None, f"{area.get('name')}: no measured member for {shader.rsplit('/', 1)[-1]}"

    tree = nodes.build_group(member)
    material = bpy.data.materials.new(f"{index:02d} {area.get('name') or member.name}")
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    group = mnodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.location = (0, 0)
    mlinks.new(group.outputs["BSDF"], output.inputs["Surface"])

    row = 900
    for resource in effect.get("resources", []):
        name, path = resource.get("name"), resource.get("resourcePath")
        socket = group.inputs.get(name)
        local = resources.get(path)
        if socket is None or not local or not os.path.exists(local):
            continue
        image = bpy.data.images.load(local, check_existing=True)
        image.colorspace_settings.name = (
            "sRGB" if member.annotation(name).srgb else "Non-Color"
        )
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-700, row)
        node.label = name
        mlinks.new(node.outputs["Color"], socket)

        scale = member.annotation(name).uv_scale
        if scale != 1.0:
            coord = mnodes.new("ShaderNodeTexCoord")
            coord.location = (-1200, row)
            mapping = mnodes.new("ShaderNodeMapping")
            mapping.location = (-1000, row)
            mapping.inputs["Scale"].default_value = (scale, scale, scale)
            mlinks.new(coord.outputs["UV"], mapping.inputs["Vector"])
            mlinks.new(mapping.outputs["Vector"], node.inputs["Vector"])
        if name == "DustNoiseMap" and nodes.DUST_ALPHA in group.inputs:
            mlinks.new(node.outputs["Alpha"], group.inputs[nodes.DUST_ALPHA])
        row -= 300

    preview_quad.fill_unbound_textures(member, group, mnodes, mlinks, row)

    for constant in effect.get("constParameters", []):
        name, value = constant.get("name"), constant.get("value") or []
        socket = group.inputs.get(nodes.socket_name(name))
        if socket is None or not value:
            continue
        if socket.type == "RGBA":
            socket.default_value = tuple(value[:3]) + (1.0,)
        else:
            socket.default_value = float(value[0])

    if "EmissionStrength" in group.inputs:
        group.inputs["EmissionStrength"].default_value = preview_quad.DEMO_EMISSION_STRENGTH

    return material, None


def assemble(args):
    document = load_document(args.sof)
    with open(os.path.join(args.resources, "manifest.json"), encoding="utf-8") as handle:
        resources = json.load(handle)
    family = load_family()

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    meshes = find_meshes(document)
    print(f"document has {len(meshes)} mesh(es)")

    primary = None
    warnings = []
    for mesh in meshes:
        path = mesh.get("geometryResPath")
        local = resources.get(path)
        if not local or not os.path.exists(local):
            warnings.append(f"geometry not downloaded: {path}")
            continue

        objects = import_geometry(local)
        if not objects:
            warnings.append(f"{path}: importer created no mesh")
            continue
        target = objects[0]
        if primary is None:
            primary = target
        print(f"\n{os.path.basename(path)} -> {target.name}, "
              f"{len(target.data.materials)} index group(s)")

        # Areas address index groups, and the importer made one slot per group
        # in the same order, so the slot index is the area index.
        areas = [(batch, area) for batch in BATCHES for area in (mesh.get(batch) or [])]
        slots = len(target.data.materials)
        for batch, area in areas:
            index = area.get("index")
            material, problem = build_area_material(area, family, resources, index or 0)
            if problem:
                warnings.append(problem)
                continue
            if not isinstance(index, int) or index >= slots:
                warnings.append(f"{area.get('name')}: index {index} outside {slots} slots")
                continue
            for offset in range(max(1, area.get("count") or 1)):
                if index + offset < slots:
                    target.data.materials[index + offset] = material
            fx = str(area.get("effect", {}).get("effectFilePath", "")).rsplit("/", 1)[-1]
            print(f"  [{batch[:-5]:11}] slot {index} <- {material.name}   ({fx})")

    for warning in warnings:
        print(f"  ! {warning}")
    return primary


def main():
    args = parse_args(sys.argv)
    primary = assemble(args)
    if primary is None:
        raise SystemExit("no geometry was assembled")

    preview_quad.frame(primary)

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
