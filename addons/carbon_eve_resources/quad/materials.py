"""Building one Blender material for one SOF mesh area, the accurate way.

This is the QUAD path: the measured node groups, the projections, the heat
shimmer, Carbon's own constants. It lives in the add-on rather than in a script
because there must be ONE answer to "what does this material look like".

There used to be two. The add-on's panel built an approximate material -- a
texture straight into Principled -- while the accurate shading lived in a
preview script the panel could not reach. Two ships in one scene then looked
like different games, which is exactly what a consumer reported.

`area` is a plain mapping from the expanded SOF document: `effect` with its
`resources` and `constParameters`. Anything holding a document in that shape can
call this.
"""

from __future__ import annotations

import os

import bpy

from . import nodes
from ..core import resfile



def load_texture(path, *, name="", logical_path="", check_existing=True):
    """A texture image, decoding BC7 when Blender cannot read it.

    Blender loads DXT1, DXT5, BC4 and BC5 natively and answers BC7 with a 0x0
    image and no data -- and BC7 is what EVE's albedo maps are.
    """

    from pathlib import Path

    from ..dds import load_image, is_bc7
    from ..dds.reader import is_volume

    text = str(path)
    # Sniffed, not judged by extension. Fetched files are cached under their
    # content HASH with no extension at all, so an extension test sent every
    # one of them to Blender, which answered BC7 with an empty image.
    try:
        with open(text, "rb") as handle:
            head = handle.read(148)
    except OSError:
        head = b""

    if head[:4] == b"DDS ":
        if is_volume(head):
            # A 3D texture is a stack of slices. Blender does not load one as
            # an image; it hangs trying. Skipped with a note rather than
            # taking the session down.
            print(f"[CarbonEngineJS SOF] {text}: 3D volume texture, not loaded")
            return None
        if is_bc7(head):
            try:
                decoded = load_image(text)
            except Exception as exc:
                print(f"[CarbonEngineJS SOF] {text}: {exc}")
                decoded = None
            if decoded is not None:
                return rename(decoded, name, logical_path)
    image = bpy.data.images.load(text, check_existing=check_existing)
    return rename(image, name, logical_path)


def rename(image, name: str, logical_path: str = ""):
    """Gives an image the name the artist knows it by.

    Blender names an image after the file it loaded, and our files are named
    by their storage address -- so every texture in the shader editor read as
    thirty-two hex digits. The datablock is renamed; the FILE is not, because
    the file's name is its identity in the cache.

    The logical path is kept on the image as well. The NAME is what a person
    reads, but exporting needs the folders it belongs in, and the name alone
    cannot say whether "ab1_t1_a" came from the amarr tree or somewhere else.
    """

    if image is None:
        return image
    if name and not image.name.startswith(name):
        image.name = name
    if logical_path:
        image["carbon_res_path"] = str(logical_path)
    return image


def local_file(resources, path):
    """The on-disk file for a `res:/` path, as a STRING, or None.

    Callers disagree about the value type: the preview script's map holds
    strings, the add-on's holds `Path`. `bpy.data.images.load` takes only a
    string, so the coercion belongs here rather than in each caller -- a Path
    reaching it raises a TypeError deep inside the builder, which reads as the
    material being broken rather than as a map holding the wrong type.
    """

    local = resources.get(path)
    if not local:
        return None
    text = str(local)
    return text if os.path.exists(text) else None


#: The shared 1x1 black image's name, so there is one of it per file.
#:
#: It was referenced and never defined, which nothing noticed because a hull
#: area binds every texture its member declares. A TURRET does not -- its
#: effect carries a handful -- so the first turret fitted raised NameError out
#: of the job thread and reported "name 'BLACK_IMAGE' is not defined" as the
#: whole of its failure.
BLACK_IMAGE = "CarbonShader black"


