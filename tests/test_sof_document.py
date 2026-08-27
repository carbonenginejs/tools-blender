import json
from pathlib import Path
import sys
import tempfile
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.sof_document import (  # noqa: E402
    SofDocumentError,
    load_sof_bundle,
    load_sof_document,
    parse_sof_document,
)
from carbon_eve_resources.sof_shading import (  # noqa: E402
    plan_material,
    should_build_material,
)


def _document():
    """Mirrors the node shape runtime-sof emits for a single-hull ship."""

    return {
        "schema": "carbon.document",
        "version": 1,
        "format": {"id": "runtime-sof", "version": 1},
        "roots": [{"name": "default", "ref": {"$ref": 20}}],
        "nodes": [
            {"id": 1, "kind": "TriTextureParameter", "fields": {
                "name": "AlbedoMap",
                "resourcePath": "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_a.dds",
            }},
            {"id": 2, "kind": "TriTextureParameter", "fields": {
                "name": "NormalMap",
                "resourcePath": "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_n.dds",
            }},
            {"id": 3, "kind": "TriTextureParameter", "fields": {
                "name": "PaintMaskMap",
                "resourcePath": "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_p3.dds",
            }},
            {"id": 4, "kind": "Tr2ConstantEffectParameter", "fields": {
                "name": "GeneralData",
                "value": [1, 0, 0, 0],
            }},
            {"id": 5, "kind": "Tr2ShaderOption", "fields": {
                "name": "SPACE_OBJECT_TRANSPARENCY",
                "value": "SOT_OPAQUE",
            }},
            {"id": 6, "kind": "Tr2Effect", "fields": {
                "name": "area_hull",
                "effectFilePath": "res:/graphics/effect/managed/space/spaceobject/v5/quad/quadv5.fx",
                "resources": [{"$ref": 1}, {"$ref": 2}, {"$ref": 3}],
                "constParameters": [{"$ref": 4}],
                "options": [{"$ref": 5}],
            }},
            {"id": 7, "kind": "Tr2MeshArea", "fields": {
                "name": "area_hull",
                "index": 0,
                "count": 2,
                "castsShadows": True,
                "effect": {"$ref": 6},
            }},
            {"id": 8, "kind": "Tr2Effect", "fields": {
                "name": "area_glass",
                "effectFilePath": "res:/graphics/effect/managed/space/spaceobject/v5/quad/quadglassv5.fx",
                "resources": [{"$ref": 1}],
                "constParameters": [],
                "options": [],
            }},
            {"id": 9, "kind": "Tr2MeshArea", "fields": {
                "name": "area_glass",
                "index": 2,
                "count": 1,
                "effect": {"$ref": 8},
            }},
            {"id": 10, "kind": "Tr2MeshArea", "fields": {
                "name": "area_glass_depth",
                "index": 2,
                "count": 1,
                "effect": {"$ref": 8},
            }},
            {"id": 11, "kind": "Tr2Mesh", "fields": {
                "geometryResPath": "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1.gr2",
                "opaqueAreas": [{"$ref": 7}],
                "transparentAreas": [{"$ref": 9}],
                "additiveAreas": [],
                "distortionAreas": [],
                "depthAreas": [{"$ref": 10}],
            }},
            {"id": 12, "kind": "Tr2Mesh", "fields": {
                "geometryResPath": "res:/graphics/generic/unitsphere/unitsphere_shieldgeo_01a.gr2",
                "opaqueAreas": [],
                "additiveAreas": [],
            }},
            {"id": 20, "kind": "EveShip2", "fields": {
                "dna": "cf1_t1:caldaribase:caldari",
                "mesh": {"$ref": 11},
                "children": [{"$ref": 12}],
            }},
        ],
    }


