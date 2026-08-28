"""The order `build_ship` runs its stages in, which is load-bearing.

None of these are enforced by the code -- they are implicit in one function's
statement order, and every one of them fails SILENTLY when broken: a dead
driver, an unbound attachment, a ragged name, a second ship inheriting the
first's collections. Pinned here so a split cannot quietly reorder them.

Read from the source rather than by running a build: this is about the order
statements appear in, and a build would need the network.
"""

from pathlib import Path
import re
import unittest


SHIP = (Path(__file__).resolve().parents[1] / "addons" / "carbon_eve_resources"
        / "ship.py")

#: The stages, in no particular order -- the tests below assert the order.
STAGES = ("reset_set_collections", "assemble", "build_decals",
          "build_plane_sets", "build_banner_sets", "apply_ship_globals",
          "drive_ship_sockets", "stamp_ship", "populate_sof", "parent_to_root",
          "prune_empty_collections", "align_names")


def build_ship_body() -> str:
    """The text of `build_ship`, up to the next top-level definition."""

    text = SHIP.read_text(encoding="utf-8")
    start = text.index("def build_ship(")
    end = text.index("\ndef ", start + 1)
    return text[start:end]


def call_order() -> dict:
    """`{stage: position}` for each stage called, in statement order.

    Position rather than line number, so the assertions read as ordering and
    do not shift when something above moves.
    """

    body = build_ship_body().split("\n")
    found = {}
    for number, line in enumerate(body):
        code = line.split("#", 1)[0]
        for name in STAGES:
            # A dotted call counts: `sof_areas.stamp_ship(...)` is the stage.
            if name not in found and re.search(r"\b" + name + r"\s*\(", code):
                found[name] = number
    return found


class BuildOrderTests(unittest.TestCase):
    def setUp(self):
        self.at = call_order()

    def test_every_stage_is_still_called(self):
        # If a stage moves out of build_ship this whole file stops meaning
        # anything, so it fails loudly rather than passing vacuously.
        missing = [name for name in STAGES if name not in self.at]
        self.assertEqual(missing, [])

    def _before(self, first, second, why):
        self.assertLess(self.at[first], self.at[second], why)

    def test_collections_are_reset_before_anything_builds(self):
        # Or a second ship inherits the first ship's set collections.
        self._before("reset_set_collections", "assemble",
                     "collections must be reset before a ship is built")

    def test_materials_exist_before_the_drivers_that_scan_them(self):
        # drive_ship_sockets walks every material slot on the ship; decals,
        # planes and banners all contribute slots.
        for stage in ("build_decals", "build_plane_sets", "build_banner_sets"):
            self._before(stage, "drive_ship_sockets",
                         f"{stage} adds materials drive_ship_sockets scans")

    def test_ship_values_exist_before_the_sockets_are_driven(self):
        self._before("apply_ship_globals", "drive_ship_sockets",
                     "the drivers read properties apply_ship_globals writes")

    def test_areas_are_typed_before_the_sof_panels_are_filled(self):
        # populate_sof reads carbon_area_type and silently degrades to one
        # shared group when the stamp has not run.
        self._before("stamp_ship", "populate_sof",
                     "populate_sof reads the area types stamp_ship writes")

    def test_the_root_is_parented_before_names_are_aligned(self):
        # parent_to_root adds an object; align_names pads to the widest name.
        self._before("parent_to_root", "align_names",
                     "align_names must see every object, including the root")

    def test_empty_collections_are_pruned_before_names_are_aligned(self):
        self._before("prune_empty_collections", "align_names",
                     "pruning after aligning leaves names padded for the dead")

    def test_align_names_is_last(self):
        self.assertEqual(max(self.at.values()), self.at["align_names"],
                         "align_names must run last; it needs every name")


if __name__ == "__main__":
    unittest.main()
