"""Open a CMF in an interactive Blender session from this source checkout."""

from __future__ import annotations

from pathlib import Path
import sys


def _source_argument() -> Path:
    try:
        separator = sys.argv.index("--")
        source = Path(sys.argv[separator + 1])
    except (ValueError, IndexError) as error:
        raise SystemExit("usage: blender --python scripts/open_cmf_in_blender.py -- <file.cmf>") from error
    if not source.is_file():
        raise SystemExit(f"CMF file not found: {source}")
    return source


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
for package in ("carbon-cmf", "carbon-granny", "carbon-gr2", "carbon-gsf"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import bpy  # noqa: E402


source = _source_argument()
bpy.ops.preferences.addon_enable(module="carbon_eve_resources")

# Start with an uncluttered scene so the imported geometry is the immediate
# subject of the viewport rather than Blender's default cube/camera/light.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

before = set(bpy.data.objects)
status = bpy.ops.import_scene.carbon_cmf(filepath=str(source))
if status != {"FINISHED"}:
    raise RuntimeError(f"CMF import failed: {status}")

created = [item for item in bpy.data.objects if item not in before]
if not created:
    raise RuntimeError("CMF import did not create any Blender objects")

bpy.ops.object.select_all(action="DESELECT")
for item in created:
    item.select_set(True)
bpy.context.view_layer.objects.active = next(
    (item for item in created if item.type == "MESH"),
    created[0],
)
bpy.context.scene["carbon_cmf_source"] = str(source)


def _frame_imported_objects():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            space = area.spaces.active
            space.shading.type = "SOLID"
            with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
                bpy.ops.view3d.view_selected(use_all_regions=False)
            space.region_3d.view_distance *= 1.15
    return None


bpy.app.timers.register(_frame_imported_objects, first_interval=0.25)
print(f"CMF_INTERACTIVE_OPEN_OK {source} ({len(created)} objects)")
