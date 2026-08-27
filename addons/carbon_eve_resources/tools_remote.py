"""A tools-core client that talks to a hosted service over HTTPS.

Same protocol and routes as the local sidecar, without Node, a checkout, or a
child process. This is what a downloaded add-on uses: the person installs a
zip, and nothing else.

Resource BYTES are not proxied. `/v1/resources/resolve` answers with a
`sourceUrl` on `resources.eveonline.com`, and the file is fetched from CCP
directly.
"""

from __future__ import annotations

import json
from typing import Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


#: The hosted service. `www` redirects to the apex, so the apex is used.
DEFAULT_SERVICE_URL = "https://caldariprimeponyclub.com"

PROTOCOL = "carbon.tools"
PROTOCOL_VERSION = 1

#: Cloudflare answers 1010 to urllib's default agent, so the add-on names
#: itself. Without this every route is a 403 that looks like the service being
#: down.
USER_AGENT = "CarbonEngineJS-Blender"


class RemoteServiceError(RuntimeError):
    """Raised when the hosted service cannot be reached or answers badly."""


class RemoteToolsClient:
    """Reads tools-core routes from a hosted service.

    Interchangeable with `ToolsServiceClient` for everything that only reads:
    `request_json`, `_request`, `health` and `supports`.
    """

    def __init__(self, base_url: str = DEFAULT_SERVICE_URL, *,
                 request_timeout: float = 60.0, opener=urlopen):
        self.base_url = str(base_url or DEFAULT_SERVICE_URL).strip().rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("service url must be http or https")
        self.request_timeout = float(request_timeout)
        self._opener = opener
        self._capabilities: Optional[dict] = None

    def request_json(self, method: str, route: str, body: Optional[Mapping] = None):
        """One route, returning whatever JSON it answers with."""

        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{route}", data=data, headers=headers,
                          method=method)
        try:
            with self._opener(request, timeout=self.request_timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RemoteServiceError(f"{self.base_url}{route}: HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError) as exc:
            raise RemoteServiceError(f"{self.base_url}{route}: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteServiceError(f"{self.base_url}{route}: invalid JSON") from exc

    def _request(self, method: str, route: str, body: Optional[Mapping] = None) -> dict:
        value = self.request_json(method, route, body)
        if not isinstance(value, dict):
            raise RemoteServiceError("JSON root must be an object")
        return value

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def supports(self, capability: str) -> bool:
        if self._capabilities is None:
            answer = self.health()
            if (answer.get("protocol") != PROTOCOL
                    or answer.get("protocolVersion") != PROTOCOL_VERSION):
                raise RemoteServiceError("unsupported tools-core protocol")
            found = answer.get("capabilities")
            self._capabilities = found if isinstance(found, dict) else {}
        return bool(self._capabilities.get(str(capability), False))

    def resolve_resource(self, logical_path: str, build: str, *,
                         target: str = "eve", provider: str = "ccp") -> dict:
        """Where one resource lives, including the URL to fetch it from.

        All three source fields are required, and the build must be an exact
        number: `latest` is refused.
        """

        return self._request("POST", "/v1/resources/resolve", {
            "source": {"target": target, "provider": provider, "build": str(build)},
            "logicalPath": str(logical_path),
        })

    def stop(self, timeout: float = 5.0) -> None:
        """Nothing to stop; kept so callers need not know which client they hold."""
