# CarbonEngineJS Blender Tools

These add-ons help you get EVE Online assets into Blender:

- **GR2 Importer** loads EVE's Granny 2 geometry, including meshes, UVs,
  normals, skeletons, weights, morphs, and embedded animations.
- **EVE Resource Browser** finds, downloads, validates, and previews the actual
  EVE files, so you do not have to work out CCP's index and download system. It
  also assembles a ship hull from a pre-compiled SOF bundle: the right mesh
  areas, with the right textures on them.

You still need to find what you want inside EVE's `res:/` folder structure—
sorry, we cannot save you from that part yet. <3

Browsing, downloading, and GR2 import are pure Python for Blender 4.0 and
newer. No Node.js, `granny2.dll`, or converter program is required. SOF
assembly reads a bundle that `tools-core` prepared for you; Blender never
composes SOF/DNA itself.

The source tree also contains a tested, pure-Python client boundary for the
optional local `tools-core` Node service. It is not enabled by the current
Blender UI; Node distribution and exact-build SOF bootstrap must be completed
first.

## What it does not do yet

SOF assembly currently builds the hull mesh: geometry, mesh areas, and the
textures each area names. It does not place boosters, turrets, decals, planes,
sprites, lights, banners, or child objects, and it cannot reproduce Carbon's
shaders—materials are a deliberate approximation, described below.

## Install

1. Download the zip for the tool you want using the links below. Do not unzip it.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk**, select the zip, then enable the add-on.

Current downloads:

- [GR2 Importer 0.1.3](https://github.com/carbonenginejs/tools-blender/releases/download/v0.3.0/io_scene_carbon_gr2-0.1.3.zip)
  — import Granny GR2 files.
- [EVE Resource Browser 0.3.0](https://github.com/carbonenginejs/tools-blender/releases/download/v0.3.0/carbon_eve_resources-0.3.0.zip)
  — browse and download EVE resources.

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

EVE ships are described by SOF/DNA, not by a single file. That composition
lives in Node (`tools-core` with the combined runtime SOF layer), so Blender drives it instead
of reimplementing it.

1. In preferences, set **tools-core checkout** to a `tools-core` directory
   (the add-on runs its `bin/cjs-sof-bundle.js`). Set **Node executable** too
   if `node` is not on your PATH.
2. In the **CarbonEngineJS** sidebar tab, type a DNA such as
   `cf1_t1:caldarinavy:caldari` and select **Build DNA**.

The add-on asks tools-core to compose that DNA into a bundle, then imports it.
Each DNA gets its own folder under the **SOF bundles** preference and is reused
on the next build; the refresh button beside **Build DNA** forces a rebuild.

You can also prepare a bundle yourself and import it with **Assemble Existing
Bundle**, which accepts a bundle folder, its `bundle.json`, or a bare
`document.json`:

```sh
npx cjs-sof-bundle --dna cf1_t1:caldarinavy:caldari --out ./cf1_t1
```

The bundle holds the GPU-free `carbon.document`, the geometry it references,
and its textures decoded to PNG (`--raw-textures` keeps the original DDS).

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
- Each folder under `addons/` is a self-contained add-on.
- The GR2 reader supports 32/64-bit little-endian Granny files, reflected type
  graphs, uncompressed/Oodle1/BitKnit2 sections, known `format-gr2` curve
  encodings, packed tangent frames, morphs, and GSF projection.
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
- Blender loads EVE's DXT and BC5/`ATI2` DDS payloads natively but not its
  BC7 ones (`DX10` header, dxgiFormat 98), verified on Blender 5.0. Bundle
  textures are converted to PNG by `tools-core`, which also reconstructs BC5
  normal Z; a texture Blender still cannot read is reported per area.
- Mesh-area routing counts geometry index groups across imported objects in
  file order, which is how Carbon numbers the groups of one geometry resource.
- DNA builds shell out to tools-core with an explicit Node executable; the
  add-on validates the DNA characters and never composes SOF itself. A DNA that
  tools-core cannot build is reported with its own message.

Reader API:

```python
from io_scene_carbon_gr2.gr2 import inspect, is_gsf, read_gr2, read_gsf

summary = inspect("model.gr2")
model = read_gr2("model.gr2")
state = read_gsf("character.gsf")
```

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

Optional JavaScript-reader parity tests use `GR2_PARITY_SAMPLE` and
`FORMAT_GR2_ROOT`; see `tests/test_parity_optional.py`.

## License

MIT. The BitKnit2 decoder is a Python port of CarbonEngineJS's MIT clean-room
implementation and has been validated byte-exact against the available GR2
codec corpus. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`. EVE content
remains subject to CCP Games' separate terms in `EVE-CREATOR-LICENSE.md`.
