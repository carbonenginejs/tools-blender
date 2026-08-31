# What tools-core can already answer

Surveyed against the hosted service backed by `@carbonenginejs/tools-core`
0.7.0. This records the HTTP surface the Blender add-on consumes and what is
genuinely missing.

SOF, types, skins, skinr, DNA, icons, and the rest are published data. ESI
credentials matter only for authenticated routes and the SKINR harvest.

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

## Existing bundles

The add-on still accepts `bundle.json` plus a GPU-free `carbon.document`,
unchanged geometry, and decoded textures mirroring the `res:/` layout. New DNA
builds use the hosted service and do not require local build scripts.

## The add-on cache

The add-on owns one configured cache. Resource indexes record exact builds and
payloads are content-addressed, so downloaded files can be validated and reused
without relying on a local service checkout.

## Smaller things that bite

- SDE and audio libraries are prepared on FIRST REQUEST, so the first
  SDE-facet call can be very slow. When a newer SDE cannot be acquired the
  service answers from the newest prepared one instead of failing -- the
  response headers report which build actually answered, so read them rather
  than assuming.
- The add-on addresses the `eve` target only.
