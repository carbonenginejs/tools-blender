"""Build every legacy-compatible Blender add-on zip using only stdlib."""

from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ADDONS = ROOT / "addons"
PACKAGES = (
    ("io_scene_carbon_gr2", "0.1.3"),
    ("carbon_eve_resources", "0.2.3"),
)
PACKAGE_DOCUMENTS = (
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY-NOTICES.md",
    "README.md",
)


def build_package(package_name: str, version: str) -> Path:
    package = ADDONS / package_name
    output = ROOT / "dist" / f"{package_name}-{version}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(package.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            archive.write(path, path.relative_to(ADDONS))
        package_documents = PACKAGE_DOCUMENTS
        if package_name == "carbon_eve_resources":
            package_documents += ("EVE-CREATOR-LICENSE.md",)
        for name in package_documents:
            archive.write(ROOT / name, Path(package.name) / name)
    return output


def build() -> tuple[Path, ...]:
    return tuple(build_package(name, version) for name, version in PACKAGES)


if __name__ == "__main__":
    for result in build():
        print(f"Built {result.relative_to(ROOT)} ({result.stat().st_size} bytes)")
