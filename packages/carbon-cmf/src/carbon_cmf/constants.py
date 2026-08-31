"""CMF v1 constants shared by the reader and graph projection."""

FILE_SIGNATURE = 0x66666D63
FILE_VERSION = 1

STRUCT_SIZE = {
    "BufferView": 16,
    "VertexElement": 8,
    "MeshArea": 64,
    "LodMeshArea": 8,
    "BoneBinding": 40,
    "MorphTarget": 24,
    "MorphTargets": 32,
    "LodMorphTarget": 16,
    "MeshLod": 72,
    "AudioOcclusionMesh": 56,
    "Transform": 40,
    "BoneWeight": 8,
    "BoneMask": 32,
    "Skeleton": 96,
    "AnimationChannel": 24,
    "AnimationCurve": 40,
    "Animation": 56,
    "MetadataEntry": 32,
    "Metadata": 16,
    "Section": 16,
    "Header": 32,
    "Data": 48,
    "Mesh": 216,
}
USAGE = (
    "Position",
    "Normal",
    "Tangent",
    "Binormal",
    "TexCoord",
    "Color",
    "BoneIndices",
    "BoneWeights",
    "PackedTangent",
    "PackedTangentLegacy",
)

ELEMENT_TYPE = (
    "Float32",
    "Float16",
    "UInt16Norm",
    "UInt16",
    "Int16Norm",
    "Int16",
    "UInt8Norm",
    "UInt8",
    "Int8Norm",
    "Int8",
)

MESH_TOPOLOGY = ("TriangleList", "PointList")
SECTION_TYPE = ("Data", "GpuBuffer", "Metadata")
SECTION_COMPRESSION = ("None", "MeshOptimizerVertexBuffer", "MeshOptimizerIndexBuffer")
ANIMATION_CHANNEL_TARGET_TYPE = (
    "BonePosition",
    "BoneRotation",
    "BoneScale",
    "MorphTarget",
    "Other",
)
INTERPOLATION = ("Step", "Linear")

ELEMENT_TYPE_SIZE = {
    "Float32": 4,
    "Float16": 2,
    "UInt16Norm": 2,
    "UInt16": 2,
    "Int16Norm": 2,
    "Int16": 2,
    "UInt8Norm": 1,
    "UInt8": 1,
    "Int8Norm": 1,
    "Int8": 1,
}
