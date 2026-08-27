"""One tools-core client for the panels to share.

The hosted service by default, so an installed add-on needs nothing but the
zip. A local tools-core checkout is used only when one is configured, which is
for people working on tools-core itself.
"""

from __future__ import annotations

from pathlib import Path

import bpy


_CLIENT = {"client": None, "key": None}


def _preferences(context):
    package = __package__.split(".")[0]
    addon = context.preferences.addons.get(package) if context else None
    return getattr(addon, "preferences", None)


def service_url(context=None) -> str:
    from .tools_remote import DEFAULT_SERVICE_URL

    prefs = _preferences(context or bpy.context)
    return str(getattr(prefs, "service_url", "") or "").strip() or DEFAULT_SERVICE_URL


def client(context=None):
    """A client for the service, or None when neither can be reached."""

    prefs = _preferences(context or bpy.context)
    # The name index is 6.4MB and changes only when EVE does, so it is kept in
    # the cache between sessions rather than downloaded on every start.
    from . import sof_lookup

    cache = str(getattr(prefs, "cache_directory", "") or "").strip()
    sof_lookup.CACHE_ROOT["path"] = bpy.path.abspath(cache) if cache else None
    root = str(getattr(prefs, "tools_core_directory", "") or "").strip()
    node = str(getattr(prefs, "node_executable", "") or "node").strip() or "node"
    url = service_url(context)

    key = (root, node, url)
    if _CLIENT["client"] is not None and _CLIENT["key"] == key:
        return _CLIENT["client"]

    made = _local(root, node) if root else None
    if made is None:
        from .tools_remote import RemoteToolsClient

        try:
            made = RemoteToolsClient(url)
        except Exception as exc:
            print(f"[CarbonEngineJS SOF] service unavailable: {exc}")
            return None

    _CLIENT["client"] = made
    _CLIENT["key"] = key
    return made


def _local(root: str, node: str):
    """A client for a local checkout, or None when it cannot be built."""

    from .tools_service import ToolsServiceClient

    script = Path(bpy.path.abspath(root)) / "bin" / "cjs-tools-service.js"
    if not script.is_file():
        return None
    try:
        return ToolsServiceClient(
            node_executable=node,
            service_script=script,
            cache_root=Path(bpy.path.abspath(root)),
        )
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] local tools-core unavailable: {exc}")
        return None


def forget():
    """Drops the client, for when preferences change."""

    _CLIENT["client"] = None
    _CLIENT["key"] = None
