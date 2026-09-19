"""Source identity must survive shared payloads and separate build domains."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.core import source, sof_fetch, sof_lookup, cache_prune


class Client:
    def __init__(self):
        self.calls = []

    def request_json(self, method, route):
        self.calls.append(route)
        return {"build": "400", "builds": {"resources": "400", "sde": "300"}}

    def resolve_resource(self, path, build, **kwargs):
        self.calls.append((path, build, kwargs))
        checksum = "a" if kwargs["target"] == "eve" else "b"
        return {"resolution": {"sourceUrl": "https://example.test/ab/" + "ab" * 8 + "_" + checksum * 32}}


class SourceTests(unittest.TestCase):
    def test_separate_optional_install_roots_share_no_fallback(self):
        prefs = SimpleNamespace(use_local_source=True, local_resfiles=" E:/EVE/ResFiles ",
                                frontier_resfiles="F:/Frontier/ResFiles")
        self.assertEqual(source.resfiles_directory(prefs, "eve"), "E:/EVE/ResFiles")
        self.assertEqual(source.resfiles_directory(prefs, "frontier"), "F:/Frontier/ResFiles")
        prefs.frontier_resfiles = ""
        self.assertIsNone(source.resfiles_directory(prefs, "frontier"))
        prefs.use_local_source = False
        self.assertIsNone(source.resfiles_directory(prefs, "eve"))

    def test_resource_and_sde_builds_remain_separate_offline(self):
        with tempfile.TemporaryDirectory() as root:
            selected = source.resolve(Client(), "infinity", root)
            self.assertEqual(selected.resources(), {"target": "infinity", "build": "400"})
            self.assertEqual(selected.sde(), {"target": "infinity", "build": "300"})
            self.assertEqual(selected.provider, "netease")
            self.assertEqual(source.resolve(None, "infinity", root), selected)

    def test_same_logical_name_has_distinct_receipts_but_shared_payload_root(self):
        with tempfile.TemporaryDirectory() as root:
            client = Client()
            first = source.resource_resolution(client, root, "res:/a.dds", "400", "eve")
            second = source.resource_resolution(client, root, "res:/a.dds", "400", "frontier")
            self.assertNotEqual(first, second)
            self.assertEqual(source.resource_resolution(None, root, "res:/a.dds", "400", "frontier"), second)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(client.calls[1][2], {"target": "frontier", "provider": "ccp"})

    def test_bad_cached_build_and_resolution_build_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sources/frontier/latest.json"
            source.write_json(path, source.Source("frontier").to_dict())
            with self.assertRaises(ValueError):
                source.resolve(None, "frontier", root)
            with self.assertRaises(ValueError):
                source.resource_resolution(Client(), root, "res:/a", "../latest", "frontier")

    def test_prune_protects_foreign_shared_and_unknown_addresses(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            resolution = source.resource_resolution(Client(), root, "res:/a.dds", "400", "frontier")
            address = "/".join(resolution["sourceUrl"].split("/")[-2:])
            old = root / "indexes/resfileindex-100.txt"
            old.parent.mkdir()
            old.write_text(f"res:/a.dds,{address},x,1,1\n")
            (old.parent / "resfileindex-200.txt").write_text("res:/other,not/an/address\n")
            payload = root / "ResFiles" / address
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"foreign")
            unknown = payload.with_name("unowned")
            unknown.write_bytes(b"unknown")
            self.assertEqual(cache_prune.plan(root)["remove"], [])
            receipt = next((root / "sources").glob("*/*/resolutions/*.json"))
            receipt.write_text("broken")
            with self.assertRaises(cache_prune.PruneError):
                cache_prune.plan(root)


if __name__ == "__main__":
    unittest.main()
