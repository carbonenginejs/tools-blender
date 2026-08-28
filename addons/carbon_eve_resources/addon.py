from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional

import bpy
import bpy.utils.previews
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, PropertyGroup, UIList

from .core.resource_index import (
    CacheStats,
    ResourceCatalog,
    ResourceIndexError,
    clear_payload_cache,
    ensure_latest_catalog,
    materialize_resource,
    payload_cache_stats,
    safe_join,
)
from .core.sof_builder import SofBuilderError, normalize_dna


ADDON_ID = __package__ or "carbon_eve_resources"


def _gr2_settings():
    """The importer's settings class, imported late.

    Late because the importer imports `bpy.types` at module scope and this
    module is imported while the add-on is still being registered.
    """

    from .gr2_importer.addon import GR2ImporterPreferences

    return GR2ImporterPreferences
IMAGE_EXTENSIONS = {".bmp", ".dds", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff"}
MODEL_EXTENSIONS = {".gr2"}
DATA_EXTENSIONS = {".black", ".blue", ".json", ".red", ".xml", ".yaml", ".yml"}
DEFAULT_DIRECTORY = "res:/dx9/model/ship/"
CREATOR_TERMS_TITLE = "EVE Online Content Creation Terms of Use"
CREATOR_TERMS_URL = (
    "https://support.eveonline.com/hc/en-us/articles/"
    "8563917741084-EVE-Online-Content-Creation-Terms-of-Use"
)
CREATOR_TERMS_REVISION = "2024-08-07"
CREATOR_TERMS_ACCEPTANCE_ID = "eve-content-creation-terms-2024-08-07"

_catalog: Optional[ResourceCatalog] = None
_job: Optional["_BackgroundJob"] = None
_registered = False
_suppress_selection_actions = False
_preview_collection = None
_cache_stats_loaded = False


def _default_cache_directory() -> str:
    return bpy.utils.user_resource(
        "DATAFILES",
        path="carbonenginejs/tool-core",
        create=False,
    )


def _detail_filter_updated(self, context) -> None:
    _populate_results(context)


def _active_result_updated(self, context) -> None:
    _on_active_result_changed(self, context)


def _creator_terms_accepted(prefs) -> bool:
    return prefs.creator_terms_revision == CREATOR_TERMS_ACCEPTANCE_ID


def _context_terms_accepted(context) -> bool:
    try:
        return _creator_terms_accepted(_prefs(context))
    except (AttributeError, ResourceIndexError):
        return False


class EVE_RESOURCE_Preferences(AddonPreferences):
    bl_idname = ADDON_ID

    cache_directory: StringProperty(
        name="Tool cache",
        description="Shared content-addressed cache used for EVE indexes and ResFiles",
        subtype="DIR_PATH",
        default=_default_cache_directory(),
    )
    auto_load: BoolProperty(
        name="Load cached/latest index on startup",
        description="Open a cached exact build, or download the latest Tranquility index when none is cached",
        default=True,
    )
    result_limit: IntProperty(
        name="Maximum visible results",
        description="Limits rows sent to Blender's UI; refine the folder, search, or type filter to see more",
        default=300,
        min=25,
        max=2000,
    )
    #: An optional folder of hand-authored files, laid out by LOGICAL path --
    #: `dx9/model/ship/.../gb2_t1_a.tga` -- so a person can drop in their own
    #: textures and geometry without knowing anything about content addressing.
    use_local_source: BoolProperty(
        name="Use local files",
        description="Look in a local folder before the cache or CCP",
        default=False,
    )
    local_source: StringProperty(
        name="Local files",
        description="Folder of hand-authored resources, laid out by logical "
                    "path. Textures are taken as .tga first, then .dds",
        subtype="DIR_PATH",
        default="",
    )
    #: The other shape a local folder comes in: somebody's ResFiles, laid out
    #: the way the cache is. Kept separate from the authored folder because
    #: they are addressed differently and a person has one, the other, or both.
    #:
    #: Both are READ ONLY. Anything translated out of them is written to our
    #: own cache -- never beside the file it came from.
    local_resfiles: StringProperty(
        name="Local ResFiles",
        description="Folder laid out like the cache (ResFiles/<shard>/<name>), "
                    "read before downloading. Never written to",
        subtype="DIR_PATH",
        default="",
    )
    #: Blender 4 and 5 default to AgX, which is a FILM look: it desaturates
    #: and rolls colour off on purpose. EVE's textures and material colours are
    #: authored to be shown as they are, so a hull came out washed out and its
    #: blacks came out grey next to the same ship in the game.
    standard_view_transform: BoolProperty(
        name="Show colours as authored",
        description="Set the scene's view transform to Standard when a ship "
                    "loads. Blender's AgX default desaturates EVE's colours",
        default=True,
    )
    creator_terms_revision: StringProperty(default="", options={"HIDDEN"})
    creator_terms_accepted_at: StringProperty(default="", options={"HIDDEN"})

    #: The GR2 importer's settings, which used to be a second AddonPreferences
    #: of its own. One add-on has one preferences class, so they live here.
    gr2: PointerProperty(type=_gr2_settings())

    def draw(self, context):
        # Two paths, and the licence. Everything else here belonged to the
        # resource browser or to running tools-core locally; both are on their
        # way out, and neither is a question to put to someone installing a zip.
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        accepted = _creator_terms_accepted(self)
        row = layout.row(align=True)
        row.label(text="I accept the EVE Online Creator License",
                  icon="CHECKBOX_HLT" if accepted else "CHECKBOX_DEHLT")
        row.operator(EVE_RESOURCE_OT_open_creator_terms.bl_idname, text="", icon="URL")
        if accepted:
            row.operator(EVE_RESOURCE_OT_revoke_creator_terms.bl_idname, text="Revoke")
        else:
            row.operator(EVE_RESOURCE_OT_accept_creator_terms.bl_idname, text="Accept")
        layout.prop(self, "cache_directory")
        layout.prop(self, "standard_view_transform")

        row = layout.row(align=True)
        row.prop(self, "use_local_source")
        local = layout.column()
        local.enabled = self.use_local_source
        local.prop(self, "local_source")
        local.prop(self, "local_resfiles")


class EVE_RESOURCE_OT_open_creator_terms(Operator):
    bl_idname = "carbon.eve_resource_open_creator_terms"
    bl_label = "Read Official Terms"
    bl_description = "Open CCP's live EVE Online Content Creation Terms of Use"

    def execute(self, context):
        bpy.ops.wm.url_open(url=CREATOR_TERMS_URL)
        return {"FINISHED"}


class EVE_RESOURCE_OT_accept_creator_terms(Operator):
    bl_idname = "carbon.eve_resource_accept_creator_terms"
    bl_label = "Accept EVE Creator License"
    bl_description = "Review and accept the official EVE Online Content Creation Terms of Use"

    agree: BoolProperty(
        name="I have read and agree to the official terms",
        default=False,
    )

    def invoke(self, context, event):
        self.agree = False
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        layout = self.layout
        layout.label(text="EVE Creator License", icon="LOCKED")
        layout.label(text=CREATOR_TERMS_TITLE)
        layout.label(text=f"Published revision: {CREATOR_TERMS_REVISION}")
        layout.label(text="The official live page controls; this add-on does not replace those terms.")
        layout.operator(EVE_RESOURCE_OT_open_creator_terms.bl_idname, text="Open Official Terms", icon="URL")
        layout.separator()
        layout.prop(self, "agree")

    def execute(self, context):
        if not self.agree:
            self.report({"ERROR"}, "Open, read, and agree to the official terms before continuing")
            return {"CANCELLED"}
        prefs = _prefs(context)
        previous_revision = prefs.creator_terms_revision
        previous_time = prefs.creator_terms_accepted_at
        prefs.creator_terms_revision = CREATOR_TERMS_ACCEPTANCE_ID
        prefs.creator_terms_accepted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            bpy.ops.wm.save_userpref()
        except Exception as exc:
            prefs.creator_terms_revision = previous_revision
            prefs.creator_terms_accepted_at = previous_time
            self.report({"ERROR"}, f"Could not save acceptance: {exc}")
            return {"CANCELLED"}
        state = context.window_manager.carbon_eve_resources
        state.status = "EVE Creator License accepted"
        if prefs.auto_load and _catalog is None and _job is None:
            _start_catalog_job(context, refresh=False)
        return {"FINISHED"}


class EVE_RESOURCE_OT_revoke_creator_terms(Operator):
    bl_idname = "carbon.eve_resource_revoke_creator_terms"
    bl_label = "Revoke Acceptance"
    bl_description = "Revoke acceptance and disable EVE resource browsing and downloads"

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy and _context_terms_accepted(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        global _catalog, _preview_collection
        prefs = _prefs(context)
        previous_revision = prefs.creator_terms_revision
        previous_time = prefs.creator_terms_accepted_at
        prefs.creator_terms_revision = ""
        prefs.creator_terms_accepted_at = ""
        try:
            bpy.ops.wm.save_userpref()
        except Exception as exc:
            prefs.creator_terms_revision = previous_revision
            prefs.creator_terms_accepted_at = previous_time
            self.report({"ERROR"}, f"Could not save revoked acceptance: {exc}")
            return {"CANCELLED"}
        _catalog = None
        state = context.window_manager.carbon_eve_resources
        state.results.clear()
        state.build = ""
        state.preview_image = None
        state.preview_logical_path = ""
        state.preview_error_path = ""
        state.status = "EVE Creator License acceptance is required"
        if _preview_collection is not None:
            _preview_collection.clear()
        return {"FINISHED"}


class EVE_RESOURCE_Result(PropertyGroup):
    logical_path: StringProperty()
    display_name: StringProperty()
    is_directory: BoolProperty(default=False)
    size: IntProperty(default=0, min=0)
    cached: BoolProperty(default=False)


class EVE_RESOURCE_State(PropertyGroup):
    status: StringProperty(default="Resource index is not loaded")
    busy: BoolProperty(default=False)
    build: StringProperty(default="")
    current_directory: StringProperty(default=DEFAULT_DIRECTORY)
    query: StringProperty(
        name="Search",
        description="Case-insensitive substring search across complete res:/ paths",
        default="",
    )
    file_filter: EnumProperty(
        name="Type",
        items=(
            ("ALL", "All files", "Show every file type"),
            ("IMAGES", "Textures", "Show Blender-previewable image and texture files"),
            ("MODELS", "GR2", "Show Granny GR2 files"),
            ("DATA", "Data", "Show common Carbon data and descriptor files"),
        ),
        default="ALL",
    )
    show_lowdetail: BoolProperty(
        name="Low",
        description="Show _lowdetail resource variants",
        default=False,
        update=_detail_filter_updated,
    )
    show_mediumdetail: BoolProperty(
        name="Medium",
        description="Show _mediumdetail resource variants",
        default=False,
        update=_detail_filter_updated,
    )
    results: CollectionProperty(type=EVE_RESOURCE_Result)
    active_index: IntProperty(
        default=0,
        update=_active_result_updated,
    )
    result_summary: StringProperty(default="")
    cache_summary: StringProperty(default="Downloaded cache: calculating...")
    dna: StringProperty(
        name="DNA",
        description="SOF DNA to build, for example cf1_t1:caldarinavy:caldari",
        default="",
    )
    preview_image: PointerProperty(type=bpy.types.Image)
    preview_logical_path: StringProperty(default="")
    preview_error_path: StringProperty(default="")


class EVE_RESOURCE_OT_build_sof_dna(Operator):
    """Builds a DNA into a bundle with tools-core, then assembles it."""

    bl_idname = "carbon.eve_resource_build_sof_dna"
    bl_label = "Build DNA"
    bl_description = (
        "Run tools-core to compose this SOF DNA into a bundle, then import its "
        "geometry, mesh areas, and textures"
    )

    #: The DNA to build, when the caller has one in hand.
    #:
    #: The panel shows the SHIP's DNA once a ship is loaded, so reading the
    #: browser's own field here built whatever was last typed there instead of
    #: what the person was looking at -- usually nothing, which failed with a
    #: message about an empty DNA while a perfectly good one was on screen.
    dna: StringProperty(default="", options={"HIDDEN"})
    refresh: BoolProperty(
        name="Rebuild",
        description="Rebuild the bundle even when one already exists for this DNA",
        default=False,
        options={"HIDDEN"},
    )
    include_secondary_meshes: BoolProperty(
        name="Include additional meshes",
        description="Also import meshes the document attaches to the hull",
        default=False,
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy and _context_terms_accepted(context)

    def execute(self, context):
        # Fetched, not built. The document comes from the service and the files
        # from CCP, so there is no bundle on disk, no Node and no checkout.
        from . import service_access
        from .core import sof_fetch

        state = context.window_manager.carbon_eve_resources
        prefs = _prefs(context)
        try:
            dna = normalize_dna(self.dna or state.dna)
        except SofBuilderError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        client = service_access.client(context)
        if client is None:
            self.report({"ERROR"}, "The CarbonEngineJS service is unreachable")
            return {"CANCELLED"}
        # Our OWN root, with the same layout beneath it. Game install,
        # tools-core and this add-on all store
        # `ResFiles/<shard>/<pathHash>_<md5>`; only the root differs, so a
        # store can be pointed at any of them and still resolve.
        cache_root = _cache_path(prefs)

        local_root = (bpy.path.abspath(prefs.local_source)
                      if prefs.use_local_source and prefs.local_source else None)
        resfiles_root = (bpy.path.abspath(prefs.local_resfiles)
                         if prefs.use_local_source and prefs.local_resfiles
                         else None)

        def fetch():
            return sof_fetch.fetch_ship(dna, client, cache_root,
                                        progress=_set_progress,
                                        cancelled=_job_cancelled,
                                        local_root=local_root,
                                        resfiles_root=resfiles_root)

        _launch_job(context, "sof_fetch",
                    lambda: _run_with_cache_stats(fetch, cache_root),
                    f"Fetching {dna}")
        return {"FINISHED"}


class EVE_RESOURCE_OT_refresh_cache_stats(Operator):
    bl_idname = "carbon.eve_resource_refresh_cache_stats"
    bl_label = "Refresh Downloaded Total"
    bl_description = "Recount unique files in the downloaded payload cache"

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy

    def execute(self, context):
        cache_root = _cache_path(_prefs(context))
        _launch_job(
            context,
            "cache_stats",
            lambda: payload_cache_stats(cache_root),
            "Counting downloaded payloads",
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_clear_cache(Operator):
    bl_idname = "carbon.eve_resource_clear_cache"
    bl_label = "Clear Download Cache"
    bl_description = "Delete downloaded payloads and previews while retaining indexes and exported files"

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        cache_root = _cache_path(_prefs(context))
        _launch_job(
            context,
            "clear_cache",
            lambda: clear_payload_cache(cache_root),
            "Clearing downloaded payloads and previews",
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_cancel(Operator):
    """Stops the running fetch.

    Cooperative: a Python thread cannot be killed, so the flag is checked
    between files and the fetch stops at the next one. Whatever was already
    downloaded stays in the cache and counts next time.
    """

    bl_idname = "carbon.eve_resource_cancel"
    bl_label = "Cancel"
    bl_description = "Stop the running fetch; downloaded files are kept"

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and state.busy and _job is not None

    def execute(self, context):
        if _job is not None:
            _job.cancelled = True
            context.window_manager.carbon_eve_resources.status = "Cancelling..."
        return {"FINISHED"}


class EVE_RESOURCE_OT_prune_cache(Operator):
    """Deletes cached files no kept build refers to any more.

    The cache is content-addressed, so a file that changes upstream arrives
    under a NEW name and sits beside the one it replaced. Nothing overwrites
    and nothing notices, which means the cache only ever grows -- 379 MB of
    ResFiles here for a handful of ships.

    The kept builds' own indexes say which files matter, so this is a real
    answer rather than "delete everything and download it again", which is
    what clearing the cache does. See `core/cache_prune.py`.
    """

    bl_idname = "carbon.eve_resource_prune_cache"
    bl_label = "Prune Old Builds"
    bl_description = ("Delete cached files that no longer belong to a kept "
                      "build; keeps the newest build's files")

    keep_latest: IntProperty(
        name="Builds to keep", default=1, min=1, max=10,
        description="How many recent builds to keep files for")

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from .core import cache_prune

        root = _cache_path(_prefs(context))
        keep = int(self.keep_latest)

        def run():
            done = cache_prune.prune(root, keep, apply=True)
            freed = done["bytes"] / (1024 * 1024)
            return [f"Removed {done['removed']} files ({freed:.0f} MB), "
                    f"kept {done['kept']} across "
                    f"{len(done['kept_builds'])} build(s)"]

        _launch_job(context, "prune_cache", run,
                    f"Pruning cached builds, keeping the newest {keep}")
        return {"FINISHED"}


@dataclass
class _BackgroundJob:
    kind: str
    worker: Callable[[], Any]
    logical_path: str = ""
    result: Any = None
    error: Optional[BaseException] = None
    thread: Optional[threading.Thread] = None
    #: The worker's own line, e.g. "Resolving 10/23: ab1_t1_a.dds". Written
    #: from the worker thread and read by the poll timer, which is why it is a
    #: plain string and nothing cleverer.
    progress: str = ""
    #: Set by the cancel operator and read by the worker between files. A
    #: thread cannot be killed, so the work has to agree to stop.
    cancelled: bool = False


def _prefs(context):
    addon = context.preferences.addons.get(ADDON_ID)
    if addon is None:
        raise ResourceIndexError("EVE Resource Browser preferences are unavailable")
    return addon.preferences


def _cache_path(prefs) -> Path:
    return Path(bpy.path.abspath(prefs.cache_directory)).expanduser().resolve()


def _start_catalog_job(context, refresh: bool) -> None:
    prefs = _prefs(context)
    if not _creator_terms_accepted(prefs):
        raise ResourceIndexError("Accept the EVE Creator License before loading the resource index")
    cache_root = _cache_path(prefs)
    _launch_job(
        context,
        "catalog",
        lambda: (
            ensure_latest_catalog(
                cache_root,
                creator_terms_accepted=_creator_terms_accepted(prefs),
                channel="TQ",
                offline_first=not refresh,
            ),
            payload_cache_stats(cache_root),
        ),
        "Checking cached EVE resource index" if not refresh else "Checking latest Tranquility build",
    )


def _run_with_cache_stats(worker: Callable[[], Any], cache_root: Path):
    return worker(), payload_cache_stats(cache_root)


def _hull_record(dna: str) -> dict:
    """The hull record for a DNA, or an empty dict if it cannot be had.

    Carries the decal sets, plane sets, banner sets and area types, so a ship
    built without it is missing real parts of itself -- which is why it goes
    through the shared client like everything else. It used to go through a
    local tools-core checkout and ONLY that, so an artist with nothing but the
    zip got an empty record on every load.

    Returns empty rather than raising: a ship whose areas could not be typed is
    still a ship, and the failure is reported alongside the other assembly
    problems instead of taking the build down with it.
    """

    from . import service_access
    from .core import sof_areas, sof_resolution  # noqa: F401

    try:
        hull = sof_resolution.parse(dna).hull
    except sof_resolution.DnaError:
        return {}
    if not hull:
        return {}

    client = service_access.client()
    if client is None:
        return {}
    try:
        # The RESOURCE build. `latest` resolves to two different numbers, one
        # for resources and one for the SDE, and a SOF route handed the SDE
        # build quietly acquires a whole second client build.
        record = client.request_json("GET", f"/eve/latest/sof/hulls/{hull}")
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] hull record unavailable for {hull}: {exc}")
        return {}
    return record if isinstance(record, dict) else {}


def apply_view_transform(prefs) -> bool:
    """Shows colours as authored rather than through a film look.

    Blender 4 and 5 default to AgX, which desaturates and rolls off highlights
    by design. Against the same ship in the game it reads as washed out, with
    grey where the blacks should be. Standard shows what the textures and the
    SOF material colours actually say.

    The scene's setting, so it is a preference rather than something done to
    somebody's scene behind their back.
    """

    if not getattr(prefs, "standard_view_transform", False):
        return False
    try:
        settings = bpy.context.scene.view_settings
        if settings.view_transform != "Standard":
            settings.view_transform = "Standard"
            return True
    except (AttributeError, TypeError) as exc:
        # A different OCIO config may not offer it under that name.
        print(f"[CarbonEngineJS SOF] view transform unchanged: {exc}")
    return False


def _build_fetched_ship(document, resources, problems) -> str:
    """Builds a ship from a fetched document and its cached files."""

    import tempfile

    from . import ship as ship_builder
    from .core import sof_fetch

    dna = str(document.get("dna") or "")
    hull_record = _hull_record(dna)
    if apply_view_transform(_prefs(bpy.context)):
        print("  view transform set to Standard; AgX desaturates EVE's colours")
    problems = list(problems)

    # `build_ship` reads a document from a path and resources from a manifest
    # directory, so the two are written to a temporary folder. Nothing is kept:
    # the FILES live in the shared cache, and this is only the map to them.
    with tempfile.TemporaryDirectory(prefix="carbon-sof-") as temporary:
        root = Path(temporary)
        sof_fetch.write_document(document, root / "document.json")
        (root / "manifest.json").write_text(json.dumps(resources), encoding="utf-8")
        primary = ship_builder.build_ship(
            str(root / "document.json"), str(root),
            clear=False,
            decal_sets=(hull_record.get("decalSets") or []),
            hull_record=hull_record,
            cache_directory=str(_cache_path(_prefs(bpy.context)) / "logos"),
        )

    if primary is None:
        problems.append("no geometry was assembled")
    if not hull_record and dna:
        problems.append(f"no hull record for {dna}; decals and area types are unavailable")
    for problem in problems:
        print(f"[CarbonEngineJS SOF] {problem}")
    summary = f"Loaded {dna or 'SOF document'}"
    if problems:
        summary += f"; {len(problems)} issue(s) logged to the console"
    return summary


def _job_cancelled() -> bool:
    """Whether the running job has been asked to stop."""

    return _job is not None and _job.cancelled


def _launch_job(
    context,
    kind: str,
    worker: Callable[[], Any],
    status: str,
    *,
    logical_path: str = "",
) -> None:
    global _job
    if _job is not None:
        raise ResourceIndexError("Another resource-browser task is already running")
    state = context.window_manager.carbon_eve_resources
    state.busy = True
    state.status = status
    job = _BackgroundJob(kind=kind, worker=worker, logical_path=logical_path)

    def run():
        try:
            job.result = worker()
        except BaseException as exc:  # Keep failures in the Blender UI instead of losing the worker.
            job.error = exc

    job.thread = threading.Thread(target=run, name=f"CarbonEVE-{kind}", daemon=True)
    _job = job
    job.thread.start()
    if not bpy.app.timers.is_registered(_poll_job):
        bpy.app.timers.register(_poll_job, first_interval=0.15)



def _redraw_sidebars() -> None:
    """Repaints the sidebar so a progress line actually appears."""

    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", []) or []:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _set_progress(line: str) -> None:
    """Called from the WORKER thread, so it only writes a plain string.

    Anything more -- touching a PropertyGroup, tagging a redraw -- is not safe
    off the main thread, and Blender's failure mode for that is a crash rather
    than an exception.
    """

    job = _job
    if job is not None:
        job.progress = str(line)


def _poll_job():
    global _job, _catalog, _preview_collection, _cache_stats_loaded
    if not _registered:
        return None
    job = _job
    if job is None:
        return None
    if job.thread is not None and job.thread.is_alive():
        # A long job with no visible progress reads as a hang. The worker
        # writes its own line as it goes; this is the only place allowed to
        # touch Blender data, so it copies that line across and asks for a
        # redraw rather than the worker doing either.
        line = job.progress
        state = getattr(bpy.context.window_manager, "carbon_eve_resources", None)
        if state is not None and line and state.status != line:
            state.status = line
            _redraw_sidebars()
        return 0.15

    _job = None
    context = bpy.context
    state = getattr(context.window_manager, "carbon_eve_resources", None)
    if state is None:
        return None
    state.busy = False
    if job.error is not None:
        state.status = f"Error: {job.error}"
        return None

    try:
        if job.kind == "catalog":
            _catalog, stats = job.result
            _set_cache_stats(state, stats)
            state.build = _catalog.build
            state.current_directory = DEFAULT_DIRECTORY
            state.query = ""
            source = "cached" if _catalog.cache_hit else "downloaded"
            if _catalog.latest_check_deferred_seconds:
                wait = _format_wait(_catalog.latest_check_deferred_seconds)
                state.status = (
                    f"Loaded {_catalog.build} ({len(_catalog.entries):,} files, {source}); "
                    f"latest-build check limited, try again in {wait}"
                )
            else:
                state.status = (
                    f"Loaded {_catalog.build} ({len(_catalog.entries):,} files, "
                    f"{_catalog.hidden_detail_count:,} detail variants hidden by default, {source})"
                )
            _populate_results(context)
        elif job.kind == "cache_stats":
            _set_cache_stats(state, job.result)
            state.status = "Downloaded cache total refreshed"
        elif job.kind == "clear_cache":
            cleared: CacheStats = job.result
            _set_cache_stats(state, CacheStats(0, 0))
            state.preview_image = None
            state.preview_logical_path = ""
            state.preview_error_path = ""
            if _preview_collection is not None:
                _preview_collection.clear()
            state.status = (
                f"Cleared {cleared.file_count:,} downloaded files "
                f"({_format_size(cleared.byte_count)}); indexes and exported files retained"
            )
            if _catalog is not None and _context_terms_accepted(context):
                _populate_results(context)
        elif job.kind == "sof_fetch":
            (document, resources, problems), stats = job.result
            _set_cache_stats(state, stats)
            state.status = _build_fetched_ship(document, resources, problems)
        elif job.kind == "import_gr2":
            fetched, stats = job.result
            _set_cache_stats(state, stats)
            state.status = f"Downloaded {fetched.entry.logical_path}"
            bpy.ops.import_scene.carbon_gr2("EXEC_DEFAULT", filepath=str(fetched.path))
            _populate_results(context)
        else:
            fetched, stats = job.result
            _set_cache_stats(state, stats)
            state.status = f"Saved {fetched.entry.logical_path} to {fetched.path}"
            _populate_results(context)
    except Exception as exc:
        state.status = f"Error: {exc}"
    return None


def _filter_extensions(filter_name: str):
    if filter_name == "IMAGES":
        return IMAGE_EXTENSIONS
    if filter_name == "MODELS":
        return MODEL_EXTENSIONS
    if filter_name == "DATA":
        return DATA_EXTENSIONS
    return None


def _populate_results(context) -> None:
    global _suppress_selection_actions
    if _catalog is None or not _context_terms_accepted(context):
        return
    state = context.window_manager.carbon_eve_resources
    prefs = _prefs(context)
    selected = _selected_result(context)
    selected_path = selected.logical_path if selected is not None else ""
    results = _catalog.browse(
        state.current_directory,
        query=state.query,
        extensions=_filter_extensions(state.file_filter),
        show_lowdetail=bool(state.show_lowdetail),
        show_mediumdetail=bool(state.show_mediumdetail),
        limit=int(prefs.result_limit),
    )
    _suppress_selection_actions = True
    try:
        state.results.clear()
        selected_index = None
        for index, result in enumerate(results):
            item = state.results.add()
            item.logical_path = result.logical_path
            item.display_name = result.name
            item.is_directory = result.is_directory
            if result.logical_path == selected_path:
                selected_index = index
            if result.resource is not None:
                item.size = int(result.resource.uncompressed_size or 0)
                try:
                    item.cached = safe_join(
                        _catalog.cache_root,
                        "ResFiles",
                        *result.resource.location.split("/"),
                    ).is_file()
                except ResourceIndexError:
                    item.cached = False
        if selected_index is None:
            selected_index = min(max(state.active_index, 0), max(len(results) - 1, 0))
        state.active_index = selected_index
    finally:
        _suppress_selection_actions = False
    mode = "matches" if state.query.strip() else "items"
    state.result_summary = f"Showing {len(results):,} {mode} (limit {prefs.result_limit:,})"


def _on_active_result_changed(state, context) -> None:
    if _suppress_selection_actions or _catalog is None:
        return


def _selected_result(context):
    state = context.window_manager.carbon_eve_resources
    if not state.results or state.active_index < 0 or state.active_index >= len(state.results):
        return None
    return state.results[state.active_index]


def _format_size(value: int) -> str:
    size = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or suffix == "GiB":
            return f"{int(size)} {suffix}" if suffix == "B" else f"{size:.1f} {suffix}"
        size /= 1024.0
    return f"{value} B"


def _format_wait(seconds: int) -> str:
    minutes = max(1, (int(seconds) + 59) // 60)
    if minutes < 60:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    hours = (minutes + 59) // 60
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _set_cache_stats(state, stats: CacheStats) -> None:
    global _cache_stats_loaded
    suffix = "file" if stats.file_count == 1 else "files"
    state.cache_summary = f"Downloaded: {stats.file_count:,} {suffix} · {_format_size(stats.byte_count)}"
    _cache_stats_loaded = True


def _auto_load():
    if not _registered:
        return None
    try:
        context = bpy.context
        if context.window_manager is None:
            return 1.0
        prefs = _prefs(context)
        state = context.window_manager.carbon_eve_resources
        if (
            prefs.auto_load
            and _creator_terms_accepted(prefs)
            and _catalog is None
            and _job is None
            and not state.busy
        ):
            _start_catalog_job(context, refresh=False)
        elif _job is None and not state.busy and not _cache_stats_loaded:
            cache_root = _cache_path(prefs)
            _launch_job(
                context,
                "cache_stats",
                lambda: payload_cache_stats(cache_root),
                "Counting downloaded payloads",
            )
    except Exception as exc:
        state = getattr(getattr(bpy.context, "window_manager", None), "carbon_eve_resources", None)
        if state is not None:
            state.status = f"Error: {exc}"
    return None


classes = (
    # The importer's settings FIRST: the preferences class below points at it,
    # and a PointerProperty to an unregistered type fails at register time.
    _gr2_settings(),
    EVE_RESOURCE_Preferences,
    EVE_RESOURCE_OT_open_creator_terms,
    EVE_RESOURCE_OT_accept_creator_terms,
    EVE_RESOURCE_OT_revoke_creator_terms,
    EVE_RESOURCE_Result,
    EVE_RESOURCE_State,
    EVE_RESOURCE_OT_build_sof_dna,
    EVE_RESOURCE_OT_refresh_cache_stats,
    EVE_RESOURCE_OT_clear_cache,
    EVE_RESOURCE_OT_prune_cache,
    EVE_RESOURCE_OT_cancel,
)


def register():
    global _registered, _preview_collection, _cache_stats_loaded
    _cache_stats_loaded = False
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.carbon_eve_resources = PointerProperty(type=EVE_RESOURCE_State)
    _preview_collection = bpy.utils.previews.new()
    from . import pattern_controls
    pattern_controls.register()
    from . import sof_panels
    sof_panels.register()
    from . import sidebar
    sidebar.register()
    _registered = True
    if not bpy.app.timers.is_registered(_auto_load):
        bpy.app.timers.register(_auto_load, first_interval=1.0)


def unregister():
    global _registered, _catalog, _preview_collection, _cache_stats_loaded
    _registered = False
    _catalog = None
    _cache_stats_loaded = False
    if bpy.app.timers.is_registered(_auto_load):
        bpy.app.timers.unregister(_auto_load)
    if bpy.app.timers.is_registered(_poll_job):
        bpy.app.timers.unregister(_poll_job)
    if hasattr(bpy.types.WindowManager, "carbon_eve_resources"):
        del bpy.types.WindowManager.carbon_eve_resources
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
    from . import sidebar
    sidebar.unregister()
    from . import sof_panels
    sof_panels.unregister()
    from . import pattern_controls
    pattern_controls.unregister()
    # Each on its own. An unhandled failure here aborts the loop, and whatever
    # is left registered then blocks the next enable -- the settings class is
    # first in the tuple, so it unregisters LAST and was the one stranded.
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError) as exc:
            print(f"[CarbonEngineJS] could not unregister {cls.__name__}: {exc}")


if __name__ == "__main__":
    register()
