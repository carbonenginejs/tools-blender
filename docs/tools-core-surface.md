# What tools-core can already answer

Surveyed against the hosted service backed by `@carbonenginejs/tools-core`
0.7.0. This records the HTTP surface the Blender add-on consumes and what is
genuinely missing.

SOF, types, skins, skinr, DNA, icons, and the rest are published data. ESI
credentials matter only for authenticated routes and the SKINR harvest.

## The trap that costs the most: two build facets

Use the tools-core [build-reference contract](https://github.com/carbonenginejs/tools-core/blob/main/docs/reference/http-routes.md#build-references)
for the two facets, route mapping, SDE clamp/null behavior and exact-build
pinning. Resolve once: storing `latest` loses reproducibility, and accidentally
using an SDE build for SOF acquires a second cold client index and `data.black`.

## SOF Editor and DNA Builder: fully served

The [SOF route contract](https://github.com/carbonenginejs/tools-core/blob/main/docs/reference/http-routes.md#gpu-free-sof-routes)
owns catalog and DNA endpoints, canonical names, case-insensitive lookup,
model-values identity, expanded defaults and path-segment selection (including
literal `?` handling).

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
