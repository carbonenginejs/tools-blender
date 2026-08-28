"""Stored documents, so a ship loads again with the service down."""

from pathlib import Path
import gzip
import json
import sys
import tempfile
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core import sof_fetch  # noqa: E402


DNA = "mf4_t1:minmatarbase:minmatar"
DOCUMENT = {"_type": "EveShip2", "dna": DNA, "boundingSphereRadius": 42.0}


class _Client:
    """Answers once, then refuses -- so a second read must come from disk."""

    def __init__(self, document=DOCUMENT, fail=False):
        self.document = document
        self.fail = fail
        self.calls = 0

    def request_json(self, method, route):
        self.calls += 1
        if self.fail:
            raise RuntimeError("service is down")
        return self.document


class PathTests(unittest.TestCase):
    def test_the_build_is_in_the_path(self):
        # Or a new client build would serve the old document forever.
        one = sof_fetch.document_path("/c", DNA, "111")
        two = sof_fetch.document_path("/c", DNA, "222")
        self.assertNotEqual(one, two)

    def test_commands_are_made_filesystem_safe(self):
        path = sof_fetch.document_path("/c", DNA + ":pattern?a;b;c", "1")
        self.assertNotRegex(path.name, r"[?;:]")

    def test_a_long_dna_is_hashed_rather_than_truncated(self):
        # Truncating would collide two ships differing only at the end.
        long_dna = DNA + ":material?" + ";".join(f"material_{n}" for n in range(30))
        other = long_dna + "_x"
        first = sof_fetch.document_path("/c", long_dna, "1")
        second = sof_fetch.document_path("/c", other, "1")
        self.assertNotEqual(first.name, second.name)
        self.assertLess(len(first.name), 120)


class DigestTests(unittest.TestCase):
    def test_the_same_document_hashes_the_same(self):
        # Key order must not change the answer, or nothing can be compared.
        self.assertEqual(sof_fetch.document_digest({"a": 1, "b": 2}),
                         sof_fetch.document_digest({"b": 2, "a": 1}))

    def test_a_changed_document_hashes_differently(self):
        self.assertNotEqual(sof_fetch.document_digest(DOCUMENT),
                            sof_fetch.document_digest({**DOCUMENT, "dna": "other"}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-doc-"))
        self.path = sof_fetch.document_path(self.root, DNA, "3482594")

    def test_round_trips(self):
        sof_fetch.write_document_cache(self.path, DOCUMENT, dna=DNA, build="3482594")
        self.assertEqual(sof_fetch.read_document(self.path), DOCUMENT)

    def test_the_envelope_reads_without_the_document(self):
        # Answering "has this changed?" should not cost 259KB of JSON.
        sof_fetch.write_document_cache(self.path, DOCUMENT, dna=DNA, build="3482594")
        envelope = sof_fetch.read_envelope(self.path)
        self.assertEqual(envelope["dna"], DNA)
        self.assertEqual(envelope["build"], "3482594")
        self.assertEqual(envelope["sha256"], sof_fetch.document_digest(DOCUMENT))
        self.assertNotIn("document", envelope)

    def test_a_tampered_document_is_refused(self):
        sof_fetch.write_document_cache(self.path, DOCUMENT, dna=DNA, build="3482594")
        with gzip.open(str(self.path), "rt", encoding="utf-8") as handle:
            envelope = json.load(handle)
        envelope["document"]["boundingSphereRadius"] = 999.0
        with gzip.open(str(self.path), "wt", encoding="utf-8") as handle:
            json.dump(envelope, handle)
        with self.assertRaises(sof_fetch.FetchError):
            sof_fetch.read_document(self.path)

    def test_a_foreign_file_is_refused(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(str(self.path), "wt", encoding="utf-8") as handle:
            json.dump({"_type": "EveShip2"}, handle)
        with self.assertRaises(sof_fetch.FetchError):
            sof_fetch.read_document(self.path)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-doc-"))

    def test_the_first_read_fetches_and_stores(self):
        client = _Client()
        sof_fetch.document_for(DNA, client, build="3482594", cache_root=self.root)
        self.assertEqual(client.calls, 1)
        self.assertTrue(sof_fetch.document_path(self.root, DNA, "3482594").is_file())

    def test_the_second_read_does_not_ask_the_service(self):
        client = _Client()
        for _ in range(3):
            sof_fetch.document_for(DNA, client, build="3482594", cache_root=self.root)
        self.assertEqual(client.calls, 1)

    def test_a_stored_ship_loads_with_the_service_down(self):
        sof_fetch.document_for(DNA, _Client(), build="3482594", cache_root=self.root)
        found = sof_fetch.document_for(DNA, _Client(fail=True), build="3482594",
                                       cache_root=self.root)
        self.assertEqual(found, DOCUMENT)

    def test_a_ship_never_loaded_still_fails_offline(self):
        # Being offline should cost the ships you have not opened, not lie
        # about the ones you have.
        with self.assertRaises(sof_fetch.FetchError):
            sof_fetch.document_for("ab1_t1:amarrbase:amarr", _Client(fail=True),
                                   build="3482594", cache_root=self.root)

    def test_a_corrupt_file_falls_back_to_the_service(self):
        path = sof_fetch.document_path(self.root, DNA, "3482594")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not gzip at all")
        client = _Client()
        self.assertEqual(
            sof_fetch.document_for(DNA, client, build="3482594", cache_root=self.root),
            DOCUMENT)
        self.assertEqual(client.calls, 1)

    def test_no_cache_root_still_works(self):
        client = _Client()
        self.assertEqual(sof_fetch.document_for(DNA, client, build="3482594"),
                         DOCUMENT)


if __name__ == "__main__":
    unittest.main()
