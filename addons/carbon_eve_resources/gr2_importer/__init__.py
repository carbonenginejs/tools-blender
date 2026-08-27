"""The pure-Python GR2 importer, as a component of the CarbonEngineJS add-on.

It used to be a second add-on that a person had to find, install and enable
before this one could load anything -- and the failure when they had not was
"Enable CarbonEngineJS GR2 Importer first", which is a support question rather
than a tool working. One download now.

`bl_info` is gone deliberately: this is not separately installable any more,
and leaving it would have Blender offer it as an add-on that cannot be enabled
on its own. The operator id is unchanged -- `import_scene.carbon_gr2` -- so
anything that called it, in this package or in someone's script, still works.
"""


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()
