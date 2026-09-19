"""Source-specific nebula choices use authored scene texture references."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.core import nebula, source


class NebulaSceneTests(unittest.TestCase):
    def setUp(self):
        nebula.forget()
        self.addCleanup(nebula.forget)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = patch.dict(nebula.CACHE_ROOT, path=self.directory.name)
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def test_candidates_exclude_sof_hulls_and_pin_resource_build(self):
        client = Mock()
        scene = "res:/dx9/scene/universe/hydrogen_alpha_nebula_cube.black"
        client.request_json.return_value = [scene, "res:/dx9/model/spaceobjectfactory/hulls/prop_cube.black", scene]
        self.assertEqual(nebula.scenes(client, target="frontier", build="3512930"), [scene])
        client.request_json.assert_called_once_with("GET", "/frontier/3512930/nebulas")
        nebula.forget()
        self.assertEqual(nebula.scenes(None, target="frontier", build="3512930"), [scene])
        self.assertEqual(nebula.scenes(None, target="eve", build="3512930"), [])
        self.assertEqual(nebula.scenes(None, target="frontier", build="3512931"), [])

    def test_scene_uses_authored_map_and_caches_it_for_offline_loads(self):
        client = Mock()
        scene = "res:/dx9/scene/universe/atomic_gas_nebula_cube.black"
        texture = "res:/dx9/scene/universe/atomic_gas_nebula_cube_cube.dds"
        client.request_json.return_value = {"object": {"_type": "EveSpaceScene",
            "envMapResPath": "res:/reflection.dds", "backgroundEffect": {"resources": [
                {"name": "AlphaMap", "resourcePath": "res:/alpha.dds"},
                {"name": "NebulaMap", "resourcePath": texture}]}}}
        self.assertEqual(nebula.scene_cube_path(client, scene, target="frontier", build="3512930"), texture)
        client.request_json.assert_called_once_with("GET",
            "/frontier/3512930/res/dx9/scene/universe/atomic_gas_nebula_cube.black?format=json")
        nebula.forget()
        self.assertEqual(nebula.scene_cube_path(None, scene, target="frontier", build="3512930"), texture)

    def test_non_scene_and_missing_map_are_rejected(self):
        client = Mock()
        for document in ({"_type": "EveSOFDataHull"}, {"_type": "EveSpaceScene"}):
            client.request_json.return_value = {"object": document}
            with self.assertRaises(ValueError):
                nebula.scene_cube_path(client, "res:/invalid.black", target="frontier", build="3512930")

    def test_damaged_catalog_cache_is_refetched(self):
        path = Path(self.directory.name) / "carbon-nebula-scenes-frontier-3512930.json"
        path.write_text("{truncated", encoding="utf-8")
        client = Mock()
        client.request_json.return_value = ["res:/dx9/scene/universe/sky_cube.black"]
        self.assertEqual(nebula.scenes(client, target="frontier", build="3512930"), client.request_json.return_value)
        client.request_json.assert_called_once()

    def test_source_change_does_not_replace_world(self):
        try:
            from carbon_eve_resources import skybox
        except ImportError:
            self.skipTest("Blender is required")
        with patch.object(skybox.service_access, "source", return_value=source.Source()), \
             patch.object(skybox, "apply_world") as apply:
            message = skybox.finish_job(None, ("Sky", (Path("sky.hdr"), "res:/sky.dds"),
                                               source.Source("frontier")))
            self.assertIn("Source changed", message)
            apply.assert_not_called()

    def test_environment_fetch_retains_source_and_optional_frontier_root(self):
        try:
            from carbon_eve_resources import skybox
        except ImportError:
            self.skipTest("Blender is required")
        from carbon_eve_resources.dds import reader, worker
        selected = source.Source("frontier", "ccp", "3512930", "123")
        cube = Path(self.directory.name) / "cube"
        hdr = Path(self.directory.name) / "cube.cube-v2.hdr"
        hdr.write_bytes(b"previously converted HDR")
        with patch.object(nebula, "scene_cube_path", return_value="res:/authored.dds") as path, \
             patch.object(skybox.sof_fetch, "fetch_resource", return_value=cube) as fetch, \
             patch.object(reader, "derived_path", return_value=hdr), \
             patch.object(worker, "convert_environment") as convert:
            result = skybox.build_environment(None, None, self.directory.name, source=selected,
                scene_path="res:/scene.black", resfiles_root="frontier-install")
        self.assertEqual(result, (hdr, "res:/authored.dds"))
        path.assert_called_once_with(None, "res:/scene.black", target="frontier", build="3512930")
        self.assertEqual(fetch.call_args.kwargs["resfiles_root"], "frontier-install")
        self.assertEqual(fetch.call_args.kwargs["target"], "frontier")
        self.assertEqual(fetch.call_args.kwargs["build"], "3512930")
        convert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
