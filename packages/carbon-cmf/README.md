# carbon-cmf

`carbon-cmf` is a dependency-free, pure-Python CMF v1 reader for mesh,
skeleton, animation, and meshoptimizer-compressed geometry data.

## Install

```text
pip install carbon-cmf
```

## Quick start

```python
from carbon_cmf import inspect, read_cmf

document = read_cmf("model.cmf")
summary = inspect("model.cmf")
```

## Documentation

`read_cmf` accepts a path, bytes, or a binary file-like object. Meshless
skeleton and animation documents are valid. Packed tangent channels are
expanded by default; pass `unpack_tangents=False` to retain packed-only
geometry.

Limitations: this release reads CMF v1 only and does not write CMF files. It
has no Blender, Node.js, WebAssembly, or native-library dependency.

## License

MIT. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`.
