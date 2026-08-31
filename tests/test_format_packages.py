import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "carbon-cmf": "carbon_cmf",
    "carbon-granny": "carbon_granny",
    "carbon-gr2": "carbon_gr2",
    "carbon-gsf": "carbon_gsf",
}
for distribution, module in PACKAGES.items():
    sys.path.insert(0, str(ROOT / "packages" / distribution / "src"))


class FormatPackageTests(unittest.TestCase):
    def test_public_packages_import_independently_of_blender(self):
        for module in PACKAGES.values():
            with self.subTest(module=module):
                imported = __import__(module)
                self.assertEqual(imported.__version__, "0.1.0")

    def test_packages_do_not_import_blender_or_wasm(self):
        forbidden = {"bpy", "mathutils", "wasmtime", "wasmer"}
        for distribution, module in PACKAGES.items():
            source = ROOT / "packages" / distribution / "src" / module
            for path in source.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                names = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names.update(alias.name.split(".")[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names.add(node.module.split(".")[0])
                self.assertFalse(names & forbidden, f"{path} imports {names & forbidden}")

    def test_each_distribution_has_build_metadata(self):
        for distribution in PACKAGES:
            with self.subTest(distribution=distribution):
                package = ROOT / "packages" / distribution
                self.assertTrue((package / "pyproject.toml").is_file())
                self.assertTrue((package / "README.md").is_file())
                self.assertTrue((package / "LICENSE").is_file())

    def test_dependency_dag_is_explicit(self):
        dependencies = {}
        for distribution in PACKAGES:
            with open(ROOT / "packages" / distribution / "pyproject.toml", "rb") as stream:
                dependencies[distribution] = tomllib.load(stream)["project"].get("dependencies", [])
        self.assertEqual(dependencies["carbon-cmf"], [])
        self.assertEqual(dependencies["carbon-granny"], [])
        self.assertEqual(dependencies["carbon-gsf"], ["carbon-granny>=0.1.0,<0.2.0"])
        self.assertEqual(
            dependencies["carbon-gr2"],
            ["carbon-granny>=0.1.0,<0.2.0", "carbon-cmf>=0.1.0,<0.2.0"],
        )

    def test_umbrella_bundle_contains_and_imports_each_library(self):
        build_path = ROOT / "scripts" / "build_addon.py"
        spec = importlib.util.spec_from_file_location("carbon_build_addon", build_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="carbon-addon-build-") as temporary:
            output = Path(temporary)
            archive_path = module.build_package(
                "carbon_eve_resources", "test", output_directory=output
            )
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                for import_name in PACKAGES.values():
                    self.assertIn(f"{import_name}/__init__.py", names)
                    self.assertIn(f"{import_name}/README.md", names)
                    self.assertIn(f"{import_name}/LICENSE", names)
                    self.assertIn(f"{import_name}/NOTICE", names)
                extracted = output / "extracted"
                archive.extractall(extracted)
            code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from carbon_eve_resources.gr2_importer.gr2 import read_gr2; "
                "import carbon_cmf, carbon_granny, carbon_gr2, carbon_gsf; "
                "print(read_gr2.__module__)"
            )
            completed = subprocess.run(
                [sys.executable, "-S", "-c", code, str(extracted)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "carbon_gr2")


if __name__ == "__main__":
    unittest.main()
