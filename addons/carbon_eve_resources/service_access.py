"""One tools-core client for the panels to share.

The hosted service, so an installed add-on needs nothing but the zip -- no
checkout, child process, or bundle.
"""

from __future__ import annotations

import bpy


_CLIENT = {"client": None, "key": None}
_SOURCES = {}


def source_target(context=None, settings=None):
    """Selected target without fetching build metadata (safe for UI drawing)."""
    if settings is not None and getattr(settings, "source_target", ""):
        return settings.source_target
    state = getattr((context or bpy.context).window_manager, "carbon_eve_resources", None)
    return getattr(state, "source", "eve")


def source(context=None, settings=None):
    """The object's pinned source, or the browser's current selection."""
    from .core.source import Source, resolve
    context = context or bpy.context
    if settings is not None and getattr(settings, "source_target", ""):
        return Source(settings.source_target, settings.source_provider,
                      settings.resource_build, settings.sde_build)
    target = source_target(context, settings)
    key = (service_url(context), target)
    if key not in _SOURCES:
        prefs = _preferences(context)
        cache = str(getattr(prefs, "cache_directory", "") or "").strip()
        _SOURCES[key] = resolve(client(context), target, bpy.path.abspath(cache) if cache else None)
    return _SOURCES[key]


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
    from .core import nebula, sof_lookup, weapons

    cache = str(getattr(prefs, "cache_directory", "") or "").strip()
    root = bpy.path.abspath(cache) if cache else None
    sof_lookup.CACHE_ROOT["path"] = root
    nebula.CACHE_ROOT["path"] = root
    weapons.CACHE_ROOT["path"] = root

    # Everything this add-on WRITES goes in the cache. The two local folders
    # are read-only source material, and the decoder needs to know which is
    # which so a translated texture never lands in one of them.
    from .dds import reader as dds_reader

    def folder(name):
        value = str(getattr(prefs, name, "") or "").strip()
        enabled = bool(getattr(prefs, "use_local_source", False))
        return bpy.path.abspath(value) if value and enabled else None

    dds_reader.ROOTS["cache"] = root
    dds_reader.ROOTS["local"] = folder("local_source")
    dds_reader.ROOTS["resfiles"] = folder("local_resfiles")
    # Keep both read-only roots registered even when a pinned ship's Source
    # differs from the browser. Derived files always belong in the shared cache.
    dds_reader.ROOTS["frontier_resfiles"] = folder("frontier_resfiles")

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
    _SOURCES.clear()
