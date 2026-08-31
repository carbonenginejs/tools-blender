# carbon-gsf

`carbon-gsf` is a pure-Python reader for Granny State files and their ordered
model and animation dependencies.

## Install

```text
pip install carbon-gsf
```

## Quick start

```python
from carbon_gsf import read_gsf

state = read_gsf("character.gsf")
for dependency in state["dependencies"]:
    print(dependency["kind"], dependency["reference"])
```

## Documentation

The reader preserves the state machine and reports its external model and
animation GR2 references in order.

Limitations: GSF does not contain render geometry. Resolving referenced GR2
files and projecting their data to CMF is the caller's responsibility. This
release is read-only and has no Blender, Node.js, WebAssembly, native-library,
or `granny2.dll` dependency.

## License

MIT. See `LICENSE` and `NOTICE`.
