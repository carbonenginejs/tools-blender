"""Approximate Blender shading for Carbon SOF mesh areas.

EVE's `res:/graphics/effect/...` shaders are compiled Carbon effects. Blender
cannot run them, so a SOF area cannot be reproduced exactly here. This module
decides a deliberate, documented approximation instead: authored textures are
loaded and the small subset with an unambiguous Principled BSDF counterpart is
connected. Every other texture is still created as an unconnected image node,
and all effect parameters, options, and the original effect path are recorded
so nothing the document said is lost.

The module has no ``bpy`` dependency so the mapping itself can be tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .sof_document import SofArea


# Principled BSDF input names, or None when a texture is loaded for reference
# only. Roles are matched case-insensitively against the effect's texture
# parameter names.
TEXTURE_ROLES: Mapping[str, tuple[str, Optional[str], str]] = {
    # parameter: (colorspace, principled input, note)
    "albedomap": ("sRGB", "Base Color", "authored albedo"),
    "roughnessmap": ("Non-Color", "Roughness", "approximate: authored roughness is per-material in Carbon"),
    "normalmap": ("Non-Color", "Normal", "approximate: routed through a Normal Map node"),
    "glowmap": ("sRGB", "Emission Color", "authored glow used as emission"),
    "materialmap": ("Non-Color", None, "material-slot mask; Carbon-only"),
    "paintmaskmap": ("Non-Color", None, "faction paint mask; Carbon-only"),
    "patternmask1map": ("Non-Color", None, "pattern layer 1 mask; Carbon-only"),
    "patternmask2map": ("Non-Color", None, "pattern layer 2 mask; Carbon-only"),
    "dirtmap": ("Non-Color", None, "dirt mask; Carbon-only"),
    "dustnoisemap": ("Non-Color", None, "shared dust noise; Carbon-only"),
    "heatglownoisemap": ("Non-Color", None, "booster heat noise; Carbon-only"),
    "ambientocclusionmap": ("Non-Color", None, "authored occlusion; Carbon-only"),
    "aomap": ("Non-Color", None, "authored occlusion; Carbon-only"),
}

# Batch types that never become their own Blender material: depth areas are
# generated clones of a transparent area, and distortion areas are a screen
# effect with no surface counterpart.
SKIPPED_BATCHES = frozenset({"depth"})

# An authored node group can express Carbon's material model far better than a
# Principled BSDF: the four material slots, their diffuse/fresnel/gloss values,
# and the paint mask that selects between them. When a shader library provides
# a group for an area's effect, SOF textures and constant parameters are wired
# to same-named group inputs and the group drives base color.
SHADER_NODE_GROUPS: Mapping[str, str] = {
    "quadv5.fx": "QuadV5",
    "quadheatv5.fx": "QuadV5",
    "quadglassv5.fx": "QuadV5",
    "skinned_quadv5.fx": "QuadV5",
}

# Group inputs the add-on knows how to drive, beyond the texture parameters.
# Carbon vector parameters are (x, y, z, w); a float input takes x.
GROUP_SCALAR_INPUTS = frozenset({
    "Mtl1Gloss", "Mtl2Gloss", "Mtl3Gloss", "Mtl4Gloss",
    "PaintMaskInfluence", "AgeInWeeks",
})

# Authored groups do not always spell a Carbon parameter the way SOF does, so
# a texture parameter may also be offered under one of these input names.
GROUP_INPUT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "AlbedoMap": ("AlebdoMap", "Albedo", "DiffuseMap"),
    "RoughnessMap": ("Roughness",),
    "NormalMap": ("Normal",),
    "GlowMap": ("Glow",),
    "PaintMaskMap": ("PaintMask",),
    "MaterialMap": ("MaterialMask",),
}


# Group inputs a SOF document never supplies but that must not sit at whatever
# the authored library happens to default to. The paint mask is the faction's
# own colour layer, so it is on unless a build says otherwise.
GROUP_INPUT_DEFAULTS: Mapping[str, float] = {
    "PaintMaskInfluence": 1.0,
}


def group_input_names(parameter: str) -> tuple[str, ...]:
    """Group input names to try for one Carbon texture parameter, in order."""

    return (parameter,) + GROUP_INPUT_ALIASES.get(parameter, ())


@dataclass(frozen=True, slots=True)
class TexturePlan:
    parameter: str
    path: str
    colorspace: str
    principled_input: Optional[str]
    note: str

    @property
    def known(self) -> bool:
        return self.parameter.lower() in TEXTURE_ROLES


@dataclass(frozen=True, slots=True)
class MaterialPlan:
    """One approximate Blender material derived from one SOF area."""

    name: str
    area_name: str
    batch: str
    shader: str
    effect_path: str
    blend_method: str
    use_alpha: bool
    emission_strength: float
    textures: tuple[TexturePlan, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    node_group: str = ""
    parameters: Mapping[str, tuple[float, ...]] = field(default_factory=dict)

    def texture(self, parameter: str) -> Optional[TexturePlan]:
        wanted = parameter.lower()
        return next((item for item in self.textures if item.parameter.lower() == wanted), None)


def plan_material(area: SofArea, *, prefix: str = "") -> MaterialPlan:
    """Chooses the approximate Blender material for one SOF mesh area."""

    textures = tuple(
        _plan_texture(parameter, path)
        for parameter, path in sorted(area.textures.items())
        if path
    )
    blend_method, use_alpha, emission_strength, notes = _surface_for_batch(area)
    name = f"{prefix}{area.name}" if prefix else area.name
    node_group = SHADER_NODE_GROUPS.get(area.shader, "")
    if node_group:
        notes = notes + (
            f"Carbon material values are driven through the {node_group} node group "
            "when a shader library provides it",
        )
    return MaterialPlan(
        name=name,
        area_name=area.name,
        batch=area.batch,
        shader=area.shader,
        effect_path=area.effect_path,
        blend_method=blend_method,
        use_alpha=use_alpha,
        emission_strength=emission_strength,
        textures=textures,
        metadata=_metadata(area),
        notes=notes,
        node_group=node_group,
        parameters=dict(area.parameters),
    )


def should_build_material(area: SofArea) -> bool:
    """Depth clones repeat an area that already produced a material."""

    return area.batch not in SKIPPED_BATCHES


def _plan_texture(parameter: str, path: str) -> TexturePlan:
    colorspace, principled_input, note = TEXTURE_ROLES.get(
        parameter.lower(),
        ("Non-Color", None, "unmapped Carbon texture parameter"),
    )
    return TexturePlan(
        parameter=parameter,
        path=path,
        colorspace=colorspace,
        principled_input=principled_input,
        note=note,
    )


def _surface_for_batch(area: SofArea) -> tuple[str, bool, float, tuple[str, ...]]:
    notes = [
        f"Carbon effect {area.effect_path or 'unknown'} is approximated by a Principled BSDF; "
        "shader behavior is not reproduced",
    ]
    if area.batch == "transparent":
        return "BLEND", True, 1.0, tuple(notes)
    if area.batch == "additive":
        notes.append("additive areas are approximated with blended emission")
        return "BLEND", True, 1.0, tuple(notes)
    if area.batch == "distortion":
        notes.append("distortion areas have no Blender counterpart and stay unshaded")
        return "BLEND", True, 0.0, tuple(notes)
    if area.options.get("SPACE_OBJECT_TRANSPARENCY") == "SOT_CLIP":
        notes.append("decal area uses alpha clipping")
        return "CLIP", True, 1.0, tuple(notes)
    return "OPAQUE", False, 1.0, tuple(notes)


def _metadata(area: SofArea) -> Mapping[str, str]:
    values: dict[str, str] = {
        "carbon_sof_area": area.name,
        "carbon_sof_batch": area.batch,
        "carbon_sof_effect": area.effect_path,
        "carbon_sof_index": str(area.index),
        "carbon_sof_count": str(area.count),
        "carbon_sof_casts_shadows": "1" if area.casts_shadows else "0",
    }
    for name, value in area.options.items():
        values[f"carbon_sof_option_{name}"] = value
    for name, numbers in area.parameters.items():
        values[f"carbon_sof_parameter_{name}"] = ", ".join(f"{number:g}" for number in numbers)
    for name, path in area.textures.items():
        values[f"carbon_sof_texture_{name}"] = path
    return values
