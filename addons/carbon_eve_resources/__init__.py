"""CarbonEngineJS tools for Blender: build an EVE ship from a SOF DNA."""

bl_info = {
    "name": "CarbonEngineJS",
    "author": "CarbonengineJS",
    "version": (0, 4, 0),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > CarbonEngineJS",
    "description": "Build EVE ships from a SOF DNA: geometry, materials, decals "
                   "and attachments, with the GR2 importer included",
    "doc_url": "https://github.com/orgs/carbonenginejs/",
    "tracker_url": "https://github.com/carbonenginejs/tools-blender/issues",
    "category": "System",
}


def register():
    # The GR2 importer FIRST: every geometry import here goes through
    # `import_scene.carbon_gr2`, and a panel that loads ships without it is a
    # panel that reports an error instead of working.
    from . import addon, gr2_importer

    gr2_importer.register()
    addon.register()


def unregister():
    from . import addon, gr2_importer

    addon.unregister()
    gr2_importer.unregister()


if __name__ == "__main__":
    register()
