"""Applies a pre-compiled SOF assembly to imported Blender geometry.

The GR2 importer already splits a hull into one material slot per geometry
index group, in file order. A SOF mesh area names the same groups through its
``index``/``count`` pair, so assembly is a slot mapping plus an approximate
material per area. Slots are counted across imported objects in import order,
which is how Carbon numbers the groups of one geometry resource.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Callable

import bpy

from .sof_document import SofArea, SofMesh
from .sof_shading import (
    GROUP_INPUT_DEFAULTS,
    GROUP_SCALAR_INPUTS,
    MaterialPlan,
    TexturePlan,
    group_input_names,
    plan_material,
    should_build_material,
)


@dataclass
class AssemblyReport:
    """What assembly actually did, so the UI can be honest about it."""

    materials: int = 0
    assigned_slots: int = 0
    skipped_areas: int = 0
    missing_textures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.materials} area materials", f"{self.assigned_slots} slots"]
        if self.missing_textures:
            parts.append(f"{len(self.missing_textures)} textures unavailable")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        return ", ".join(parts)


def apply_mesh_areas(
    mesh: SofMesh,
    objects: Sequence[bpy.types.Object],
    resources: Mapping[str, Path],
    *,
    prefix: str = "",
    shader_library: str = "",
    progress: Optional[Callable[[str], None]] = None,
) -> AssemblyReport:
    """Assigns one approximate material per SOF area onto the imported slots.

    `progress` is called with a line per area, so a long assembly says what it
    is doing instead of looking like a hang. It is optional because nothing
    about the assembly depends on being watched.
    """

    report = AssemblyReport()
    slots = _slot_table(objects)
    if not slots:
        report.warnings.append(f"{mesh.name}: imported geometry has no material slots")
        return report

    buildable = [area for area in mesh.areas if should_build_material(area)]
    for position, area in enumerate(mesh.areas, start=1):
        if progress is not None and should_build_material(area):
            done = sum(1 for other in buildable if mesh.areas.index(other) < mesh.areas.index(area))
            progress(f"{mesh.name}: area {done + 1}/{len(buildable)} {area.name}")
        if not should_build_material(area):
            report.skipped_areas += 1
            continue
        targets = [index for index in area.slot_indices if index < len(slots)]
        if not targets:
            report.warnings.append(
                f"{area.name}: slots {area.index}-{area.index + area.count - 1} are outside the "
                f"{len(slots)} slots imported from {mesh.name}"
            )
            continue
        # The accurate quad material first; the approximation only when the
        # effect is outside the measured family.
        material, problem = _carbon_material(area, resources, report.materials)
        if material is None:
            if problem:
                report.warnings.append(f"{area.name}: approximated ({problem})")
            plan = plan_material(area, prefix=prefix)
            material = build_material(plan, resources, report, shader_library=shader_library)
        report.materials += 1
        for slot_index in targets:
            target_object, local_index = slots[slot_index]
            target_object.data.materials[local_index] = material
            report.assigned_slots += 1
    return report



def _as_document_area(area) -> dict:
    """A `SofArea` in the shape the quad builder reads.

    The quad builder takes an area straight out of the expanded SOF document --
    an `effect` with `resources` and `constParameters` -- because that is what
    the document holds. `SofArea` is the same information already flattened, so
    this is a translation and not a conversion: no value changes, only its
    shape.
    """

    return {
        "name": area.name,
        "effect": {
            "effectFilePath": area.effect_path,
            "resources": [{"name": name, "resourcePath": path}
                          for name, path in (area.textures or {}).items()],
            "constParameters": [{"name": name, "value": list(value)}
                                for name, value in (area.parameters or {}).items()],
        },
    }


def _carbon_material(area, resources: Mapping[str, Path], index: int):
    """The accurate material for an area, or None if it cannot be built.

    None is not a failure to report loudly: a hull can use an effect outside the
    measured quad family, and the approximation is the right answer there. It IS
    worth knowing which happened, so the caller records it.
    """

    try:
        from .quad import interface as quad_interface
        from .quad import materials as quad_materials
        family = quad_interface.load_family()
    except Exception:
        return None, "quad interface data is unavailable"

    material, problem = quad_materials.build_area_material(
        _as_document_area(area), family, resources, index)
    return material, problem

def build_material(
    plan: MaterialPlan,
    resources: Mapping[str, Path],
    report: Optional[AssemblyReport] = None,
    *,
    shader_library: str = "",
) -> bpy.types.Material:
    """Creates the approximate material described by ``plan``.

    When a shader library supplies the plan's node group, SOF textures and
    constant parameters drive it and it replaces the Principled base color;
    otherwise the Principled approximation is used alone.
    """

    material = bpy.data.materials.new(name=plan.name)
    material.use_nodes = True
    tree = material.node_tree
    principled = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    group = _create_shader_group(tree, plan, shader_library, report)
    albedo_node = None
    for position, texture in enumerate(plan.textures):
        image = _load_image(texture, resources, report)
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = texture.parameter
        node.label = f"{texture.parameter} ({texture.note})"
        node.location = (-900, 400 - position * 300)
        if image is not None:
            node.image = image
            _set_colorspace(image, texture.colorspace)
            if group is not None:
                socket = _group_input(group, texture.parameter)
                if socket is not None:
                    tree.links.new(node.outputs["Color"], socket)
        if texture.principled_input is None or image is None:
            continue
        if group is not None and texture.principled_input == "Base Color":
            # The group owns base color once it is driving the material.
            albedo_node = node
            continue
        if texture.principled_input == "Normal":
            normal_map = tree.nodes.new("ShaderNodeNormalMap")
            normal_map.location = (-560, node.location[1])
            tree.links.new(node.outputs["Color"], normal_map.inputs["Color"])
            tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
            continue
        socket = principled.inputs.get(texture.principled_input)
        if socket is None:
            if report is not None:
                report.warnings.append(
                    f"{plan.name}: this Blender version has no '{texture.principled_input}' input"
                )
            continue
        tree.links.new(node.outputs["Color"], socket)
        if texture.principled_input == "Base Color":
            albedo_node = node
        if texture.principled_input == "Emission Color":
            strength = principled.inputs.get("Emission Strength")
            if strength is not None:
                strength.default_value = plan.emission_strength

    if group is not None:
        _apply_group_parameters(group, plan)
        base_color = principled.inputs.get("Base Color")
        output = group.outputs.get("Albedo") or (group.outputs[0] if len(group.outputs) else None)
        if base_color is not None and output is not None:
            tree.links.new(output, base_color)

    if plan.use_alpha and albedo_node is not None:
        alpha = principled.inputs.get("Alpha")
        if alpha is not None:
            tree.links.new(albedo_node.outputs["Alpha"], alpha)
    _set_blend_method(material, plan.blend_method)

    for key, value in plan.metadata.items():
        material[key] = value
    if plan.notes:
        material["carbon_sof_notes"] = " | ".join(plan.notes)
    return material


def _create_shader_group(tree, plan: MaterialPlan, shader_library: str, report):
    """Adds the plan's node group, appending it from the library when needed."""

    if not plan.node_group or not shader_library:
        return None
    node_tree = load_shader_group(plan.node_group, shader_library, report)
    if node_tree is None:
        return None
    group = tree.nodes.new("ShaderNodeGroup")
    group.node_tree = node_tree
    group.name = plan.node_group
    group.label = f"{plan.node_group} ({plan.shader})"
    group.location = (-300, 400)
    return group


