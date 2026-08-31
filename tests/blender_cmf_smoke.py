"""Headless Blender smoke test for the public CMF file-import operator."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def _source_argument() -> Path:
    try:
        separator = sys.argv.index("--")
        source = Path(sys.argv[separator + 1])
    except (ValueError, IndexError) as error:
        raise SystemExit("usage: blender --background --python tests/blender_cmf_smoke.py -- <file.cmf>") from error
    if not source.is_file():
        raise SystemExit(f"CMF fixture not found: {source}")
    return source


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
for package in ("carbon-cmf", "carbon-granny", "carbon-gr2", "carbon-gsf"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import bpy  # noqa: E402


source = _source_argument()
bpy.ops.preferences.addon_enable(module="carbon_eve_resources")
before = set(bpy.data.objects)
status = bpy.ops.import_scene.carbon_cmf(filepath=str(source))
assert status == {"FINISHED"}, status

created = [item for item in bpy.data.objects if item not in before]
meshes = [item for item in created if item.type == "MESH"]
armatures = [item for item in created if item.type == "ARMATURE"]
assert meshes or armatures, "CMF import produced neither meshes nor an armature"
for mesh in meshes:
    assert len(mesh.data.vertices) > 0
    assert len(mesh.data.polygons) > 0
for armature in armatures:
    assert len(armature.data.bones) > 0

print(
    "CMF_BLENDER_OPERATOR_SMOKE_OK",
    json.dumps(
        {
            "source": str(source),
            "meshes": [
                {
                    "name": mesh.name,
                    "vertices": len(mesh.data.vertices),
                    "triangles": len(mesh.data.polygons),
                }
                for mesh in meshes
            ],
            "armatures": [
                {"name": armature.name, "bones": len(armature.data.bones)}
                for armature in armatures
            ],
        },
        sort_keys=True,
    ),
)
