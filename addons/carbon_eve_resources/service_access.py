"""One tools-core client for the panels to share.

The panels need the service for two small things -- the material catalog and a
material's values -- and both are reached from UI callbacks that can fire on
every redraw. Starting a sidecar per callback would be absurd, so one client is
made and kept.

Failure is normal here and is not an error: a person may have no tools-core
checkout configured, and everything except the dropdown works without one. So
this returns None rather than raising, and the callers show an empty catalog.
"""

from __future__ import annotations

from pathlib import Path

import bpy


_CLIENT = {"client": None, "key": None}


def _preferences(context):
    package = __package__.split(".")[0]
    addon = context.preferences.addons.get(package) if context else None
    return getattr(addon, "preferences", None)


def client(context=None):
    """A started client, or None when tools-core is not configured."""

    prefs = _preferences(context or bpy.context)
    if prefs is None:
        return None
    root = str(getattr(prefs, "tools_core_directory", "") or "").strip()
    if not root:
        return None
    node = str(getattr(prefs, "node_executable", "") or "node").strip() or "node"

    # Rebuilt only when the settings that define it change, so a redraw reuses
    # the running sidecar.
    key = (root, node)
    if _CLIENT["client"] is not None and _CLIENT["key"] == key:
        return _CLIENT["client"]

    from .tools_service import ToolsServiceClient

    try:
        cache = Path(bpy.path.abspath(
            str(getattr(prefs, "cache_directory", "") or "").strip() or root))
        made = ToolsServiceClient(
            node_executable=node,
            service_script=Path(bpy.path.abspath(root)) / "bin" / "cjs-tools-service.js",
            cache_root=cache,
        )
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] tools-core unavailable: {exc}")
        return None
    _CLIENT["client"] = made
    _CLIENT["key"] = key
    return made


def forget():
    """Drops the client, for when preferences change."""

    _CLIENT["client"] = None
    _CLIENT["key"] = None
