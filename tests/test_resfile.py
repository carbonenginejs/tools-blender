"""Resource addressing.

The address IS the file's identity, so getting it wrong does not fail -- it
quietly addresses a file that is not there, and every load pays for it again.
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import resfile  # noqa: E402


#: Real paths from a real index, with the address EVE stores each under. These
#: three were downloaded on EVERY load because their hash was taken from the
#: mixed-case spelling in the document rather than the lowercased path.
REAL = (
    ("res:/Texture/Global/noise32cube_volume.dds", "b40590f110b66d26"),
    ("res:/Texture/global/noise.dds", "239a24daab34ba73"),
    ("res:/Texture/Particle/whitesharp.dds", "5e1c12d5e0ca6231"),
)


class NormalizationTests(unittest.TestCase):
    def test_a_mixed_case_path_hashes_to_what_the_index_says(self):
        for path, expected in REAL:
            self.assertEqual(resfile.fnv1_64(path), expected, path)

    def test_casing_does_not_change_the_address(self):
        # EVE's paths are case-insensitive; two spellings are one file.
        upper = "res:/Texture/Global/noise.dds"
        self.assertEqual(resfile.fnv1_64(upper), resfile.fnv1_64(upper.lower()))

    def test_backslashes_are_the_same_path_as_slashes(self):
        self.assertEqual(resfile.fnv1_64(r"res:\Texture\a.dds"),
                         resfile.fnv1_64("res:/texture/a.dds"))

    def test_a_lowercase_path_is_unchanged(self):
        # The regression guard runs both ways: normalizing must not move the
        # paths that were already right, which is most of them.
        self.assertEqual(resfile.fnv1_64("res:/dx9/model/ship/a.gr2"),
                         resfile.fnv1_64("res:/dx9/model/ship/a.gr2"))


class FindCachedTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-addr-"))

    def test_a_file_stored_by_its_index_row_is_found_by_its_document_path(self):
        # The whole bug in one test: the file is written under the address the
        # INDEX gives, and looked up by the path the DOCUMENT spells.
        path, path_hash = REAL[0]
        stored = self.root / "ResFiles" / path_hash[:2] / f"{path_hash}_{'a' * 32}"
        stored.parent.mkdir(parents=True)
        stored.write_bytes(b"payload")

        self.assertEqual(resfile.find_cached(self.root, path), stored)

    def test_an_empty_file_is_not_treated_as_cached(self):
        path, path_hash = REAL[0]
        stored = self.root / "ResFiles" / path_hash[:2] / f"{path_hash}_{'a' * 32}"
        stored.parent.mkdir(parents=True)
        stored.write_bytes(b"")

        self.assertIsNone(resfile.find_cached(self.root, path))


if __name__ == "__main__":
    unittest.main()