def black_image():
    """A shared 1x1 black image, for texture slots with nothing authored.

    A SOF document with no SKIN resolves its pattern masks to
    `res:/texture/global/black.dds`, so black is EVE's own neutral rather than a
    stand-in: a mask of zero covers nothing. Generated rather than downloaded so
    the preview works offline.
    """

    existing = bpy.data.images.get(BLACK_IMAGE)
    if existing:
        return existing
    image = bpy.data.images.new(BLACK_IMAGE, width=1, height=1, alpha=True)
    image.generated_color = (0.0, 0.0, 0.0, 1.0)
    image.pixels = [0.0, 0.0, 0.0, 1.0]
    image.colorspace_settings.name = "Non-Color"
    return image


def fill_unbound_textures(member, group, mnodes, mlinks, row):
    """Gives every unfilled texture slot a labelled black image node.

    The socket defaults are already black, so this changes nothing visually. It
    makes the material self-describing instead: every map the shader binds is
    present as a node a user can point at a file, which is the same reason the
    add-on already creates unconnected nodes for Carbon-only maps.

    Colourspace comes from Carbon's `Tr2sRGB` annotation, so the pattern masks
    -- which do not carry it -- land as Non-Color, as do all the other masks.
    """

    filled = []
    for texture in member.textures:
        socket = group.inputs.get(texture)
        if socket is None or socket.is_linked:
            continue
        node = mnodes.new("ShaderNodeTexImage")
        node.image = black_image()
        node.location = (-600, row)
        node.label = texture
        # Per-node, so pointing this at a real file keeps the right space.
        node.image.colorspace_settings.name = (
            "sRGB" if member.annotation(texture).srgb else "Non-Color"
        )
        mlinks.new(node.outputs["Color"], socket)
        filled.append(texture)
        row -= 300
    if filled:
        print(f"  black (Non-Color) for unauthored: {', '.join(filled)}")


def ensure_projection(mnodes):
    """One projection-group node per material, reusing the shared group."""

    for node in mnodes:
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree                 and node.node_tree.name == nodes.PROJECTION_GROUP:
            return node
    tree = bpy.data.node_groups.get(nodes.PROJECTION_GROUP) or nodes.build_projection_group()
    node = mnodes.new("ShaderNodeGroup")
    node.node_tree = tree
    node.location = (-1400, 400)
    return node


def wire_heat_shimmer(member, effect, group, mnodes, mlinks, resources):
    """Displaces the glow lookup by the heat shimmer, for heat members.

    The chain has to run through the material because each step needs a texture
    sampled between groups: noise UVs, sample the noise twice, work out the
    displacement, then sample the GLOW map at the displaced coordinate. A group
    cannot feed a texture that feeds itself back.

    Heat scales the glow map rather than adding a texture of its own, so this
    replaces the glow the quad group would otherwise sample at a plain UV.
    """

    if "HeatGlowNoiseMap" not in member.textures or "GlowMap" not in group.inputs:
        return

    noise_path = effect.get("resources") or []
    noise = next((r.get("resourcePath") for r in noise_path
                  if r.get("name") == "HeatGlowNoiseMap"), None)
    glow = next((r.get("resourcePath") for r in noise_path
                 if r.get("name") == "GlowMap"), None)
    noise_local = local_file(resources, noise or "")
    glow_local = local_file(resources, glow or "")
    if noise_local is None or glow_local is None:
        return

    lanes = {name: value for name, value in member.defaults().items() if "HeatGlowData" in name}
    for constant in effect.get("constParameters", []):
        name = str(constant.get("name", ""))
        if "HeatGlowData" in name:
            lanes[name] = tuple(constant.get("value") or (0.0, 0.0, 1.0, 0.0))

    material_map = next((n for n in mnodes
                         if n.bl_idname == "ShaderNodeTexImage" and n.label == "MaterialMap"), None)
    if material_map is None:
        return

    separate = mnodes.new("ShaderNodeSeparateColor")
    separate.location = (-1500, 600)
    mlinks.new(material_map.outputs["Color"], separate.inputs[0])

    uv_group = mnodes.new("ShaderNodeGroup")
    uv_group.node_tree = nodes.build_heat_uv_group(authored_uv=member.target == "frontier")
    uv_group.location = (-1300, 500)
    mlinks.new(separate.outputs["Red"], uv_group.inputs["MaterialMap"])

    displace = mnodes.new("ShaderNodeGroup")
    displace.node_tree = nodes.build_heat_displace_group(authored_uv=member.target == "frontier")
    displace.location = (-700, 500)
    mlinks.new(separate.outputs["Red"], displace.inputs["MaterialMap"])

    # Carbon's own component names, so the lanes land where they belong.
    for layer in range(1, 5):
        value = lanes.get(f"Mtl{layer}HeatGlowData")
        if not value:
            continue
        for socket, index in (("Shimmer speed", 1), ("Shimmer size", 2)):
            key = f"Mtl{layer}HeatGlow {socket}"
            if key in uv_group.inputs:
                uv_group.inputs[key].default_value = float(value[index])
        for socket, index in (("Shimmer strength", 3), ("boosterGain influence", 0)):
            key = f"Mtl{layer}HeatGlow {socket}"
            if key in displace.inputs:
                displace.inputs[key].default_value = float(value[index])

    noise_image = rename(bpy.data.images.load(noise_local, check_existing=True),
                         resfile.display_name(noise or ""), noise or "")
    noise_image.colorspace_settings.name = "Non-Color"
    for index in (1, 2):
        node = mnodes.new("ShaderNodeTexImage")
        node.image = noise_image
        node.location = (-1000, 700 - index * 260)
        node.label = f"HeatGlowNoiseMap {index}"
        mlinks.new(uv_group.outputs[f"Noise UV {index}"], node.inputs["Vector"])
        mlinks.new(node.outputs["Color"], displace.inputs[f"Noise {index}"])

    glow_node = next((n for n in mnodes
                      if n.bl_idname == "ShaderNodeTexImage" and n.label == "GlowMap"), None)
    if glow_node is None:
        return
    mlinks.new(displace.outputs["Glow UV"], glow_node.inputs["Vector"])
    print("  heat shimmer wired (glow sampled at a displaced UV)")


