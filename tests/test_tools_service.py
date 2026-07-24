import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.tools_service import (  # noqa: E402
    ToolsServiceClient,
    ToolsServiceError,
    parse_bootstrap,
)


class FakeProcess:
    def __init__(self, bootstrap):
        self.stdout = io.StringIO(json.dumps(bootstrap) + "\n")
        self.stderr = io.StringIO("")
        self.exit_code = None

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.exit_code = 0

    def kill(self):
        self.exit_code = -9

    def wait(self, timeout=None):
        return self.exit_code


class FakeResponse:
    status = 200

    def __init__(self, value):
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class ToolsServiceTests(unittest.TestCase):
    def test_starts_loopback_sidecar_and_fetches_to_the_shared_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            bootstrap = make_bootstrap(cache)
            commands = []
            requests = []

            def process_factory(command, **kwargs):
                commands.append((command, kwargs))
                return FakeProcess(bootstrap)

            def opener(request, timeout=None):
                requests.append((request, timeout))
                if request.full_url.endswith("/v1/health"):
                    return FakeResponse(
                        {
                            "ok": True,
                            "protocol": "carbon.tools",
                            "protocolVersion": 1,
                            "capabilities": bootstrap["capabilities"],
                        }
                    )
                return FakeResponse(
                    {
                        "resolution": {"logicalPath": "res:/test.gr2"},
                        "byteLength": 42,
                        "cacheHit": True,
                        "cachePath": str(cache / "ResFiles" / "aa" / "content"),
                    }
                )

            client = ToolsServiceClient(
                "node",
                Path(temporary) / "cjs-tools-service.js",
                cache,
                process_factory=process_factory,
                opener=opener,
            )

            self.assertTrue(client.health()["ok"])
            result = client.fetch_resource(
                {"provider": "ccp", "build": "3435006"},
                "res:/test.gr2",
            )

            self.assertTrue(client.running)
            self.assertTrue(client.supports("resources"))
            self.assertEqual(result["byteLength"], 42)
            self.assertEqual(commands[0][0][-2:], ["--cache", str(cache.resolve())])
            self.assertEqual(len(commands), 1, "subsequent requests must reuse the sidecar")
            self.assertIsNone(requests[0][0].get_header("Authorization"))
            request_body = json.loads(requests[1][0].data.decode("utf-8"))
            self.assertEqual(request_body["logicalPath"], "res:/test.gr2")
            self.assertEqual(request_body["options"], {})

            client.stop()
            self.assertFalse(client.running)

    def test_requires_an_exact_resource_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = ToolsServiceClient(
                "node",
                Path(temporary) / "cjs-tools-service.js",
                Path(temporary) / "cache",
            )
            with self.assertRaisesRegex(ValueError, "exact numeric build"):
                client.resolve_resource(
                    {"provider": "ccp", "build": "latest"},
                    "res:/test.gr2",
                )

    def test_rejects_non_loopback_or_incompatible_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            bootstrap = make_bootstrap(cache)
            bootstrap["host"] = "0.0.0.0"
            with self.assertRaisesRegex(ToolsServiceError, "loopback"):
                parse_bootstrap(json.dumps(bootstrap), cache)

            bootstrap = make_bootstrap(cache)
            bootstrap["protocolVersion"] = 2
            with self.assertRaisesRegex(ToolsServiceError, "protocol"):
                parse_bootstrap(json.dumps(bootstrap), cache)


def make_bootstrap(cache):
    return {
        "schema": "carbon.tools-service.bootstrap",
        "protocol": "carbon.tools",
        "protocolVersion": 1,
        "host": "127.0.0.1",
        "port": 43123,
        "pid": 1234,
        "cacheDirectory": str(cache.resolve()),
        "capabilities": {"resources": True},
    }


if __name__ == "__main__":
    unittest.main()
