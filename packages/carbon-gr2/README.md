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

Both functions accept a path, bytes, or a binary file-like object. Meshes,
root and model skeletons, transform tracks, and arbitrary-dimensional vector
tracks are retained.

Limitations: this release reads GR2 files but does not write them. GSF state
documents must be read with `carbon-gsf`. No Blender, Node.js, WebAssembly,
native library, or `granny2.dll` is required.

## License

MIT. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`.
