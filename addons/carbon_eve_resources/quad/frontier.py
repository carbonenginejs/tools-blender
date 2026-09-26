"""Frontier surface arithmetic, qualified against non-SOPPT containers.

Blender supplies lighting. These graphs only produce material inputs. The
compiled interface, exact defaults and named options live in frontier-family.json.
"""
import hashlib

import bpy

from . import nodes
from .graph import Graph as _Graph, constant_sockets


VERSION = 1
# Frontier 3512930 depth VS: one palette index, affine position and raw TBN.
# Use measured bytecode identity, not the 'skinned_' filename. In particular
# skinned_quadheatv5 has a static VS while unpackedskinned_quadheatv5 is rigid.
RIGID_VERTEX_SHA256 = "0af988abdfcd7d16c6a028563fecabf57b32286adf8bede905362babfcbebe8d"
SUPPORTED = {"turret", "simplepbr", "standardpbr", "ship", "structure", "quadv5", "quaddetailv5", "quadheatv5",
             "quadenvironmentv5", "quadsailsv5", "asteroidv5", "quadtriplanarv5", "asteroid"}


def mip0_first_row(image, *, srgb=False):
    """Read source V=0 RGBA texels from a Non-Color Blender image.

    DDS and Blender rows have opposite origins. Decode sRGB before filtering,
    as the GPU sampler does; alpha remains linear. The caller selects a
    Non-Color image so pixel-buffer color interpretation is unambiguous.
    """
    if image.colorspace_settings.name != "Non-Color":
        raise ValueError("Explicit mip-zero lookup requires a Non-Color source image")
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("Explicit mip-zero lookup requires image texels")
    start = (height - 1) * width * 4
    pixels = image.pixels[start:start + width * 4]
    def linear(value):
        return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
    return tuple(tuple(linear(pixels[index + lane]) if srgb and lane < 3 else pixels[index + lane]
                       for lane in range(4)) for index in range(0, len(pixels), 4))


