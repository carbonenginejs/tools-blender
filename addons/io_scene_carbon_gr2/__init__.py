"""CarbonEngineJS pure-Python GR2 importer for Blender."""

bl_info = {
    "name": "CarbonEngineJS GR2 Importer",
    "author": "CarbonengineJS",
    "version": (0, 1, 2),
    "blender": (4, 0, 0),
    "location": "File > Import > Granny GR2 (.gr2)",
    "description": "Import GR2 meshes, skeletons, skinning, morphs, and animations without Granny or Node.js",
    "doc_url": "https://github.com/orgs/carbonenginejs/",
    "tracker_url": "https://github.com/carbonenginejs/tools-blender/issues",
    "category": "Import-Export",
}


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()


if __name__ == "__main__":
    register()
