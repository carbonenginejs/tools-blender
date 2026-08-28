"""Pruning the resource cache without a tools-core checkout.

Deletion is the one operation here that cannot be undone by running it again,
so what it keeps and what it refuses matter more than what it removes.
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import cache_prune, resfile  # noqa: E402


#: A stand-in index row. The address is opaque to everything here: the point
#: is that whatever the index says is what is stored, verbatim.
def address(path: str, md5: str) -> str:
    stem = f"{abs(hash(path)) % (16 ** 16):016x}"
    return f"{stem[:2]}/{stem}_{md5}"


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-prune-"))
        (self.root / "indexes").mkdir(parents=True)

    def write_index(self, build: str, rows):
        lines = [f"{path},{address(path, md5)},{md5},10,10"
                 for path, md5 in rows]
        (self.root / "indexes" / f"resfileindex-{build}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def write_file(self, path: str, md5: str, data: bytes = b"x" * 16):
        stored = self.root / "ResFiles" / address(path, md5)
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        return stored

    def test_it_refuses_when_no_index_is_cached(self):
        # With nothing to compare against every file looks unreferenced, and
        # "delete everything" is not a prune.
        self.write_file("res:/a.dds", "a" * 32)
        with self.assertRaises(cache_prune.PruneError):
            cache_prune.plan(self.root)

    def test_a_superseded_file_goes_and_the_current_one_stays(self):
        # The same logical path at two digests: this is the whole reason the
        # cache grows, since the new one arrives BESIDE the old.
        self.write_index("300", [("res:/a.dds", "b" * 32)])
        old = self.write_file("res:/a.dds", "a" * 32)
        current = self.write_file("res:/a.dds", "b" * 32)

        cache_prune.prune(self.root, apply=True)

        self.assertFalse(old.exists())
        self.assertTrue(current.exists())

    def test_keeping_two_builds_keeps_both_their_files(self):
        self.write_index("300", [("res:/a.dds", "a" * 32)])
        self.write_index("400", [("res:/a.dds", "b" * 32)])
        old = self.write_file("res:/a.dds", "a" * 32)
        new = self.write_file("res:/a.dds", "b" * 32)

        cache_prune.prune(self.root, keep_latest=2, apply=True)

        self.assertTrue(old.exists())
        self.assertTrue(new.exists())

    def test_builds_are_ordered_as_numbers(self):
        # 999999 outranks 1000000 as text, and the newest build would be the
        # one deleted.
        self.write_index("999999", [("res:/a.dds", "a" * 32)])
        self.write_index("1000000", [("res:/b.dds", "b" * 32)])
        self.assertEqual(cache_prune.cached_builds(self.root)[0], "1000000")

    def test_planning_removes_nothing(self):
        self.write_index("300", [("res:/a.dds", "b" * 32)])
        stale = self.write_file("res:/a.dds", "a" * 32)

        decided = cache_prune.plan(self.root)

        self.assertTrue(stale.exists())
        self.assertEqual(len(decided["remove"]), 1)
        self.assertEqual(decided["bytes"], 16)

    def test_a_dropped_build_takes_its_index_with_it(self):
        # Keeping it would make the NEXT prune protect files this one just
        # deleted, and the cache would never shrink.
        self.write_index("300", [("res:/a.dds", "a" * 32)])
        self.write_index("400", [("res:/a.dds", "b" * 32)])

        cache_prune.prune(self.root, apply=True)

        self.assertFalse((self.root / "indexes" / "resfileindex-300.txt").exists())
        self.assertTrue((self.root / "indexes" / "resfileindex-400.txt").exists())

    def test_nothing_outside_resfiles_is_touched(self):
        # Documents, logos and derived files are not in any index, and a prune
        # that swept them would delete every ship a person had loaded.
        self.write_index("300", [("res:/a.dds", "a" * 32)])
        documents = self.root / "documents" / "300" / "eve"
        documents.mkdir(parents=True)
        kept = documents / "ab1_t1_amarr.json.gz"
        kept.write_bytes(b"document")
        logo = self.root / "logos" / "corp.png"
        logo.parent.mkdir(parents=True)
        logo.write_bytes(b"png")

        cache_prune.prune(self.root, apply=True)

        self.assertTrue(kept.exists())
        self.assertTrue(logo.exists())

    def test_overlay_rows_are_not_mistaken_for_stored_files(self):
        # An index carries plain paths alongside content-addressed ones.
        index = self.root / "indexes" / "resfileindex-300.txt"
        index.write_text("res:/a.dds,not/an/address,x,1,1\n", encoding="utf-8")
        self.assertEqual(cache_prune.addresses_in(index), set())


if __name__ == "__main__":
    unittest.main()