class Graph(_Graph):
    """Frontier's surfaces over the shared node helpers."""

    def decoded_normal(self, texture="NormalMap", pbr=True):
        r, g, _ = self.split(self.tex(texture))
        if pbr:
            x = self.math("SUBTRACT", self.math("MULTIPLY", r, 2.007874011993408), 1)
            y = self.math("SUBTRACT", self.math("MULTIPLY", g, 2.007874011993408), 1)
            zz = self.math("SUBTRACT", 1, self.math("ADD", self.math("MULTIPLY", x, x), self.math("MULTIPLY", y, y)))
            z = self.math("SQRT", self.math("MAXIMUM", zz, 0))
        else:
            x = self.math("SUBTRACT", self.math("MULTIPLY", self.math("ADD", r, .002), 2), 1)
            y = self.math("SUBTRACT", self.math("MULTIPLY", self.math("ADD", g, .002), 2), 1)
            z = 1
        return self.combine(x, y, z)

    def normal(self, texture="NormalMap", pbr=True):
        decoded = self.decoded_normal(texture, pbr)
        if self.member.name in ("turret", "ship", "simplepbr", "standardpbr"):
            return self.vector("NORMALIZE", self.frame_vector(self.frame(0), decoded))
        return self.tangent_normal(decoded)

    def weights(self, selector):
        return [self.sat(self.math("SUBTRACT", 1.0319149494,
                    self.math("ABSOLUTE", self.math("MULTIPLY",
                        self.math("SUBTRACT", selector, center), 3.1914894581))))
                for center in (0, .3333333433, .6666666865, 1)]

    def weighted(self, weights, values, vector=False):
        result = (0, 0, 0) if vector else 0
        for weight, value in zip(weights, values):
            result = (self.vector("ADD", result, self.vector("SCALE", value, weight)) if vector
                      else self.math("ADD", result, self.math("MULTIPLY", value, weight)))
        return result

    def channel(self, value, selector, width=3, alpha=None):
        lanes = self.split(value)
        chosen = self.math("MODULO", self.math("TRUNC", selector), width)
        result = 0
        for index in range(width):
            lane = lanes[index] if index < 3 else alpha
            factor = self.math("COMPARE", chosen, index, .1)
            result = self.math("ADD", result, self.math("MULTIPLY", lane, factor))
        return result

    def packed_material(self, roughness, metallic):
        """Depth asteroid's finite PBR pair: half pack, uint->float32->uint.

        VS86-89/PS162-170, Frontier 3512930. Shader-node arithmetic is float32;
        the large packed ADD deliberately retains its additional rounding.
        Keep this in the graph so editing a material updates the packed pair.
        """
        def half_bits(value):
            magnitude = self.math("ABSOLUTE", value)
            exponent = self.math("MAXIMUM", self.math("FLOOR", self.math("LOGARITHM",
                self.math("MAXIMUM", magnitude, 2 ** -24), 2)), -14)
            quantum = self.math("POWER", 2, self.math("SUBTRACT", exponent, 10))
            mantissa = self.math("DIVIDE", magnitude, quantum)
            lower = self.math("FLOOR", mantissa)
            fraction = self.math("SUBTRACT", mantissa, lower)
            tie = self.math("MULTIPLY", self.math("COMPARE", fraction, .5, 0), self.math("MODULO", lower, 2))
            rounded = self.math("ADD", lower, self.math("ADD", self.math("GREATER_THAN", fraction, .5), tie))
            bits = self.math("ADD", self.math("MULTIPLY", self.math("ADD", exponent, 14), 1024), rounded)
            return self.math("ADD", bits, self.math("MULTIPLY", self.math("LESS_THAN", value, 0), 32768))

        def from_half(bits):
            sign = self.math("SUBTRACT", 1, self.math("MULTIPLY", self.math("FLOOR", self.math("DIVIDE", bits, 32768)), 2))
            magnitude = self.math("MODULO", bits, 32768)
            exponent = self.math("FLOOR", self.math("DIVIDE", magnitude, 1024))
            mantissa = self.math("DIVIDE", self.math("MODULO", magnitude, 1024), 1024)
            mantissa = self.math("ADD", mantissa, self.math("GREATER_THAN", exponent, 0))
            scale = self.math("POWER", 2, self.math("MAXIMUM", self.math("SUBTRACT", exponent, 15), -14))
            return self.math("MULTIPLY", sign, self.math("MULTIPLY", mantissa, scale))

        packed = self.math("ADD", half_bits(roughness), self.math("MULTIPLY", half_bits(metallic), 65536))
        low = self.math("MODULO", packed, 65536)
        high = self.math("FLOOR", self.math("DIVIDE", packed, 65536))
        return from_half(low), from_half(high)

    def lookup_mip0(self, samples, selector):
        """Explicit clamped linear sampling of a mip-zero red texel row.

        A Blender image node chooses mip level implicitly. A curve node also
        resamples its control points into a smaller table. Neither preserves
        the asteroid's explicit sample_l(..., 0) lookup. Select the original
        texel interval, then interpolate its original endpoint values instead.
        """
        if not samples:
            raise ValueError("Material lookup has no texels")
        width = len(samples)
        segments = [(0.5 / width, (samples[0], samples[0], 0.0))]
        for index, (a, b) in enumerate(zip(samples, samples[1:])):
            segments.append(((index + 1.5) / width, (a, b, float(index) if a != b else 0.0)))
        segments.append((float("inf"), (samples[-1], samples[-1], 0.0)))
        compact = []
        for upper, coefficients in segments:
            if compact and compact[-1][1] == coefficients:
                compact[-1] = (upper, coefficients)
            else:
                compact.append((upper, coefficients))

        def choose(entries, graph=self, coordinate=selector, partition=True):
            if len(entries) == 1:
                return entries[0][1]
            if partition and len(compact) > 32 and len(entries) <= 32:
                # Blender maintains tree invariants after each node/link edit.
                # Bound each tree's size and reuse byte-identical row segments
                # across material areas instead of rebuilding thousands of
                # nodes in one tree.
                digest = hashlib.sha256(repr(entries).encode("ascii")).hexdigest()
                name = "Carbon Mip0 " + digest
                tree = bpy.data.node_groups.get(name)
                if tree is None:
                    tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
                    tree.interface.new_socket(name="Selector", in_out="INPUT", socket_type="NodeSocketFloat")
                    tree.interface.new_socket(name="Texels", in_out="OUTPUT", socket_type="NodeSocketVector")
                    branch = Graph(tree, self.member)
                    branch.bind(choose(entries, branch, branch.input.outputs["Selector"], False),
                                branch.output.inputs["Texels"])
                node = graph.nodes.new("ShaderNodeGroup")
                node.node_tree = tree
                graph.bind(coordinate, node.inputs["Selector"])
                return node.outputs["Texels"]
            middle = len(entries) // 2
            left = graph.math("LESS_THAN", coordinate, entries[middle - 1][0])
            # VECTOR/UNIFORM Mix uses separately weighted endpoints in both
            # Eevee and Cycles SVM. Unlike A+(B-A)*factor, boolean selection
            # retains the endpoint without subtract/add cancellation.
            node = graph.nodes.new("ShaderNodeMix")
            node.data_type = "VECTOR"
            node.factor_mode = "UNIFORM"
            graph.bind(left, node.inputs[0])
            graph.bind(choose(entries[middle:], graph, coordinate, partition), node.inputs[4])
            graph.bind(choose(entries[:middle], graph, coordinate, partition), node.inputs[5])
            return node.outputs[1]

        a, b, index = self.split(choose(compact))
        fraction = self.sat(self.math("SUBTRACT", self.math("SUBTRACT",
            self.math("MULTIPLY", selector, width), .5), index))
        # Interpolate locally; global slope/intercept loses precision near
        # the far end of a high-frequency lookup texture.
        return self.math("ADD", self.math("MULTIPLY", a, self.math("SUBTRACT", 1, fraction)),
                         self.math("MULTIPLY", b, fraction))

    def hue(self, value, turns, desaturation):
        rotate = self.nodes.new("ShaderNodeVectorRotate")
        rotate.rotation_type = "AXIS_ANGLE"
        rotate.inputs["Axis"].default_value = (1, 1, 1)
        self.bind(value, rotate.inputs["Vector"])
        self.bind(self.math("MULTIPLY", turns, 6.283185307179586), rotate.inputs["Angle"])
        color = self.satv(rotate.outputs[0])
        grey = self.vector("DOT_PRODUCT", color, (.299, .587, .114))
        return self.mixv(color, self.combine(grey, grey, grey), desaturation)

    def asteroid(self, lookup_samples):
        # Frontier 3512930 asteroid.sm_depth, non-instanced, debug/clipping off.
        # Carbon lighting is replaced by Blender; authored surface arithmetic
        # and the VS secondary-material packing remain explicit.
        vertex = self.nodes.new("ShaderNodeVertexColor")
        vertex.layer_name = "Color0"
        red, green, blue = self.split(vertex.outputs["Color"])

        def remap(value, lower, upper):
            return self.sat(self.math("DIVIDE", self.math("SUBTRACT", value, self.c(lower)),
                                     self.math("SUBTRACT", self.c(upper), self.c(lower))))

        def shaped(value, power, multiplier):
            return self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", value), self.c(power)), self.c(multiplier))

        curvature = self.mix(red, self.math("SUBTRACT", 1, red), self.math("TRUNC", self.sat(self.c("CurvatureFlip"))))
        blend1 = remap(curvature, "BlendMask1LowerLimit", "BlendMask1UpperLimit")
        thickness = remap(green, "ThicknessLowerLimit", "ThicknessUpperLimit")
        blend2 = self.mix(self.sat(self.math("DIVIDE", vertex.outputs["Alpha"], self.c("BlendMask2UpperLimit"))),
                          thickness, self.math("TRUNC", self.sat(self.c("BlendMask2UseThickness"))))

        normals = []
        for index in (1, 2, 3):
            x, y, _ = self.split(self.decoded_normal(f"Normal{index}Map", pbr=False))
            normals.append(self.combine(self.math("MULTIPLY", x, self.c(f"NormalMap{index}Intensity")),
                                        self.math("MULTIPLY", y, self.c(f"NormalMap{index}Intensity")), 1))
        primary = self.vector("NORMALIZE", self.mixv(self.mixv(normals[0], normals[1], blend1), normals[2], blend2))
        primary = self.vector("NORMALIZE", self.frame_vector(self.frame(0), primary))

        attr = self.nodes.new("ShaderNodeAttribute")
        attr.attribute_name = "gr2_triplanar_normal"
        tri = attr.outputs["Vector"]
        powers = [self.math("POWER", component, 4) for component in self.split(tri)]
        total = self.math("ADD", powers[0], self.math("ADD", powers[1], powers[2]))
        for axis, power in zip("XYZ", powers):
            weight = self.math("MULTIPLY", self.math("DIVIDE", power, total), self.c("TriPlanarIntensity"))
            x, y, _ = self.split(self.decoded_normal("TriPlanarNormalMap" + axis, pbr=False))
            offset = (self.combine(0, self.math("MULTIPLY", y, -1), x) if axis == "X" else
                      self.combine(x, 0, self.math("MULTIPLY", y, -1)) if axis == "Y" else
                      self.combine(x, self.math("MULTIPLY", y, -1), 0))
            tri = self.vector("ADD", tri, self.vector("SCALE", offset, weight))
        transform = self.nodes.new("ShaderNodeVectorTransform")
        transform.vector_type = "VECTOR"
        transform.convert_from, transform.convert_to = "OBJECT", "WORLD"
        self.bind(self.vector("NORMALIZE", tri), transform.inputs[0])
        seam = self.math("POWER", self.math("ABSOLUTE", blue), self.c("SeamPower"))
        normal = self.vector("NORMALIZE", self.mixv(primary, transform.outputs[0], seam))

        noise = self.sat(self.weighted([self.c(f"NoiseMap{i}Weight") for i in (1, 2, 3)],
            [self.sat(shaped(self.red(f"Noise{i}Map"), f"NoiseMap{i}Power", f"NoiseMap{i}Multiplier")) for i in (1, 2, 3)]))
        noise = remap(self.sat(self.mix(red, noise, self.c("NoiseBlendFactor"))), "NoiseMaskLowerLimit", "NoiseMaskUpperLimit")
        detail_values = []
        details = []
        for index in (1, 2, 3):
            convex, concave, ao = self.split(self.tex(f"Detail{index}Map"))
            details.append((convex, concave))
            value = self.mix(noise, self.math("MULTIPLY", noise, ao), self.c(f"DetailMap{index}AoOpacity"))
            value = self.mix(value, self.c(f"DetailMap{index}ConvexityIntensity"), convex)
            detail_values.append(self.mix(value, self.c(f"DetailMap{index}ConcavityIntensity"), concave))
        selector = self.sat(self.mix(self.mix(detail_values[0], detail_values[1], blend1), detail_values[2], blend2))
        selector = self.mix(selector, self.c("UvSeamNoiseValue"), self.math("POWER", self.math("ABSOLUTE", blue), self.c("UvSeamPower")))
        selector = self.lookup_mip0(lookup_samples, selector)

        def material(selector):
            values = []
            for field, lane in (("BaseColor", None), ("GeneralData", 0), ("GeneralData", 1)):
                slots = [self.color(f"Mtl{i}{field}") if lane is None else self.c(f"Mtl{i}{field}", lane) for i in (1, 2, 3, 4)]
                mix = self.mixv if lane is None else self.mix
                intervals = [mix(slots[i], slots[i + 1], self.math("MULTIPLY", self.math("SUBTRACT", selector, i / 3), 3)) for i in (0, 1, 2)]
                value = mix(intervals[2], intervals[1], self.math("LESS_THAN", selector, 2 / 3))
                values.append(mix(value, intervals[0], self.math("LESS_THAN", selector, 1 / 3)))
            return values

        base, rough, metal = material(selector)
        secondary = []
        for kind in ("Convexity", "Concavity"):
            color, r, m = material(self.c(f"DetailMap3{kind}MaterialIndex"))
            secondary.append((color, *self.packed_material(r, m)))
        convex, concave = details[2]
        ao = shaped(red, "AoPower", "AoMultiplier")
        ao = self.sat(self.mix(ao, self.math("SUBTRACT", 1, ao), self.math("TRUNC", self.sat(self.c("FlipAO")))))
        base = self.vector("MULTIPLY", base, self.mixv(self.color("AoColor1"), self.color("AoColor2"), ao))
        for factor, (color, _, _) in zip((convex, concave), secondary):
            base = self.vector("ADD", base, self.vector("SCALE", color, self.math("MULTIPLY", blend2, factor)))
        base = self.vector("MAXIMUM", base, (0, 0, 0))
        rough = self.sat(self.mix(self.mix(shaped(rough, "RoughnessPower", "RoughnessMultiplier"), secondary[0][1], convex), secondary[1][1], concave))
        metal = self.sat(self.mix(self.mix(shaped(metal, "MetalPower", "MetalMultiplier"), secondary[0][2], convex), secondary[1][2], concave))
        self.surface(base, rough, metal, normal=normal)

    def structure(self):
        vertex = self.nodes.new("ShaderNodeVertexColor")
        vertex.layer_name = "Color0"
        lanes = (*self.split(vertex.outputs["Color"]), vertex.outputs["Alpha"])
        colors = [self.sat(self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", lane), self.c("VertexColorPower", index)), self.c("VertexColorMultiplier", index))) for index, lane in enumerate(lanes)]

        def shaped(value, power, multiplier, clamp=True):
            result = self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", value), self.c(power)), self.c(multiplier))
            return self.sat(result) if clamp else result

        def grunge(selector):
            return self.channel(self.tex("GrungeMap"), self.c(selector), 4, self.input.outputs["GrungeAlpha"])

        masks = []
        for prefix, lane in (("EdgeMask", colors[0]), ("SurfaceMask", colors[1])):
            noise = shaped(grunge(prefix + "GrungeSelector"), prefix + "GrungePower", prefix + "GrungeMultiplier")
            value = self.math("MAXIMUM", self.math("SUBTRACT", lane, noise), 0)
            masks.append(shaped(value, prefix + "Power", prefix + "Multiplier", False))
        blend = self.sat(self.math("MAXIMUM", *masks))
        variation = shaped(self.math("MAXIMUM", self.math("SUBTRACT", colors[2], self.sat(grunge("ColorVariationGrungeChannelSelector"))), 0), "ColorVariationMaskPower", "ColorVariationMaskMultiplier")
        paint_mask = shaped(self.math("MAXIMUM", self.math("SUBTRACT", colors[3], self.sat(grunge("PaintMaskGrungeMapSelector"))), 0), "PaintMaskPower", "PaintMaskMultiplier")
        paint_texture = shaped(self.red("PaintMap"), "PaintMapTexturePower", "PaintMapTextureMultiplier")
        paint = self.math("MAXIMUM", self.math("MINIMUM", paint_mask, self.math("SUBTRACT", paint_texture, blend)), 0)
        ao1 = shaped(self.red("AmbientOcclusion1Map"), "AmbientOcclusion1Power", "AmbientOcclusion1Multiplier")
        ao2 = shaped(self.red("AmbientOcclusion2Map"), "AmbientOcclusion2Power", "AmbientOcclusion2Multiplier")
        curv1 = shaped(self.red("Curvature1Map"), "Curvature1Power", "Curvature1Multiplier")
        curv2 = shaped(self.red("Curvature2Map"), "Curvature2Power", "Curvature2Multiplier")

        def layer(base, third, second, vector=False):
            mix = self.mixv if vector else self.mix
            first = mix(mix(base, third, self.math("SUBTRACT", 1, ao1)), second, curv1)
            other = mix(mix(base, third, self.math("SUBTRACT", 1, ao2)), second, curv2)
            return mix(first, other, blend)

        grunge_base = self.red("GrungeBaseMap")
        base = self.vector("SCALE", self.color("Mtl1BaseColor"), shaped(grunge_base, "GrungeBaseColorPower", "GrungeBaseColorMultiplier", False))
        shifted = self.hue(base, self.math("MULTIPLY", variation, self.c("HueShift")), self.c("ColorVariationMaskDesaturationFraction"))
        base = self.mixv(base, shifted, self.math("MULTIPLY", variation, self.c("ColorVariationMaskOpacity")))
        base = self.mixv(layer(base, self.color("Mtl3BaseColor"), self.color("Mtl2BaseColor"), True), self.color("Mtl4BaseColor"), paint)
        properties = []
        for prefix, lane in (("Roughness", 0), ("Metallic", 1)):
            grunge_value = shaped(grunge_base, "GrungeBase" + prefix + "Power", "GrungeBase" + prefix + "Multiplier", False)
            value = layer(self.math("MULTIPLY", grunge_value, self.c("Mtl1GeneralData", lane)), self.c("Mtl3GeneralData", lane), self.c("Mtl2GeneralData", lane))
            value = shaped(value, prefix + "Power", prefix + "Multiplier", False)
            value = self.math("MULTIPLY", value, self.mix(1, self.c(prefix + "BlendMaskMultiplier"), blend))
            properties.append(self.sat(self.mix(value, self.c("Mtl4GeneralData", lane), paint)))
        primary = self.frame(0, negate=True)
        secondary = self.frame(1, negate=True)

        def secondary_in_primary(texture):
            decoded = self.decoded_normal(texture)
            world = self.vector("NORMALIZE", self.frame_vector(secondary, decoded))
            projected = self.combine(*(self.vector("DOT_PRODUCT", world, basis) for basis in primary))
            return self.vector("NORMALIZE", projected)

        normal = self.mixv((0, 0, 1), self.decoded_normal(), self.c("NormalMap1Intensity"))
        for texture, intensity, factor in (("Normal2Map", "NormalMap2Intensity", blend),
                                           ("NormalDetailMap", "NormalDetailIntensity", self.math("SUBTRACT", 1, blend))):
            x, y, _ = self.split(secondary_in_primary(texture))
            detail = self.vector("SCALE", self.combine(x, y, 0), self.math("MULTIPLY", self.c(intensity), factor))
            normal = self.vector("NORMALIZE", self.vector("ADD", normal, detail))
        normal = self.vector("NORMALIZE", self.frame_vector(primary, normal))
        alpha = 1
        if self.member.selected_options.get("SPACE_OBJECT_TRANSPARENCY") == "SOT_CLIP":
            alpha = self.math("GREATER_THAN", self.math("SUBTRACT", 1, self.red("PaintClipMap")), 127 / 255)
        self.surface(base, properties[0], properties[1], normal=normal, alpha=alpha)

    def frame_vector(self, frame, vector):
        return self.weighted(self.split(vector), frame, True)

    def ship(self):
        uv = self.nodes.new("ShaderNodeAttribute")
        uv.attribute_name = "gr2_texcoord2"
        selector, uv_y, _ = self.split(uv.outputs["Vector"])
        weights = [self.math("SUBTRACT", self.math("LESS_THAN", selector, hi), self.math("LESS_THAN", selector, lo))
                   for lo, hi in ((-1e30, .25), (.25, .5), (.5, .75), (.75, 1e30))]
        base = (0, 0, 0)
        rough = metal = 0
        tiling_enabled = self.math("LESS_THAN", uv_y, .5)
        for index, weight in enumerate(weights, 1):
            color = self.satv(self.color(f"Mtl{index}BaseColor"))
            r = self.c(f"Mtl{index}GeneralData", 0)
            m = self.sat(self.c(f"Mtl{index}GeneralData", 1))
            q = self.red(f"Roughness{index}Map")
            factors = []
            for setup in (f"TileColor{index}Setup", f"Roughness{index}Setup"):
                flipped = self.mix(q, self.math("SUBTRACT", 1, q), self.c(setup, 1))
                shaped = self.math("MINIMUM", self.math("POWER", self.sat(self.math("MULTIPLY", flipped, self.c(setup, 3))), self.c(setup, 2)), 1)
                factors.append(shaped)
            tiled_color = self.mixv(color, self.vector("SCALE", color, factors[0]), self.c(f"TileColor{index}Setup", 0))
            tiled_r = self.sat(self.mix(r, self.math("MULTIPLY", r, factors[1]), self.c(f"Roughness{index}Setup", 0)))
            color = self.mixv(color, tiled_color, tiling_enabled)
            r = self.mix(r, tiled_r, tiling_enabled)
            base = self.vector("ADD", base, self.vector("SCALE", color, weight))
            rough = self.math("ADD", rough, self.math("MULTIPLY", r, weight))
            metal = self.math("ADD", metal, self.math("MULTIPLY", m, weight))
        gradient = self.red("GradientMap")
        factors = []
        for setup in ("GradientColorSetup", "GradientRoughnessSetup"):
            flipped = self.mix(gradient, self.math("SUBTRACT", 1, gradient), self.c(setup, 1))
            factors.append(self.sat(self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", flipped), self.c(setup, 2)), self.c(setup, 3))))
        base = self.mixv(base, self.vector("SCALE", base, factors[0]), self.sat(self.c("GradientColorSetup", 0)))
        rough = self.mix(rough, factors[1], self.c("GradientRoughnessSetup", 0))
        for index in (1, 2):
            z = self.sat(self.channel(self.tex(f"DirtGrunge{index}"), self.c(f"DirtChannel{index}")))
            edge = self.sat(self.math("MULTIPLY", self.math("POWER", z, self.math("MULTIPLY", self.c(f"DirtPower{index}"), .1)), self.math("MULTIPLY", self.c(f"DirtMultiply{index}"), .1)))
            dirt = self.sat(self.math("SUBTRACT", self.math("MULTIPLY", self.red(f"DirtMap{index}"), 2), edge))
            data = f"DirtData{index}"
            if index == 1:
                base = self.mixv(base, self.color("DirtColor1"), self.sat(self.math("MULTIPLY", dirt, self.c(data, 1))))
                target_r = self.sat(self.math("ADD", rough, self.c(data, 2)))
            else:
                color = self.vector("MULTIPLY", base, self.color("DirtColor2"))
                color = self.hue(color, self.math("MULTIPLY", dirt, self.c("DirtHueshift2")), self.c("DirtDesaturate2"))
                base = self.mixv(base, color, self.math("MULTIPLY", dirt, self.c(data, 1)))
                target_r = self.c(data, 2)
            rough = self.mix(rough, target_r, self.math("MULTIPLY", dirt, self.c(data, 3)))
            metal = self.math("MULTIPLY", metal, self.math("SUBTRACT", 1, self.math("MULTIPLY", dirt, self.c(f"DirtMetallicSubtractIntensity{index}"))))
        k = self.red("CurvatureMap")
        z = self.channel(self.tex("CurvatureGrunge"), self.c("CurvatureGrungeChannel"))
        edge = self.math("POWER", self.math("ABSOLUTE", self.math("MULTIPLY", z, self.c("GrungeMaskMultiply"))), self.c("GrungeMaskPower"))
        kc = self.sat(self.math("SUBTRACT", self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", k), self.c("CurvatureColorSetup", 1)), 2), edge))
        base = self.mixv(base, self.vector("SCALE", self.color("CurvatureBaseColor"), kc), self.math("MULTIPLY", kc, self.c("CurvatureColorSetup", 0)))
        kr = self.mix(k, self.math("SUBTRACT", 1, k), self.c("CurvatureRoughnessSetup", 1))
        kr = self.sat(self.math("SUBTRACT", self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", kr), self.c("CurvatureRoughnessSetup", 2)), 2), edge))
        rough = self.mix(rough, self.sat(self.math("ADD", rough, kr)), self.c("CurvatureRoughnessSetup", 0))
        ao = self.sat(self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", self.red("AtlasAOMap")), self.c("AtlasAO", 1)), self.c("AtlasAO", 2)))
        base = self.mixv(base, self.vector("SCALE", base, ao), self.c("AtlasAO", 0))
        rough = self.mix(rough, self.sat(self.math("ADD", rough, self.math("SUBTRACT", 1, ao))), self.c("AtlasAO", 0))
        paint = self.math("MULTIPLY", self.red("AtlasPaintMap"), self.c("AtlasPaintData", 0))
        base = self.mixv(base, self.color("AtlasPaintColor"), paint)
        rough = self.sat(self.mix(rough, self.c("AtlasPaintData", 1), paint))
        metal = self.mix(metal, self.c("AtlasPaintData", 2), paint)
        curv = self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", self.red("AtlasCurvatureMap")), self.c("AtlasCurvature", 1)), self.math("MULTIPLY", self.c("AtlasCurvature", 2), self.c("AtlasCurvature", 0)))
        base = self.mixv(base, self.color("AtlasCurvatureColor"), curv)
        global_noise = self.channel(self.tex("GlobalGrunge"), self.c("GlobalGrungeChannel"))
        global_factor = self.sat(self.math("MULTIPLY", global_noise, self.c("GlobalGrungeRoughnessOpacity")))
        color = self.nodes.new("ShaderNodeVertexColor")
        color.layer_name = "Color0"
        red = self.split(color.outputs["Color"])[0]
        rough = self.mix(rough, self.c("GlobalGrungeRoughness"), self.math("MULTIPLY", red, global_factor))
        self.surface(base, rough, metal, normal=self.normal())

    def turret(self):
        # Frontier 3512930 turret.sm_depth: UV1 quadrants select four materials.
        uv = self.nodes.new("ShaderNodeAttribute")
        uv.attribute_name = "gr2_texcoord1"
        x, y, _ = self.split(uv.outputs["Vector"])
        left, bottom = self.math("LESS_THAN", x, .5), self.math("LESS_THAN", y, .5)
        weights = [self.math("MULTIPLY", a, b) for a, b in (
            (left, bottom), (self.math("SUBTRACT", 1, left), bottom),
            (left, self.math("SUBTRACT", 1, bottom)),
            (self.math("SUBTRACT", 1, left), self.math("SUBTRACT", 1, bottom)))]
        base = self.weighted(weights, [self.color(f"Mtl{i}BaseColor") for i in range(1, 5)], True)
        rough = self.weighted(weights, [self.c(f"Mtl{i}GeneralData", 0) for i in range(1, 5)])
        metal = self.weighted(weights, [self.c(f"Mtl{i}GeneralData", 1) for i in range(1, 5)])
        for index in (1, 2):
            z = self.sat(self.channel(self.tex(f"DirtGrunge{index}"), self.c(f"DirtChannel{index}")))
            edge = self.sat(self.math("MULTIPLY", self.math("POWER", z, self.math("MULTIPLY", self.c(f"DirtPower{index}"), .1)), self.math("MULTIPLY", self.c(f"DirtMultiply{index}"), .1)))
            dirt = self.sat(self.math("SUBTRACT", self.math("MULTIPLY", self.red(f"DirtMap{index}"), 2), edge))
            data = f"DirtData{index}"
            if index == 1:
                base = self.mixv(base, self.color("DirtColor1"), self.sat(self.math("MULTIPLY", dirt, self.c(data, 1))))
                target_r = self.sat(self.math("ADD", rough, self.c(data, 2)))
            else:
                color = self.vector("MULTIPLY", base, self.color("DirtColor2"))
                color = self.hue(color, self.math("MULTIPLY", dirt, self.c("DirtHueshift2")), self.c("DirtDesaturate2"))
                base = self.mixv(base, color, self.math("MULTIPLY", dirt, self.c(data, 1)))
                target_r = self.c(data, 2)
            rough = self.mix(rough, target_r, self.math("MULTIPLY", dirt, self.c(data, 3)))
            metal = self.math("MULTIPLY", metal, self.math("SUBTRACT", 1, self.math("MULTIPLY", dirt, self.c(f"DirtMetallicSubtractIntensity{index}"))))
        ao = self.sat(self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", self.red("AtlasAOMap")), self.c("AtlasAO", 1)), self.c("AtlasAO", 2)))
        base = self.mixv(base, self.vector("SCALE", base, ao), self.c("AtlasAO", 0))
        rough = self.mix(rough, self.sat(self.math("ADD", rough, self.math("SUBTRACT", 1, ao))), self.c("AtlasAO", 0))
        curv = self.math("MULTIPLY", self.math("POWER", self.math("ABSOLUTE", self.red("AtlasCurvatureMap")), self.c("AtlasCurvature", 1)), self.math("MULTIPLY", self.c("AtlasCurvature", 2), self.c("AtlasCurvature", 0)))
        base = self.mixv(base, self.color("AtlasCurvatureColor"), curv)
        global_noise = self.channel(self.tex("GlobalGrunge"), self.c("GlobalGrungeChannel"))
        global_factor = self.sat(self.math("MULTIPLY", global_noise, self.c("GlobalGrungeRoughnessOpacity")))
        color = self.nodes.new("ShaderNodeVertexColor")
        color.layer_name = "Color0"
        red = self.split(color.outputs["Color"])[0]
        rough = self.mix(rough, self.c("GlobalGrungeRoughness"), self.math("MULTIPLY", red, global_factor))
        self.surface(base, rough, metal, normal=self.normal())

    def quad(self):
        transparency = self.member.selected_options.get("SPACE_OBJECT_TRANSPARENCY")
        selector = self.red("MaterialMap")
        weights = self.weights(selector)
        if self.member.name == "quadsailsv5":
            selector = self.mix(selector, self.red("SailsDetailMap"), weights[0])
            weights = self.weights(selector)
        diffuse = self.weighted(weights, [self.color(f"Mtl{i}DiffuseColor") for i in range(1, 5)], True)
        fresnel = self.weighted(weights, [self.color(f"Mtl{i}FresnelColor") for i in range(1, 5)], True)
        gloss = self.weighted(weights, [self.c(f"Mtl{i}Gloss") for i in range(1, 5)])
        gloss = self.math("MULTIPLY", self.red("RoughnessMap"), gloss)
        normal = self.normal(pbr=False)
        if self.member.name == "asteroidv5":
            # Frontier 3512930 sm_depth organic PS19-35: detail precedes
            # paint; neither the selector nor the color factor is clamped.
            strength = self.weighted(weights, [self.c("DetailSelector", lane) for lane in range(4)])
            detail = self.vector("SUBTRACT", self.vector("SCALE", self.tex("Detail1Map"), 2), (1, 1, 1))
            tangent = self.vector("ADD", self.decoded_normal(pbr=False),
                                  self.vector("SCALE", detail, self.math("MULTIPLY", strength, self.c("Detail1Data", 1))))
            normal = self.tangent_normal(self.vector("NORMALIZE", tangent))
            factor = self.math("MULTIPLY", strength, self.input.outputs["Detail1Alpha"])
            diffuse = self.mixv(diffuse, self.color("DetailAlbedoColor"), factor)
            fresnel = self.mixv(fresnel, self.color("DetailFresnelColor"), factor)
        alpha = 1
        if self.member.name in ("quadenvironmentv5", "quadsailsv5"):
            alpha = self.math("SUBTRACT", 1, self.red("PaintMaskMap"))
            if self.member.name == "quadenvironmentv5":
                flags = self.c("VertexColorFlags", 0)
                color = self.nodes.new("ShaderNodeVertexColor")
                color.layer_name = "Color0"
                noisy = self.math("LESS_THAN", self.math("ABSOLUTE", self.math("SUBTRACT",
                    self.red("TransparencyNoiseMap"), color.outputs["Alpha"])), color.outputs["Alpha"])
                enabled = self.math("GREATER_THAN", self.math("ABSOLUTE", flags), 0)
                alpha = self.mix(alpha, noisy, enabled)
            alpha = self.math("GREATER_THAN", alpha, 127 / 255)
            if self.member.name == "quadenvironmentv5" and transparency == "SOT_TRANSPARENT":
                alpha = self.math("MULTIPLY", alpha, self.math("SUBTRACT", 1, self.red("PaintMaskMap")))
        else:
            paint = self.math("MULTIPLY", self.red("PaintMaskMap"), self.c("GeneralData", 0))
            diffuse = self.mixv(diffuse, (1, 1, 1), paint)
            fresnel = self.mixv(fresnel, (.0383840017, .0393519998, .0391650014), paint)
            gloss = self.mix(gloss, .400000006, paint)
            if transparency == "SOT_CLIP":
                alpha = self.math("GREATER_THAN", self.math("SUBTRACT", 1, self.red("PaintClipMap")), 127 / 255)
                if self.member.name == "asteroidv5":
                    alpha = self.math("MULTIPLY", alpha, self.math("GREATER_THAN",
                        self.math("SUBTRACT", 1, self.red("PaintMaskMap")), 127 / 255))
            elif transparency == "SOT_TRANSPARENT":
                alpha = self.math("SUBTRACT", 1, self.red("PaintMaskMap"))
        if self.member.name == "quadenvironmentv5":
            r, g, _ = self.split(self.tex("NormalMap"))
            tangent = self.combine(self.math("SUBTRACT", self.math("MULTIPLY", self.math("ADD", r, .002), 2), 1),
                                   self.math("SUBTRACT", self.math("MULTIPLY", self.math("ADD", g, .002), 2), 1), 1)
            for index in (1, 2):
                strength = self.weighted(weights, [self.c(f"Detail{index}Material", lane) for lane in range(4)])
                detail = self.vector("SUBTRACT", self.vector("SCALE", self.tex(f"Detail{index}Map"), 2), (1, 1, 1))
                tangent = self.vector("ADD", tangent, self.vector("SCALE", detail, strength))
                factor = self.math("MULTIPLY", self.sat(strength), self.input.outputs[f"Detail{index}Alpha"])
                diffuse = self.mixv(diffuse, self.color(f"Detail{index}AlbedoColor"), factor)
                fresnel = self.mixv(fresnel, self.color(f"Detail{index}FresnelColor"), factor)
            normal = self.tangent_normal(self.vector("NORMALIZE", tangent))
        base = self.vector("MULTIPLY", self.tex("AlbedoMap"), diffuse)
        rough = self.sat(self.math("SUBTRACT", 1, gloss))
        glow = self.red("GlowMap")
        glow = self.math("POWER", self.math("MULTIPLY", glow, glow), 1.200000048)
        glow = self.math("MULTIPLY", glow, self.input.outputs["activationStrength"])
        glow_color = "GeneralGlowColor"
        if self.member.name == "quadheatv5":
            influence = self.weighted(weights, [self.c(f"Mtl{i}HeatGlowData", 0) for i in range(1, 5)])
            gate = self.sat(self.math("MULTIPLY", self.math("SUBTRACT", self.input.outputs["boosterGain"], .005), 66.66667175))
            amount = self.sat(self.math("ADD", 1, self.math("MULTIPLY", influence, self.math("SUBTRACT", gate, 1))))
            glow = self.math("MULTIPLY", glow, amount)
            glow_color = "GeneralHeatGlowColor"
        emission = self.vector("SCALE", self.color(glow_color), glow)
        self.dust_surface(weights, base, rough, fresnel, normal, emission, alpha)

    def lookup_rgba_mip0(self, samples, selector):
        """Two nearest mip-zero fetches, then linear RGBA interpolation.

        Blender's Closest image sampler disables mip filtering. Fetch original
        adjacent texels at their centers rather than constructing a large
        conditional node tree, which exhausts Cycles' SVM stack in full heat.
        The float image is already linear RGB with independent alpha.
        """
        samples = tuple(tuple(float(value) for value in texel) for texel in samples)
        if not samples or any(len(texel) != 4 for texel in samples):
            raise ValueError("Mip-zero lookup requires RGBA texels")
        digest = hashlib.sha256(repr(samples).encode("ascii")).hexdigest()
        name = "Carbon Linear Mip0 " + digest
        image = bpy.data.images.get(name)
        if image is None:
            image = bpy.data.images.new(name, width=len(samples), height=1, alpha=True, float_buffer=True)
            image.colorspace_settings.name = "Non-Color"
            image.alpha_mode = "CHANNEL_PACKED"
            image.pixels = [value for texel in samples for value in texel]
            image.pack()
        image.use_half_precision = False
        m = self.math
        coordinate = m("MINIMUM", m("MAXIMUM", m("SUBTRACT", m("MULTIPLY", selector, len(samples)), .5), 0), len(samples)-1)
        left = m("FLOOR", coordinate)
        right = m("MINIMUM", m("ADD", left, 1), len(samples)-1)
        fraction = m("SUBTRACT", coordinate, left)
        fetched = []
        for index in (left, right):
            node = self.nodes.new("ShaderNodeTexImage")
            node.image = image
            node.interpolation, node.extension = "Closest", "EXTEND"
            self.bind(self.combine(m("DIVIDE", m("ADD", index, .5), len(samples)), .5, 0), node.inputs["Vector"])
            fetched.append(node)
        color = self.vector("ADD", self.vector("SCALE", fetched[0].outputs["Color"], m("SUBTRACT", 1, fraction)),
                            self.vector("SCALE", fetched[1].outputs["Color"], fraction))
        alpha = m("ADD", m("MULTIPLY", fetched[0].outputs["Alpha"], m("SUBTRACT", 1, fraction)),
                  m("MULTIPLY", fetched[1].outputs["Alpha"], fraction))
        return color, alpha

    def heat_emission(self, heat, gradient_samples):
        """Depth heat PS128–133: clamped mip-zero RGBA row at V=0.

        Samples must already contain linear RGB and unchanged alpha, with the
        source's first row first (before Blender's image-space vertical flip).
        DX11 TF_NONE fixes MaxLOD to MinLOD, disabling lower gradient mips.
        Gradient alpha scales emission; HeatTint alpha is unused.
        """
        if not gradient_samples or any(len(texel) != 4 for texel in gradient_samples):
            raise ValueError("Heat gradient requires an RGBA texel row")
        ramp, alpha = self.lookup_rgba_mip0(gradient_samples, heat)
        color = self.vector("MULTIPLY", ramp, self.color("HeatTint"))
        return self.vector("SCALE", color, self.math("MULTIPLY", self.c("HeatOutput"), alpha))

    def heat_sun(self, sun, current, lagged):
        """Depth heat PS56–83: rotate sun by current * conjugate(lagged).

        Quaternion lanes are Carbon XYZW. Either squared length strictly below
        1e-6 bypasses the lag correction. This is an authored heat input, not
        a replacement for Blender's lighting or animation evaluation.
        """
        m, v = self.math, self.vector
        sun = v("NORMALIZE", sun)
        def quaternion(lanes):
            xyz = self.combine(*lanes[:3])
            squared = m("ADD", v("DOT_PRODUCT", xyz, xyz), m("MULTIPLY", lanes[3], lanes[3]))
            inverse = m("DIVIDE", 1, m("SQRT", squared))
            return v("SCALE", xyz, inverse), m("MULTIPLY", lanes[3], inverse), squared
        c, cw, cn = quaternion(current)
        l, lw, ln = quaternion(lagged)
        q = v("ADD", v("SUBTRACT", v("SCALE", c, lw), v("SCALE", l, cw)), v("CROSS_PRODUCT", l, c))
        qw = m("ADD", m("MULTIPLY", cw, lw), v("DOT_PRODUCT", c, l))
        corrected = v("NORMALIZE", v("ADD", sun, v("SCALE",
            v("CROSS_PRODUCT", q, v("ADD", v("CROSS_PRODUCT", q, sun), v("SCALE", sun, qw))), 2)))
        bypass = m("MAXIMUM", m("LESS_THAN", cn, .000001), m("LESS_THAN", ln, .000001))
        return self.mixv(corrected, sun, bypass)

    def heat_field(self, position, distance, normal, view, sun, radius, time,
                   noise, atlas_ao, atlas_curvature, grunge):
        """Frontier 3512930 fxheatv5 sm_depth scalar GradientMap coordinate.

        Texture samples and vertex interpolants are supplied by the caller.
        Sun is the normalized direction after the authored ship/lagged rotation
        correction; it controls heat coverage, not Blender scene lighting.
        This alone does not qualify the material's volume/gradient samplers.
        """
        m, c = self.math, self.c
        start = self.sat(c("HeatOutput", 2))
        growth = self.sat(m("DIVIDE", m("SUBTRACT", c("Growth"), start),
                           m("MAXIMUM", m("SUBTRACT", 1, start), .001)))
        shape = self.sat(m("ADD", c("HeatShape"), m("ADD",
            m("MULTIPLY", m("SUBTRACT", 1, atlas_ao), c("HeatShape", 1)),
            m("MULTIPLY", self.sat(atlas_curvature), c("HeatShape", 2)))))
        fade = m("MULTIPLY", self.sat(m("SUBTRACT", 1,
            m("DIVIDE", distance, m("MAXIMUM", c("HeatSurface", 2), 1)))), c("HeatSurface", 1))
        shape = self.sat(m("ADD", shape, m("MULTIPLY", m("SUBTRACT", grunge, .5), fade)))
        radius = m("MAXIMUM", radius, .0001)
        width = m("MAXIMUM", c("HeatShape", 3), .001)
        phase = m("SUBTRACT", m("ADD", m("MULTIPLY", noise, 6.283199787),
            m("MULTIPLY", self.split(position)[1], c("HeatShimmer", 1))),
            m("MULTIPLY", m("MULTIPLY", time, c("HeatShimmer", 3)), 1.382303953))
        shimmer = m("ADD", m("MULTIPLY", .65, m("SINE", phase)),
            m("MULTIPLY", .35, m("SINE", m("ADD", m("MULTIPLY", phase, 2.369999886),
                m("MULTIPLY", noise, 11)))))
        threshold = m("ADD", m("SUBTRACT", m("ADD", 1, width),
            m("MULTIPLY", growth, m("ADD", 1, m("MULTIPLY", 2, width)))),
            m("MULTIPLY", c("HeatOutput", 1), shimmer))
        flow = self.combine(*(c("HeatFlow", lane) for lane in range(3)))
        flowing = m("GREATER_THAN", self.vector("LENGTH", flow), .0001)
        flow_coordinate = self.sat(m("ADD", .5, m("MULTIPLY", .5,
            m("DIVIDE", m("ADD", self.vector("DOT_PRODUCT", position, flow), c("HeatFlow", 3)), radius))))
        threshold = m("ADD", threshold, m("MULTIPLY", flowing,
            m("MULTIPLY", m("SUBTRACT", 1, flow_coordinate), c("HeatOutput", 3))))
        normal = self.vector("NORMALIZE", normal)
        solar = self.sat(m("DIVIDE", m("ADD", self.vector("DOT_PRODUCT", normal, sun), c("HeatSolar", 1)),
            m("MAXIMUM", m("ADD", 1, c("HeatSolar", 1)), .001)))
        solar = self.mix(self.sat(c("HeatSolar", 2)), 1, solar)
        threshold = m("ADD", threshold, m("MULTIPLY", m("SUBTRACT", 1, solar), c("HeatRange")))
        coverage = self.sat(m("DIVIDE", m("SUBTRACT", shape, m("SUBTRACT", threshold, width)),
            m("MULTIPLY", 2, width)))
        coverage = m("MULTIPLY", m("MULTIPLY", coverage, coverage),
            m("SUBTRACT", 3, m("MULTIPLY", 2, coverage)))
        heat = m("MULTIPLY", self.mix(.35, m("MAXIMUM", c("HeatRange", 1), .01), growth),
            m("MULTIPLY", shape, coverage))
        heat = m("MULTIPLY", heat, self.mix(1, solar, self.sat(c("HeatSolar"))))
        heat = m("MULTIPLY", heat, m("ADD", 1, m("MULTIPLY", c("HeatShimmer", 2),
            m("MULTIPLY", c("HeatOutput", 1), shimmer))))
        facing = m("SUBTRACT", 1, self.sat(self.vector("DOT_PRODUCT", normal, self.vector("NORMALIZE", view))))
        heat = m("MULTIPLY", heat, m("ADD", 1, m("MULTIPLY", m("MULTIPLY", facing, facing), c("HeatSurface", 3))))
        # Growth <= 0 still samples GradientMap at zero; never skip its emission.
        return m("MULTIPLY", self.sat(heat), m("GREATER_THAN", growth, 0))

    def triplanar(self):
        # Frontier 3512930 depth PS0-111: projection-space normal offsets,
        # followed by inverse-transpose world transform (not the quad TBN).
        weights = self.weights(self.red("MaterialMap"))
        strengths = [self.weighted(weights, [self.c(f"Detail{i}Material", lane) for lane in range(4)]) for i in (1, 2)]
        normal = self.input.outputs["ProjectionNormal"]
        projection_weights = self.split(self.input.outputs["ProjectionWeights"])
        for axis, weight in zip("XYZ", projection_weights):
            lanes = []
            for name in ("NormalMap", "Detail1Map", "Detail2Map"):
                channels = self.split(self.tex(name + axis))
                lanes.append([self.math("SUBTRACT", self.math("MULTIPLY", self.math("ADD", value, .002), 2), 1) for value in channels[:2]])
            x, y = [self.math("ADD", lanes[0][lane], self.math("ADD",
                self.math("MULTIPLY", lanes[1][lane], strengths[0]),
                self.math("MULTIPLY", lanes[2][lane], strengths[1]))) for lane in (0, 1)]
            offset = (self.combine(0, self.math("MULTIPLY", y, -1), x) if axis == "X" else
                      self.combine(x, 0, self.math("MULTIPLY", y, -1)) if axis == "Y" else
                      self.combine(x, self.math("MULTIPLY", y, -1), 0))
            normal = self.vector("ADD", normal, self.vector("SCALE", offset, weight))
        normal = self.world_normal(normal)
        diffuse = self.weighted(weights, [self.color(f"Mtl{i}DiffuseColor") for i in range(1, 5)], True)
        fresnel = self.weighted(weights, [self.color(f"Mtl{i}FresnelColor") for i in range(1, 5)], True)
        for i, strength in enumerate(strengths, 1):
            factor = self.math("MULTIPLY", self.sat(strength), self.input.outputs[f"Detail{i}Alpha"])
            diffuse = self.mixv(diffuse, self.color(f"Detail{i}AlbedoColor"), factor)
            fresnel = self.mixv(fresnel, self.color(f"Detail{i}FresnelColor"), factor)
        gloss = self.weighted(weights, [self.c(f"Mtl{i}Gloss") for i in range(1, 5)])
        rough = self.sat(self.math("SUBTRACT", 1, self.math("MULTIPLY", self.red("RoughnessMap"), gloss)))
        self.dust_surface(weights, self.vector("MULTIPLY", self.tex("AlbedoMap"), diffuse), rough, fresnel, normal, (0, 0, 0))

    def world_normal(self, value):
        # Explicit inverse transpose: Blender 5 Eevee's Vector Transform
        # NORMAL path applies M*v then normalizes, unlike Cycles. Dotting
        # with inverse-matrix columns applies the transpose explicitly.
        components = []
        for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            transform = self.nodes.new("ShaderNodeVectorTransform")
            transform.vector_type = "VECTOR"
            transform.convert_from, transform.convert_to = "WORLD", "OBJECT"
            transform.inputs[0].default_value = axis
            components.append(self.vector("DOT_PRODUCT", transform.outputs[0], value))
        return self.vector("NORMALIZE", self.combine(*components))

    def dust_surface(self, weights, base, rough, fresnel, normal, emission, alpha=1):
        noise = [self.math("ADD", lane, .5) for lane in self.split(self.tex("DustNoiseMap"))]
        noise_alpha = self.math("ADD", self.input.outputs[nodes.DUST_ALPHA], .5)
        dust_color = self.weighted(weights, [self.color(f"Mtl{i}DustDiffuseColor") for i in range(1, 5)], True)
        dusty = self.vector("SCALE", self.vector("MULTIPLY", self.tex("AlbedoMap"), dust_color), noise[0])
        dust_f0 = self.vector("SCALE", (.0189999994, .0170000009, .0140000004), noise[1])
        dust_rough = self.sat(self.math("SUBTRACT", 1, self.math("MULTIPLY", self.red("RoughnessMap"), self.math("MULTIPLY", noise[2], .400000006))))
        mask = self.sat(self.math("DIVIDE", self.math("MULTIPLY", self.red("DirtMap"), noise_alpha),
                                  self.math("SUBTRACT", 1, self.input.outputs["dirtLevel"])))
        clean_weight = self.math("POWER", self.math("SUBTRACT", 1, mask), 3)
        clean_bsdf = self.surface(base, rough, normal=normal, fresnel=fresnel)
        dust_bsdf = self.surface(dusty, dust_rough, normal=normal, fresnel=dust_f0, write_outputs=False)
        # Carbon weights two independently lit surfaces. Preserve the closure
        # weights while letting Blender evaluate both under its own lights.
        weighted = []
        for closure, weight in ((clean_bsdf, clean_weight), (dust_bsdf, mask)):
            mix = self.nodes.new("ShaderNodeMixShader")
            self.bind(weight, mix.inputs[0])
            self.bind(closure, mix.inputs[2])
            weighted.append(mix.outputs[0])
        add = self.nodes.new("ShaderNodeAddShader")
        for closure, socket in zip(weighted, add.inputs):
            self.bind(closure, socket)
        emit = self.nodes.new("ShaderNodeEmission")
        self.bind(emission, emit.inputs["Color"])
        final = self.nodes.new("ShaderNodeAddShader")
        self.bind(add.outputs[0], final.inputs[0])
        self.bind(emit.outputs[0], final.inputs[1])
        transparent = self.nodes.new("ShaderNodeBsdfTransparent")
        coverage = self.nodes.new("ShaderNodeMixShader")
        self.bind(alpha, coverage.inputs[0])
        self.bind(transparent.outputs[0], coverage.inputs[1])
        self.bind(final.outputs[0], coverage.inputs[2])
        self.bind(coverage.outputs[0], self.output.inputs["BSDF"])
        self.bind(alpha, self.output.inputs["Alpha"])
        self.bind(emission, self.output.inputs["Emission"])

    def tangent_normal(self, value):
        # Frontier 3512930 sm_depth quad VS/PS: positive authored T/B/N,
        # direct world transform, then normalize the assembled normal.
        # Image-space V conversion must not change this imported frame.
        return self.vector("NORMALIZE", self.frame_vector(self.frame(0), value))

    def surface(self, base, rough, metal=0, emission=(0, 0, 0), normal=None, alpha=1, fresnel=None, write_outputs=True):
        bsdf = self.nodes.new("ShaderNodeBsdfPrincipled")
        for name, value in (("Base Color", base), ("Roughness", rough), ("Metallic", metal),
                            ("Emission Color", emission), ("Emission Strength", 1), ("Alpha", alpha)):
            self.bind(value, bsdf.inputs[name])
        if normal is not None:
            self.bind(normal, bsdf.inputs["Normal"])
        if fresnel is not None:
            self.bind(1, bsdf.inputs["Specular IOR Level"])
            self.bind(self.vector("SCALE", fresnel, 12.5), bsdf.inputs["Specular Tint"])
            if write_outputs:
                self.bind(fresnel, self.output.inputs["Fresnel"])
        if not write_outputs:
            return bsdf.outputs[0]
        for name, value in (("BSDF", bsdf.outputs[0]), ("Albedo", base), ("Roughness", rough),
                            ("Metallic", metal), ("Emission", emission), ("Alpha", alpha)):
            self.bind(value, self.output.inputs[name])
        if normal is not None:
            self.bind(normal, self.output.inputs["Normal"])
        return bsdf.outputs[0]


def build_heat_group(member, gradient_samples):
    """Compose measured thermal emission with explicit sampler/scene inputs.

    The material importer supplies explicit texture and scene bindings.
    In particular Noise is a sampled
    3D texture value, not procedural Blender noise or a substituted asset.
    """
    if member.target != "frontier" or member.name != "fxheatv5" or member.tier != "sm_depth":
        raise ValueError("Heat emission graph requires Frontier fxheatv5 sm_depth")
    samples = tuple(tuple(float(value) for value in texel) for texel in gradient_samples)
    if not samples or any(len(texel) != 4 for texel in samples):
        raise ValueError("Heat gradient requires an RGBA texel row")
    digest = hashlib.sha256(repr(samples).encode("ascii")).hexdigest()
    name = f"Carbon Heat Emission v2 {member.identity} {digest}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        return tree
    tree = nodes._new_group(name)
    tree["carbon_external_bindings_required"] = True
    for label, kind in (("BSDF", "NodeSocketShader"), ("Emission", "NodeSocketVector"),
                        ("Heat", "NodeSocketFloat"), ("Alpha", "NodeSocketFloat")):
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type=kind)
    for label in ("LocalPosition", "WorldNormal", "VertexView", "SunDirection"):
        nodes._socket(tree, label, "NodeSocketVector", default=(0, 0, 0))
    for label in ("VertexDistance", "shipRadius", "time", "Noise", "AtlasAO", "AtlasCurvature", "Grunge"):
        nodes._socket(tree, label, "NodeSocketFloat", default=0)
    for constant in member.constants:
        for label, kind, default in constant_sockets(member, constant):
            nodes._socket(tree, label, kind, default=default)
    graph = Graph(tree, member)
    inputs = graph.input.outputs
    sun = graph.heat_sun(inputs["SunDirection"],
        tuple(graph.c("HeatShipRotation", lane) for lane in range(4)),
        tuple(graph.c("HeatShipRotationLagged", lane) for lane in range(4)))
    heat = graph.heat_field(inputs["LocalPosition"], inputs["VertexDistance"],
        inputs["WorldNormal"], inputs["VertexView"], sun, inputs["shipRadius"], inputs["time"],
        inputs["Noise"], inputs["AtlasAO"], inputs["AtlasCurvature"], inputs["Grunge"])
    emission = graph.heat_emission(heat, samples)
    emit = graph.nodes.new("ShaderNodeEmission")
    graph.bind(emission, emit.inputs["Color"])
    transparent = graph.nodes.new("ShaderNodeBsdfTransparent")
    add = graph.nodes.new("ShaderNodeAddShader")
    graph.bind(transparent.outputs[0], add.inputs[0])
    graph.bind(emit.outputs[0], add.inputs[1])
    for label, value in (("BSDF", add.outputs[0]), ("Emission", emission), ("Heat", heat), ("Alpha", 1)):
        graph.bind(value, graph.output.inputs[label])
    return tree


def wire_heat_inputs(member, effect, material, group, resources, *, radius, sun_direction):
    """Bind heat emission's static-mesh inputs under the runtime null contract.

    Radius and scene sun are explicit caller inputs, not inferred from a shader
    basename. Geometry must carry the imported attributes and FX view modifier.
    A bound volume is rejected until a real 3D sampler is qualified. As with
    other sampled coordinate transforms, editing HeatSurface.x requires rebuild.
    """
    from .materials import load_texture, local_file
    bindings = {r.get("name"): r.get("resourcePath") for r in effect.get("resources", [])}
    if bindings.get("NoiseMap"):
        raise ValueError("Bound heat NoiseMap requires a qualified 3D volume sampler")
    images = {}
    for name in ("AtlasAOMap", "AtlasCurvatureMap", "GrungeMap"):
        path = bindings.get(name)
        if not path:
            continue
        local = local_file(resources, path)
        image = load_texture(local, logical_path=path) if local else None
        if image is None:
            raise ValueError(f"Heat texture is unavailable: {name}")
        if image.colorspace_settings.name != "Non-Color":
            image = image.copy()
            image.colorspace_settings.name = "Non-Color"
        images[name] = image
    graph = Graph(material.node_tree, member, False)
    def attribute(name, scalar=False):
        node = graph.nodes.new("ShaderNodeAttribute")
        node.attribute_name = name
        return node.outputs["Fac" if scalar else "Vector"]
    position = attribute("carbon_rest_position")
    uv = attribute("gr2_texcoord0")
    values = member.defaults()
    values.update({c["name"]: tuple(c["value"]) for c in effect.get("constParameters", [])
                   if c.get("name") in values and c.get("value")})
    px, _, pz = graph.split(position)
    grunge_uv = graph.vector("ADD", graph.vector("SCALE", uv, values["HeatSurface"][0]),
                            graph.vector("SCALE", graph.combine(px, pz, 0), .05))
    def sample(name, coordinate):
        if name not in images:
            return (0, 0, 0)
        node = graph.nodes.new("ShaderNodeTexImage")
        node.label, node.image = name, images[name]
        node.extension, node.interpolation = "REPEAT", "Linear"
        graph.bind(nodes._image_uv(material.node_tree, coordinate), node.inputs["Vector"])
        return node.outputs["Color"]
    first = graph.split(sample("GrungeMap", grunge_uv))[0]
    second_uv = graph.vector("ADD", graph.vector("SCALE", grunge_uv, 2.7), (.31, .31, 0))
    second = graph.split(sample("GrungeMap", second_uv))[1]
    grunge = graph.math("ADD", graph.math("MULTIPLY", first, .6), graph.math("MULTIPLY", second, .4))
    normal = graph.frame(0)[2]
    for name, value in (("LocalPosition", position), ("WorldNormal", normal),
                        ("VertexView", attribute("carbon_fx_vertex_view")),
                        ("VertexDistance", attribute("carbon_fx_vertex_distance", True)),
                        ("shipRadius", radius), ("SunDirection", sun_direction),
                        ("time", nodes.time_value(material.node_tree)), ("Noise", 0),
                        ("AtlasAO", graph.split(sample("AtlasAOMap", uv))[0]),
                        ("AtlasCurvature", graph.split(sample("AtlasCurvatureMap", uv))[0]),
                        ("Grunge", grunge)):
        graph.bind(value, group.inputs[name])
    material["carbon_fx_vertex_view"] = True
    material["carbon_heat_noise_contract"] = "runtime-null-texture"
    material["carbon_heat_sampler_limit"] = "Blender 2D filtering; Carbon anisotropy and scene mip bias are not reproduced"
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    else:
        material.blend_method = "BLEND"


def build_group(member, lookup_samples=None):
    if member.tier != "sm_depth":
        raise ValueError("Frontier surface graphs require sm_depth")
    if member.name not in SUPPORTED:
        raise ValueError(f"Frontier {member.name}: surface implementation is not qualified")
    lookup_samples = tuple(float(value) for value in lookup_samples) if lookup_samples is not None else (0.0,)
    suffix = " " + hashlib.sha256(repr(lookup_samples).encode("ascii")).hexdigest() if member.name == "asteroid" else ""
    name = f"CarbonShader {member.identity}{suffix}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.get("carbon_frontier_version") == VERSION:
        return tree
    tree = nodes._new_group(name)
    tree["carbon_source"] = member.target
    tree["carbon_effect_path"] = member.effect_path
    tree["carbon_effect_sha256"] = member.digest
    for label, kind, _ in nodes.OUTPUTS:
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type=kind)
    for label in ("Metallic", "Alpha"):
        tree.interface.new_socket(name=label, in_out="OUTPUT", socket_type="NodeSocketFloat")
    for texture in member.textures:
        nodes._socket(tree, texture, "NodeSocketColor", default=(0, 0, 0, 1))
    if member.name in ("ship", "turret"):
        for texture in ("DirtGrunge1", "DirtGrunge2", "CurvatureGrunge", "GlobalGrunge"):
            nodes._socket(tree, texture, "NodeSocketColor", default=(0, 0, 0, 1))
    if member.name == "structure":
        nodes._socket(tree, "GrungeAlpha", "NodeSocketFloat", default=1.0)
        nodes._socket(tree, "PaintClipMap", "NodeSocketColor", default=(0, 0, 0, 1))
    elif member.name in ("quadv5", "quadheatv5", "quaddetailv5", "asteroidv5") and member.selected_options.get("SPACE_OBJECT_TRANSPARENCY") == "SOT_CLIP":
        nodes._socket(tree, "PaintClipMap", "NodeSocketColor", default=(0, 0, 0, 1))
    if member.name in ("quadenvironmentv5", "quadtriplanarv5"):
        for index in (1, 2):
            nodes._socket(tree, f"Detail{index}Alpha", "NodeSocketFloat", default=1.0)
    if member.name == "quadtriplanarv5":
        for label in ("ProjectionNormal", "ProjectionWeights"):
            nodes._socket(tree, label, "NodeSocketVector", default=(0, 0, 1))
        for texture in ("NormalMap", "Detail1Map", "Detail2Map"):
            for axis in "XYZ":
                nodes._socket(tree, texture + axis, "NodeSocketColor", default=(.498, .498, 0, 1))
    if member.name == "asteroidv5":
        nodes._socket(tree, "Detail1Alpha", "NodeSocketFloat", default=1.0)
    if member.name == "asteroid":
        for axis in "XYZ":
            nodes._socket(tree, "TriPlanarNormalMap" + axis, "NodeSocketColor", default=(.498, .498, 0, 1))
    if member.name.startswith("quad") or member.name == "asteroidv5":
        nodes._socket(tree, "activationStrength", "NodeSocketFloat", default=1.0)
        nodes._socket(tree, "dirtLevel", "NodeSocketFloat", default=0.0)
        nodes._socket(tree, nodes.DUST_ALPHA, "NodeSocketFloat", default=1.0)
    if member.name == "quadheatv5":
        nodes._socket(tree, "boosterGain", "NodeSocketFloat", default=1.0)
    for constant in member.constants:
        for label, kind, default in constant_sockets(member, constant):
            nodes._socket(tree, label, kind, default=default)
    graph = Graph(tree, member)
    if member.name == "simplepbr":
        selector = graph.red("MaterialIndexMap")
        # Strict comparisons: exactly .25/.5/.75 selects the next material.
        base = graph.satv(graph.color("Mtl4BaseColor"))
        rough = graph.sat(graph.c("Mtl4GeneralData", 0))
        metal = graph.sat(graph.c("Mtl4GeneralData", 1))
        for index, boundary in ((3, .75), (2, .5), (1, .25)):
            factor = graph.math("LESS_THAN", selector, boundary)
            base = graph.mixv(base, graph.satv(graph.color(f"Mtl{index}BaseColor")), factor)
            rough = graph.mix(rough, graph.sat(graph.c(f"Mtl{index}GeneralData", 0)), factor)
            metal = graph.mix(metal, graph.sat(graph.c(f"Mtl{index}GeneralData", 1)), factor)
        base = graph.vector("MULTIPLY", graph.tex("AlbedoMap"), base)
        rough = graph.sat(graph.math("ADD", rough, graph.math("MULTIPLY",
            graph.math("SUBTRACT", graph.red("RoughnessMap"), .5), graph.c("RoughnessTextureIntensity"))))
        emission = graph.vector("SCALE", graph.color("GlowColor"), graph.red("GlowMap"))
        alpha = (graph.math("GREATER_THAN", graph.red("AlphaMaskMap"), 127 / 255)
                 if member.selected_options.get("SPACE_OBJECT_TRANSPARENCY") == "SOT_CLIP" else 1)
        graph.surface(base, rough, metal, emission, graph.normal(), alpha)
    elif member.name == "standardpbr":
        graph.surface(graph.tex("AlbedoMap"), graph.red("RoughnessMap"), graph.red("MetalnessMap"), normal=graph.normal())
    elif member.name == "turret":
        graph.turret()
    elif member.name == "ship":
        graph.ship()
    elif member.name == "structure":
        graph.structure()
    elif member.name == "quadtriplanarv5":
        graph.triplanar()
    elif member.name == "asteroid":
        graph.asteroid(lookup_samples)
    else:
        graph.quad()
    # Keep generated arithmetic navigable without hand-maintaining positions.
    for index, node in enumerate(tree.nodes):
        node.location = ((index % 10) * 220, -(index // 10) * 180)
    tree["carbon_frontier_version"] = VERSION
    return tree


def wire_coordinates(member, effect, material):
    """The measured UV selections; texture reads stay outside shared groups."""
    graph = Graph(material.node_tree, member, False)
    values = member.defaults()
    values.update({c["name"]: c["value"] for c in effect.get("constParameters", [])})
    if member.name == "quadtriplanarv5":
        wire_triplanar(graph, values)
        return
    uv = []
    for index in (0, 1, 2):
        node = graph.nodes.new("ShaderNodeAttribute")
        node.attribute_name = f"gr2_texcoord{index}"
        node.label = f"Authored UV{index}"
        uv.append(node.outputs["Vector"])
    assignments = {}
    assignments["DustNoiseMap"] = graph.vector("SCALE", uv[0], 20)
    extra_samples = {}
    if member.name == "asteroid":
        for index in (1, 2, 3):
            for prefix, textures, source in (("Noise", (f"Noise{index}Map",), uv[0]),
                                             ("Normal", (f"Normal{index}Map", f"Detail{index}Map"), uv[1] if index == 3 else uv[0])):
                coordinates = graph.vector("ADD", graph.vector("SCALE", source, values[f"{prefix}Map{index}Tiling"][0]),
                                           (*values[f"{prefix}Map{index}Offset"][:2], 0))
                for texture in textures:
                    assignments[texture] = coordinates
        position = graph.nodes.new("ShaderNodeAttribute")
        position.attribute_name = "carbon_rest_position"
        scale = 1 / values["TriPlanarTiling"][0] if values["TriPlanarTiling"][0] else 0
        x, y, z = graph.split(graph.vector("SCALE", position.outputs["Vector"], scale))
        for axis, projection, offset in zip("XYZ", (graph.combine(z, y, 0), graph.combine(x, z, 0), graph.combine(x, y, 0)), (0, .3300000131, .6700000167)):
            extra_samples["TriPlanarNormalMap" + axis] = ("TriPlanarNormalMap", graph.vector("ADD", projection, (offset, offset, 0)))
    if member.name == "structure":
        for texture in ("NormalMap", "AmbientOcclusion1Map", "Curvature1Map", "PaintMap"):
            assignments[texture] = graph.vector("SCALE", uv[0], values["UV1Tiling"][0])
        for texture in ("AmbientOcclusion2Map", "Curvature2Map", "GrungeBaseMap", "Normal2Map"):
            assignments[texture] = graph.vector("SCALE", uv[1], values["UV2Tiling"][0])
        assignments["NormalDetailMap"] = graph.vector("SCALE", uv[1], values["UV2DetailTiling"][0])
        assignments["GrungeMap"] = graph.vector("SCALE", assignments["Normal2Map"], values["GrungeMaskTiling"][0])
        extra_samples["PaintClipMap"] = ("PaintMap", uv[0])
    elif member.name in ("quadv5", "quadheatv5", "quaddetailv5", "asteroidv5") and member.selected_options.get("SPACE_OBJECT_TRANSPARENCY") == "SOT_CLIP":
        general, tiling = values["GeneralData"], values["GeneralTiling"]
        extra_samples["PaintClipMap"] = ("PaintMaskMap", graph.vector("SCALE", graph.mixv(uv[0], uv[1], general[2]), tiling[1]))
    if member.name == "asteroidv5":
        assignments["Detail1Map"] = graph.vector("SCALE", uv[0], values["Detail1Data"][0])
    if member.name == "ship":
        for texture in ("DirtMap1", "DirtMap2", "GradientMap", "CurvatureMap"):
            assignments[texture] = uv[1]
        for index in range(1, 5):
            assignments[f"Roughness{index}Map"] = graph.vector("SCALE", uv[2], values["RoughnessTiling"][index - 1] * 100)
        extra = {
            "DirtGrunge1": graph.vector("SCALE", uv[2], values["DirtData1"][0] * 100),
            "DirtGrunge2": graph.vector("SCALE", uv[2], values["DirtData2"][0] * 100),
            "CurvatureGrunge": graph.vector("SCALE", uv[1], values["CurvatureGrungeTile"][0]),
            "GlobalGrunge": graph.vector("SCALE", uv[2], values["GlobalGrungeTile"][0] * 100),
        }
        for label, coordinates in extra.items():
            extra_samples[label] = ("GrungeMap", coordinates)
    if member.name == "turret":
        assignments["DirtMap1"] = assignments["DirtMap2"] = uv[1]
        for label, scale in (("DirtGrunge1", values["DirtData1"][0]),
                             ("DirtGrunge2", values["DirtData2"][0]),
                             ("GlobalGrunge", values["GlobalGrungeTile"][0])):
            extra_samples[label] = ("GrungeMap", graph.vector("SCALE", uv[1], scale * 100))
    group = next(n for n in graph.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree.name.startswith("CarbonShader"))
    for label, (texture, coordinates) in extra_samples.items():
        source = next((n for n in graph.nodes if n.bl_idname == "ShaderNodeTexImage" and n.label == texture), None)
        sample = graph.nodes.new("ShaderNodeTexImage")
        sample.image = source.image if source else None
        sample.label = label
        graph.links.new(sample.outputs["Color"], group.inputs[label])
        assignments[label] = coordinates
    if member.name == "structure":
        grunge = next(n for n in graph.nodes if n.bl_idname == "ShaderNodeTexImage" and n.label == "GrungeMap")
        graph.links.new(grunge.outputs["Alpha"], group.inputs["GrungeAlpha"])
    if member.name in ("quadenvironmentv5", "quadsailsv5"):
        general, tiling = values["GeneralData"], values["GeneralTiling"]
        base_uv = graph.vector("SCALE", graph.mixv(uv[0], uv[1], general[2]), tiling[1])
        assignments["PaintMaskMap"] = base_uv
        if member.name == "quadenvironmentv5":
            for texture in ("AlbedoMap", "NormalMap", "GlowMap", "RoughnessMap", "DirtMap"):
                assignments[texture] = base_uv
            assignments["MaterialMap"] = graph.vector("SCALE", graph.mixv(uv[0], uv[1], general[1]), tiling[0])
            assignments["TransparencyNoiseMap"] = uv[1]
            for index in (1, 2):
                assignments[f"Detail{index}Map"] = graph.vector("SCALE", uv[0], values["DetailData"][index - 1])
    for node in list(graph.nodes):
        if node.bl_idname != "ShaderNodeTexImage":
            continue
        if node.label == "SailsDetailMap":
            continue  # existing measured sails transform group
        coordinates = assignments.get(node.label, uv[0])
        # Perform all authored math first; convert to Blender image space
        # only at the sample. Flipping before tiling changes fractional scales
        # and reverses UV2.y's non-texture selector meaning.
        coordinates = graph.vector("ADD", graph.vector("MULTIPLY", coordinates, (1, -1, 1)), (0, 1, 0))
        graph.links.new(coordinates, node.inputs["Vector"])


def wire_triplanar(graph, values):
    """Three authored projections per texture, with depth's fixed offsets."""
    group = next(n for n in graph.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree.name.startswith("CarbonShader"))
    normal = graph.nodes.new("ShaderNodeAttribute")
    normal.attribute_name = "gr2_triplanar_normal"
    n = normal.outputs["Vector"]
    powers = [graph.math("POWER", value, 4) for value in graph.split(n)]
    total = graph.math("ADD", powers[0], graph.math("ADD", powers[1], powers[2]))
    weights = [graph.math("DIVIDE", value, total) for value in powers]
    graph.bind(n, group.inputs["ProjectionNormal"])
    graph.bind(graph.combine(*weights), group.inputs["ProjectionWeights"])
    position = graph.nodes.new("ShaderNodeAttribute")
    position.attribute_name = "carbon_rest_position"
    basis = graph.nodes.new("ShaderNodeVectorTransform")
    basis.vector_type = "VECTOR"
    basis.convert_from, basis.convert_to = "OBJECT", "WORLD"
    basis.inputs[0].default_value = (1, 0, 0)
    scale = graph.math("DIVIDE", graph.vector("LENGTH", basis.outputs[0]), values["TextureTiling"][0])
    x, y, z = graph.split(graph.vector("SCALE", position.outputs["Vector"], scale))
    projections = [graph.combine(z, y, 0), graph.combine(x, z, 0), graph.combine(x, y, 0)]
    tiling, tiling2, detail = values["TextureTiling"], values["TextureTiling2"], values["DetailData"]
    scales = {"AlbedoMap": tiling2[0], "DirtMap": tiling2[0], "RoughnessMap": tiling[1],
              "MaterialMap": tiling[3], "NormalMap": tiling[2], "DustNoiseMap": 20 * tiling2[1],
              "Detail1Map": detail[0], "Detail2Map": detail[1]}
    for source in list(graph.nodes):
        if source.bl_idname != "ShaderNodeTexImage" or source.label not in scales:
            continue
        name = source.label
        samples = []
        for index, (axis, projection, offset) in enumerate(zip("XYZ", projections, (0, .3300000131, .6700000167))):
            sample = source if index == 0 else graph.nodes.new("ShaderNodeTexImage")
            sample.image = source.image
            sample.label = name + axis
            uv = graph.vector("ADD", graph.vector("SCALE", projection, scales[name]), (offset, offset, 0))
            uv = graph.vector("ADD", graph.vector("MULTIPLY", uv, (1, -1, 1)), (0, 1, 0))
            graph.bind(uv, sample.inputs["Vector"])
            samples.append(sample)
            if name + axis in group.inputs:
                graph.bind(sample.outputs["Color"], group.inputs[name + axis])
        graph.bind(graph.weighted(weights, [s.outputs["Color"] for s in samples], True), group.inputs[name])
        alpha = nodes.DUST_ALPHA if name == "DustNoiseMap" else name.replace("Map", "Alpha")
        if alpha in group.inputs:
            graph.bind(graph.weighted(weights, [s.outputs["Alpha"] for s in samples]), group.inputs[alpha])

