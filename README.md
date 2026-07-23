# CarbonEngineJS Blender Tools

These add-ons help you get EVE Online assets into Blender:

- **GR2 Importer** loads EVE's Granny 2 geometry, including meshes, UVs,
  normals, skeletons, weights, morphs, and embedded animations.
- **EVE Resource Browser** finds, downloads, validates, and previews the actual
  EVE files, so you do not have to work out CCP's index and download system.

You still need to find what you want inside EVE's `res:/` folder structure—
sorry, we cannot save you from that part yet. <3

Everything is pure Python for Blender 4.0 and newer. No Node.js,
`granny2.dll`, or converter program is required.

The source tree also contains a tested, pure-Python client boundary for the
optional local `tools-core` Node service. It is not enabled by the current
Blender UI or required by the released GR2 importer/resource browser; Node
distribution and exact-build SOF bootstrap must be completed first.

## What it does not do yet

These tools do not build a complete EVE ship. For now, they retrieve and load
the individual model files. Full SOF/DNA ship assembly—including choosing
variants and referenced parts and assigning materials and textures—will come
later.

## Install

1. Download the zip for the tool you want using the links below. Do not unzip it.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk**, select the zip, then enable the add-on.

Current downloads:

- [GR2 Importer 0.1.3](https://github.com/carbonenginejs/tools-blender/releases/download/v0.2.3/io_scene_carbon_gr2-0.1.3.zip)
  — import Granny GR2 files.
- [EVE Resource Browser 0.2.3](https://github.com/carbonenginejs/tools-blender/releases/download/v0.2.3/carbon_eve_resources-0.2.3.zip)
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

## Technical reference

- Author: **CarbonengineJS** — https://github.com/orgs/carbonenginejs/
- Blender 4.0+; integration tested with Blender 5.0.
- Each folder under `addons/` is a self-contained add-on.
- The GR2 reader supports 32/64-bit little-endian Granny files, reflected type
  graphs, uncompressed/Oodle1/BitKnit2 sections, known `format-gr2` curve
  encodings, packed tangent frames, morphs, and GSF projection.
- Authored normals and tangent frames are preserved as mesh data; the missing
  normal fallback uses a 30-degree smoothing angle.
- Cached EVE payloads follow the tools-core layout under
  `ccp/builds/<build>/indexes/` and `ResFiles/<shard>/`. **Clear Cache** keeps
  indexes, the 12-hour check marker, and files copied into Downloads.
- Existing installs retain the `carbonenginejs/tool-core` cache directory as
  the shared v1 on-disk location; the directory name is not the package name.

Reader API:

```python
from io_scene_carbon_gr2.gr2 import inspect, is_gsf, read_gr2, read_gsf

summary = inspect("model.gr2")
model = read_gr2("model.gr2")
state = read_gsf("character.gsf")
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
