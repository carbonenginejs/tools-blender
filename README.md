# CarbonEngineJS Blender Tools

These add-ons help you get EVE Online assets into Blender:

- **GR2 Importer** loads EVE's Granny 2 geometry, including meshes, UVs,
  normals, skeletons, weights, morphs, and embedded animations.
- **CMF Importer** loads Carbon Mesh Format geometry, LODs, UVs, packed or
  unpacked tangent frames, morphs, weights, and skeletons. CMF animation curves
  are readable by the library, but creating Blender Actions from them is still
  pending.
- **EVE Resource Browser** finds, downloads, validates, and previews the actual
  EVE files, so you do not have to work out CCP's index and download system. It
  also assembles a ship hull from a pre-compiled SOF bundle: the right mesh
  areas, with the right textures on them.

You still need to find what you want inside EVE's `res:/` folder structure—
sorry, we cannot save you from that part yet. <3

Browsing, downloading, GR2/CMF import, and ship assembly are pure Python for
Blender 4.0 and newer. No Node.js, `granny2.dll`, WASM, or converter program is
required. The add-on asks the hosted CarbonEngineJS service to compose SOF/DNA
and downloads the resolved EVE resources directly.

## What it does not do yet

The format libraries are readers only. CMF animation curves are not yet turned
into Blender Actions, and the CMF importer currently accepts triangle-list
geometry with one selected skeleton. Carbon's shaders cannot be reproduced
exactly in Blender, so generated materials are a deliberate approximation.

## Install

1. Download the zip for the tool you want using the links below. Do not unzip it.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk**, select the zip, then enable the add-on.

Current download:

