import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.resource_index import (  # noqa: E402
    APP_BASE_URL,
    INDEX_BASE_URL,
    LATEST_BUILD_CHECK_INTERVAL_SECONDS,
    METADATA_BASE_URL,
    RESOURCE_BASE_URL,
    ResourceCatalog,
    ResourceIndexError,
    clear_payload_cache,
    default_cache_root,
    ensure_latest_catalog,
    fetch_resource,
    materialize_resource,
    normalize_directory,
    parse_index,
    parse_index_line,
    payload_cache_stats,
    safe_join,
)


def index_row(logical_path, location, payload):
    checksum = hashlib.md5(payload).hexdigest()
    return f"{logical_path},{location},{checksum},{len(payload)},{len(payload)}"


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.data


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        if url not in self.responses:
            raise AssertionError(f"Unexpected URL: {url}")
        return FakeResponse(self.responses[url])


class ResourceIndexTests(unittest.TestCase):
    def test_cache_override_prefers_the_plural_name_and_accepts_the_legacy_name(self):
        with patch.dict(
            os.environ,
            {
                "CARBONENGINEJS_TOOLS_CACHE": "new-cache",
                "CARBONENGINEJS_TOOL_CACHE": "legacy-cache",
            },
            clear=True,
        ):
            self.assertEqual(default_cache_root(), Path("new-cache"))

        with patch.dict(os.environ, {"CARBONENGINEJS_TOOL_CACHE": "legacy-cache"}, clear=True):
            self.assertEqual(default_cache_root(), Path("legacy-cache"))

    def test_parses_ccp_rows_and_rejects_unsafe_paths(self):
        payload = b"texture"
        entry = parse_index_line(index_row("res:/dx9/model/test.dds", "ab/hash_md5", payload))
        self.assertEqual(entry.logical_path, "res:/dx9/model/test.dds")
        self.assertEqual(entry.relative_path, "dx9/model/test.dds")
        self.assertEqual(entry.uncompressed_size, len(payload))
        self.assertEqual(entry.checksum, hashlib.md5(payload).hexdigest())

        with self.assertRaises(ResourceIndexError):
            parse_index_line("res:/../escape.dds,ab/hash")
        with self.assertRaises(ResourceIndexError):
            parse_index_line("res:/safe.dds,../escape")
        with self.assertRaises(ResourceIndexError):
            parse_index_line("res:/safe.dds,ab/hash,not-an-md5")

    def test_catalog_browses_folders_searches_and_filters(self):
        rows = "\n".join(
            (
                "res:/dx9/model/ship/hull.gr2,aa/one",
                "res:/dx9/model/ship/hull_d.dds,bb/two",
                "res:/dx9/model/ship/hull_d_lowdetail.dds,ee/five",
                "res:/dx9/model/ship/hull_pmdg_mediumdetail.dds,ff/six",
                "res:/dx9/model/turret/turret.gr2,cc/three",
                "res:/audio/music/test.ogg,dd/four",
            )
        )
        catalog = ResourceCatalog("42", parse_index(rows), Path.cwd(), False)
        root = catalog.browse("res:/", limit=20)
        self.assertEqual([item.name for item in root], ["audio", "dx9"])
        model = catalog.browse("res:/dx9/model/", limit=20)
        self.assertEqual([item.name for item in model], ["ship", "turret"])
        textures = catalog.browse("res:/", query="hull", extensions={".dds"}, limit=20)
        self.assertEqual([item.logical_path for item in textures], ["res:/dx9/model/ship/hull_d.dds"])
        self.assertEqual(catalog.hidden_detail_count, 2)
        self.assertEqual(catalog.browse("res:/", query="lowdetail", limit=20), ())
        self.assertEqual(catalog.browse("res:/", query="mediumdetail", limit=20), ())
        low = catalog.browse("res:/", query="hull", show_lowdetail=True, limit=20)
        self.assertIn("res:/dx9/model/ship/hull_d_lowdetail.dds", [item.logical_path for item in low])
        self.assertNotIn("res:/dx9/model/ship/hull_pmdg_mediumdetail.dds", [item.logical_path for item in low])
        medium = catalog.browse("res:/", query="hull", show_mediumdetail=True, limit=20)
        self.assertIn("res:/dx9/model/ship/hull_pmdg_mediumdetail.dds", [item.logical_path for item in medium])
        self.assertNotIn("res:/dx9/model/ship/hull_d_lowdetail.dds", [item.logical_path for item in medium])
        self.assertEqual(normalize_directory("res:/dx9/model"), "res:/dx9/model/")

    def test_acquires_exact_build_then_reuses_validated_cache(self):
        build = "1234567"
        texture = b"DDS test payload"
        res_text = (index_row("res:/dx9/model/test_d.dds", "cd/texture", texture) + "\n").encode()
        app_text = (index_row("app:/resfileindex.txt", "ab/main-index", res_text) + "\n").encode()
        responses = {
            f"{METADATA_BASE_URL}/eveclient_TQ.json": json.dumps({"build": int(build)}).encode(),
            f"{INDEX_BASE_URL}/eveonline_{build}.txt": app_text,
            f"{APP_BASE_URL}/ab/main-index": res_text,
            f"{RESOURCE_BASE_URL}/cd/texture": texture,
        }
        opener = FakeOpener(responses)

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            first = ensure_latest_catalog(
                cache,
                creator_terms_accepted=True,
                clock=lambda: 1000.0,
                opener=opener,
            )
            self.assertEqual(first.build, build)
            self.assertFalse(first.cache_hit)
            self.assertEqual(len(first.entries), 1)
            self.assertTrue((cache / "ccp" / "builds" / build / "indexes" / "resfileindex.txt").is_file())

            calls = len(opener.urls)
            second = ensure_latest_catalog(cache, creator_terms_accepted=True, opener=opener)
            self.assertTrue(second.cache_hit)
            self.assertEqual(len(opener.urls), calls, "offline-first load should not touch the network")

            limited = ensure_latest_catalog(
                cache,
                creator_terms_accepted=True,
                offline_first=False,
                clock=lambda: 1060.0,
                opener=opener,
            )
            self.assertEqual(limited.build, build)
            self.assertEqual(
                limited.latest_check_deferred_seconds,
                LATEST_BUILD_CHECK_INTERVAL_SECONDS - 60,
            )
            self.assertEqual(len(opener.urls), calls, "cooldown refresh should not touch metadata")

            refreshed = ensure_latest_catalog(
                cache,
                creator_terms_accepted=True,
                offline_first=False,
                clock=lambda: 1001.0 + LATEST_BUILD_CHECK_INTERVAL_SECONDS,
                opener=opener,
            )
            self.assertEqual(refreshed.latest_check_deferred_seconds, 0)
            self.assertEqual(opener.urls[-1], f"{METADATA_BASE_URL}/eveclient_TQ.json")
            self.assertEqual(len(opener.urls), calls + 1)

            fetched = fetch_resource(second.entries[0], cache, creator_terms_accepted=True, opener=opener)
            self.assertFalse(fetched.cache_hit)
            self.assertEqual(fetched.path, cache / "ResFiles" / "cd" / "texture")
            self.assertEqual(fetched.path.read_bytes(), texture)

            fetched_again = fetch_resource(
                second.entries[0],
                cache,
                creator_terms_accepted=True,
                opener=opener,
            )
            self.assertTrue(fetched_again.cache_hit)

            output = Path(temporary) / "downloads"
            materialized = materialize_resource(
                second.entries[0],
                cache,
                output,
                creator_terms_accepted=True,
                opener=opener,
            )
            self.assertEqual(materialized.path, output / "dx9" / "model" / "test_d.dds")
            self.assertEqual(materialized.path.read_bytes(), texture)

            stats = payload_cache_stats(cache)
            self.assertEqual((stats.file_count, stats.byte_count), (1, len(texture)))
            preview = cache / "Previews" / build / "test_d.dds"
            preview.parent.mkdir(parents=True)
            preview.write_bytes(texture)
            index = cache / "ccp" / "builds" / build / "indexes" / "resfileindex.txt"
            cleared = clear_payload_cache(cache)
            self.assertEqual(cleared, stats)
            self.assertEqual(payload_cache_stats(cache).file_count, 0)
            self.assertTrue(index.is_file(), "clearing payloads must retain cached indexes")
            self.assertTrue(materialized.path.is_file(), "clearing cache must retain exported downloads")
            self.assertFalse(preview.exists())
            self.assertTrue(
                (cache / "ccp" / "channels" / "tq" / "latest-build-check.json").is_file(),
                "clearing payloads must retain the build-check cooldown",
            )

    def test_recent_build_check_resumes_an_interrupted_index_without_rechecking_metadata(self):
        build = "7654321"
        old_build = "7654000"
        res_text = b"res:/test.bin,cd/content\n"
        app_text = (index_row("app:/resfileindex.txt", "ab/main-index", res_text) + "\n").encode()
        old_res_text = b"res:/old.bin,ef/old-content\n"
        old_app_text = (index_row("app:/resfileindex.txt", "ef/old-index", old_res_text) + "\n").encode()
        metadata_url = f"{METADATA_BASE_URL}/eveclient_TQ.json"

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            old_indexes = cache / "ccp" / "builds" / old_build / "indexes"
            old_indexes.mkdir(parents=True)
            (old_indexes / "appfileindex.txt").write_bytes(old_app_text)
            (old_indexes / "resfileindex.txt").write_bytes(old_res_text)
            interrupted = FakeOpener({metadata_url: json.dumps({"build": int(build)}).encode()})
            with self.assertRaises(ResourceIndexError):
                ensure_latest_catalog(
                    cache,
                    creator_terms_accepted=True,
                    offline_first=False,
                    clock=lambda: 2000.0,
                    opener=interrupted,
                )

            resumed = FakeOpener(
                {
                    f"{INDEX_BASE_URL}/eveonline_{build}.txt": app_text,
                    f"{APP_BASE_URL}/ab/main-index": res_text,
                }
            )
            catalog = ensure_latest_catalog(
                cache,
                creator_terms_accepted=True,
                offline_first=False,
                clock=lambda: 2060.0,
                opener=resumed,
            )
            self.assertEqual(catalog.build, build)
            self.assertNotIn(metadata_url, resumed.urls)
            self.assertEqual(
                catalog.latest_check_deferred_seconds,
                LATEST_BUILD_CHECK_INTERVAL_SECONDS - 60,
            )

    def test_requires_creator_terms_before_catalog_or_payload_access(self):
        entry = parse_index_line("res:/test.bin,ef/content")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with self.assertRaisesRegex(ResourceIndexError, "Content Creation Terms"):
                ensure_latest_catalog(cache)
            with self.assertRaisesRegex(ResourceIndexError, "Content Creation Terms"):
                fetch_resource(entry, cache)
            with self.assertRaisesRegex(ResourceIndexError, "Content Creation Terms"):
                materialize_resource(entry, cache, cache / "downloads")

    def test_rejects_corrupt_download_and_cache_escape(self):
        entry = parse_index_line(index_row("res:/test.bin", "ef/content", b"expected"))
        opener = FakeOpener({f"{RESOURCE_BASE_URL}/ef/content": b"corrupt"})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ResourceIndexError):
                fetch_resource(
                    entry,
                    Path(temporary),
                    creator_terms_accepted=True,
                    opener=opener,
                )
            with self.assertRaises(ResourceIndexError):
                safe_join(Path(temporary), "..", "escape")


if __name__ == "__main__":
    unittest.main()
