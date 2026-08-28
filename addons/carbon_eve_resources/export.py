"""Handing the files over: readable names, in a folder somebody chose.

Two things people want, and they are not the same thing:

**Save Textures As** puts the cache's files somewhere readable and points the
scene at them. It skips anything the artist supplied, because that file is
already theirs, already under a name they can read, and copying it would make
a second copy that starts drifting from the one they are editing.

**Export Standalone** is the opposite trade on purpose: EVERY texture goes to
the folder, including the artist's own, and a `.blend` is saved beside them
with relative paths. That one opens on a machine with no cache, no service and
no add-on -- which is worth the duplication, because the duplication is the
point.

Nothing here writes to the cache, and nothing writes to the artist's folders.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
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


def is_cached(image, cache_root) -> bool:
    """Whether this image is reading OUR copy rather than the artist's.

    Decided by where the file is, not by what it looks like. A name can be
    guessed at; a location cannot.
    """

    if not cache_root:
        return False
    try:
        current = Path(bpy.path.abspath(image.filepath)).resolve()
        return current.is_relative_to(Path(cache_root).resolve())
    except (OSError, ValueError):
        return False


def export_images(folder, cache_root, *, everything: bool, repoint: bool = True):
    """Copies the images out and, optionally, points the scene at the copies.

    Returns `(written, skipped, failed)`. A file already at the destination is
    counted as written and not copied again, so exporting twice into the same
    folder is free.
    """

    folder = Path(bpy.path.abspath(str(folder)))
    written, skipped, failed = [], [], []

    for image, logical in eve_images():
        source = Path(bpy.path.abspath(image.filepath))
        destination = resfile.export_destination(folder, logical, source)
        if destination is None:
            failed.append(f"{image.name}: no path to export it under")
            continue

        # Already there is the FIRST question, before whose file it is. After
        # one export the scene points at the export folder, so asking about
        # ownership first would report a finished export as "34 of yours,
        # skipped" -- true, and useless.
        already = destination.is_file() and destination.stat().st_size > 0
        if not already and not everything and not is_cached(image, cache_root):
            # Theirs. They have it, under a name they can already read.
            skipped.append(image.name)
            continue
        try:
            if not already:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            if repoint:
                image.filepath = str(destination)
            written.append(destination)
        except OSError as exc:
            failed.append(f"{image.name}: {exc}")

    return written, skipped, failed


class CARBON_OT_export_textures(Operator):
    """Copies this scene's EVE textures somewhere readable."""

    bl_idname = "carbon.eve_export_textures"
    bl_label = "Save Textures As"
    bl_description = ("Copy the textures this scene loaded from the cache into "
                      "a folder, under their real names, and point the scene "
                      "at the copies. Files you supplied are left alone")
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH")
    everything: BoolProperty(
        name="Include my own files",
        description="Also copy textures that came from your local folders. Off "
                    "by default: you already have those",
        default=False,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import addon

        try:
            cache_root = str(addon._cache_path(addon._prefs(context)))
        except Exception:
            cache_root = ""

        written, skipped, failed = export_images(
            self.directory, cache_root, everything=self.everything)
        for problem in failed:
            print(f"[CarbonEngineJS SOF] export: {problem}")
        if not written and not skipped:
            self.report({"WARNING"}, "No EVE textures in this scene to export")
            return {"CANCELLED"}

        message = f"Exported {len(written)} texture(s) to {self.directory}"
        if skipped:
            message += f"; {len(skipped)} already yours, left where they are"
        if failed:
            message += f"; {len(failed)} failed, see the console"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CARBON_OT_export_standalone(Operator):
    """Writes a folder that opens anywhere, with no cache and no add-on."""

    bl_idname = "carbon.eve_export_standalone"
    bl_label = "Export Standalone"
    bl_description = ("Write every texture and a .blend into one folder, with "
                      "relative paths, so it opens on a machine without this "
                      "add-on or the cache")
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH")
    filename: StringProperty(name="Blend file", default="ship.blend")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import addon

        try:
            cache_root = str(addon._cache_path(addon._prefs(context)))
        except Exception:
            cache_root = ""

        folder = Path(bpy.path.abspath(self.directory))
        # Everything, the artist's files included. A standalone folder that
        # depends on a file still sitting in somebody's working tree is not
        # standalone.
        written, _, failed = export_images(folder, cache_root, everything=True)
        for problem in failed:
            print(f"[CarbonEngineJS SOF] export: {problem}")

        name = self.filename or "ship.blend"
        if not name.lower().endswith(".blend"):
            name += ".blend"
        target = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(target), copy=False)
            # Relative AFTER saving: there is no "relative to" until the blend
            # has a location of its own.
            bpy.ops.file.make_paths_relative()
            bpy.ops.wm.save_mainfile()
        except (RuntimeError, OSError) as exc:
            self.report({"ERROR"}, f"Could not write {target.name}: {exc}")
            return {"CANCELLED"}

        message = f"Wrote {target.name} and {len(written)} texture(s) to {folder}"
        if failed:
            message += f"; {len(failed)} failed, see the console"
        self.report({"INFO"}, message)
        return {"FINISHED"}


CLASSES = (CARBON_OT_export_textures, CARBON_OT_export_standalone)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
