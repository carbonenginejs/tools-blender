# carbon-granny

`carbon-granny` is the dependency-free, pure-Python container, compression,
relocation, and reflection reader shared by `carbon-gr2` and `carbon-gsf`.

## Install

```text
pip install carbon-granny
```

## Quick start

```python
from carbon_granny import read_raw

raw = read_raw(source)
file_info = raw.file_info
```

## Documentation

`source` may be bytes or a binary file-like object. This low-level package
returns the reflected Granny graph; most applications should use `carbon-gr2`
or `carbon-gsf` for semantic output.

Limitations: this release is read-only and does not project render geometry or
Granny State semantics. It has no Blender, Node.js, WebAssembly, native-library,
or `granny2.dll` dependency.

## License

MIT. See `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md`.
