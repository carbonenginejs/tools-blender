"""Saving a ship as a folder somebody else can open.

One button. It writes the model, a `.blend`, and every texture under the name
the texture actually has, with the blend pointing at them by relative path.
That folder opens on a machine with no cache, no service and no add-on.

Every texture goes, the artist's own included. A folder that still depends on
a file sitting in somebody's working tree is not one you can hand over, so the
duplication here is the feature rather than a cost.

Nothing here writes to the cache, and nothing writes to the artist's folders.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from .core import resfile


def eve_images():
    """Every image that came from an EVE resource, with its logical path.

    Stamped at load. An image without the stamp is somebody's own -- a render
    result, a packed logo, something they added -- and is not ours to move.
    """

    found = []
    for image in bpy.data.images:
        logical = image.get("carbon_res_path")
        if logical and image.filepath:
            found.append((image, str(logical)))
    return found


def export_images(folder, *, repoint: bool = True):
    """Copies the textures out and points the scene at the copies.

    Returns `(written, failed)`. A file already at the destination counts as
    written and is not copied again, so saving twice into the same folder
    costs nothing and cannot clobber an edit somebody made there.
    """

    folder = Path(bpy.path.abspath(str(folder)))
    written, failed = [], []

    for image, logical in eve_images():
        source = Path(bpy.path.abspath(image.filepath))
        destination = resfile.export_destination(folder, logical, source)
        if destination is None:
            failed.append(f"{image.name}: no path to export it under")
            continue
        try:
            if not destination.is_file() or destination.stat().st_size == 0:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            if repoint:
                image.filepath = str(destination)
            written.append(destination)
        except OSError as exc:
            failed.append(f"{image.name}: {exc}")

    return written, failed


class CARBON_OT_save_standalone(Operator):
    """Writes the model, its blend and its textures into one folder.

    Standalone is the promise the name makes: nothing in that folder points
    back at the cache, the service, or this add-on.
    """

    bl_idname = "carbon.eve_save_standalone"
    bl_label = "Save as Standalone"
    bl_description = ("Write the model, a .blend and every texture under its "
                      "real name into one folder, with relative paths, so it "
                      "opens without this add-on or the cache")
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH")
    filename: StringProperty(name="Blend file", default="ship.blend")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        folder = Path(bpy.path.abspath(self.directory))
        name = self.filename or "ship.blend"
        if not name.lower().endswith(".blend"):
            name += ".blend"
        target = folder / name

        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"Could not use {folder}: {exc}")
            return {"CANCELLED"}

        written, failed = export_images(folder)
        for problem in failed:
            print(f"[CarbonEngineJS SOF] save: {problem}")

        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(target))
            # Relative AFTER saving: there is no "relative to" until the blend
            # has a location of its own.
            bpy.ops.file.make_paths_relative()
            bpy.ops.wm.save_mainfile()
        except (RuntimeError, OSError) as exc:
            self.report({"ERROR"}, f"Could not write {target.name}: {exc}")
            return {"CANCELLED"}

        message = f"Saved {target.name} and {len(written)} texture(s) to {folder}"
        if failed:
            message += f"; {len(failed)} failed, see the console"
        self.report({"INFO"}, message)
        return {"FINISHED"}


CLASSES = (CARBON_OT_save_standalone,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
