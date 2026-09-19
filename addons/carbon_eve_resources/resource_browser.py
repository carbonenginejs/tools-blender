"""Alternative resource loading from the selected Source's resfileindex."""
from pathlib import Path
import bpy
from bpy.props import BoolProperty
from bpy.types import Operator, Panel, UIList
from . import addon, service_access


class EVE_RESOURCE_UL_results(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        layout.label(text=item.display_name,
                     icon="FILE_FOLDER" if item.is_directory else "FILE")


class EVE_RESOURCE_OT_load_index(Operator):
    bl_idname = "carbon.eve_resource_load_index"
    bl_label = "Load Resource Index"
    refresh: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return (not context.window_manager.carbon_eve_resources.busy
                and addon._context_terms_accepted(context))

    def execute(self, context):
        try:
            addon._start_catalog_job(context, self.refresh)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class EVE_RESOURCE_OT_search(Operator):
    bl_idname = "carbon.eve_resource_search"
    bl_label = "Search Resources"

    def execute(self, context):
        addon._populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_browse_up(Operator):
    bl_idname = "carbon.eve_resource_browse_up"
    bl_label = "Parent Folder"

    def execute(self, context):
        state = context.window_manager.carbon_eve_resources
        parent = state.current_directory[5:].rstrip("/").rpartition("/")[0]
        state.current_directory = f"res:/{parent}/" if parent else "res:/"
        state.query = ""
        addon._populate_results(context)
        return {"FINISHED"}


class EVE_RESOURCE_OT_open_selected(Operator):
    bl_idname = "carbon.eve_resource_open_selected"
    bl_label = "Open / Load Selected"
    bl_options = {"REGISTER", "UNDO"}
    download_only: BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return (addon._selected_result(context) is not None
                and not context.window_manager.carbon_eve_resources.busy
                and addon._context_terms_accepted(context))

    def execute(self, context):
        selected = addon._selected_result(context)
        state = context.window_manager.carbon_eve_resources
        logical = selected.logical_path
        if selected.is_directory:
            state.current_directory, state.query = logical, ""
            addon._populate_results(context)
            return {"FINISHED"}
        from .core import sof_fetch
        from .core.source import resfiles_directory, Source
        prefs = addon._prefs(context)
        resolved = service_access.source(context)
        source = Source(resolved.target, resolved.provider, state.build, resolved.sde_build)
        client, cache = service_access.client(context), addon._cache_path(prefs)
        local = resfiles_directory(prefs, source.target)
        download_only = self.download_only
        def work():
            path = sof_fetch.fetch_resource(logical, client, cache,
                **source.resources(), resfiles_root=local)
            return str(path), logical, source.target, download_only
        addon._launch_job(context, "browser_resource", work, f"Loading {logical}")
        return {"FINISHED"}


class EVE_RESOURCE_OT_copy_path(Operator):
    bl_idname = "carbon.eve_resource_copy_path"
    bl_label = "Copy Resource Path"

    def execute(self, context):
        selected = addon._selected_result(context)
        if selected is None:
            return {"CANCELLED"}
        context.window_manager.clipboard = selected.logical_path
        return {"FINISHED"}


class EVE_RESOURCE_PT_browser(Panel):
    bl_label = "Resource Index"
    bl_idname = "EVE_RESOURCE_PT_browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CarbonEngineJS"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.carbon_eve_resources
        layout.prop(state, "source")
        row = layout.row(align=True)
        row.operator(EVE_RESOURCE_OT_load_index.bl_idname, text="Load Index")
        row.operator(EVE_RESOURCE_OT_load_index.bl_idname, text="Refresh", icon="FILE_REFRESH").refresh = True
        if addon._catalog is None:
            layout.label(text=state.status)
            return
        layout.label(text=f"{state.source} · build {state.build}")
        body = layout.column()
        body.enabled = not state.busy
        row = body.row(align=True)
        row.prop(state, "query", text="", icon="VIEWZOOM")
        row.operator(EVE_RESOURCE_OT_search.bl_idname, text="Search")
        body.prop(state, "file_filter")
        row = body.row(align=True)
        row.prop(state, "show_lowdetail")
        row.prop(state, "show_mediumdetail")
        row = body.row(align=True)
        row.operator(EVE_RESOURCE_OT_browse_up.bl_idname, text="", icon="FILE_PARENT")
        row.label(text=state.current_directory)
        body.template_list("EVE_RESOURCE_UL_results", "resources", state, "results", state, "active_index", rows=10)
        body.label(text=state.result_summary)
        selected = addon._selected_result(context)
        if selected:
            row = body.row(align=True)
            if selected.is_directory or Path(selected.logical_path).suffix.lower() in addon.MODEL_EXTENSIONS:
                row.operator(EVE_RESOURCE_OT_open_selected.bl_idname, text="Open Folder" if selected.is_directory else "Load Geometry")
            if not selected.is_directory:
                row.operator(EVE_RESOURCE_OT_open_selected.bl_idname, text="Download").download_only = True
            row.operator(EVE_RESOURCE_OT_copy_path.bl_idname, text="Copy Path")
        layout.label(text=state.status)


classes = (EVE_RESOURCE_UL_results, EVE_RESOURCE_OT_load_index, EVE_RESOURCE_OT_search,
           EVE_RESOURCE_OT_browse_up, EVE_RESOURCE_OT_open_selected,
           EVE_RESOURCE_OT_copy_path, EVE_RESOURCE_PT_browser)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
