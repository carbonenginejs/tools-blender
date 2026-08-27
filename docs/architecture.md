# The shape of the Blender tools

## The one idea

**The SOF drives the scene. The scene mirrors an `EveShip2`.**

Everything else follows from that. A ship in Blender is not a Blender scene that
happens to contain a ship; it is the engine's own structure, rebuilt in Blender
objects, driven by the SOF that describes it.

## Why the tree mirrors EveShip2

What gets exported is the **SOF hull and faction**, never the built `EveShip2`.
`EveShip2` is the hydrated result -- one ship, already resolved. The hull and
the faction are the authored, reusable inputs, and they are what a consumer
edits and ships.

So a scene that does not map back to a hull and a faction cannot be exported at
all. The structure is the feature, not a convenience: the hierarchy, the naming
and the grouping follow the engine so that walking the tree is enough to write a
SOF back out.

The same reasoning puts SOF-shaped parameters on **every object** rather than in
a side table. An object carries what its engine counterpart owns, so the tree is
self-describing, an exporter needs no index, and there is one place to look when
something renders wrong.

## Values are driven, and local edits lose

A value belongs to the SOF. The scene reads it.

Editing an object directly is possible and unsupported: it cannot be exported,
and the next SOF change overwrites it. That is a deliberate contract. It is what
keeps one source of truth, and it is the reason the driven sockets hide their
value widgets -- a slider that takes an edit and then discards it is worse than
no slider at all.

## The four tools

| Tool | Owns |
| --- | --- |
| **SOF Editor** | SOF elements: hull, race, faction, pattern |
| **SOF DNA Builder** | Composing a DNA, and loading ships from one |
| **Type Browser** | Items, skins, and the rest of the type data |
| **Attribute Editor** | Values shared across a hull -- speed, lights, dirt -- passed into the Blender constant buffers |

Each owns one job. The Attribute Editor generalises what already works: age,
activation, booster gain and the kill count are per-ship values driven into
constant-buffer sockets from the object.

## What the tools are built on

These are kept and not rewritten, because they are measured rather than
designed:

- **`quad/`** -- the shader family. `family.json` is GENERATED from the shipped
  containers; the reference maths and node-group builders are measured against
  DXBC-derived GLSL and covered by tests. Re-deriving this is the single most
  expensive thing that could be lost, and the organisation has already paid for
  that lesson more than once.
- **`ship.py`** -- one call that builds a ship: geometry, areas, decals, the
  per-ship values and the SOF that drives them. The panel and the command line
  MUST build the same ship. When they did not, decals came through on one path
  and not the other, and two hulls in one scene looked like different games.
- **the readers** -- `sof_document` (bundle and document), `sof_builder`
  (tools-core DNA), `tools_service` (the sidecar), `resource_index`.

## Names come from Carbon

The values a consumer edits ARE Trinity's constant buffer data, so they carry
Trinity's names: `boosterGain`, `activationStrength`, `dirtLevel`, `killCount`,
`Mtl1DiffuseColor`. An invented name is a second vocabulary to keep in step with
the first, and the drift is silent -- one socket here read `PaintMaskInfluence`
where Carbon says `PaintMapInfluence`, one letter apart, and nothing would ever
have failed because of it.

Where something has no Carbon counterpart it says so where it is defined:
`previewGlowScale` exists because EVE blooms its glows and Blender does not.

## Traps this design already paid for

- **Blender loads add-ons from its own scripts directory, never from a
  checkout.** Run `scripts/install_addon.py` after changes, or the panel keeps
  running an older copy and a tested change appears to do nothing. A month-old
  installed copy once sent a whole debugging session after the wrong question.
- **EEVEE delivers only EIGHT object attributes per material** and silently
  returns zero beyond that. Per-object values are driven through sockets, not
  Attribute nodes, for exactly this reason.
- **Zero-user data is purged on save.** The GR2 importer creates an Action per
  animation and assigns none of them, so the dope sheet was empty in any saved
  file. Imported actions get a fake user, and the armature gets the idle one.
- **A projection must be built from the REST position.** Blender's Texture
  Coordinate is the DEFORMED position, so a posed bone slides the hull through
  a pattern that stays put in space. Carbon projects from the raw model
  position; `rest_position` is opt-in per mesh and absent in Blender 5, so the
  builder stores `carbon_rest_position` at import, when the vertices are the
  rest pose. Decals need their own copy.
- **A skinned mesh must be parented to its armature.** The importer leaves them
  as siblings, which looks fine until the ship is moved and the geometry deforms
  against a rig that is no longer where it is.
