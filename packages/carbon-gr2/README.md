# carbon-gr2

`carbon-gr2` is a pure-Python semantic reader for Granny render and animation
documents, with an optional projection into the in-memory CMF v1 graph.

## Install

```text
pip install carbon-gr2
```

## Quick start

```python
from carbon_gr2 import read_gr2, read_gr2_as_cmf

gr2_document = read_gr2("character.gr2")
cmf_document = read_gr2_as_cmf("character.gr2")
```

## Documentation

Both functions accept a path, bytes, or a binary file-like object. `read_gr2`
retains meshes, root and model skeletons, transform tracks, and
arbitrary-dimensional vector tracks.

`read_gr2_as_cmf` projects renderable geometry and animation into CMF:

- Exact, unambiguous `<mesh> LOD <threshold>` siblings become ordered LODs.
- Bone palettes select compatible skeletons; ambiguous assignments fail.
  Rigid GR2 attachment palettes without vertex bone indices are removed after
  retaining their skeleton assignment.
- Float32-scale shear residue is tolerated; genuine shear is rejected.
- Only scalar vector tracks matching geometry morph names become CMF morph
  channels. One trailing `Shape` is removed from morph names for matching,
  provided a nonempty name remains. Repeated morph channels keep the first;
  conflicting bone channels fail.
- Non-rendering vector metadata remains available through `read_gr2`, but is
  omitted from CMF. Positive-duration GR2 clips with no retained channels are
  omitted; non-positive or non-finite durations fail.

Limitations: this release reads GR2 files but does not write them. GSF state
documents must be read with `carbon-gsf`. No Blender, Node.js, WebAssembly,
native library, or `granny2.dll` is required.

## License

MIT. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`.

CarbonEngine and Fenris Creations (CCP Games) formats are referenced for
interoperability. This project is not affiliated with or endorsed by them.
