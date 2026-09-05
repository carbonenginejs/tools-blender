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

`build_shared_from_cmf` preserves absolute morph positions (`dataIsDeltas=False`)
and LOD thresholds. `build_cmf_from_shared` expands sparse/delta morph input to
absolute channels, preserves indexed vertex usages, and calculates missing UV
densities from LOD0. Lower LODs require explicit descending thresholds and the
same declaration, area count, and morph target layout as the base.

Reads validate CRC (including a stored zero), section/span boundaries,
declarations, LODs, palettes, skeletons, and animation curves. Float vertex
finiteness is checked when buffers are decoded. Use `validate_crc=False` only
when intentionally inspecting a file with an invalid checksum.

Limitations: this release reads CMF v1 only and does not write CMF files. It
has no Blender, Node.js, WebAssembly, or native-library dependency.

## License

MIT. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`.

CarbonEngine and Fenris Creations (CCP Games) formats are referenced for
interoperability. This project is not affiliated with or endorsed by them.
