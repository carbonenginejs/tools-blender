"""Blender node construction helpers shared by the measured Carbon graphs.

`frontier.Graph` extends these with Frontier's surfaces; `fx` builds EVE's
fxv5 with them directly. This module has no ``bpy`` dependency.
"""


def constant_sockets(member, name):
    constant = member.constants[name]
    if member.annotation(name).is_color or name.endswith("Color"):
        return [(name, "NodeSocketColor", tuple(constant.default[:3]) + (1.0,))]
    if len(constant.default) == 1:
        return [(name, "NodeSocketFloat", constant.default[0])]
    return [(f"{name}.{lane}", "NodeSocketFloat", value)
            for lane, value in zip("xyzw", constant.default)]


class Graph:
    """Small Blender node construction helpers; operations remain explicit."""

    def __init__(self, tree, member, create_io=True):
        self.tree, self.member = tree, member
        self.nodes, self.links = tree.nodes, tree.links
        self.input = self.nodes.new("NodeGroupInput") if create_io else None
        self.output = self.nodes.new("NodeGroupOutput") if create_io else None

    def bind(self, value, socket):
        if hasattr(value, "node"):
            self.links.new(value, socket)
        else:
            if socket.type == "RGBA" and isinstance(value, (tuple, list)) and len(value) == 3:
                value = tuple(value) + (1.0,)
            socket.default_value = value

    def math(self, operation, *values):
        node = self.nodes.new("ShaderNodeMath")
        node.operation = operation
        for value, socket in zip(values, node.inputs):
            self.bind(value, socket)
        return node.outputs[0]

    def vector(self, operation, a, b=None):
        node = self.nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        self.bind(a, node.inputs[0])
        if b is not None:
            self.bind(b, node.inputs["Scale"] if operation == "SCALE" else node.inputs[1])
        return node.outputs["Value"] if operation in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else node.outputs["Vector"]

    def split(self, value):
        node = self.nodes.new("ShaderNodeSeparateXYZ")
        self.bind(value, node.inputs[0])
        return tuple(node.outputs)

    def combine(self, x, y, z):
        node = self.nodes.new("ShaderNodeCombineXYZ")
        for v, socket in zip((x, y, z), node.inputs):
            self.bind(v, socket)
        return node.outputs[0]

    def sat(self, value):
        return self.math("MINIMUM", self.math("MAXIMUM", value, 0), 1)

    def satv(self, value):
        return self.vector("MINIMUM", self.vector("MAXIMUM", value, (0, 0, 0)), (1, 1, 1))

    def mix(self, a, b, factor):
        return self.math("ADD", a, self.math("MULTIPLY", self.math("SUBTRACT", b, a), factor))

    def mixv(self, a, b, factor):
        return self.vector("ADD", a, self.vector("SCALE", self.vector("SUBTRACT", b, a), factor))

    def c(self, name, lane=0):
        outputs = self.input.outputs
        if f"{name}.{'xyzw'[lane]}" in outputs:
            return outputs[f"{name}.{'xyzw'[lane]}"]
        if outputs[name].type == "RGBA":
            return self.split(outputs[name])[lane]
        return outputs[name]

    def color(self, name):
        return self.input.outputs[name]

    def tex(self, name):
        return self.input.outputs[name]

    def red(self, name):
        return self.split(self.tex(name))[0]

    def frame(self, index, negate=False):
        basis = []
        for channel in ("tangent", "binormal", "normal"):
            attribute = self.nodes.new("ShaderNodeAttribute")
            suffix = str(index) if index and channel != "normal" else ""
            attribute.attribute_name = f"gr2_{channel}{suffix}"
            transform = self.nodes.new("ShaderNodeVectorTransform")
            transform.vector_type = "VECTOR"
            transform.convert_from, transform.convert_to = "OBJECT", "WORLD"
            self.bind(attribute.outputs["Vector"], transform.inputs[0])
            # The static donor vertex stage uses direct world3x3, without
            # normalizing individual axes. Structure negates only T and B.
            basis.append(self.vector("SCALE", transform.outputs[0], -1 if negate and channel != "normal" else 1))
        return basis
