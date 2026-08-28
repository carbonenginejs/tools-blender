"""The layers, and the one rule that keeps them apart.

    addon / sidebar / sof_panels   the add-on: operators, panels, preferences
    ship / quad / dds / *_nodes    Blender adapters
    core/                          no bpy, testable on its own

A rewrite of the Blender side should not be able to reach into `core`, and
`core` must not reach back out. Checked here rather than trusted, because an
import is one line and nothing else would notice.
"""

from pathlib import Path
import re
import unittest


PACKAGE = Path(__file__).resolve().parents[1] / "addons" / "carbon_eve_resources"
CORE = PACKAGE / "core"

#: Modules above core, which core must never import.
OUTER = ("addon", "sidebar", "sof_panels", "ship", "service_access",
         "sof_material_nodes", "placeholders", "pattern_controls", "logos",
         "quad", "dds", "gr2_importer")


def core_modules():
    return sorted(p for p in CORE.glob("*.py") if p.name != "__init__.py")


class CoreIsPureTests(unittest.TestCase):
    def test_core_has_modules(self):
        # A guard that would otherwise pass vacuously if the folder moved.
        self.assertGreaterEqual(len(core_modules()), 10)

    def test_core_never_imports_bpy(self):
        # The whole point of the layer: it can be read and tested without
        # Blender, and a stray `import bpy` silently ends that.
        offenders = []
        for path in core_modules():
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(import bpy|from bpy)", text, re.M):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_core_never_imports_the_layers_above_it(self):
        offenders = []
        for path in core_modules():
            text = path.read_text(encoding="utf-8")
            for module in OUTER:
                if re.search(r"^\s*from \.\.?%s import|^\s*from \.\.? import [^\n]*\b%s\b"
                             % (module, module), text, re.M):
                    offenders.append(f"{path.name} -> {module}")
        self.assertEqual(offenders, [])

    def test_core_imports_resolve(self):
        # Every sibling a core module imports must actually be in core.
        names = {path.stem for path in core_modules()}
        missing = []
        for path in core_modules():
            text = path.read_text(encoding="utf-8")
            for found in re.findall(r"^\s*from \.(\w+) import", text, re.M):
                if found not in names:
                    missing.append(f"{path.name} -> .{found}")
            for line in re.findall(r"^\s*from \. import ([\w, ]+)$", text, re.M):
                for found in (n.strip() for n in line.split(",")):
                    if found and found not in names:
                        missing.append(f"{path.name} -> .{found}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