def build_heat_area_material(area, member, resources, index, context, *, base_material=None):
    """Thermal emission with explicit scene inputs and no bound volume texture.

    A coincident second surface loses the opaque hit in Cycles. When heat
    redraws an existing area, add its emission to a copy of that surface's
    closure instead. This is Blender's adaptation of Carbon's ONE/ONE draw;
    the original material and its SOF/editor identity remain intact.
    """
    from . import frontier
    effect = area.get("effect") or {}
    if context is None:
        return None, "Heat emission requires ship radius and thermal sun direction"
    if any(r.get("name") == "NoiseMap" and r.get("resourcePath") for r in effect.get("resources", [])):
        return None, "Bound heat NoiseMap requires a qualified 3D volume sampler"
    resource = next((r for r in effect.get("resources", []) if r.get("name") == "GradientMap"), None)
    samples = ((0, 0, 0, 0),)
    if resource and resource.get("resourcePath"):
        local = local_file(resources, resource["resourcePath"])
        image = load_texture(local, logical_path=resource["resourcePath"]) if local else None
        if image is None:
            return None, "Heat GradientMap is unavailable"
        if image.colorspace_settings.name != "Non-Color":
            image = image.copy()
            image.colorspace_settings.name = "Non-Color"
        samples = frontier.mip0_first_row(image, srgb=True)
    tree = frontier.build_heat_group(member, samples)
    material = (base_material.copy() if base_material else
                bpy.data.materials.new(f"{index:02d} {area.get('name') or member.name}"))
    material.use_nodes = True
    if base_material is None:
        material.node_tree.nodes.clear()
    else:
        material.name = f"{base_material.name} + {area.get('name') or 'Heat'}"
    group = material.node_tree.nodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.label = "Frontier thermal emission"
    if base_material is None:
        output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
        material.node_tree.links.new(group.outputs["BSDF"], output.inputs["Surface"])
    else:
        outputs = [n for n in material.node_tree.nodes
                   if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output]
        if len(outputs) != 1 or not outputs[0].inputs["Surface"].is_linked:
            bpy.data.materials.remove(material)
            return None, "Heat overlay requires one active base surface output"
        output = outputs[0]
        base_surface = output.inputs["Surface"].links[0].from_socket
        emission = material.node_tree.nodes.new("ShaderNodeEmission")
        material.node_tree.links.new(group.outputs["Emission"], emission.inputs["Color"])
        addition = material.node_tree.nodes.new("ShaderNodeAddShader")
        material.node_tree.links.new(base_surface, addition.inputs[0])
        material.node_tree.links.new(emission.outputs[0], addition.inputs[1])
        material.node_tree.links.new(addition.outputs[0], output.inputs["Surface"])
    for constant in effect.get("constParameters", []):
        name, value = constant.get("name"), constant.get("value") or []
        if name not in member.constants or not value:
            continue
        for lane, (label, kind, _) in enumerate(frontier.constant_sockets(member, name)):
            if kind == "NodeSocketColor":
                group.inputs[label].default_value = tuple(value[:3]) + (1.0,)
            elif lane < len(value):
                group.inputs[label].default_value = float(value[lane])
    try:
        frontier.wire_heat_inputs(member, effect, material, group, resources,
                                 radius=context["radius"], sun_direction=context["sun_direction"])
    except ValueError as error:
        bpy.data.materials.remove(material)
        return None, str(error)
    if base_material is None:
        material["carbon_source"] = member.target
        material["carbon_effect_path"] = effect.get("effectFilePath", "")
        material["carbon_effect_identity"] = member.identity
    else:
        material["carbon_heat_effect_path"] = effect.get("effectFilePath", "")
        material["carbon_heat_effect_identity"] = member.identity
        material.surface_render_method = base_material.surface_render_method
    material["carbon_heat_context"] = "Growth and thermal sun direction are editable material inputs; status controllers are not imported"
    return material, None


