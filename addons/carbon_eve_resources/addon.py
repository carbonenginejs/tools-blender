from __future__ import annotations

from dataclasses import dataclass
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
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList

from .resource_index import (
    BrowserEntry,
    CacheStats,
    FetchResult,
    LATEST_BUILD_CHECK_INTERVAL_SECONDS,
    ResourceCatalog,
    ResourceIndexError,
    clear_payload_cache,
    ensure_latest_catalog,
    materialize_resource,
    payload_cache_stats,
    safe_join,
)
from .sof_assembly import apply_mesh_areas, area_slot_report
from .sof_builder import BundleBuild, SofBuilderError, build_bundle, normalize_dna
from .sof_document import SofBundle, SofDocumentError, load_sof_bundle


ADDON_ID = __package__ or "carbon_eve_resources"
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
_last_row_click: Optional[tuple[str, float]] = None
_suppress_selection_actions = False
_preview_collection = None
_cache_stats_loaded = False


def _default_cache_directory() -> str:
    return bpy.utils.user_resource(
        "DATAFILES",
        path="carbonenginejs/tool-core",
        create=False,
    )


def _default_download_directory() -> str:
    return str(Path.home() / "Downloads" / "EVE Resources")


def _default_bundle_directory() -> str:
    return str(Path.home() / "Downloads" / "EVE Resources" / "SOF Bundles")


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
    download_directory: StringProperty(
        name="Downloaded files",
        description="Folder where selected files are materialized with their original EVE paths and extensions",
        subtype="DIR_PATH",
        default=_default_download_directory(),
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
    tools_core_directory: StringProperty(
        name="tools-core checkout",
        description="Directory containing tools-core's bin/cjs-sof-bundle.js; required to build a DNA",
        subtype="DIR_PATH",
        default="",
    )
    node_executable: StringProperty(
        name="Node executable",
        description="Node.js used to run tools-core; leave empty to use the first node on PATH",
        subtype="FILE_PATH",
        default="",
    )
    bundle_directory: StringProperty(
        name="SOF bundles",
        description="Where DNA builds are written; each DNA gets its own folder",
        subtype="DIR_PATH",
        default=_default_bundle_directory(),
    )
    shader_library: StringProperty(
        name="Shader library",
        description=(
            "Optional .blend supplying node groups such as QuadV5; when present, "
            "SOF material values drive it instead of a plain Principled BSDF"
        ),
        subtype="FILE_PATH",
        default="",
    )
    creator_terms_revision: StringProperty(default="", options={"HIDDEN"})
    creator_terms_accepted_at: StringProperty(default="", options={"HIDDEN"})

    def draw(self, context):
        layout = self.layout
        terms = layout.box()
        terms.label(text="EVE Creator License", icon="CHECKMARK" if _creator_terms_accepted(self) else "LOCKED")
        terms.label(text=CREATOR_TERMS_TITLE)
        terms.label(text=f"Published revision: {CREATOR_TERMS_REVISION}")
        row = terms.row(align=True)
        row.operator(EVE_RESOURCE_OT_open_creator_terms.bl_idname, text="Read Official Terms", icon="URL")
        if _creator_terms_accepted(self):
            terms.label(text=f"Accepted: {self.creator_terms_accepted_at or 'this revision'}")
            row.operator(EVE_RESOURCE_OT_revoke_creator_terms.bl_idname, text="Revoke", icon="X")
        else:
            row.operator(EVE_RESOURCE_OT_accept_creator_terms.bl_idname, text="Review and Accept", icon="CHECKMARK")
        layout.prop(self, "cache_directory")
        layout.prop(self, "download_directory")
        sof = layout.box()
        sof.label(text="SOF assembly", icon="OUTLINER_OB_MESH")
        sof.prop(self, "tools_core_directory")
        sof.prop(self, "node_executable")
        sof.prop(self, "bundle_directory")
        sof.prop(self, "shader_library")
        layout.prop(self, "auto_load")
        layout.prop(self, "result_limit")
        layout.label(text="Default channel: Tranquility (TQ)", icon="WORLD")
        layout.label(
            text=f"Latest-build checks: at most once every {_format_wait(LATEST_BUILD_CHECK_INTERVAL_SECONDS)}",
            icon="TIME",
        )


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


class EVE_RESOURCE_UL_results(UIList):
    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_property,
        index=0,
        flt_flag=0,
    ):
        if item.is_directory:
            row_icon = "FILE_FOLDER"
        else:
            suffix = Path(item.logical_path).suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                row_icon = "IMAGE_DATA"
            elif suffix in MODEL_EXTENSIONS:
                row_icon = "MESH_DATA"
            else:
                row_icon = "FILE"
        row = layout.row(align=True)
        split = row.split(factor=0.72, align=True)
        name_column = split.row(align=True)
        name_column.alignment = "LEFT"
        metadata_column = split.row(align=True)
        metadata_column.alignment = "RIGHT"
        if item.is_directory:
            open_row = name_column.operator(
                EVE_RESOURCE_OT_activate_folder_row.bl_idname,
                text=item.display_name,
                icon=row_icon,
                emboss=False,
            )
            open_row.logical_path = item.logical_path
            open_row.result_index = index
        elif Path(item.logical_path).suffix.lower() == ".gr2":
            import_row = name_column.operator(
                EVE_RESOURCE_OT_activate_gr2_row.bl_idname,
                text=item.display_name,
                icon=row_icon,
                emboss=False,
            )
            import_row.logical_path = item.logical_path
            import_row.result_index = index
        else:
            name_column.label(text=item.display_name, icon=row_icon)
        if not item.is_directory:
            if item.size:
                metadata_column.label(text=_format_size(item.size))
            metadata_column.label(text="", icon="CHECKMARK" if item.cached else "BLANK1")


