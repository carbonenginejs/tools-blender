"""CarbonEngineJS tools for Blender: build an EVE ship from a SOF DNA."""

bl_info = {
    "name": "CarbonEngineJS",
    "author": "CarbonengineJS",
    "version": (0, 7, 1),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > CarbonEngineJS",
    "description": "Build EVE ships from a SOF DNA: geometry, materials, decals "
                   "and attachments, with GR2 and CMF importers included",
    "doc_url": "https://github.com/orgs/carbonenginejs/",
    "tracker_url": "https://github.com/carbonenginejs/tools-blender/issues",
    "category": "System",
}


def register():
    # Geometry importers first: ship assembly calls the GR2 operator, while CMF
    # files share its mesh and armature construction path.
    from . import addon, animation, export, gr2_importer, skybox, turrets

    gr2_importer.register()
    addon.register()
    export.register()
    skybox.register()
    turrets.register()
    animation.register()


def unregister():
    from . import addon, animation, export, gr2_importer, skybox, turrets

    animation.unregister()
    turrets.unregister()
    skybox.unregister()
    export.unregister()
    addon.unregister()
    gr2_importer.unregister()


if __name__ == "__main__":
    register()
