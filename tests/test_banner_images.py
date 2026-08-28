"""Choosing your own picture for a banner slot.

A ship that belongs to nobody yet has no corp and no alliance to fetch a logo
for, and somebody showing a design rather than a character's actual affiliation
wants their own artwork regardless.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

try:
    import bpy
except ImportError:                     # pragma: no cover - outside Blender
    bpy = None


class Prefs:
    """Only what the reader looks at."""

    def __init__(self, **values):
        self.use_corp_banner = values.get("use_corp_banner", False)
        self.corp_banner = values.get("corp_banner", "")
        self.use_alliance_banner = values.get("use_alliance_banner", False)
        self.alliance_banner = values.get("alliance_banner", "")


@unittest.skipIf(bpy is None, "needs Blender")
class OverrideTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources import addon

        self.addon = addon

    def test_nothing_chosen_overrides_nothing(self):
        self.assertEqual(self.addon.banner_overrides(Prefs()), {})

    def test_a_path_alone_does_nothing(self):
        # The tick is the switch. A half-filled setting behaves as off rather
        # than as a missing file.
        found = self.addon.banner_overrides(Prefs(corp_banner="//mine.png"))
        self.assertEqual(found, {})

    def test_a_tick_alone_does_nothing(self):
        self.assertEqual(self.addon.banner_overrides(
            Prefs(use_corp_banner=True)), {})

    def test_a_ticked_path_is_used_for_that_slot(self):
        found = self.addon.banner_overrides(
            Prefs(use_corp_banner=True, corp_banner="//mine.png"))
        self.assertEqual(list(found), ["corp_logo"])

    def test_the_two_slots_are_independent(self):
        found = self.addon.banner_overrides(Prefs(
            use_corp_banner=True, corp_banner="//corp.png",
            use_alliance_banner=True, alliance_banner="//alliance.png"))
        self.assertEqual(sorted(found), ["alliance_logo", "corp_logo"])

    def test_the_usages_are_the_sof_names(self):
        # Not our own labels: these key straight into the banner set's usage,
        # so a rename here would silently stop matching.
        from carbon_eve_resources import ship

        for usage in self.addon.BANNER_OVERRIDES:
            self.assertIn(usage, ship.BANNER_USAGES)


@unittest.skipIf(bpy is None, "needs Blender")
class LoadingTests(unittest.TestCase):
    def test_a_path_that_will_not_open_falls_through(self):
        # Reported, and treated as absent. A banner that falls back to the
        # fetched logo is recoverable; one silently left blank is not.
        from carbon_eve_resources import ship

        self.assertIsNone(ship.own_banner_image(
            "corp_logo", {"corp_logo": "/nowhere/at/all/missing.png"}))

    def test_no_override_for_the_slot_is_not_an_error(self):
        from carbon_eve_resources import ship

        self.assertIsNone(ship.own_banner_image("corp_logo", {}))
        self.assertIsNone(ship.own_banner_image("corp_logo", None))


if __name__ == "__main__":
    unittest.main()