class EVE_RESOURCE_OT_load_index(Operator):
    bl_idname = "carbon.eve_resource_load_index"
    bl_label = "Load EVE Resource Index"
    bl_description = (
        "Open the cached exact build or check Tranquility; remote build checks "
        "are limited to once every 12 hours per cache"
    )

    refresh: BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy and _context_terms_accepted(context)

    def execute(self, context):
        _start_catalog_job(context, refresh=bool(self.refresh))
        return {"FINISHED"}


class EVE_RESOURCE_OT_search(Operator):
    bl_idname = "carbon.eve_resource_search"
    bl_label = "Search EVE Resources"

    @classmethod
    def poll(cls, context):
        return (
            _catalog is not None
            and not context.window_manager.carbon_eve_resources.busy
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        _populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_browse_up(Operator):
    bl_idname = "carbon.eve_resource_browse_up"
    bl_label = "Parent Folder"

    @classmethod
    def poll(cls, context):
        state = context.window_manager.carbon_eve_resources
        return (
            _catalog is not None
            and not state.busy
            and state.current_directory != "res:/"
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        state = context.window_manager.carbon_eve_resources
        relative = state.current_directory[5:].rstrip("/")
        parent = relative.rpartition("/")[0]
        state.current_directory = f"res:/{parent}/" if parent else "res:/"
        state.query = ""
        _populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_activate_folder_row(Operator):
    """Select a folder on the first click and open it on the second."""

    bl_idname = "carbon.eve_resource_activate_folder_row"
    bl_label = "Select / Open Folder"
    bl_description = "Double-click to open this folder"
    bl_options = {"INTERNAL"}

    logical_path: StringProperty(options={"HIDDEN"})
    result_index: IntProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return (
            _catalog is not None
            and state is not None
            and not state.busy
            and _context_terms_accepted(context)
        )

    def invoke(self, context, event):
        state = context.window_manager.carbon_eve_resources
        if 0 <= self.result_index < len(state.results):
            state.active_index = self.result_index
        if _is_row_double_click(context, f"folder:{self.logical_path}"):
            return self.execute(context)
        return {"FINISHED"}

    def execute(self, context):
        state = context.window_manager.carbon_eve_resources
        folder = next(
            (
                item
                for item in state.results
                if item.is_directory and item.logical_path == self.logical_path
            ),
            None,
        )
        if folder is None:
            self.report({"ERROR"}, "Folder is no longer in the current result list")
            return {"CANCELLED"}
        state.current_directory = folder.logical_path
        state.query = ""
        _populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_activate_gr2_row(Operator):
    """Select a GR2 on the first click and import it on the second."""

    bl_idname = "carbon.eve_resource_activate_gr2_row"
    bl_label = "Select / Import GR2"
    bl_description = "Double-click to download and import this GR2"
    bl_options = {"INTERNAL"}

    logical_path: StringProperty(options={"HIDDEN"})
    result_index: IntProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return (
            _catalog is not None
            and state is not None
            and not state.busy
            and _context_terms_accepted(context)
        )

    def invoke(self, context, event):
        state = context.window_manager.carbon_eve_resources
        if 0 <= self.result_index < len(state.results):
            state.active_index = self.result_index
        if _is_row_double_click(context, f"gr2:{self.logical_path}"):
            return self.execute(context)
        return {"FINISHED"}

    def execute(self, context):
        selected = _selected_result(context)
        if (
            selected is None
            or selected.logical_path != self.logical_path
            or Path(selected.logical_path).suffix.lower() != ".gr2"
        ):
            self.report({"ERROR"}, "GR2 is no longer selected")
            return {"CANCELLED"}
        return bpy.ops.carbon.eve_resource_import_gr2("EXEC_DEFAULT")


class EVE_RESOURCE_OT_open_selected(Operator):
    bl_idname = "carbon.eve_resource_open_selected"
    bl_label = "Open Folder"

    @classmethod
    def poll(cls, context):
        selected = _selected_result(context)
        return (
            selected is not None
            and selected.is_directory
            and not context.window_manager.carbon_eve_resources.busy
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        state = context.window_manager.carbon_eve_resources
        selected = _selected_result(context)
        state.current_directory = selected.logical_path
        state.query = ""
        _populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_download_selected(Operator):
    bl_idname = "carbon.eve_resource_download_selected"
    bl_label = "Download Selected"
    bl_description = "Download, validate, and save the selected file using its original res:/ path"

    @classmethod
    def poll(cls, context):
        selected = _selected_result(context)
        return (
            selected is not None
            and not selected.is_directory
            and not context.window_manager.carbon_eve_resources.busy
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        entry = _selected_catalog_entry(context)
        prefs = _prefs(context)
        _launch_job(
            context,
            "download",
            lambda: _run_with_cache_stats(
                lambda: materialize_resource(
                    entry,
                    _cache_path(prefs),
                    _download_path(prefs),
                    creator_terms_accepted=_creator_terms_accepted(prefs),
                ),
                _cache_path(prefs),
            ),
            f"Downloading {entry.logical_path}",
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_preview_selected(Operator):
    bl_idname = "carbon.eve_resource_preview_selected"
    bl_label = "Download and Preview"
    bl_description = "Download and validate the selected texture, then show it in this panel"

    @classmethod
    def poll(cls, context):
        selected = _selected_result(context)
        return (
            selected is not None
            and not selected.is_directory
            and Path(selected.logical_path).suffix.lower() in IMAGE_EXTENSIONS
            and not context.window_manager.carbon_eve_resources.busy
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        entry = _selected_catalog_entry(context)
        _start_preview(context, entry)
        return {"FINISHED"}


class EVE_RESOURCE_OT_import_gr2(Operator):
    bl_idname = "carbon.eve_resource_import_gr2"
    bl_label = "Download and Import GR2"
    bl_description = "Download the selected GR2 and pass it to the CarbonEngineJS GR2 importer"

    @classmethod
    def poll(cls, context):
        selected = _selected_result(context)
        return (
            selected is not None
            and not selected.is_directory
            and Path(selected.logical_path).suffix.lower() == ".gr2"
            and not context.window_manager.carbon_eve_resources.busy
            and _context_terms_accepted(context)
        )

    def execute(self, context):
        if not hasattr(bpy.ops.import_scene, "carbon_gr2"):
            self.report({"ERROR"}, "Enable CarbonEngineJS GR2 Importer first")
            return {"CANCELLED"}
        entry = _selected_catalog_entry(context)
        prefs = _prefs(context)
        _launch_job(
            context,
            "import_gr2",
            lambda: _run_with_cache_stats(
                lambda: materialize_resource(
                    entry,
                    _cache_path(prefs),
                    _download_path(prefs),
                    creator_terms_accepted=_creator_terms_accepted(prefs),
                ),
                _cache_path(prefs),
            ),
            f"Downloading {entry.logical_path}",
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_import_sof_document(Operator):
    """Builds a ship from a pre-compiled tools-core SOF bundle."""

    bl_idname = "carbon.eve_resource_import_sof_document"
    bl_label = "Assemble SOF Bundle"
    bl_description = (
        "Import the geometry, mesh areas, and textures described by a "
        "pre-compiled tools-core SOF bundle or carbon.document JSON file"
    )

    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    include_secondary_meshes: BoolProperty(
        name="Include additional meshes",
        description=(
            "Also import meshes the document attaches to the hull, such as the "
            "shield impact overlay sphere"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "carbon_eve_resources", None)
        return state is not None and not state.busy and _context_terms_accepted(context)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not hasattr(bpy.ops.import_scene, "carbon_gr2"):
            self.report({"ERROR"}, "Enable CarbonEngineJS GR2 Importer first")
            return {"CANCELLED"}
        if not self.filepath:
            self.report({"ERROR"}, "Select a SOF bundle or document first")
            return {"CANCELLED"}
        try:
            bundle = load_sof_bundle(self.filepath)
        except SofDocumentError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        prefs = _prefs(context)
        primary_only = not self.include_secondary_meshes
        pending = bundle.unresolved(primary_only=primary_only)
        if pending and _catalog is None:
            self.report(
                {"ERROR"},
                f"{len(pending)} resources are missing from the bundle; load the resource index "
                "to download them",
            )
            return {"CANCELLED"}
        _launch_job(
            context,
            "sof_document",
            lambda: _run_with_cache_stats(
                lambda: (bundle, primary_only, _fetch_sof_resources(pending, prefs)),
                _cache_path(prefs),
            ),
            f"Assembling {bundle.assembly.dna or 'SOF bundle'}"
            + (f" ({len(pending)} downloads)" if pending else ""),
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_build_sof_dna(Operator):
    """Builds a DNA into a bundle with tools-core, then assembles it."""

    bl_idname = "carbon.eve_resource_build_sof_dna"
    bl_label = "Build DNA"
    bl_description = (
        "Run tools-core to compose this SOF DNA into a bundle, then import its "
        "geometry, mesh areas, and textures"
    )

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
        if not hasattr(bpy.ops.import_scene, "carbon_gr2"):
            self.report({"ERROR"}, "Enable CarbonEngineJS GR2 Importer first")
            return {"CANCELLED"}
        state = context.window_manager.carbon_eve_resources
        prefs = _prefs(context)
        try:
            dna = normalize_dna(state.dna)
        except SofBuilderError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not str(prefs.tools_core_directory).strip():
            self.report({"ERROR"}, "Set the tools-core checkout in preferences first")
            return {"CANCELLED"}

        primary_only = not self.include_secondary_meshes
        refresh = bool(self.refresh)
        _launch_job(
            context,
            "sof_dna",
            lambda: _run_with_cache_stats(
                lambda: (
                    build_bundle(
                        dna,
                        tools_core_directory=Path(bpy.path.abspath(prefs.tools_core_directory)),
                        output_root=Path(bpy.path.abspath(prefs.bundle_directory)),
                        cache_root=_cache_path(prefs),
                        node_executable=str(prefs.node_executable).strip(),
                        refresh=refresh,
                    ),
                    primary_only,
                ),
                _cache_path(prefs),
            ),
            f"Building {dna} with tools-core",
        )
        return {"FINISHED"}


class EVE_RESOURCE_OT_open_downloads(Operator):
    bl_idname = "carbon.eve_resource_open_downloads"
    bl_label = "Open Download Folder"

    def execute(self, context):
        directory = _download_path(_prefs(context))
        directory.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.path_open(filepath=str(directory))
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


class EVE_RESOURCE_PT_browser(Panel):
    bl_label = "EVE Resource Browser"
    bl_idname = "EVE_RESOURCE_PT_browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarbonEngineJS"

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.carbon_eve_resources
        prefs = _prefs(context)

        if not _creator_terms_accepted(prefs):
            layout.label(text="EVE Creator License required", icon="LOCKED")
            terms = layout.box()
            terms.label(text=CREATOR_TERMS_TITLE)
            terms.label(text=f"Published revision: {CREATOR_TERMS_REVISION}")
            terms.label(text="Read the official live terms before using this tool.")
            row = terms.row(align=True)
            row.operator(EVE_RESOURCE_OT_open_creator_terms.bl_idname, text="Read Official Terms", icon="URL")
            row.operator(EVE_RESOURCE_OT_accept_creator_terms.bl_idname, text="Review and Accept", icon="CHECKMARK")
            _draw_cache_controls(layout, state)
            return

        header = layout.row(align=True)
        if state.build:
            header.label(text=f"TQ build {state.build}", icon="WORLD")
        else:
            header.label(text="No index loaded", icon="INFO")
        load = header.operator(EVE_RESOURCE_OT_load_index.bl_idname, text="", icon="FILE_REFRESH")
        load.refresh = True

        status = layout.row()
        status.alert = state.status.startswith("Error:")
        status.label(text=state.status, icon="TIME" if state.busy else "NONE")

        _draw_sof_controls(layout, state)

        if _catalog is None:
            button = layout.operator(EVE_RESOURCE_OT_load_index.bl_idname, text="Load Cached / Latest Index", icon="IMPORT")
            button.refresh = False
            return

        preview_item = _active_preview_item()
        if state.preview_image is not None and preview_item is not None:
            preview = layout.column(align=True)
            preview.alignment = "CENTER"
            preview_scale = max(4.0, min(14.0, (float(context.region.width) - 36.0) / 24.0))
            preview.template_icon(icon_value=preview_item.icon_id, scale=preview_scale)
        else:
            preview_box = layout.box()
            preview_box.label(text="Texture preview", icon="IMAGE_DATA")
            placeholder = preview_box.column(align=True)
            placeholder.scale_y = 1.6
            placeholder.label(text="Select an image resource")
            placeholder.label(text="to preview it automatically")

        search = layout.row(align=True)
        search.prop(state, "query", text="", icon="VIEWZOOM")
        search.operator(EVE_RESOURCE_OT_search.bl_idname, text="", icon="FORWARD")
        layout.prop(state, "file_filter", text="")
        variants = layout.row(align=True)
        variants.label(text="Detail variants:")
        variants.prop(state, "show_lowdetail", toggle=True)
        variants.prop(state, "show_mediumdetail", toggle=True)

        path_row = layout.row(align=True)
        path_row.operator(EVE_RESOURCE_OT_browse_up.bl_idname, text="", icon="FILE_PARENT")
        path_row.label(text=state.current_directory)

        preview_visible = state.preview_image is not None and preview_item is not None
        ui_scale = max(0.75, float(context.preferences.system.ui_scale))
        reserved_height = 500 if preview_visible else 330
        available_height = max(0, int(context.region.height) - reserved_height)
        result_rows = max(6, min(28, int(available_height / (21 * ui_scale))))

        layout.template_list(
            EVE_RESOURCE_UL_results.__name__,
            "resources",
            state,
            "results",
            state,
            "active_index",
            rows=result_rows,
        )
        if state.result_summary:
            layout.label(text=state.result_summary, icon="INFO")

        selected = _selected_result(context)
        if selected is not None:
            actions = layout.row(align=True)
            if selected.is_directory:
                actions.operator(EVE_RESOURCE_OT_open_selected.bl_idname, icon="FILE_FOLDER")
            else:
                actions.operator(EVE_RESOURCE_OT_download_selected.bl_idname, text="Download", icon="IMPORT")
                suffix = Path(selected.logical_path).suffix.lower()
                if suffix in IMAGE_EXTENSIONS and state.preview_error_path == selected.logical_path:
                    actions.operator(EVE_RESOURCE_OT_preview_selected.bl_idname, text="Retry", icon="FILE_REFRESH")
                elif suffix == ".gr2":
                    actions.operator(EVE_RESOURCE_OT_import_gr2.bl_idname, text="Import GR2", icon="MESH_DATA")

        _draw_cache_controls(layout, state)


def _draw_sof_controls(layout, state) -> None:
    box = layout.box()
    box.label(text="Build from DNA", icon="OUTLINER_OB_MESH")
    if not hasattr(bpy.ops.import_scene, "carbon_gr2"):
        box.label(text="Enable the GR2 importer to assemble", icon="ERROR")
        return
    box.prop(state, "dna", text="", icon="RNA")
    build = box.row(align=True)
    build.operator(EVE_RESOURCE_OT_build_sof_dna.bl_idname, text="Build DNA", icon="PLAY")
    rebuild = build.operator(EVE_RESOURCE_OT_build_sof_dna.bl_idname, text="", icon="FILE_REFRESH")
    rebuild.refresh = True
    box.operator(
        EVE_RESOURCE_OT_import_sof_document.bl_idname,
        text="Assemble Existing Bundle",
        icon="IMPORT",
    )
    note = box.column(align=True)
    note.scale_y = 0.8
    note.label(text="tools-core composes SOF/DNA")
    note.label(text="Materials approximate Carbon shaders")


def _draw_cache_controls(layout, state) -> None:
    cache = layout.box()
    summary = cache.row(align=True)
    summary.label(text=state.cache_summary, icon="DISK_DRIVE")
    summary.operator(EVE_RESOURCE_OT_refresh_cache_stats.bl_idname, text="", icon="FILE_REFRESH")
    actions = cache.row(align=True)
    actions.operator(EVE_RESOURCE_OT_open_downloads.bl_idname, text="Open Downloads", icon="FILE_FOLDER")
    actions.operator(EVE_RESOURCE_OT_clear_cache.bl_idname, text="Clear Cache", icon="TRASH")

@dataclass
class _BackgroundJob:
    kind: str
    worker: Callable[[], Any]
    logical_path: str = ""
    result: Any = None
    error: Optional[BaseException] = None
    thread: Optional[threading.Thread] = None
    #: The worker's own line, e.g. "Downloading 10/23: ab1_t1_a.dds". Written
    #: from the worker thread and read by the poll timer, which is why it is a
    #: plain string and nothing cleverer.
    progress: str = ""


def _prefs(context):
    addon = context.preferences.addons.get(ADDON_ID)
    if addon is None:
        raise ResourceIndexError("EVE Resource Browser preferences are unavailable")
    return addon.preferences


def _cache_path(prefs) -> Path:
    return Path(bpy.path.abspath(prefs.cache_directory)).expanduser().resolve()


def _download_path(prefs) -> Path:
    return Path(bpy.path.abspath(prefs.download_directory)).expanduser().resolve()


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


def _start_preview(context, entry) -> None:
    state = context.window_manager.carbon_eve_resources
    prefs = _prefs(context)
    if not _creator_terms_accepted(prefs):
        raise ResourceIndexError("Accept the EVE Creator License before downloading previews")
    preview_root = _cache_path(prefs) / "Previews" / _catalog.build
    state.preview_error_path = ""
    _launch_job(
        context,
        "preview",
        lambda: _run_with_cache_stats(
            lambda: materialize_resource(
                entry,
                _cache_path(prefs),
                preview_root,
                creator_terms_accepted=_creator_terms_accepted(prefs),
            ),
            _cache_path(prefs),
        ),
        f"Downloading preview for {entry.logical_path}",
        logical_path=entry.logical_path,
    )


def _run_with_cache_stats(worker: Callable[[], Any], cache_root: Path):
    return worker(), payload_cache_stats(cache_root)


def _shader_library() -> str:
    """The optional .blend supplying Carbon-shaped node groups."""

    try:
        return str(_prefs(bpy.context).shader_library or "").strip()
    except (AttributeError, ResourceIndexError):
        return ""


def _fetch_sof_resources(paths: tuple[str, ...], prefs) -> tuple[dict[str, Path], list[str]]:
    """Materializes the document resources a bundle did not already provide."""

    if not paths:
        return {}, []
    if _catalog is None:
        raise ResourceIndexError("Load the EVE resource index before assembling a SOF document")
    accepted = _creator_terms_accepted(prefs)
    cache_root = _cache_path(prefs)
    download_root = _download_path(prefs)
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    total = len(paths)
    for index, logical_path in enumerate(paths, start=1):
        _set_progress(f"Downloading {index}/{total}: {logical_path.rsplit('/', 1)[-1]}")
        try:
            entry = _catalog.get(logical_path)
            fetched = materialize_resource(
                entry,
                cache_root,
                download_root,
                creator_terms_accepted=accepted,
            )
        except ResourceIndexError as exc:
            missing.append(f"{logical_path}: {exc}")
            continue
        resolved[logical_path] = fetched.path
    return resolved, missing


def _hull_record(dna: str) -> dict:
    """The hull record for a DNA, or an empty dict if it cannot be had.

    Returns empty rather than raising: a ship whose areas could not be typed is
    still a ship, and the failure is reported alongside the other assembly
    problems instead of taking the build down with it.
    """

    from . import sof_areas, sof_resolution  # noqa: F401
    from .tools_service import ToolsServiceClient, ToolsServiceError

    try:
        hull = sof_resolution.parse(dna).hull
    except sof_resolution.DnaError:
        return {}
    if not hull:
        return {}

    prefs = _prefs(bpy.context)
    root = str(prefs.tools_core_directory or "").strip()
    if not root:
        return {}
    try:
        client = ToolsServiceClient(
            node_executable=str(prefs.node_executable or "node").strip() or "node",
            service_script=Path(bpy.path.abspath(root)) / "bin" / "cjs-tools-service.js",
            cache_root=_cache_path(prefs),
        )
        # The RESOURCE build. `latest` resolves to two different numbers, one
        # for resources and one for the SDE, and a SOF route handed the SDE
        # build quietly acquires a whole second client build.
        record = client._request("GET", f"/eve/latest/sof/hulls/{hull}")
    except (ToolsServiceError, OSError, ValueError) as exc:
        print(f"[CarbonEngineJS SOF] hull record unavailable for {hull}: {exc}")
        return {}
    return record if isinstance(record, dict) else {}


def _assemble_sof_document(
    bundle: SofBundle,
    primary_only: bool,
    downloaded: dict[str, Path],
    missing: list[str],
) -> str:
    """Imports each document mesh and routes its SOF areas onto the slots."""

    assembly = bundle.assembly
    resolved: dict[str, Path] = dict(bundle.resources)
    resolved.update(downloaded)
    imported = 0
    materials = 0
    slots = 0
    problems = list(missing)
    for mesh in assembly.meshes:
        if primary_only and mesh.role != "primary":
            continue
        geometry = resolved.get(mesh.geometry_path)
        if geometry is None:
            problems.append(f"{mesh.geometry_path}: geometry was not downloaded")
            continue
        before = {item.name for item in bpy.data.objects}
        bpy.ops.import_scene.carbon_gr2("EXEC_DEFAULT", filepath=str(geometry))
        objects = [
            item
            for item in bpy.data.objects
            if item.name not in before and item.type == "MESH"
        ]
        if not objects:
            problems.append(f"{mesh.geometry_path}: the GR2 importer created no mesh objects")
            continue
        imported += 1
        slot_count = sum(len(item.data.materials) for item in objects)
        problems.extend(area_slot_report(mesh, slot_count))
        report = apply_mesh_areas(
            mesh,
            objects,
            resolved,
            prefix=f"{mesh.name}.",
            shader_library=str(_shader_library()),
            progress=_set_progress,
        )
        materials += report.materials
        slots += report.assigned_slots
        problems.extend(report.warnings)
        problems.extend(
            f"{path}: texture unavailable" for path in report.missing_textures
        )
        for item in objects:
            item["carbon_sof_dna"] = assembly.dna
            item["carbon_sof_geometry"] = mesh.geometry_path

    # Recover each material's AREA TYPE. A faction stores four material names
    # per area type, so without this every material looks alike and an edit
    # meant for the hull reaches the sails as well. The built document cannot
    # answer it -- `Tr2MeshArea` drops the area type -- so it comes from the
    # hull record, where it was authored.
    from . import sof_areas
    hull_record = _hull_record(assembly.dna)
    if hull_record:
        stamped = sof_areas.stamp_ship(bpy.data.objects, hull_record)
        if stamped["types"]:
            problems.append("areas: " + ", ".join(
                f"{name} x{count}" for name, count in sorted(stamped["types"].items())))
        if stamped["unmatched"]:
            problems.append(f"areas: {len(stamped['unmatched'])} could not be "
                            "identified and are left as built")

    for problem in problems:
        print(f"[CarbonEngineJS SOF] {problem}")
    summary = (
        f"Assembled {assembly.dna or 'SOF document'}: {imported} meshes, "
        f"{materials} area materials, {slots} slots"
    )
    if problems:
        summary += f"; {len(problems)} issues logged to the console"
    return summary


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
        if job.kind == "preview":
            state.preview_error_path = job.logical_path
        state.status = f"Error: {job.error}"
        _auto_preview_selected(context)
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
        elif job.kind == "preview":
            fetched, stats = job.result
            _set_cache_stats(state, stats)
            image = bpy.data.images.load(str(fetched.path), check_existing=True)
            try:
                image.reload()
            except RuntimeError:
                pass
            if _preview_collection is not None:
                _preview_collection.clear()
                _preview_collection.load(
                    "active",
                    str(fetched.path),
                    "IMAGE",
                    force_reload=True,
                )
            state.preview_image = image
            state.preview_logical_path = fetched.entry.logical_path
            state.preview_error_path = ""
            state.status = f"Previewing {fetched.entry.logical_path}"
            _populate_results(context)
        elif job.kind == "sof_dna":
            (built, primary_only), stats = job.result
            _set_cache_stats(state, stats)
            bundle = load_sof_bundle(built.directory)
            source = "built" if built.created else "reused"
            state.status = (
                f"{_assemble_sof_document(bundle, primary_only, {}, list(bundle.unresolved(primary_only=primary_only)))}"
                f" ({source} {built.directory.name})"
            )
        elif job.kind == "sof_document":
            (bundle, primary_only, (downloaded, missing)), stats = job.result
            _set_cache_stats(state, stats)
            state.status = _assemble_sof_document(bundle, primary_only, downloaded, missing)
            if _catalog is not None:
                _populate_results(context)
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
    if job.kind != "clear_cache":
        _auto_preview_selected(context)
    return None


def _filter_extensions(filter_name: str):
    if filter_name == "IMAGES":
        return IMAGE_EXTENSIONS
    if filter_name == "MODELS":
        return MODEL_EXTENSIONS
    if filter_name == "DATA":
        return DATA_EXTENSIONS
    return None


def _active_preview_item():
    if _preview_collection is None:
        return None
    return _preview_collection.get("active")


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
    _auto_preview_selected(context)


def _auto_preview_selected(context) -> None:
    if _catalog is None or _job is not None or not _context_terms_accepted(context):
        return
    state = context.window_manager.carbon_eve_resources
    selected = _selected_result(context)
    if selected is None or selected.is_directory:
        return
    if Path(selected.logical_path).suffix.lower() not in IMAGE_EXTENSIONS:
        return
    if selected.logical_path in {state.preview_logical_path, state.preview_error_path}:
        return
    try:
        _start_preview(context, _catalog.get(selected.logical_path))
    except Exception as exc:
        state.preview_error_path = selected.logical_path
        state.status = f"Error: {exc}"


def _is_row_double_click(context, key: str) -> bool:
    global _last_row_click
    now = time.monotonic()
    previous = _last_row_click
    _last_row_click = (key, now)
    double_click_seconds = max(
        0.1,
        float(context.preferences.inputs.mouse_double_click_time) / 1000.0,
    )
    if previous is None or previous[0] != key or now - previous[1] > double_click_seconds:
        return False
    _last_row_click = None
    return True


def _selected_result(context):
    state = context.window_manager.carbon_eve_resources
    if not state.results or state.active_index < 0 or state.active_index >= len(state.results):
        return None
    return state.results[state.active_index]


def _selected_catalog_entry(context):
    if not _context_terms_accepted(context):
        raise ResourceIndexError("Accept the EVE Creator License before using EVE resources")
    selected = _selected_result(context)
    if selected is None or selected.is_directory or _catalog is None:
        raise ResourceIndexError("Select a file first")
    return _catalog.get(selected.logical_path)


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
    EVE_RESOURCE_Preferences,
    EVE_RESOURCE_OT_open_creator_terms,
    EVE_RESOURCE_OT_accept_creator_terms,
    EVE_RESOURCE_OT_revoke_creator_terms,
    EVE_RESOURCE_Result,
    EVE_RESOURCE_State,
    EVE_RESOURCE_UL_results,
    EVE_RESOURCE_OT_load_index,
    EVE_RESOURCE_OT_search,
    EVE_RESOURCE_OT_browse_up,
    EVE_RESOURCE_OT_activate_folder_row,
    EVE_RESOURCE_OT_activate_gr2_row,
    EVE_RESOURCE_OT_open_selected,
    EVE_RESOURCE_OT_download_selected,
    EVE_RESOURCE_OT_preview_selected,
    EVE_RESOURCE_OT_import_gr2,
    EVE_RESOURCE_OT_import_sof_document,
    EVE_RESOURCE_OT_build_sof_dna,
    EVE_RESOURCE_OT_open_downloads,
    EVE_RESOURCE_OT_refresh_cache_stats,
    EVE_RESOURCE_OT_clear_cache,
    EVE_RESOURCE_PT_browser,
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
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
