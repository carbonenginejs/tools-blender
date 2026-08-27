# What tools-core can already answer

Surveyed against `@carbonenginejs/tools-core` 0.7.0. This is what the four tools
are built on, and what is genuinely missing.

The service is loopback-only and unauthenticated: SOF, types, skins, skinr, dna,
icons and the rest are published data. ESI credentials matter only for the
authenticated routes and the SKINR harvest.

## The trap that costs the most: two build facets

`latest` is **not one answer**. `GET /<target>/<ref>/build` returns
`{buildRef, build, builds: {resources, sde}}`.

- Resource-facet routes take `builds.resources`: `res`, `app`, `resources`,
  **`sof`**, `audio`, `character`, `resfiles`, `billboards`, `cubes`, `nebulas`.
- SDE-facet routes take `builds.sde`: `sde`, `icons`, `map`, **`skin`**,
  **`skinr`**, `weapons`, `dogma`, `industry`, `fitting`, `skills`.

Carrying one across facets is silently expensive -- an SDE build on a SOF route
acquires an entire second client build, another file index, another
`data.black`, all cold. There is deliberately no alias collapsing the two.

`builds.sde` clamps to `builds.resources` (the SDE is never newer) and may be
null. **Resolve once, then address by the exact number**: anything stored under
`latest` cannot later be matched back to the data it came from.

## SOF Editor and DNA Builder: fully served

    GET /eve/<build>/sof/hulls | factions | races | materials | layouts | patterns
    GET /eve/<build>/sof/hulls/<hull>
    GET /eve/<build>/sof/hulls/<hull>/patterns/
    GET /eve/<build>/sof/factions/<faction>
    GET /eve/<build>/sof/races/<race>
    GET /eve/<build>/sof/materials/<material>
    GET /eve/<build>/sof/patterns/<pattern>/hulls/<hull>
    GET /eve/<build>/sof/dna/<dna>
    GET /eve/<build>/sof/dna/<dna>/expanded
    GET /eve/<build>/sof/dna/<dna>/visibilityGroups

Collections return sorted canonical lowercase names; detail lookups are
case-insensitive. `/sof/dna/<dna>` returns a model-values graph carrying `_type`
and `_id`/`_ref`; `/expanded` fills in registered class defaults.

The selector is a PATH SEGMENT, not a query parameter -- a literal `?` after
`/sof/dna/` is treated as part of the DNA.

A **faction detail record** is what an editor needs for materials: `colorSet`,
`areaTypes`, `materialUsageMtl1..4` (the material-slot remap), `defaultPattern`
and its two layer material names, `logoSet`, `planeSets`, `spotlightSets`,
`visibilityGroupSet`, `resPathInsert`. Colour resolution is index-aligned, and
area material lookup falls back faction -> race -> generic wreck: the faction is
consulted LAST, not first.

Patterns have a list and a fetch but no whole-record getter, which is why they
are addressable only through `/sof/patterns/<pattern>/hulls/<hull>`.

Start the service with `--sof-full` for anything that scans catalogs
repeatedly. The default is lazy, and `/sof/hulls/<hull>/patterns/` then has to
read every indexed pattern record.

## Type Browser: the real gap

**There is no route that lists or browses types.** `/types` with no segments
returns provenance metadata, not a list; only `/types/<typeID>` returns a type,
and that record is well composed (name with resolved language, group, category,
meta group, faction, race, volume, published, graphics, manufacturers).

Three ways to populate a browser, none a first-class list:

1. `GET /eve/<sde-build>/skin/names` -- the offline library's normalised-name
   index. Each candidate carries `kind` ("type" or "skin"), `typeID`, `skinID`,
   `graphicID`, `groupID`. The `graphicID` and `groupID` are there precisely so
   a consumer can filter to drawable ships. **This is the best fit.**
2. `GET /eve/<sde-build>/dna/search?q=<term>&limit=40` -- returns candidates
   with `dna`, `typeID`, `skinID` and a `total` before limiting. DNA-oriented
   rather than type-oriented.
3. `GET /eve/<sde-build>/sde/types?field=groupID&value=<id>` -- the inspection
   surface. Its own documentation says reaching for it means an endpoint is
   missing.

## Skins

    GET /eve/<sde-build>/skin[/<section>[/<id>]]

Sections: `skins`, `skinMaterials`, `skinMaterialSets`, `skinLicenses`, `names`,
`typesToSkins`, `skinMaterialsToTypes`, `skinsToLicenses`. `typesToSkins` is the
join a browser wants -- hull type to its skins -- and
`GET /eve/<sde-build>/dna/resolve?typeID=&skinID=` turns that pair into
renderable DNA.

`skinr` is a separate topic covering the customisation system: slot
configurations, component categories, licences, tiers, ship trees, and
`sofPattern` names. A further `/v1/skinr` family serves the harvested
player-design marketplace and answers 501 until the harvest has been run.

## Attribute Editor

**There is no write or mutation route of any kind.** Attribute values cannot be
persisted through the service; they live in the blend and go out through the
SOF the tools write.

## Bundles

    cjs-sof-bundle --dna <dna> --out <directory> [--target eve] [--build latest]
                   [--cache <directory>] [--raw-textures]

Writes `bundle.json`, `document.json` (a GPU-free `carbon.document`), geometry
unchanged, and textures decoded to PNG mirroring the `res:/` layout. It names
the Blender add-ons as its intended consumer.

## Running the service

    cjs-tools-service [--host 127.0.0.1] [--port 5510] [--cache <dir>] [--sof-full]

**Discover the port; do not assume it.** The first stdout line is a JSON
bootstrap record with the host, port, pid, cache and data directories and a
`capabilities` map -- read that map before routing to a family, because it
reports which services were actually constructed. `--port 0` takes any free
port.

## The cache

Two roots, and conflating them is the trap:

| Root | Resolution order | Losing it costs |
| --- | --- | --- |
| cache | `--cache` -> `CJS_TOOL_CACHE` -> **a cwd-relative default** | a download |
| data | explicit -> `CJS_TOOL_DATA` -> package-relative | the fact itself |

The cache default FOLLOWS THE SHELL, which has already produced two separate
payload stores on one machine holding overlapping copies. **Set
`CJS_TOOL_CACHE` and pass `--cache` explicitly.**

Indexes are keyed by target (not game plus provider -- that changed on
2026-08-15); payloads are content-addressed, so every target shares them and a
second root is pure duplication. In-process, only the four most recent SOF
catalogs are retained, so a tool switching between more than four builds will
re-decode.

## Smaller things that bite

- `cjs-tool-index` takes colon-style arguments (`--target:eve`); every other
  entry point takes spaces.
- SDE and audio libraries are prepared on FIRST REQUEST, so the first
  SDE-facet call can be very slow. When a newer SDE cannot be acquired the
  service answers from the newest prepared one instead of failing -- the
  response headers report which build actually answered, so read them rather
  than assuming.
- Target ids are `eve`, `frontier`, `serenity`, `infinity`. The `/ccp/` routes
  and the game/provider route pair were removed.
