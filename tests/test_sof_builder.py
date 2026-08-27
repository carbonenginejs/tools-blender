import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.sof_builder import (  # noqa: E402
    SofBuilderError,
    build_bundle,
    bundle_directory_name,
    normalize_dna,
    resolve_bundle_script,
)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _tools_core(directory: Path) -> Path:
    script = directory / "bin" / "cjs-sof-bundle.js"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("// stub", encoding="utf-8")
    return directory


class NormalizeDnaTests(unittest.TestCase):
    def test_accepts_a_dna_with_commands(self):
        self.assertEqual(
            normalize_dna("  cf1_t1:caldarinavy:caldari:pattern?stripes;none;none  "),
            "cf1_t1:caldarinavy:caldari:pattern?stripes;none;none",
        )

    def test_rejects_incomplete_or_unsafe_dna(self):
        for value in ["", "   ", "cf1_t1:caldari", "cf1_t1:a:b; rm -rf /", "../../etc:a:b"]:
            with self.assertRaises(SofBuilderError, msg=value):
                normalize_dna(value)

    def test_directory_name_is_filesystem_safe_and_stable(self):
        name = bundle_directory_name("cf1_t1:caldarinavy:caldari:pattern?stripes;none;none")
        self.assertEqual(name, "cf1_t1_caldarinavy_caldari_pattern_stripes_none_none")
        self.assertEqual(name, bundle_directory_name("cf1_t1:caldarinavy:caldari:pattern?stripes;none;none"))


class BuildBundleTests(unittest.TestCase):
    def test_runs_tools_core_with_the_expected_command(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            core = _tools_core(root / "tools-core")
            output = root / "bundles"
            recorded = {}

            def runner(command, **kwargs):
                recorded["command"] = command
                recorded["kwargs"] = kwargs
                destination = Path(command[command.index("--out") + 1])
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "bundle.json").write_text(
                    json.dumps({"schema": "carbon.sof-bundle", "version": 1}),
                    encoding="utf-8",
                )
                return _Completed(stderr="Wrote 62 resources")

            built = build_bundle(
                "cf1_t1:caldarinavy:caldari",
                tools_core_directory=core,
                output_root=output,
                cache_root=root / "cache",
                node_executable=sys.executable,
                runner=runner,
            )

            self.assertTrue(built.created)
            self.assertEqual(built.directory, output / "cf1_t1_caldarinavy_caldari")
            self.assertTrue((built.directory / "bundle.json").is_file())
            command = recorded["command"]
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[1], str(core / "bin" / "cjs-sof-bundle.js"))
            self.assertEqual(command[command.index("--dna") + 1], "cf1_t1:caldarinavy:caldari")
            self.assertEqual(command[command.index("--build") + 1], "latest")
            self.assertIn("--cache", command)
            self.assertNotIn("--raw-textures", command)
            self.assertFalse(recorded["kwargs"]["check"])

    def test_reuses_an_existing_bundle_unless_refreshed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            core = _tools_core(root / "tools-core")
            output = root / "bundles"
            existing = output / "cf1_t1_caldarinavy_caldari"
            existing.mkdir(parents=True)
            (existing / "bundle.json").write_text("{}", encoding="utf-8")
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                Path(command[command.index("--out") + 1]).mkdir(parents=True, exist_ok=True)
                (Path(command[command.index("--out") + 1]) / "bundle.json").write_text(
                    "{}", encoding="utf-8"
                )
                return _Completed()

            reused = build_bundle(
                "cf1_t1:caldarinavy:caldari",
                tools_core_directory=core,
                output_root=output,
                node_executable=sys.executable,
                runner=runner,
            )
            self.assertFalse(reused.created)
            self.assertEqual(calls, [])

            rebuilt = build_bundle(
                "cf1_t1:caldarinavy:caldari",
                tools_core_directory=core,
                output_root=output,
                node_executable=sys.executable,
                refresh=True,
                runner=runner,
            )
            self.assertTrue(rebuilt.created)
            self.assertEqual(len(calls), 1)

    def test_reports_failures_with_the_tools_core_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            core = _tools_core(root / "tools-core")

            def failing(command, **kwargs):
                return _Completed(returncode=1, stderr="SOF DNA \"x\" is not buildable")

            with self.assertRaises(SofBuilderError) as failure:
                build_bundle(
                    "x:y:z",
                    tools_core_directory=core,
                    output_root=root / "bundles",
                    node_executable=sys.executable,
                    runner=failing,
                )
            self.assertIn("not buildable", str(failure.exception))

            def silent(command, **kwargs):
                return _Completed()

            with self.assertRaises(SofBuilderError) as missing:
                build_bundle(
                    "x:y:z",
                    tools_core_directory=core,
                    output_root=root / "bundles",
                    node_executable=sys.executable,
                    runner=silent,
                )
            self.assertIn("did not write a bundle", str(missing.exception))

    def test_reports_a_timeout_and_a_missing_checkout(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            core = _tools_core(root / "tools-core")

            def slow(command, **kwargs):
                raise subprocess.TimeoutExpired(command, 900)

            with self.assertRaises(SofBuilderError) as timeout:
                build_bundle(
                    "x:y:z",
                    tools_core_directory=core,
                    output_root=root / "bundles",
                    node_executable=sys.executable,
                    runner=slow,
                )
            self.assertIn("timed out", str(timeout.exception))

            with self.assertRaises(SofBuilderError):
                resolve_bundle_script(root / "absent")
            with self.assertRaises(SofBuilderError):
                resolve_bundle_script(root)


if __name__ == "__main__":
    unittest.main()
