# Sources and Frontier materials

The sidebar's **Source** selector chooses EVE Online, Frontier, Infinity or
Serenity for resource browsing and SOF/type lookup. An imported SOF object
records its source and resource/SDE builds; editing or rebuilding that object
uses its recorded source. Changing the browser source does not retarget an
existing object.
Changing Source starts an empty draft and clears the selection. **New Ship**
does the same without changing Source. Select an existing ship again to edit
its pinned settings.

All sources share the downloaded **Tool cache** and its **ResFiles** directory.
The optional read-only **EVE ResFiles** and **Frontier ResFiles** preferences
point to separate game installations. Frontier uses its own configured input;
an empty Frontier path falls through to the shared cache/downloads, not the
EVE installation. Existing EVE paths remain preserved. Decoded textures and
parsed geometry from either installation are written to the shared tool cache.
Payloads retain their
hash-safe storage names; source/build-specific catalogs, lookup results and
resource-resolution receipts are kept separately. Cache pruning preserves
addresses referenced by these receipts and files whose ownership is unknown.
When exporting, different payloads with the same logical texture name receive
distinct filenames. Re-exporting preserves edits to the exported files.

Rebuild ships saved with an older add-on to update killmark direction controls.
The previous importer omitted authored scaling values, so reopening the old
material alone cannot recover them.

Frontier materials use the compiled **sm_depth** tier, with SOPPT disabled
where that option exists. Shader identity includes the source, full effect
path and named options. A matching EVE shader basename is insufficient.
Blender supplies lighting and shadows.
Material selection checks the destination shader's slots. Choosing a legacy
material for a PBR slot, or the reverse, reports an error and preserves the
previous name and bindings; it does not invent a conversion between them.
For Frontier DNA composition, **Mesh**, **Pattern**, and **Layout** controls
are disabled and their commands are omitted. **RespathInsert** remains available.
Corporation/alliance banner controls are also disabled in Frontier mode.
Their saved image preferences remain available when switching back to EVE;
Frontier imports do not create those banner sets.

The implemented Frontier surface families are:

- `simplepbr`, `standardpbr`, `ship`, and `structure`;
- `quadv5`, `quaddetailv5`, `quadheatv5`, `quadenvironmentv5`, `quadsailsv5`, and `quadtriplanarv5`;
- organic `asteroidv5` and PBR `asteroid`;
- additive `fxv5`.

These include source-specific texture color interpretation, material layers,
UV selection, normals, cutouts and emission. Heat shimmer reuses the existing
effect. Frontier's lack of SOPPT does not change EVE's material selection.

**Frontier thermal emission and native heat haze are currently disabled in
ship imports**, following visual review of the chassis preview. Their builders
remain available for investigation, but their intended asset appearance has
not been established. Existing EVE/quad heat is unchanged.

The experimental thermal emission (`fxheatv5`) builder requires a ship radius
and does not bind a volume NoiseMap. It adds emission to the affected base
material, retaining the mesh, animation and material editing. The thermal
group exposes Growth and SunDirection; the latter starts at Carbon's scene
default `(0, -1, 0)`. Game status controllers are not imported. Gradient RGB
is decoded before mip-zero interpolation; gradient alpha remains independent.
Bound volume noise remains unsupported and is reported during assembly.
Thermal rendering has been checked on static meshes; preserving animation
data does not establish matching heat behavior on deformed geometry.

Previously built `fxheatdistortionv5` previews have a **Native heat haze (approximation)**
control in the Attribute Editor. Strength zero removes the
rendered shell. Increasing it enables Blender refraction; Coverage controls
the thermal field independently of game status controllers, and Shell width
sets the separation from the hull as a fraction of its authored radius.
The shell follows the evaluated source geometry and retains the original
opaque surface and thermal glow. Eevee requires **Eevee ray tracing** enabled.
Refraction strength, overlaps and silhouettes differ from Frontier's
screen-space distortion. Procedural bump supplies the native shimmer; it is
not a replacement decoder for an authored volume NoiseMap.
Eevee can show grain or darkening where screen-space rays miss the underlying
surface; lower Strength if this is distracting. Cycles generally gives a
cleaner refractive result.

PBR asteroid preserves the material gradient's mip-zero lookup and secondary
material packing. Rebuild the material after replacing or editing its lookup
image or changing authored UV transforms; these are captured during construction.

`fxv5` and thermal emission use the scene camera for per-vertex view inputs. Camera rendering
is supported; an independently navigated material-preview viewport does not
have the same view source. Its Geometry Nodes modifier should remain after
deformation modifiers. Eevee uses blended mode to preserve additive emission.

Unpacked, quaternion-packed and legacy angle-packed tangent data share the
existing import decoders. For verified rigid shared-quad aliases, a Geometry
Nodes modifier transforms the decoded frames using Blender's evaluated bones.
Its hidden palette helper belongs to the armature and must remain in the scene.
Shader bytecode identity selects this path; a `skinned_` name alone does not.
Meshes mixing different vertex-deformation paths retain a warning. Authored
morph changes to tangent/normal vectors and arbitrary preceding deformation
modifiers are not yet qualified.

With Source set to Frontier, **Nebula → Scene** lists scene resources instead
of EVE regions. Selecting one reads its authored NebulaMap and converts the
HDR cubemap into Blender's world environment. It uses the Frontier installation
path when configured and shares converted resources through the Tool cache.
Separate alpha, star and volume-noise layers are not reproduced. EVE, Infinity
and Serenity retain the region picker. Frontier turret fitting uses its own weapon catalog and measured depth materials.


## Weapons and the resource index

Frontier weapon pickers show stable type IDs beside names. They include authored
weapon and extractor models, including types without market groups. Models use
CMF import and the measured PBR turret or turret-quad material. Fitting respects
the selected hull's Source and build and the optional Frontier ResFiles folder.
This is visual mounting; modular assembly, game fitting restrictions and firing
FX parity are not established.

The **Resource Index** panel is an alternative to SOF DNA loading. Select Source,
load its index, browse folders or search logical paths, then use **Load Geometry**
for CMF/GR2, **Download** for any resource, or **Copy Path**. Low/medium-detail
variants have separate toggles. A capped result list reports that more matches
exist. Changing Source during a job prevents the old result from loading into
the new selection. Direct geometry loading does not assemble a SOF ship or its
materials.


Frontier nebula verification (build 3512930): eleven scene references resolve
supported BC6H cubemaps. Hydrogen Alpha was rendered as a Blender world.
Atomic Gas and Infrared reference textures absent from that build's resource
index; they cannot load. Near Infrared is a separate, available scene.
Applying a nebula enables the scene world in Material Preview and rendered
viewports. Switching sources during a fetch discards its pending world change.


Turret parameters follow Carbon's `SetupTurretMaterialFromDNA` path for both
EVE and Frontier. Constants take precedence over vector parameters. Material
prefixes and `turretAreaType` come from the selected source's generic data;
material slots are remapped through the ship faction's usage list. DNA material
overrides, applicable race values, faction materials and named colour values
are resolved before building the shader. Unmatched values retain the turret
resource defaults; there is no fixed list of colour fields or glow multiplier.
Frontier uses its turret area (10), while EVE defaults to primary (0).
The hull's `sof6` flag controls the second default pattern layer, as in Carbon.
Turrets share geometry but keep material bindings per object and material
drivers per owning ship. Each copied turret mesh follows its own armature.

The service must return turret constants as named values rather than opaque
structure-list bytes, and expose the selected source's weapons catalogue.
Update the service before deploying this addon against an older installation.
