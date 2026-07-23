"""CarbonEngineJS EVE resource browser for Blender."""

bl_info = {
    "name": "CarbonEngineJS EVE Resource Browser",
    "author": "CarbonengineJS",
    "version": (0, 2, 3),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > CarbonEngineJS",
    "description": "Browse, validate, download, and preview EVE Online resources",
    "doc_url": "https://github.com/orgs/carbonenginejs/",
    "tracker_url": "https://github.com/carbonenginejs/tools-blender/issues",
    "category": "System",
}


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()


if __name__ == "__main__":
    register()
