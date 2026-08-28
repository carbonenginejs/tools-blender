"""Client boundary for the local CarbonEngineJS tools-core service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import queue
import subprocess
import threading
from typing import Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BOOTSTRAP_SCHEMA = "carbon.tools-service.bootstrap"
PROTOCOL = "carbon.tools"
PROTOCOL_VERSION = 1
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

#: The port the sidecar binds. It is fixed rather than negotiated -- an OAuth
#: callback is registered against it -- so only one service can run per machine
#: and a client's real job is to find that one, not to own it.
DEFAULT_PORT = 5510


class ToolsServiceError(RuntimeError):
    """Raised when the local service cannot start or violates its protocol."""


@dataclass(frozen=True, slots=True)
class ToolsServiceBootstrap:
    protocol: str
    protocol_version: int
    host: str
    port: int
    pid: int
    cache_directory: Path
    capabilities: Mapping[str, bool]


class ToolsServiceClient:
    """Starts one tools-core sidecar and performs loopback JSON requests."""

    def __init__(
        self,
        node_executable: str,
        service_script: Path,
        cache_root: Path,
        *,
        startup_timeout: float = 15.0,
        request_timeout: float = 60.0,
        process_factory: Callable = subprocess.Popen,
        opener: Callable = urlopen,
    ):
        executable = str(node_executable or "").strip()
        if not executable:
            raise ValueError("node_executable is required")
        if startup_timeout <= 0 or request_timeout <= 0:
            raise ValueError("service timeouts must be positive")

        self.node_executable = executable
        self.service_script = Path(service_script).expanduser().resolve()
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.startup_timeout = float(startup_timeout)
        self.request_timeout = float(request_timeout)
        self._process_factory = process_factory
        self._opener = opener
        self._process = None
        self._bootstrap: Optional[ToolsServiceBootstrap] = None

    @property
    def bootstrap(self) -> Optional[ToolsServiceBootstrap]:
        return self._bootstrap

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> ToolsServiceBootstrap:
        if self.running and self._bootstrap is not None:
            return self._bootstrap

        # Attach to a service that is already up before trying to start one.
        # The sidecar binds a fixed port, so a second instance does not get a
        # port of its own -- it prints "already in use" and exits, and every
        # request then failed with "exited before bootstrap" for as long as
        # anyone had a service running elsewhere. Which is most of the time.
        attached = self._attach()
        if attached is not None:
            self._bootstrap = attached
            return attached

        self.stop()
        command = [
            self.node_executable,
            str(self.service_script),
            "--cache",
            str(self.cache_root),
        ]
        self._process = self._process_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        try:
            line = _readline_with_timeout(self._process.stdout, self.startup_timeout)
            if not line:
                raise ToolsServiceError("tools-core service exited before bootstrap")
            self._bootstrap = parse_bootstrap(line, self.cache_root)
            return self._bootstrap
        except Exception as exc:
            self.stop()
            if isinstance(exc, ToolsServiceError):
                raise
            raise ToolsServiceError(f"Invalid tools-core service bootstrap: {exc}") from exc

    def _attach(self) -> Optional[ToolsServiceBootstrap]:
        """A service already listening on the fixed port, or None.

        Health is checked rather than assumed: something else could hold the
        port, and talking SOF routes at it would produce confusing failures
        far from here. A service that answers with the wrong protocol is
        treated as no service at all.

        The process is NOT ours, so `stop` must never kill it -- `_process`
        stays None, which is exactly what makes `running` false for an attached
        client and keeps `stop` a no-op.
        """

        # A Request, not a bare URL: the opener is injectable and every other
        # call hands it one, so a string here only works against the real
        # urlopen and breaks anything standing in for it.
        probe = Request(f"http://127.0.0.1:{DEFAULT_PORT}/v1/health", method="GET")
        try:
            with self._opener(probe, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or not payload.get("ok"):
            return None
        if (payload.get("protocol") != PROTOCOL
                or payload.get("protocolVersion") != PROTOCOL_VERSION):
            return None
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict):
            return None
        # Health does not report a cache directory or a pid, and neither is
        # ours to claim. The pid is recorded as 0 to say plainly that this
        # client did not start the process it is talking to.
        return ToolsServiceBootstrap(
            protocol=PROTOCOL,
            protocol_version=PROTOCOL_VERSION,
            host="127.0.0.1",
            port=DEFAULT_PORT,
            pid=0,
            cache_directory=Path(self.cache_root),
            capabilities={str(name): bool(value)
                          for name, value in capabilities.items()},
        )

    def stop(self, timeout: float = 5.0) -> None:
        process = self._process
        self._process = None
        self._bootstrap = None
        if process is None or process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def supports(self, capability: str) -> bool:
        name = str(capability or "").strip()
        if not name:
            raise ValueError("capability is required")
        return bool(self.start().capabilities.get(name, False))

    def resolve_resource(
        self,
        source: Mapping[str, object],
        logical_path: str,
        options: Optional[Mapping[str, object]] = None,
    ) -> dict:
        request = _resource_request(source, logical_path, options)
        if not self.supports("resources"):
            raise ToolsServiceError("tools-core service does not advertise resource support")
        return self._request(
            "POST",
            "/v1/resources/resolve",
            request,
        )

    def fetch_resource(
        self,
        source: Mapping[str, object],
        logical_path: str,
        options: Optional[Mapping[str, object]] = None,
    ) -> dict:
        request = _resource_request(source, logical_path, options)
        if not self.supports("resources"):
            raise ToolsServiceError("tools-core service does not advertise resource support")
        return self._request(
            "POST",
            "/v1/resources/fetch",
            request,
        )

    def hull_decal_sets(self, hull: str, build: str = "latest", target: str = "eve") -> list:
        """The named decal sets of one hull, with their visibility groups.

        A built ship carries neither: `EveSOF` copies a decal's transform, bone
        and effect and leaves the set's name and visibility group behind. They
        exist only here, on the hull record.

        The build must be the RESOURCE build. `latest` resolves to two different
        numbers -- one for resources, one for the SDE -- and a SOF route given
        the SDE build silently acquires a whole second client build.

        Returns an empty list when the hull has none, rather than raising: a
        hull without decals is ordinary.
        """

        record = self._request("GET", f"/{target}/{build}/sof/hulls/{hull}")
        sets = record.get("decalSets") if isinstance(record, Mapping) else None
        return list(sets or [])

    def _request(self, method: str, route: str, body: Optional[Mapping] = None) -> dict:
        """A route whose answer is an object.

        Most are. The few that are not go through `request_json`, and keeping
        the object check here means a route that starts returning something
        else is caught rather than quietly handed on.
        """

        value = self.request_json(method, route, body)
        if not isinstance(value, dict):
            raise ToolsServiceError("tools-core service JSON root must be an object")
        return value

    def request_json(self, method: str, route: str, body: Optional[Mapping] = None):
        """A route whose answer is any JSON value.

        The SOF catalog routes answer with an ARRAY -- `/sof/materials` is a
        list of 1149 names -- and requiring an object root made every one of
        them fail with a message about the root that gave no hint which route
        was at fault.
        """

        bootstrap = self.start()
        host = f"[{bootstrap.host}]" if ":" in bootstrap.host else bootstrap.host
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"http://{host}:{bootstrap.port}{route}",
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with self._opener(request, timeout=self.request_timeout) as response:
                status = int(getattr(response, "status", 200))
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise ToolsServiceError(f"tools-core service HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError) as exc:
            raise ToolsServiceError(f"tools-core service request failed: {exc}") from exc

        if not 200 <= status < 300:
            raise ToolsServiceError(f"tools-core service returned HTTP {status}")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ToolsServiceError("tools-core service returned invalid JSON") from exc
        return value

    def __enter__(self) -> "ToolsServiceClient":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.stop()
        return False


def parse_bootstrap(line: str, expected_cache_root: Path) -> ToolsServiceBootstrap:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ToolsServiceError("bootstrap is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != BOOTSTRAP_SCHEMA:
        raise ToolsServiceError("unsupported bootstrap schema")
    if value.get("protocol") != PROTOCOL or value.get("protocolVersion") != PROTOCOL_VERSION:
        raise ToolsServiceError("unsupported tools-core service protocol")

    host = str(value.get("host", "")).lower()
    if host not in LOOPBACK_HOSTS:
        raise ToolsServiceError("tools-core service did not bind to loopback")
    port = value.get("port")
    pid = value.get("pid")
    capabilities = value.get("capabilities")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ToolsServiceError("bootstrap port is invalid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        raise ToolsServiceError("bootstrap process id is invalid")
    if not isinstance(capabilities, dict) or any(
        not isinstance(name, str) or not isinstance(enabled, bool)
        for name, enabled in capabilities.items()
    ):
        raise ToolsServiceError("bootstrap capabilities are invalid")

    cache_directory = Path(str(value.get("cacheDirectory", ""))).expanduser().resolve()
    if cache_directory != Path(expected_cache_root).expanduser().resolve():
        raise ToolsServiceError("tools-core service selected an unexpected cache directory")
    return ToolsServiceBootstrap(
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        host=host,
        port=port,
        pid=pid,
        cache_directory=cache_directory,
        capabilities=dict(capabilities),
    )


def _resource_request(
    source: Mapping[str, object],
    logical_path: str,
    options: Optional[Mapping[str, object]],
) -> dict:
    if not isinstance(source, Mapping):
        raise TypeError("source must be a mapping")
    path = str(logical_path or "").strip()
    if not path:
        raise ValueError("logical_path is required")
    if options is not None and not isinstance(options, Mapping):
        raise TypeError("options must be a mapping")
    normalized_source = dict(source)
    provider = str(normalized_source.get("provider", "")).strip().lower()
    build = str(normalized_source.get("build", "")).strip()
    if not provider:
        raise ValueError("source provider is required")
    if not build.isdigit():
        raise ValueError("source build must be an exact numeric build")
    normalized_source["provider"] = provider
    normalized_source["build"] = build
    return {
        "source": normalized_source,
        "logicalPath": path,
        "options": dict(options or {}),
    }


def _readline_with_timeout(stream, timeout: float) -> str:
    result = queue.Queue(maxsize=1)

    def read_line() -> None:
        try:
            result.put((True, stream.readline()))
        except Exception as exc:
            result.put((False, exc))

    threading.Thread(target=read_line, daemon=True).start()
    try:
        succeeded, value = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise ToolsServiceError("timed out waiting for tools-core service bootstrap") from exc
    if not succeeded:
        raise ToolsServiceError(f"failed to read tools-core service bootstrap: {value}")
    return value