- [CarbonEngineJS Blender Tools 0.7.0](https://github.com/carbonenginejs/tools-blender/releases/download/v0.7.0/carbon_eve_resources-0.7.0.zip)
  — EVE resource browsing and ship loading, with GR2 and CMF importers.

The readers can also be installed without Blender:

```text
pip install carbon-cmf carbon-gr2 carbon-gsf
```

Do not install GitHub's `tools-blender-main.zip`; it is the repository source,
not a Blender add-on package.

## Import a GR2

1. Open **File > Import > Granny GR2 (.gr2)**.
2. Choose a file and select **Import Granny GR2**.

The defaults suit most EVE models. Import options are grouped into Transform,
Geometry, Rigging, and Animation; their persistent defaults are in the
add-on's preferences.

A GR2 alone is not a complete character. Characters also need their resource
metadata, materials, textures, and often GSF data; character assembly will be
a separate tool.

## Import a CMF

1. Open **File > Import > Carbon Mesh Format (.cmf)**.
2. Choose a file and select **Import Carbon CMF**.

The importer uses the same Blender mesh, morph, skinning, and armature builders
as GR2. Additional CMF LODs follow the existing **Skip LOD meshes** preference.
Meshless CMFs are accepted and create an armature when they contain a skeleton.
CMF animation channels are preserved by the reader but do not create Blender
Actions yet. The current Blender projection imports triangle-list geometry and
one selected skeleton; it reports unsupported topology or multi-skeleton mesh
bindings instead of silently constructing the wrong scene.

## Browse EVE resources

1. In the 3D View, press **N** and open the **CarbonEngineJS** tab.
2. Accept CCP Games' EVE content-creation terms.
3. Select **Load Resources**.

The browser starts at `res:/dx9/model/ship/`. Double-click folders to open
them, select an image to preview it, or double-click a GR2 to download and
import it when the GR2 add-on is enabled. Search and the **Low**/**Medium**
switches help control the file list.

The latest index is downloaded only when needed. **Refresh** can check for a
new build at most once every 12 hours per cache; repeated clicks reuse the
last checked build. Downloaded files are size- and MD5-validated. Preferences
show cache locations and downloaded totals, and provide **Clear Cache**.

You must accept the live
[EVE Online Content Creation Terms of Use](https://support.eveonline.com/hc/en-us/articles/8563917741084-EVE-Online-Content-Creation-Terms-of-Use)
before the browser accesses indexes or resources. The accepted revision and
time are stored in Blender preferences. See `EVE-CREATOR-LICENSE.md` for the
bundled notice.

## Build a ship from DNA

EVE ships are described by SOF/DNA, not by a single file. The hosted
CarbonEngineJS service performs that composition so Blender does not
reimplement it.

1. In the **CarbonEngineJS** sidebar tab, type a DNA such as
   `cf1_t1:caldarinavy:caldari` and select **Build DNA**.

The add-on requests the built document, downloads its geometry and textures,
and stores them in its validated cache. The refresh button beside **Build DNA**
forces a fresh request.

**Assemble Existing Bundle** remains available for an existing bundle folder,
`bundle.json`, or bare `document.json`.

The add-on imports the hull geometry through the GR2 importer, maps each
`Tr2MeshArea` onto the geometry index groups it names, and builds one material
per area. Anything the bundle does not contain is downloaded through the
resource browser, so a bare document still works when the index is loaded.

### Use your own shader as the material

Set the **Shader library** preference to a `.blend` containing a node group
named for the effect family—`QuadV5` covers `quadv5.fx`, `quadheatv5.fx`, and
`quadglassv5.fx`. When it is present, the add-on appends that group and drives
it with the document's own values: each SOF texture parameter is linked to the
same-named group input, every `Mtl1-4` diffuse/fresnel/gloss constant is set
from the build, and the group's `Albedo` output replaces the Principled base
color. A few common authored spellings are accepted as aliases.

This is how faction colors and the paint mask actually show up: Carbon selects
four materials per area from the faction record, and a group with a material
mask can blend them the way the real shader does. Without a shader library the
add-on falls back to the Principled approximation below.

### Why materials are approximate

EVE's `res:/graphics/effect/...` shaders are compiled Carbon effects. Blender
cannot run them, so equal shading is not possible here and the add-on does not
pretend otherwise:

- `AlbedoMap`, `RoughnessMap`, `NormalMap`, and `GlowMap` are connected to the
  matching Principled BSDF inputs, with sRGB or non-color set per map.
- Carbon-only maps—`MaterialMap`, `PaintMaskMap`, `DirtMap`, the pattern
  masks, and any parameter this version does not know—are still loaded as
  labelled, unconnected image nodes so nothing is lost.
- Every effect path, shader option, and constant parameter is written to the
  material as a `carbon_sof_*` custom property.
- Transparent areas blend, decal areas clip, and depth-only clones are skipped
  because they repeat an area that already has a material.

Faction colors, patterns, heat glow, and the rest of Carbon's per-material
shading are therefore visible as data, not as shading.

## Technical reference

- Author: **CarbonengineJS** — https://github.com/orgs/carbonenginejs/
- Blender 4.0+; integration tested with Blender 5.0.
- Release bundles include the `carbon_cmf`, `carbon_granny`, `carbon_gr2`, and
  `carbon_gsf` libraries beside the Blender add-on. The same libraries can be
  installed independently from `packages/`.
- The GR2 reader supports 32/64-bit little-endian Granny files, reflected type
  graphs, uncompressed/Oodle1/BitKnit2 sections, known curve encodings, packed
  tangent frames, and morphs. GSF state documents use the separate GSF reader.
- Authored normals and tangent frames are preserved as mesh data; the missing
  normal fallback uses a 30-degree smoothing angle.
- Cached EVE payloads use **this add-on's own layout**, `ccp/builds/<build>/indexes/`,
  alongside a `ResFiles/<shard>/` store that follows the same content-addressed
  convention tools-core uses. The index layout is not tools-core's and never
  was: tools-core keys its sidecars by target (`targets/<target>/builds/…`),
  changed on 2026-08-15, and this add-on reaches it only over the HTTP service.
  **Clear Cache** keeps indexes, the 12-hour check marker, and files copied into
  Downloads.
- Existing installs retain the `carbonenginejs/tool-core` cache directory as
  the shared v1 on-disk location; the directory name is not the package name.
- SOF bundles are read as `carbon.sof-bundle` version 1 manifests wrapping a
  `carbon.document` version 1 graph; a newer document or bundle version is
  refused rather than partly understood.
- Blender loads EVE's DXT and BC5/`ATI2` DDS payloads natively but not its BC7
  ones (`DX10` header, dxgiFormat 98), verified on Blender 5.0. The add-on's
  pure-Python decoder translates unsupported payloads and reconstructs BC5
  normal Z; a texture Blender still cannot read is reported per area.
- Mesh-area routing counts geometry index groups across imported objects in
  file order, which is how Carbon numbers the groups of one geometry resource.
- DNA builds use the hosted CarbonEngineJS service. The add-on validates the
  DNA characters and never composes SOF itself. A DNA the service cannot build
  is reported with its own message.

Reader API:

```python
from carbon_cmf import read_cmf
from carbon_gr2 import inspect, read_gr2, read_gr2_as_cmf
from carbon_gsf import is_gsf, read_gsf

summary = inspect("model.gr2")
model = read_gr2("model.gr2")
interchange = read_gr2_as_cmf("model.gr2")
native_interchange = read_cmf("model.cmf")
state = read_gsf("character.gsf")
```

The former nested reader path remains as a compatibility shim in the Blender
add-on; new non-Blender code should import the standalone packages directly.

SOF bundle API:

```python
from carbon_eve_resources.sof_document import load_sof_bundle

bundle = load_sof_bundle("cf1_t1")
mesh = bundle.assembly.primary_mesh
areas = [(area.name, area.slot_indices, area.shader) for area in mesh.areas]
```

Build and test:

```text
py -3 scripts/build_addon.py
py -3 -m unittest discover -s tests -v
```

## License

MIT. The BitKnit2 decoder is a Python port of CarbonEngineJS's MIT clean-room
implementation and has been validated byte-exact against the available GR2
codec corpus. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`. EVE content
remains subject to CCP Games' separate terms in `EVE-CREATOR-LICENSE.md`.
