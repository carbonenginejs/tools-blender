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
