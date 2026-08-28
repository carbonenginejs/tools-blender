import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import URLError


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core.tools_service import (  # noqa: E402
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

            listening = {"yet": False}

            def opener(request, timeout=None):
                # The client probes for an ALREADY running service before
                # spawning one. Nothing is listening here until the sidecar
                # this test is about has been started, so the first probe
                # fails -- which is what makes the spawn happen at all.
                if request.full_url.endswith("/v1/health") and not listening["yet"]:
                    listening["yet"] = True
                    raise URLError("connection refused")
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


class AttachTests(unittest.TestCase):
    """A client must find the service that is already up, not fight it for the port."""

    def _client(self, opener, process_factory):
        with tempfile.TemporaryDirectory() as temporary:
            return ToolsServiceClient(
                "node",
                Path(temporary) / "cjs-tools-service.js",
                Path(temporary) / "cache",
                process_factory=process_factory,
                opener=opener,
            )

    def test_attaches_instead_of_spawning(self):
        # The sidecar binds a FIXED port -- an OAuth callback is registered
        # against it -- so a second instance cannot get a port of its own. It
        # prints "already in use" and exits, and before this every request
        # failed with "exited before bootstrap" whenever a service was running.
        spawned = []

        def process_factory(command, **kwargs):
            spawned.append(command)
            raise AssertionError("must not spawn when a service is already up")

        def opener(request, timeout=None):
            return FakeResponse({
                "ok": True,
                "protocol": "carbon.tools",
                "protocolVersion": 1,
                "capabilities": {"sofCatalog": True},
            })

        client = self._client(opener, process_factory)
        bootstrap = client.start()
        self.assertEqual(spawned, [])
        self.assertEqual(bootstrap.port, 5510)
        self.assertTrue(bootstrap.capabilities["sofCatalog"])

    def test_an_attached_service_is_not_ours_to_stop(self):
        # `running` stays false for an attached client precisely so that stop
        # cannot terminate somebody else's process.
        def opener(request, timeout=None):
            return FakeResponse({
                "ok": True, "protocol": "carbon.tools",
                "protocolVersion": 1, "capabilities": {},
            })

        client = self._client(opener, lambda *a, **k: None)
        client.start()
        self.assertFalse(client.running)
        client.stop()

    def test_a_stranger_on_the_port_is_not_a_service(self):
        # Something else could hold 5510. Talking SOF routes at it would fail
        # far from here and confusingly.
        spawned = []

        def process_factory(command, **kwargs):
            spawned.append(command)
            return FakeProcess({
                "schema": "carbon.tools-service.bootstrap",
                "protocol": "carbon.tools",
                "protocolVersion": 1,
                "host": "127.0.0.1",
                "port": 5510,
                "pid": 4242,
                "cacheDirectory": "",
                "capabilities": {},
            })

        def opener(request, timeout=None):
            return FakeResponse({"ok": True, "protocol": "something.else"})

        client = self._client(opener, process_factory)
        with self.assertRaises(Exception):
            client.start()
        self.assertEqual(len(spawned), 1, "a stranger must not stop us starting our own")
