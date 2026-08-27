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

from carbon_eve_resources.quad import decals as decal_module, load_family, nodes  # noqa: E402
import preview_quad  # noqa: E402

BATCHES = ("opaqueAreas", "transparentAreas", "additiveAreas", "distortionAreas")


# The ship builder lives in the ADD-ON, not here. The panel and this script
# must build the SAME ship: when each assembled its own way, decals came
# through on one path and not the other, and two hulls in one scene looked
# like different games.
from carbon_eve_resources import ship as ship_builder


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sof", required=True)
    parser.add_argument("--resources", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--environment", default="",
                        help="Equirectangular nebula for the world environment")
    parser.add_argument("--sun-strength", type=float, default=None,
                        help="Scales the star's intensity into Blender sun energy; "
                             "0 leaves the environment as the only light")
    parser.add_argument("--render", default="")
    parser.add_argument("--hull-record", default="",
                        help="A SOF hull record, whose decalSets name the decals "
                             "and carry their visibility groups. A built document "
                             "has neither.")
    return parser.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def main():
    args = parse_args(sys.argv)
    hull_record = {}
    if args.hull_record:
        with open(args.hull_record, encoding="utf-8") as handle:
            hull_record = json.load(handle) or {}
        print(f"  hull record: {len(hull_record.get('decalSets') or [])} decal set(s), "
              f"{len(hull_record.get('planeSets') or [])} plane set(s), "
              f"{len(hull_record.get('bannerSets') or [])} banner set(s)")
    decal_sets = hull_record.get("decalSets") or []

    primary = ship_builder.build_ship(
        args.sof, args.resources,
        globals_overrides={"previewGlowScale": preview_quad.DEMO_EMISSION_STRENGTH},
        decal_sets=decal_sets, hull_record=hull_record)
    ship_builder.hide_non_geometry()
    if primary is None:
        raise SystemExit("no geometry was assembled")

    preview_quad.ENVIRONMENT[:] = [args.environment] if args.environment else []
    if args.sun_strength is not None:
        preview_quad.SUN_SCALE[0] = args.sun_strength
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
