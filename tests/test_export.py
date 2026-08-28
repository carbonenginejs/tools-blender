"""Handing the files over.

The decisions worth pinning are about DUPLICATION: which files get copied,
where they land, and what happens on a second export into the same folder.
"""

from pathlib import Path
import sys
import inspect
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import resfile  # noqa: E402


PATH = "res:/dx9/model/ship/amarr/ab1/ab1_t1_a.dds"


class DestinationTests(unittest.TestCase):
    def test_it_lands_in_the_layout_the_local_folder_reads(self):
        # So an export folder IS an authored folder: export, edit a file, and
        # the next load picks up the edit.
        found = resfile.export_destination("/out", PATH, "/cache/ResFiles/b4/abc")
        self.assertEqual(found,
                         Path("/out/dx9/model/ship/amarr/ab1/ab1_t1_a.dds"))

    def test_a_translated_texture_keeps_the_extension_it_can_be_opened_with(self):
        # BC7 goes out as the PNG we made of it. Handing somebody a .dds they
        # cannot open, under a name that says .dds, is worse than useless.
        found = resfile.export_destination("/out", PATH,
                                           "/cache/ResFiles/b4/abc.png")
        self.assertEqual(found.suffix, ".png")
        self.assertEqual(found.stem, "ab1_t1_a")

    def test_a_source_with_no_extension_keeps_the_logical_one(self):
        # The cache stores sources with no extension at all.
        found = resfile.export_destination("/out", PATH, "/cache/ResFiles/b4/abc")
        self.assertEqual(found.suffix, ".dds")

    def test_nothing_to_export_it_under(self):
        self.assertIsNone(resfile.export_destination("/out", "", "/cache/x"))


class EveImagesTests(unittest.TestCase):
    """What counts as ours to move."""

    def test_the_stamp_is_what_marks_a_texture_as_an_eve_resource(self):
        # A render result, a packed logo, or anything the artist added has no
        # stamp and must not be swept into the folder.
        from carbon_eve_resources import export

        source = inspect.getsource(export.eve_images)
        self.assertIn("carbon_res_path", source)
        self.assertIn("image.filepath", source)


if __name__ == "__main__":
    unittest.main()


class FileNameTests(unittest.TestCase):
    """The saved file is called what the ship is called."""

    def setUp(self):
        from carbon_eve_resources.core import sof_lookup

        self.lookup = sof_lookup

    def test_a_skin_name_becomes_a_filename(self):
        self.assertEqual(self.lookup.file_name("Svipul Hrada-Oki Offender"),
                         "svipul_hrada-oki_offender")

    def test_hyphens_survive(self):
        # They are part of the name. Dropping them makes the ship harder to
        # recognise, which is the whole reason for using its name.
        self.assertIn("-", self.lookup.file_name("Hrada-Oki"))

    def test_punctuation_a_filesystem_argues_about_goes(self):
        self.assertEqual(
            self.lookup.file_name("Omen YC119 'Glacial Drift' Edition"),
            "omen_yc119_glacial_drift_edition")

    def test_it_never_returns_something_unusable(self):
        for awkward in ("", "   ", "///", "***"):
            self.assertNotIn("/", self.lookup.file_name(awkward))
            self.assertNotIn("*", self.lookup.file_name(awkward))


class NameLookupTests(unittest.TestCase):
    """Reverse lookup over the name index that is already on disk."""

    INDEX = {
        "svipul": [{"typeID": 34562, "graphicID": 21052, "skinID": None,
                    "kind": "type"}],
        "svipul abyssal glory": [{"typeID": 34562, "kind": "skin",
                                  "skinID": 10845}],
    }

    def setUp(self):
        from carbon_eve_resources.core import sof_lookup

        self.lookup = sof_lookup
        self.previous = sof_lookup.names
        sof_lookup.names = lambda *a, **k: self.INDEX

    def tearDown(self):
        self.lookup.names = self.previous

    def test_the_skin_name_wins(self):
        # It is the ship a person means, and it already contains the hull.
        self.assertEqual(self.lookup.name_for(34562, 10845),
                         "svipul abyssal glory")

    def test_the_hull_alone_when_there_is_no_skin(self):
        self.assertEqual(self.lookup.name_for(34562, 0), "svipul")

    def test_an_unknown_ship_gives_nothing_rather_than_a_guess(self):
        self.assertEqual(self.lookup.name_for(999999, 0), "")
        self.assertEqual(self.lookup.name_for(0, 0), "")
