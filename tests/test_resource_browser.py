"""Source changes must not publish a stale index or load stale resources."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, "needs Blender")
class ResourceBrowserTests(unittest.TestCase):
    def test_source_change_rejects_completed_index(self):
        from carbon_eve_resources import addon
        state = SimpleNamespace(source="frontier", busy=True, status="")
        context = SimpleNamespace(window_manager=SimpleNamespace(carbon_eve_resources=state))
        job = SimpleNamespace(thread=None, error=None, kind="catalog", result=(object(), None, "eve"))
        with patch.object(addon, "bpy", SimpleNamespace(context=context)), \
             patch.object(addon, "_registered", True), patch.object(addon, "_job", job), \
             patch.object(addon, "_catalog", None):
            addon._poll_job()
            self.assertIsNone(addon._catalog)
            self.assertIn("Source changed", state.status)

    def test_source_change_keeps_download_out_of_scene(self):
        from carbon_eve_resources import addon, ship
        state = SimpleNamespace(source="eve", busy=True, status="")
        context = SimpleNamespace(window_manager=SimpleNamespace(carbon_eve_resources=state))
        job = SimpleNamespace(thread=None, error=None, kind="browser_resource",
                              result=("cached", "res:/ship.cmf", "frontier", False))
        with patch.object(addon, "bpy", SimpleNamespace(context=context)), \
             patch.object(addon, "_registered", True), patch.object(addon, "_job", job), \
             patch.object(ship, "import_geometry") as importer:
            addon._poll_job()
            importer.assert_not_called()
            self.assertIn("without loading", state.status)

    def test_restored_browser_registers(self):
        from carbon_eve_resources import resource_browser
        resource_browser.register()
        try:
            self.assertTrue(hasattr(bpy.types, "EVE_RESOURCE_PT_browser"))
            self.assertTrue(hasattr(bpy.ops.carbon, "eve_resource_open_selected"))
        finally:
            resource_browser.unregister()

    def test_source_change_keeps_fetched_turret_out_of_scene(self):
        from carbon_eve_resources import turrets
        from carbon_eve_resources.core.source import Source
        state = SimpleNamespace(status="")
        context = SimpleNamespace(window_manager=SimpleNamespace(carbon_eve_turrets=state))
        result = ("Gun", "res:/gun.black", {}, {}, {}, [], Source("frontier"))
        with patch.object(turrets.sof_panels, "_catalog_source", return_value=Source("eve")), \
             patch.object(turrets, "clear_fitted") as clear, \
             patch.object(turrets, "fit") as fit:
            self.assertIn("Source changed", turrets.finish_job(context, result))
            clear.assert_not_called()
            fit.assert_not_called()

    def test_turret_mount_removes_locator_scale_but_keeps_ship_scale(self):
        from carbon_eve_resources import turrets
        from mathutils import Matrix, Euler, Vector
        ship = Matrix.Translation((7, 8, 9)) @ Euler((.2, .4, .6)).to_matrix().to_4x4() @ Matrix.Diagonal((.01, .02, .03, 1))
        rotation = Euler((.3, -.2, .7)).to_matrix().to_4x4()
        position = Matrix.Translation((10, 20, 30))
        locator = position @ rotation @ Matrix.Diagonal((1000, 500, 2000, 1))
        world = ship @ locator
        actual = world @ turrets.turret_mount_matrix(SimpleNamespace(matrix_world=world), SimpleNamespace(matrix_world=ship))
        expected = ship @ position @ rotation
        for a, b in zip(actual, expected):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=5)
