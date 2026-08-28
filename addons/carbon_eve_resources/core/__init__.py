"""Everything that does not need Blender.

Nothing here imports `bpy`. That is the whole point: this layer is the EVE and
SOF knowledge -- DNA grammar, name and material lookups, resource addressing,
index reading, fetching bytes -- and it can be read, tested and reused without
Blender in the room.

The layers, outermost first:

    addon / sidebar / sof_panels     the add-on: operators, panels, preferences
    ship / quad / dds / *_nodes      Blender adapters: objects, materials, images
    core/                            this: no bpy, testable on its own

An import that points from `core` at anything above it is a mistake, and the
suite checks for exactly that.
"""