class ParseSofDocumentTests(unittest.TestCase):
    def test_projects_dna_root_and_meshes(self):
        assembly = parse_sof_document(_document())

        self.assertEqual(assembly.dna, "cf1_t1:caldaribase:caldari")
        self.assertEqual(assembly.root_kind, "EveShip2")
        self.assertEqual(len(assembly.meshes), 2)
        self.assertEqual(assembly.primary_mesh.geometry_path,
                         "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1.gr2")
        self.assertEqual([mesh.role for mesh in assembly.meshes], ["primary", "secondary"])

    def test_projects_areas_with_batch_slots_and_effect_data(self):
        mesh = parse_sof_document(_document()).primary_mesh

        self.assertEqual([area.name for area in mesh.areas],
                         ["area_hull", "area_glass", "area_glass_depth"])
        hull, glass, depth = mesh.areas
        self.assertEqual(hull.batch, "opaque")
        self.assertEqual(hull.slot_indices, (0, 1))
        self.assertTrue(hull.casts_shadows)
        self.assertEqual(hull.shader, "quadv5.fx")
        self.assertEqual(hull.parameters["GeneralData"], (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(hull.options["SPACE_OBJECT_TRANSPARENCY"], "SOT_OPAQUE")
        self.assertEqual(
            hull.textures["AlbedoMap"],
            "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_a.dds",
        )
        self.assertEqual(glass.batch, "transparent")
        self.assertEqual(depth.batch, "depth")
        self.assertEqual(mesh.area_slot_count, 3)

    def test_resource_paths_deduplicate_and_respect_primary_only(self):
        assembly = parse_sof_document(_document())

        primary = assembly.resource_paths(primary_only=True)
        self.assertEqual(primary[0], "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1.gr2")
        self.assertEqual(len(primary), len(set(primary)))
        self.assertEqual(len(primary), 4)
        self.assertIn(
            "res:/graphics/generic/unitsphere/unitsphere_shieldgeo_01a.gr2",
            assembly.resource_paths(),
        )

    def test_rejects_documents_this_add_on_cannot_read(self):
        with self.assertRaises(SofDocumentError):
            parse_sof_document({"schema": "carbon.values", "nodes": [], "roots": []})
        with self.assertRaises(SofDocumentError):
            parse_sof_document(dict(_document(), version=99))
        with self.assertRaises(SofDocumentError):
            parse_sof_document(dict(_document(), roots=[]))
        with self.assertRaises(SofDocumentError):
            parse_sof_document("not a document")

    def test_document_without_geometry_is_rejected(self):
        document = _document()
        document["nodes"] = [node for node in document["nodes"] if node["kind"] != "Tr2Mesh"]
        document["nodes"][-1]["fields"] = {"dna": "x:y:z"}

        with self.assertRaises(SofDocumentError):
            parse_sof_document(document)

    def test_loads_from_disk_and_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            path.write_text(json.dumps(_document()), encoding="utf-8")
            self.assertEqual(load_sof_document(path).dna, "cf1_t1:caldaribase:caldari")

            broken = Path(directory) / "broken.json"
            broken.write_text("{", encoding="utf-8")
            with self.assertRaises(SofDocumentError):
                load_sof_document(broken)
            with self.assertRaises(SofDocumentError):
                load_sof_document(Path(directory) / "absent.json")


class LoadSofBundleTests(unittest.TestCase):
    def _bundle(self, directory: Path, resources: dict) -> Path:
        (directory / "document.json").write_text(json.dumps(_document()), encoding="utf-8")
        manifest = {
            "schema": "carbon.sof-bundle",
            "version": 1,
            "build": "3444265",
            "dna": "cf1_t1:caldaribase:caldari",
            "document": "document.json",
            "resources": resources,
        }
        (directory / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory

    def test_loads_a_bundle_directory_with_its_local_files(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            texture = directory / "textures" / "cf1_t1_a.dds.png"
            texture.parent.mkdir(parents=True)
            texture.write_bytes(b"png")
            bundle = self._bundle(directory, {
                "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_a.dds": "textures/cf1_t1_a.dds.png",
                "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_n.dds": "textures/absent.png",
            })

            loaded = load_sof_bundle(bundle)

            self.assertEqual(loaded.build, "3444265")
            self.assertEqual(loaded.assembly.dna, "cf1_t1:caldaribase:caldari")
            self.assertEqual(len(loaded.resources), 1)
            self.assertIn(
                "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_n.dds",
                loaded.unresolved(primary_only=True),
            )
            self.assertNotIn(
                "res:/dx9/model/ship/caldari/frigate/cf1/cf1_t1_a.dds",
                loaded.unresolved(primary_only=True),
            )

    def test_accepts_the_manifest_path_and_a_bare_document(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            manifest = self._bundle(directory, {}) / "bundle.json"

            self.assertEqual(load_sof_bundle(manifest).assembly.root_kind, "EveShip2")

            document_only = load_sof_bundle(directory / "document.json")
            self.assertEqual(document_only.resources, {})
            self.assertEqual(
                len(document_only.unresolved(primary_only=True)),
                len(document_only.assembly.resource_paths(primary_only=True)),
            )

    def test_rejects_unusable_bundles(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with self.assertRaises(SofDocumentError):
                load_sof_bundle(directory)

            self._bundle(directory, {})
            (directory / "bundle.json").write_text(
                json.dumps({"schema": "carbon.sof-bundle", "version": 99}),
                encoding="utf-8",
            )
            with self.assertRaises(SofDocumentError):
                load_sof_bundle(directory)

            (directory / "bundle.json").write_text(
                json.dumps({"schema": "carbon.other", "version": 1}),
                encoding="utf-8",
            )
            with self.assertRaises(SofDocumentError):
                load_sof_bundle(directory)

    def test_rejects_entries_outside_the_bundle_directory(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / "bundle"
            directory.mkdir()
            self._bundle(directory, {"res:/x.dds": "../escaped.png"})

            with self.assertRaises(SofDocumentError):
                load_sof_bundle(directory)


class SofShadingTests(unittest.TestCase):
    def test_known_textures_map_to_principled_inputs(self):
        hull = parse_sof_document(_document()).primary_mesh.areas[0]
        plan = plan_material(hull, prefix="cf1_t1.")

        self.assertEqual(plan.name, "cf1_t1.area_hull")
        self.assertEqual(plan.blend_method, "OPAQUE")
        self.assertFalse(plan.use_alpha)
        self.assertEqual(plan.texture("AlbedoMap").principled_input, "Base Color")
        self.assertEqual(plan.texture("AlbedoMap").colorspace, "sRGB")
        self.assertEqual(plan.texture("NormalMap").principled_input, "Normal")
        self.assertEqual(plan.texture("NormalMap").colorspace, "Non-Color")

    def test_carbon_only_textures_are_loaded_but_unconnected(self):
        hull = parse_sof_document(_document()).primary_mesh.areas[0]
        paint = plan_material(hull).texture("PaintMaskMap")

        self.assertIsNone(paint.principled_input)
        self.assertTrue(paint.known)

    def test_transparent_areas_blend_and_depth_clones_are_skipped(self):
        _, glass, depth = parse_sof_document(_document()).primary_mesh.areas

        glass_plan = plan_material(glass)
        self.assertEqual(glass_plan.blend_method, "BLEND")
        self.assertTrue(glass_plan.use_alpha)
        self.assertTrue(should_build_material(glass))
        self.assertFalse(should_build_material(depth))

    def test_plan_records_every_document_fact_as_metadata(self):
        hull = parse_sof_document(_document()).primary_mesh.areas[0]
        metadata = plan_material(hull).metadata

        self.assertEqual(metadata["carbon_sof_batch"], "opaque")
        self.assertEqual(metadata["carbon_sof_index"], "0")
        self.assertEqual(metadata["carbon_sof_count"], "2")
        self.assertEqual(metadata["carbon_sof_parameter_GeneralData"], "1, 0, 0, 0")
        self.assertEqual(
            metadata["carbon_sof_option_SPACE_OBJECT_TRANSPARENCY"],
            "SOT_OPAQUE",
        )
        self.assertTrue(metadata["carbon_sof_effect"].endswith("quadv5.fx"))

    def test_unmapped_texture_parameters_still_plan_a_reference_node(self):
        hull = parse_sof_document(_document()).primary_mesh.areas[0]
        unknown = plan_material(
            type(hull)(
                name=hull.name,
                batch=hull.batch,
                index=hull.index,
                count=hull.count,
                effect_path=hull.effect_path,
                textures={"FutureMap": "res:/texture/global/black.dds"},
            )
        ).texture("FutureMap")

        self.assertIsNone(unknown.principled_input)
        self.assertFalse(unknown.known)
        self.assertEqual(unknown.colorspace, "Non-Color")


if __name__ == "__main__":
    unittest.main()
