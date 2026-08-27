# How a material resolves

A hull does not know its colours. Nothing in a hull record carries one — the
hull says which areas exist and which material SLOT each uses, and the colours
come from somewhere else entirely.

That is the reason there has to be a SOF Builder rather than a hull loader: the
resolved values have to be worked out from several sources and then pushed down
to every child, because no single source holds them.

## The order

1. **The DNA's `material` command wins**, unless the slot says `none`.

   The DNA can name a material per slot. `mesh` is an alias — when a DNA carries
   `mesh` and no `material`, the mesh arguments become the material arguments.
   A slot of `none` is not an override; it is an absence, and the search
   continues.

2. **Otherwise the FACTION supplies it**, per AREA TYPE.

   This is the part that catches people. A ship does not have four material
   slots; it has four per area type. A faction's `areaMaterials` map
   `areaType:slotIndex` to a material name, so the same slot NUMBER is a
   different material on a hull area than on a sails area.

   Measured on `mde3_t3:legion_minmatar`: primary `Mtl4DiffuseColor` is
   `(0.076, 0.065, 0.047)` and sails `Mtl4DiffuseColor` is
   `(0.002, 0.003, 0.004)`. A consumer that pushes one set of values into
   every material on the ship overwrites the second with the first.

   The eleven types are `primary, glass, sails, reactor, darkhull, wreck,
   rock, monument, ornament, simpleprimary, turret`.

3. **Failing that, the primary area is tried**, and then the generic wreck data.

   An area material lookup that finds nothing falls back to the PRIMARY area
   before giving up, so a hull that authors only its primary area still shades
   its other areas.

## What a skin may not repaint

A hull area carries `blockedMaterials`, a bitmask over the four slots. It is
consulted in exactly one place — the DNA-override step above — so it means
precisely *"a SKIN may not repaint this slot on this area"*, and nothing else.
It does not touch faction materials or patterns.

`mde3_t3`'s two sails areas carry `12`, which is bits 2 and 3. A skin naming
four materials repaints that hull and its booster with all four, and its sails
with only the first two.

*Read from the runtime and confirmed on the live hull record; the visual
consequence has not been observed.*

**Pattern materials are always in the DNA**, and they are ship-wide. The `pattern` command carries the
pattern name and its two layer materials, and the pattern branch of the chain
never consults the area type -- so PMtl values are the same on every area whose
shader asks for them. A pattern's colours are never a faction lookup — which is why a SKIN can repaint a hull that has no faction
override at all.

## The shader only ever sees the answer

By the time a value reaches the hull's shaders it is a constant: `Mtl1DiffuseColor`
and its neighbours, four numbers in a buffer. The shader has no idea a faction
exists, cannot tell an override from a default, and could not resolve one if it
wanted to.

Resolution is therefore entirely a CPU-side concern, and what sits in the scene's
shader sockets is a RESULT. That is the whole reason the SOF has to be the source
and the scene the consequence: read the sockets back and you learn what the colour
IS, never what decided it — and only the thing that decided it can be exported.

## What that means for the tools

A material slot names a MATERIAL. The colours under it are what that material
holds, which is why the editor leads with the name and shows the values beneath
it rather than offering colour pickers: picking a colour is a material editor's
model, not the SOF's.

Editing a colour directly therefore means the slot has left its material behind.
The slot says `custom` at that point, because a name that no longer describes
its values is worse than no name — and a custom material exists nowhere but the
blend until it is exported, which is the same trap as editing an object instead
of its SOF.

## Where the values come from

| Source | Route | Carries |
| --- | --- | --- |
| DNA | the `material` / `mesh` command | per-slot overrides, or `none` |
| DNA | the `pattern` command | the pattern and its two layer materials |
| faction | `/eve/<build>/sof/factions/<faction>` | `areaTypes`, `colorSet`, `materialUsageMtl1..4` |
| material | `/eve/<build>/sof/materials/<name>` | the parameters a slot resolves to |

The build must be the RESOURCE build. `latest` resolves to two different
numbers, one for resources and one for the SDE, and a SOF route given the SDE
build silently acquires a whole second client build.
