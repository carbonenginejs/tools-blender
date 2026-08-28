"""Colour management, which is not a material problem but looks like one.

Blender 4 and 5 default to AgX -- a film look that desaturates and rolls
highlights off by design. EVE's textures and SOF material colours are authored
to be shown as they are, so a hull rendered under AgX reads as washed out with
grey where its blacks should be.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

try:
    import bpy
except ImportError:                    # pragma: no cover - outside Blender
    bpy = None


class Prefs:
    """Enough of the preferences for the choice: the mode, and the memory."""

    def __init__(self, mode="OVERRIDE", previous=""):
        self.view_transform_mode = mode
        self.previous_view_transform = previous


@unittest.skipIf(bpy is None, "needs Blender")
class ViewTransformTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources import addon

        self.addon = addon
        self.previous = bpy.context.scene.view_settings.view_transform

    def tearDown(self):
        bpy.context.scene.view_settings.view_transform = self.previous

    def test_override_moves_a_scene_off_agx(self):
        bpy.context.scene.view_settings.view_transform = "AgX"
        self.assertTrue(self.addon.apply_view_transform(Prefs()))
        self.assertEqual(bpy.context.scene.view_settings.view_transform,
                         "Standard")

    def test_override_remembers_what_it_replaced(self):
        # So choosing Blender gives back what the person had, rather than
        # Blender's factory default.
        prefs = Prefs()
        bpy.context.scene.view_settings.view_transform = "AgX"
        self.addon.apply_view_transform(prefs)
        self.assertEqual(prefs.previous_view_transform, "AgX")

    def test_blender_puts_back_what_was_there(self):
        prefs = Prefs(mode="BLENDER", previous="AgX")
        bpy.context.scene.view_settings.view_transform = "Standard"
        self.assertTrue(self.addon.apply_view_transform(prefs))
        self.assertEqual(bpy.context.scene.view_settings.view_transform, "AgX")

    def test_blender_with_nothing_remembered_changes_nothing(self):
        # Never seen an override, so there is nothing to put back and the
        # scene is not ours to touch.
        bpy.context.scene.view_settings.view_transform = "Filmic"
        self.assertFalse(self.addon.apply_view_transform(Prefs(mode="BLENDER")))
        self.assertEqual(bpy.context.scene.view_settings.view_transform,
                         "Filmic")

    def test_a_scene_already_standard_is_left_alone(self):
        # Reported as "changed nothing", so the console does not announce a
        # change that did not happen.
        bpy.context.scene.view_settings.view_transform = "Standard"
        self.assertFalse(self.addon.apply_view_transform(Prefs()))

    def test_blender_mode_leaves_a_scene_it_never_touched(self):
        # Somebody lighting a shot has their own colour management, and this
        # must not reach into it.
        bpy.context.scene.view_settings.view_transform = "AgX"
        self.assertFalse(self.addon.apply_view_transform(Prefs(mode="BLENDER")))
        self.assertEqual(bpy.context.scene.view_settings.view_transform, "AgX")

    def test_it_survives_preferences_that_do_not_have_the_setting(self):
        bpy.context.scene.view_settings.view_transform = "AgX"
        self.assertFalse(self.addon.apply_view_transform(object()))


if __name__ == "__main__":
    unittest.main()