def build_distortion_area_material(area, member, shader, index):
    """A surface that draws nothing, because Carbon's pass adds no colour.

    EVE's fxdistortionv5 Main pass (TQ 3542233 depth, PS0-35) writes only a
    screen offset, a constant 1/256 and `DistortionFactors.y` into the
    distortion buffer; a post-process then bends the frame behind the area by
    it. Without a member the area kept the importer's opaque white, which on a
    Nestor is the whole disc inside the ring. Transparent is what the colour
    pass sees; the bending is not reproduced.
    """

    material = bpy.data.materials.new(f"{index:02d} {area.get('name') or member.name}")
    material["carbon_source"] = member.target
    material["carbon_effect_path"] = shader
    material["carbon_effect_identity"] = member.identity
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    else:
        material.blend_method = "BLEND"
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (200, 0)
    transparent = mnodes.new("ShaderNodeBsdfTransparent")
    mlinks.new(transparent.outputs[0], output.inputs["Surface"])
    return material


def build_area_material(area, family, resources, index, *, heat_context=None):
    """One material for one mesh area, from its own effect."""

    effect = area.get("effect") or {}
    shader = str(effect.get("effectFilePath", ""))
    member = family.member(shader, effect.get("options"))
    if member is None:
        return None, f"{area.get('name')}: no measured member for {shader.rsplit('/', 1)[-1]}"

    if member.name == "fxdistortionv5":
        return build_distortion_area_material(area, member, shader, index), None

    if member.name == "fxv5":
        # EVE's shader; Frontier's fxv5 is an inherited copy. Both targets'
        # members build the one graph in `fx`.
        from . import fx
        tree = fx.build_group(member)
    elif member.target == "frontier":
        from . import frontier
        if member.name == "fxheatv5":
            return build_heat_area_material(area, member, resources, index, heat_context)
        if member.name not in frontier.SUPPORTED:
            return None, f"{area.get('name')}: Frontier {member.name} surface is not yet qualified"
        lookup_samples = None
        if member.name == "asteroid":
            resource = next((r for r in effect.get("resources", []) if r.get("name") == "MaterialLookupGradient"), None)
            local = local_file(resources, resource.get("resourcePath")) if resource else None
            if local:
                image = load_texture(local, name=resfile.display_name(resource["resourcePath"]),
                                     logical_path=resource["resourcePath"])
                if image is not None and image.size[0] and image.size[1]:
                    if image.colorspace_settings.name != "Non-Color" and image.users:
                        image = image.copy()
                    if image.colorspace_settings.name != "Non-Color":
                        image.colorspace_settings.name = "Non-Color"
                    lookup_samples = tuple(texel[0] for texel in frontier.mip0_first_row(image))
            if lookup_samples is None:
                print(f"  ! {area.get('name')}: MaterialLookupGradient unavailable; using the unbound black lookup")
        tree = frontier.build_group(member, lookup_samples=lookup_samples)
    else:
        tree = nodes.build_group(member)
    material = bpy.data.materials.new(f"{index:02d} {area.get('name') or member.name}")
    material["carbon_source"] = member.target
    material["carbon_effect_path"] = shader
    material["carbon_effect_identity"] = member.identity
    if member.name == "fxv5":
        material["carbon_fx_vertex_view"] = True
        # Eevee's dithered path discards the fully transparent closure and
        # loses its additive emission. The blended path preserves ONE+ONE.
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "BLENDED"
        else:
            material.blend_method = "BLEND"
    if member.target == "frontier" and "skinned_" in shader.rsplit("/", 1)[-1].lower():
        # Blender's Armature modifier leaves imported generic vector
        # attributes in the rest pose; pixel-bytecode aliases do not prove
        # equivalent animated vertex frames.
        from .frontier import RIGID_VERTEX_SHA256
        material["carbon_normal_limit"] = "Authored tangent frame remains in the rest pose"
        alias = member.aliases.get(shader.lower(), {})
        material["carbon_rigid_frame_required"] = alias.get("vertexSha256") == RIGID_VERTEX_SHA256
    elif member.name == "fxv5" and "skinned_" in shader.rsplit("/", 1)[-1].lower():
        # The fx graph reads the imported normal attribute, which the Armature
        # modifier leaves in the rest pose.
        material["carbon_normal_limit"] = "Authored tangent frame remains in the rest pose"
    material.use_nodes = True
    mnodes, mlinks = material.node_tree.nodes, material.node_tree.links
    mnodes.clear()
    output = mnodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    group = mnodes.new("ShaderNodeGroup")
    group.node_tree = tree
    group.location = (0, 0)
    mlinks.new(group.outputs["BSDF"], output.inputs["Surface"])

    row = 900
    for resource in effect.get("resources", []):
        name, path = resource.get("name"), resource.get("resourcePath")
        socket = group.inputs.get(name)
        local = local_file(resources, path)
        if socket is None or local is None:
            continue
        image = load_texture(local, name=resfile.display_name(path),
                             logical_path=path)
        if image is None:
            continue          # a 3D volume texture, or unreadable
        color_space = "sRGB" if member.annotation(name).srgb else "Non-Color"
        # Colorspace belongs to the Blender image datablock, not the texture
        # node. Shared EVE/Frontier bytes may have different interpretations.
        if image.colorspace_settings.name != color_space and image.users:
            image = next((other for other in bpy.data.images
                if other.filepath == image.filepath and other.colorspace_settings.name == color_space), None) or image.copy()
        # Even assigning the existing colorspace can discard edited/generated
        # pixel data in Blender. Only change interpretation when necessary.
        if image.colorspace_settings.name != color_space:
            image.colorspace_settings.name = color_space
        node = mnodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-700, row)
        node.label = name
        mlinks.new(node.outputs["Color"], socket)

        # Pattern masks are sampled with projected coordinates from the shared
        # projection group, and the per-axis wrapping is done there, so the
        # image node must not wrap on its own.
        # The sails detail texture is looked up with a scaled and rotated UV0,
        # not a projection, so it gets its own small transform group fed from
        # this area's own SailsDetailData. Two areas of one hull share the
        # texture and differ only in the rotation.
        if name == "SailsDetailMap":
            node.extension = "REPEAT"
            data = next((c.get("value") for c in effect.get("constParameters", [])
                         if c.get("name") == "SailsDetailData"), member.defaults().get("SailsDetailData"))
            sails = mnodes.new("ShaderNodeGroup")
            sails.node_tree = nodes.build_sails_group(authored_uv=member.target == "frontier")
            sails.location = (-1000, row)
            if data:
                sails.inputs["Tiling"].default_value = float(data[0])
                sails.inputs["Rotation"].default_value = float(data[1])
                print(f"  sails uv: tiling {data[0]:g}, rotation {data[1]:g} rad")
            mlinks.new(sails.outputs["UV"], node.inputs["Vector"])

        pattern_index = {"PatternMask1Map": 1, "PatternMask2Map": 2}.get(name)
        if pattern_index is not None:
            node.extension = "EXTEND"
            projection = ensure_projection(mnodes)
            mlinks.new(projection.outputs[f"UV {pattern_index}"], node.inputs["Vector"])
            coverage = group.inputs.get(f"Pattern{pattern_index}Coverage")
            if coverage is not None:
                mlinks.new(projection.outputs[f"Coverage {pattern_index}"], coverage)

        scale = member.annotation(name).uv_scale
        if scale != 1.0:
            coord = mnodes.new("ShaderNodeTexCoord")
            coord.location = (-1200, row)
            mapping = mnodes.new("ShaderNodeMapping")
            mapping.location = (-1000, row)
            mapping.inputs["Scale"].default_value = (scale, scale, scale)
            mlinks.new(coord.outputs["UV"], mapping.inputs["Vector"])
            mlinks.new(mapping.outputs["Vector"], node.inputs["Vector"])
        if name == "DustNoiseMap" and nodes.DUST_ALPHA in group.inputs:
            mlinks.new(node.outputs["Alpha"], group.inputs[nodes.DUST_ALPHA])
        if member.target == "frontier" and name in ("Detail1Map", "Detail2Map"):
            alpha = group.inputs.get(name.replace("Map", "Alpha"))
            if alpha is not None:
                mlinks.new(node.outputs["Alpha"], alpha)
        if member.name == "fxv5":
            mlinks.new(node.outputs["Alpha"], group.inputs[name + "Alpha"])
        row -= 300

    if member.target != "frontier":
        wire_heat_shimmer(member, effect, group, mnodes, mlinks, resources)
    fill_unbound_textures(member, group, mnodes, mlinks, row)
    if member.name == "fxv5":
        from .fx import wire_coordinates
        wire_coordinates(member, effect, material)
    elif member.target == "frontier":
        from .frontier import wire_coordinates
        wire_coordinates(member, effect, material)
        wire_heat_shimmer(member, effect, group, mnodes, mlinks, resources)

    for constant in effect.get("constParameters", []):
        name, value = constant.get("name"), constant.get("value") or []
        if member.target == "frontier" or member.name == "fxv5":
            if name not in member.constants or not value:
                continue
            if member.name == "fxv5" and name == "BaseColor" and len(value) > 3:
                group.inputs["BaseColorAlpha"].default_value = float(value[3])
            from .graph import constant_sockets
            for lane, (label, kind, _) in enumerate(constant_sockets(member, name)):
                socket = group.inputs.get(label)
                if socket is not None:
                    socket.default_value = (tuple(value[:3]) + (1.0,) if kind == "NodeSocketColor"
                                            else float(value[lane]) if lane < len(value) else socket.default_value)
            continue
        annotation = member.annotation(name)
        lanes = [] if nodes.socket_name(name) != name else annotation.components()
        if len(lanes) > 1 and not annotation.is_color:
            for lane, label in enumerate(lanes):
                socket = group.inputs.get(f"{name.replace('Data', '')} {label}")
                if socket is not None and lane < len(value):
                    socket.default_value = float(value[lane])
            continue
        socket = group.inputs.get(nodes.socket_name(name))
        if socket is None or not value:
            continue
        if socket.type == "RGBA":
            socket.default_value = tuple(value[:3]) + (1.0,)
        else:
            socket.default_value = float(value[0])

    return material, None
