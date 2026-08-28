"""One tools-core client for the panels to share.

The hosted service, so an installed add-on needs nothing but the zip -- no
checkout, no Node, no bundle. `core/tools_service.py` still holds a client for
a local sidecar, against the day one ships with the tool; nothing reaches it
today, and nothing should reach it through a preference an artist has to fill
in.
"""

from __future__ import annotations

import bpy


_CLIENT = {"client": None, "key": None}


def _preferences(context):
    package = __package__.split(".")[0]
    addon = context.preferences.addons.get(package) if context else None
    return getattr(addon, "preferences", None)


def service_url(context=None) -> str:
    from .core.tools_remote import DEFAULT_SERVICE_URL

    prefs = _preferences(context or bpy.context)
    return str(getattr(prefs, "service_url", "") or "").strip() or DEFAULT_SERVICE_URL


def client(context=None):
    """A client for the service, or None when neither can be reached."""

    prefs = _preferences(context or bpy.context)
    # The name index is 6.4MB and changes only when EVE does, so it is kept in
    # the cache between sessions rather than downloaded on every start.
    from .core import sof_lookup

    cache = str(getattr(prefs, "cache_directory", "") or "").strip()
    root = bpy.path.abspath(cache) if cache else None
    sof_lookup.CACHE_ROOT["path"] = root

    url = service_url(context)
    if _CLIENT["client"] is not None and _CLIENT["key"] == url:
        return _CLIENT["client"]

    from .core.tools_remote import RemoteToolsClient

    try:
        made = RemoteToolsClient(url)
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] service unavailable: {exc}")
        return None

    _CLIENT["client"] = made
    _CLIENT["key"] = url
    return made


def forget():
    """Drops the client, for when preferences change."""

    _CLIENT["client"] = None
    _CLIENT["key"] = None
