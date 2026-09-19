"""Transport compression must not enter geometry/texture readers as raw bytes."""
import gzip
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.core import resource_index, sof_fetch


class ResourceCompressionTests(unittest.TestCase):
    def test_http_content_encoding_is_decoded(self):
        response = io.BytesIO(gzip.compress(b"cmff resource"))
        response.headers = {"Content-Encoding": "gzip"}
        self.assertEqual(sof_fetch.read_url("https://example.test/resource", opener=lambda *a, **k: response),
                         b"cmff resource")

    def test_headerless_gzip_requires_the_decoded_content_hash(self):
        payload = b"cmff resource"
        encoded = gzip.compress(payload)
        address = "ab/0123456789abcdef_" + hashlib.md5(payload).hexdigest()
        self.assertEqual(sof_fetch.unpack_resource_payload(encoded, address), payload)
        with self.assertRaises(sof_fetch.FetchError):
            sof_fetch.unpack_resource_payload(encoded, "ab/0123456789abcdef_" + "0" * 32)
        authored_gzip = "ab/0123456789abcdef_" + hashlib.md5(encoded).hexdigest()
        self.assertEqual(sof_fetch.unpack_resource_payload(encoded, authored_gzip), encoded)

    def test_cache_repair_is_hash_checked_and_atomic(self):
        payload = b"cmff resource"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("0123456789abcdef_" + hashlib.md5(payload).hexdigest())
            path.write_bytes(gzip.compress(payload))
            sof_fetch.repair_compressed_resource(path)
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(path.with_name(path.name + ".part").exists())
            corrupt = gzip.compress(b"wrong content")
            path.write_bytes(corrupt)
            with self.assertRaises(sof_fetch.FetchError):
                sof_fetch.repair_compressed_resource(path)
            self.assertEqual(path.read_bytes(), corrupt)

    def test_optional_resfiles_decodes_into_cache_for_index_and_resolution(self):
        payload = b"cmff resource"
        compressed = gzip.compress(payload)
        address = "ab/0123456789abcdef_" + hashlib.md5(payload).hexdigest()
        for indexed in (False, True):
            for shared in (False, True):
                with self.subTest(indexed=indexed, shared=shared), tempfile.TemporaryDirectory() as directory:
                    cache = Path(directory) / "cache"
                    optional = cache / "ResFiles" if shared else Path(directory) / "install"
                    source = optional / address
                    source.parent.mkdir(parents=True)
                    source.write_bytes(compressed)
                    with patch.object(sof_fetch.resindex, "locate", return_value=address), \
                         patch("carbon_eve_resources.core.source.resource_resolution",
                               return_value={"sourceUrl": "https://example.test/" + address}):
                        result = sof_fetch.fetch_resource(
                            "res:/ship.cmf", None, cache, build="1", target="frontier",
                            index={"present": True} if indexed else None, resfiles_root=optional,
                            opener=lambda *a, **k: self.fail("Cached resources must not download"))
                    self.assertEqual(result, cache / "ResFiles" / address)
                    self.assertEqual(result.read_bytes(), payload)
                    self.assertEqual(source.read_bytes(), payload if shared else compressed)

    def test_download_helper_decodes_http_transport(self):
        response = io.BytesIO(gzip.compress(b"cmff resource"))
        response.headers = {"Content-Encoding": "gzip"}
        with tempfile.TemporaryDirectory() as directory:
            result = sof_fetch.download("https://example.test/resource", Path(directory) / "ship.cmf",
                                        opener=lambda *a, **k: response)
            self.assertEqual(result.read_bytes(), b"cmff resource")

    def test_resource_browser_download_decodes_http_transport(self):
        response = io.BytesIO(gzip.compress(b"indexed resource"))
        response.headers = {"Content-Encoding": "gzip"}
        self.assertEqual(resource_index._download("https://example.test/resource",
                         lambda *a, **k: response, 1), b"indexed resource")


if __name__ == "__main__":
    unittest.main()