def load_shader_group(name: str, shader_library: str, report=None):
    """Returns the named node group, appending it from a .blend once."""

    existing = bpy.data.node_groups.get(name)
    if existing is not None:
        return existing
    path = Path(bpy.path.abspath(shader_library)).expanduser()
    if not path.is_file():
        if report is not None:
            report.warnings.append(f"Shader library not found: {path}")
        return None
    try:
        with bpy.data.libraries.load(str(path), link=False) as (source, target):
            if name not in source.node_groups:
                available = ", ".join(sorted(source.node_groups)) or "none"
                if report is not None:
                    report.warnings.append(
                        f"{path.name} has no '{name}' node group (contains: {available})"
                    )
                return None
            target.node_groups = [name]
    except (OSError, RuntimeError) as exc:
        if report is not None:
            report.warnings.append(f"Could not read shader library {path.name}: {exc}")
        return None
    return bpy.data.node_groups.get(name)


def _group_input(group, parameter: str):
    """Finds a group input for one Carbon parameter, allowing authored aliases."""

    for name in group_input_names(parameter):
        socket = group.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _apply_group_parameters(group, plan: MaterialPlan) -> None:
    """Feeds Carbon's constant effect parameters into same-named group inputs."""

    for name, value in GROUP_INPUT_DEFAULTS.items():
        if name in plan.parameters:
            continue
        socket = group.inputs.get(name)
        if socket is not None and not hasattr(socket.default_value, "__len__"):
            socket.default_value = float(value)

    for name, values in plan.parameters.items():
        socket = group.inputs.get(name)
        if socket is None or not values:
            continue
        try:
            if name in GROUP_SCALAR_INPUTS or not hasattr(socket.default_value, "__len__"):
                socket.default_value = float(values[0])
            else:
                width = len(socket.default_value)
                padded = list(values[:width]) + [1.0] * max(0, width - len(values))
                socket.default_value = padded[:width]
        except (TypeError, ValueError, AttributeError):
            continue


