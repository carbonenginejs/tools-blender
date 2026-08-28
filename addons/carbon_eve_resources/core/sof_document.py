"""Reader for pre-compiled runtime-sof ``carbon.document`` graphs.

`tools-core` composes SOF/DNA semantics in Node and emits one GPU-free
``carbon.document``. This module only projects that finished document into the
flat mesh/area/texture facts Blender needs; it never resolves DNA, hulls,
factions, patterns, or any other SOF rule. Keep it that way: SOF composition
belongs to `runtime-sof` through `tools-core`.

The module has no ``bpy`` dependency so it can be tested with the standard
library alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


DOCUMENT_SCHEMA = "carbon.document"
SUPPORTED_DOCUMENT_VERSION = 1
BUNDLE_SCHEMA = "carbon.sof-bundle"
SUPPORTED_BUNDLE_VERSION = 1
BUNDLE_MANIFEST_NAME = "bundle.json"
MESH_KINDS = ("Tr2Mesh", "Tr2InstancedMesh")
AREA_FIELDS = (
    ("opaqueAreas", "opaque"),
    ("transparentAreas", "transparent"),
    ("additiveAreas", "additive"),
    ("distortionAreas", "distortion"),
    ("depthAreas", "depth"),
)
TEXTURE_KIND = "TriTextureParameter"
CONSTANT_PARAMETER_KIND = "Tr2ConstantEffectParameter"
SHADER_OPTION_KIND = "Tr2ShaderOption"


class SofDocumentError(RuntimeError):
    """Raised when a document is not a usable pre-compiled SOF build."""


@dataclass(frozen=True, slots=True)
class SofArea:
    """One `Tr2MeshArea` and its `Tr2Effect` projected into flat values."""

    name: str
    batch: str
    index: int
    count: int
    effect_path: str
    textures: Mapping[str, str] = field(default_factory=dict)
    parameters: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    options: Mapping[str, str] = field(default_factory=dict)
    casts_shadows: bool = False

    @property
    def shader(self) -> str:
        """The effect file name, for example ``quadv5.fx``."""

        return self.effect_path.rsplit("/", 1)[-1].lower()

    @property
    def slot_indices(self) -> tuple[int, ...]:
        return tuple(range(self.index, self.index + max(1, self.count)))


@dataclass(frozen=True, slots=True)
class SofMesh:
    """One geometry resource and the areas routed onto its index groups."""

    geometry_path: str
    areas: tuple[SofArea, ...]
    role: str = "primary"

    @property
    def name(self) -> str:
        return Path(self.geometry_path).stem or "mesh"

    @property
    def area_slot_count(self) -> int:
        return max((area.index + max(1, area.count) for area in self.areas), default=0)


@dataclass(frozen=True, slots=True)
class SofAssembly:
    """Everything a pre-compiled SOF document says about buildable geometry."""

    dna: str
    root_kind: str
    meshes: tuple[SofMesh, ...]

    @property
    def primary_mesh(self) -> Optional[SofMesh]:
        return next((mesh for mesh in self.meshes if mesh.role == "primary"), None)

    def areas(self) -> tuple[SofArea, ...]:
        return tuple(area for mesh in self.meshes for area in mesh.areas)

    def resource_paths(self, *, primary_only: bool = False) -> tuple[str, ...]:
        """Every geometry and texture path this assembly needs, deduplicated."""

        paths: list[str] = []
        for mesh in self.meshes:
            if primary_only and mesh.role != "primary":
                continue
            paths.append(mesh.geometry_path)
            for area in mesh.areas:
                paths.extend(area.textures.values())
        seen: dict[str, None] = {}
        for path in paths:
            normalized = str(path or "").strip()
            if normalized:
                seen.setdefault(normalized, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class SofBundle:
    """A pre-compiled document plus the local files `tools-core` wrote for it."""

    assembly: SofAssembly
    resources: Mapping[str, Path]
    build: str = ""
    directory: Optional[Path] = None
    #: The document the manifest names, so a caller that wants to run the full
    #: ship builder over this bundle does not have to guess the file name.
    document_path: Optional[Path] = None

    def unresolved(self, *, primary_only: bool = False) -> tuple[str, ...]:
        """Document resources the bundle does not provide locally."""

        return tuple(
            path
            for path in self.assembly.resource_paths(primary_only=primary_only)
            if path not in self.resources
        )


def load_sof_document(path: Path | str) -> SofAssembly:
    """Reads and projects one pre-compiled SOF document from disk."""

    return parse_sof_document(_read_json(Path(path).expanduser(), "SOF document"))


def load_sof_bundle(path: Path | str) -> SofBundle:
    """Loads a `tools-core` SOF bundle, or a bare document with no resources.

    ``path`` may be a bundle directory, its ``bundle.json``, or a standalone
    ``carbon.document`` JSON file.
    """

    source = Path(path).expanduser()
    if source.is_dir():
        manifest_path = source / BUNDLE_MANIFEST_NAME
        if not manifest_path.is_file():
            raise SofDocumentError(f"{source} contains no {BUNDLE_MANIFEST_NAME}")
        source = manifest_path

    data = _read_json(source, "SOF bundle")
    if not isinstance(data, Mapping):
        raise SofDocumentError("SOF bundle root must be a JSON object")
    if data.get("schema") == DOCUMENT_SCHEMA:
        return SofBundle(assembly=parse_sof_document(data), resources={})
    if data.get("schema") != BUNDLE_SCHEMA:
        raise SofDocumentError(
            f"Unsupported bundle schema: {data.get('schema')!r}; expected {BUNDLE_SCHEMA!r}"
        )

    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SofDocumentError("SOF bundle version must be a positive integer")
    if version > SUPPORTED_BUNDLE_VERSION:
        raise SofDocumentError(
            f"SOF bundle version {version} is newer than this add-on supports "
            f"({SUPPORTED_BUNDLE_VERSION}); update the Blender tools"
        )

    root = source.parent
    document_name = str(data.get("document", "document.json") or "document.json")
    assembly = parse_sof_document(_read_json(_bundle_path(root, document_name), "SOF document"))

    resources: dict[str, Path] = {}
    declared = data.get("resources")
    if declared is not None and not isinstance(declared, Mapping):
        raise SofDocumentError("SOF bundle resources must be an object")
    for logical_path, relative in (declared or {}).items():
        if not isinstance(logical_path, str) or not isinstance(relative, str):
            raise SofDocumentError("SOF bundle resource entries must be strings")
        local = _bundle_path(root, relative)
        if local.is_file():
            resources[logical_path] = local

    return SofBundle(
        assembly=assembly,
        resources=resources,
        build=str(data.get("build", "") or ""),
        directory=root,
        document_path=_bundle_path(root, document_name),
    )


def _bundle_path(root: Path, relative: str) -> Path:
    """Keeps bundle entries inside the bundle directory."""

    candidate = (root / relative).resolve()
    anchor = root.resolve()
    if candidate != anchor and anchor not in candidate.parents:
        raise SofDocumentError(f"SOF bundle entry escapes the bundle directory: {relative}")
    return candidate


def _read_json(path: Path, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SofDocumentError(f"Could not read {label}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SofDocumentError(f"{label} is not valid JSON: {exc}") from exc


def parse_sof_document(data: Any) -> SofAssembly:
    """Projects one decoded ``carbon.document`` into a `SofAssembly`."""

    if not isinstance(data, Mapping):
        raise SofDocumentError("SOF document root must be a JSON object")
    if data.get("schema") != DOCUMENT_SCHEMA:
        raise SofDocumentError(
            f"Unsupported document schema: {data.get('schema')!r}; expected {DOCUMENT_SCHEMA!r}"
        )
    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SofDocumentError("SOF document version must be a positive integer")
    if version > SUPPORTED_DOCUMENT_VERSION:
        raise SofDocumentError(
            f"SOF document version {version} is newer than this add-on supports "
            f"({SUPPORTED_DOCUMENT_VERSION}); update the Blender tools"
        )

    nodes = _index_nodes(data.get("nodes"))
    root_id = _root_id(data.get("roots"), nodes)
    root = nodes[root_id]
    root_fields = _fields(root)

    primary_id = _optional_ref(root_fields.get("mesh"))
    discovered = _discover_mesh_ids(nodes, root_id)
    if primary_id is not None and primary_id in nodes:
        # The root's own mesh is the hull, whatever order traversal found it in.
        mesh_ids = (primary_id,) + tuple(item for item in discovered if item != primary_id)
    else:
        mesh_ids = discovered

    meshes = tuple(
        _build_mesh(nodes, mesh_id, "primary" if mesh_id == primary_id else "secondary")
        for mesh_id in mesh_ids
    )
    if not meshes:
        raise SofDocumentError("SOF document contains no mesh geometry")

    return SofAssembly(
        dna=str(root_fields.get("dna", "") or ""),
        root_kind=str(root.get("kind", "") or ""),
        meshes=meshes,
    )


def _index_nodes(value: Any) -> dict[int, Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SofDocumentError("SOF document nodes must be a list")
    nodes: dict[int, Mapping[str, Any]] = {}
    for node in value:
        if not isinstance(node, Mapping):
            raise SofDocumentError("SOF document node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, int) or isinstance(node_id, bool) or node_id <= 0:
            raise SofDocumentError("SOF document node id must be a positive integer")
        if node_id in nodes:
            raise SofDocumentError(f"SOF document repeats node id {node_id}")
        if not isinstance(node.get("kind"), str) or not node.get("kind"):
            raise SofDocumentError(f"SOF document node {node_id} has no kind")
        nodes[node_id] = node
    if not nodes:
        raise SofDocumentError("SOF document contains no nodes")
    return nodes


def _root_id(value: Any, nodes: Mapping[int, Mapping[str, Any]]) -> int:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise SofDocumentError("SOF document declares no roots")
    preferred = None
    for root in value:
        if not isinstance(root, Mapping):
            continue
        node_id = _optional_ref(root.get("ref"))
        if node_id is None or node_id not in nodes:
            continue
        if root.get("name") == "default":
            return node_id
        if preferred is None:
            preferred = node_id
    if preferred is None:
        raise SofDocumentError("SOF document root does not reference a known node")
    return preferred


def _discover_mesh_ids(nodes: Mapping[int, Mapping[str, Any]], root_id: int) -> tuple[int, ...]:
    """Walks the graph from the root, recording mesh nodes in discovery order."""

    found: list[int] = []
    seen: set[int] = set()
    pending: list[int] = [root_id]
    while pending:
        node_id = pending.pop(0)
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        node = nodes[node_id]
        if node.get("kind") in MESH_KINDS:
            found.append(node_id)
        pending.extend(_child_refs(_fields(node)))
    return tuple(found)


def _child_refs(value: Any) -> list[int]:
    refs: list[int] = []
    pending: list[Any] = [value]
    while pending:
        current = pending.pop(0)
        if isinstance(current, Mapping):
            reference = _optional_ref(current)
            if reference is not None:
                refs.append(reference)
                continue
            pending = list(current.values()) + pending
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending = list(current) + pending
    return refs


def _build_mesh(nodes: Mapping[int, Mapping[str, Any]], mesh_id: int, role: str) -> SofMesh:
    fields = _fields(nodes[mesh_id])
    areas: list[SofArea] = []
    for field_name, batch in AREA_FIELDS:
        for area_id in _ref_list(fields.get(field_name)):
            area = nodes.get(area_id)
            if area is None:
                continue
            areas.append(_build_area(nodes, area, batch))
    return SofMesh(
        geometry_path=str(fields.get("geometryResPath", "") or ""),
        areas=tuple(areas),
        role=role,
    )


def _build_area(
    nodes: Mapping[int, Mapping[str, Any]],
    area: Mapping[str, Any],
    batch: str,
) -> SofArea:
    fields = _fields(area)
    effect_id = _optional_ref(fields.get("effect"))
    effect_fields = _fields(nodes[effect_id]) if effect_id in nodes else {}

    textures: dict[str, str] = {}
    for resource_id in _ref_list(effect_fields.get("resources")):
        resource = nodes.get(resource_id)
        if resource is None or resource.get("kind") != TEXTURE_KIND:
            continue
        resource_fields = _fields(resource)
        name = str(resource_fields.get("name", "") or "")
        path = str(resource_fields.get("resourcePath", "") or "")
        if name and path:
            textures[name] = path

    parameters: dict[str, tuple[float, ...]] = {}
    for parameter_id in _ref_list(effect_fields.get("constParameters")):
        parameter = nodes.get(parameter_id)
        if parameter is None or parameter.get("kind") != CONSTANT_PARAMETER_KIND:
            continue
        parameter_fields = _fields(parameter)
        name = str(parameter_fields.get("name", "") or "")
        values = parameter_fields.get("value")
        if name and isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            parameters[name] = tuple(_finite(value) for value in values)

    options: dict[str, str] = {}
    for option_id in _ref_list(effect_fields.get("options")):
        option = nodes.get(option_id)
        if option is None or option.get("kind") != SHADER_OPTION_KIND:
            continue
        option_fields = _fields(option)
        name = str(option_fields.get("name", "") or "")
        if name:
            options[name] = str(option_fields.get("value", "") or "")

    return SofArea(
        name=str(fields.get("name", "") or "area"),
        batch=batch,
        index=_non_negative(fields.get("index")),
        count=max(1, _non_negative(fields.get("count"), default=1)),
        effect_path=str(effect_fields.get("effectFilePath", "") or ""),
        textures=dict(sorted(textures.items())),
        parameters=dict(sorted(parameters.items())),
        options=dict(sorted(options.items())),
        casts_shadows=bool(fields.get("castsShadows", False)),
    )


def _fields(node: Mapping[str, Any]) -> Mapping[str, Any]:
    fields = node.get("fields")
    return fields if isinstance(fields, Mapping) else {}


def _optional_ref(value: Any) -> Optional[int]:
    if not isinstance(value, Mapping):
        return None
    reference = value.get("$ref")
    if isinstance(reference, bool) or not isinstance(reference, int) or reference <= 0:
        return None
    return reference


def _ref_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    refs = (_optional_ref(item) for item in value)
    return tuple(ref for ref in refs if ref is not None)


def _non_negative(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0, int(value))


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def iter_texture_paths(assembly: SofAssembly) -> Iterable[str]:
    """Yields every unique texture path in the assembly."""

    seen: set[str] = set()
    for area in assembly.areas():
        for path in area.textures.values():
            if path and path not in seen:
                seen.add(path)
                yield path
