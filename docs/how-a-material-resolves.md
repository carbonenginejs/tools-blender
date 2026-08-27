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

2. **Otherwise the FACTION supplies it**, per area.

   A faction's `areaMaterials` map `areaType:slotIndex` to a material name, so
   the same slot resolves differently on a hull area than on a booster area.

3. **Failing that, the primary area is tried**, and then the generic wreck data.

   An area material lookup that finds nothing falls back to the PRIMARY area
   before giving up, so a hull that authors only its primary area still shades
   its other areas.

**Pattern materials are always in the DNA.** The `pattern` command carries the
pattern name and its two layer materials, so a pattern's colours are never a
faction lookup — which is why a SKIN can repaint a hull that has no faction
override at all.

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