def _slot_table(objects: Sequence[bpy.types.Object]) -> list[tuple[bpy.types.Object, int]]:
    slots: list[tuple[bpy.types.Object, int]] = []
    for candidate in objects:
        data = getattr(candidate, "data", None)
        materials = getattr(data, "materials", None)
        if materials is None:
            continue
        for local_index in range(len(materials)):
            slots.append((candidate, local_index))
    return slots


def _load_image(
    texture: TexturePlan,
    resources: Mapping[str, Path],
    report: Optional[AssemblyReport],
) -> Optional[bpy.types.Image]:
    path = resources.get(texture.path)
    if path is None or not Path(path).is_file():
        if report is not None and texture.path not in report.missing_textures:
            report.missing_textures.append(texture.path)
        return None
    try:
        image = bpy.data.images.load(str(path), check_existing=True)
    except RuntimeError as exc:
        if report is not None:
            report.warnings.append(f"{texture.path}: Blender could not load this texture ({exc})")
        return None
    if image.size[0] < 1 or image.size[1] < 1:
        # Blender keeps a zero-sized datablock for formats it cannot decode.
        # EVE's BC7 payloads (DX10 header, dxgiFormat 98) are the known case;
        # DXT and BC5/ATI2 load natively. Image data itself loads lazily, so
        # size is the only reliable signal here. Build the bundle with
        # tools-core to convert what Blender cannot read.
        if report is not None:
            report.warnings.append(
                f"{texture.path}: Blender cannot decode this texture format; "
                "rebuild the bundle with tools-core to convert it"
            )
        if image.users == 0:
            bpy.data.images.remove(image)
        return None
    image["carbon_sof_resource"] = texture.path
    return image


def _set_colorspace(image: bpy.types.Image, colorspace: str) -> None:
    try:
        image.colorspace_settings.name = colorspace
    except (TypeError, AttributeError):
        pass


def _set_blend_method(material: bpy.types.Material, blend_method: str) -> None:
    """Blender 4.2 replaced ``blend_method`` with render-method properties."""

    if hasattr(material, "blend_method"):
        try:
            material.blend_method = blend_method
            return
        except TypeError:
            pass
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED" if blend_method == "OPAQUE" else "BLENDED"


def area_slot_report(mesh: SofMesh, slot_count: int) -> list[str]:
    """Explains any mismatch between document areas and imported slots."""

    warnings: list[str] = []
    required = mesh.area_slot_count
    if required > slot_count:
        warnings.append(
            f"{mesh.name}: the document routes {required} index groups but the imported "
            f"geometry has {slot_count}; the surplus areas were skipped"
        )
    elif slot_count > required and required:
        warnings.append(
            f"{mesh.name}: {slot_count - required} imported index groups have no SOF area and "
            "keep their placeholder material"
        )
    return warnings


def unique_area_names(areas: Sequence[SofArea]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(area.name for area in areas))
