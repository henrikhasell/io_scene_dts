"""In-memory model of a Torque DTS shape and DSQ sequence file.

The model is version-agnostic: readers normalize old files (pre-v23 skin
sections, pre-v22 ground-frames-in-node-arrays) into the v24-style layout on
load, so a Shape always looks like a modern shape in memory.

Fields marked "runtime" occupy space in the file but are recomputed by the
engine at load; they are preserved verbatim so rewrites are byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .primitives import Quat16, TSIntegerSet

# mesh types (tsMesh.h)
STANDARD_MESH = 0
SKIN_MESH = 1
DECAL_MESH = 2
SORTED_MESH = 3
NULL_MESH = 4

# TSDrawPrimitive matIndex bits
PRIM_TRIANGLES = 0 << 30
PRIM_STRIP = 1 << 30
PRIM_FAN = 2 << 30
PRIM_INDEXED = 1 << 29
PRIM_NO_MATERIAL = 1 << 28
PRIM_TYPE_MASK = 3 << 30
PRIM_MATERIAL_MASK = 0x0FFFFFFF

# mesh flags (stored OR'd with the mesh type)
MESH_BILLBOARD = 1 << 31
MESH_HAS_DETAIL_TEXTURE = 1 << 30
MESH_BILLBOARD_Z_AXIS = 1 << 29
MESH_USE_ENCODED_NORMALS = 1 << 28

# sequence flags (tsShape.h)
SEQ_UNIFORM_SCALE = 1 << 0
SEQ_ALIGNED_SCALE = 1 << 1
SEQ_ARBITRARY_SCALE = 1 << 2
SEQ_BLEND = 1 << 3
SEQ_CYCLIC = 1 << 4
SEQ_MAKE_PATH = 1 << 5
SEQ_IFL_INIT = 1 << 6
SEQ_HAS_TRANSLUCENCY = 1 << 7
SEQ_ANY_SCALE = SEQ_UNIFORM_SCALE | SEQ_ALIGNED_SCALE | SEQ_ARBITRARY_SCALE

# trigger state bits
TRIGGER_STATE_ON = 1 << 31
TRIGGER_INVERT_ON_REVERSE = 1 << 30

# material flags (tsShape.h TSMaterialList)
MAT_S_WRAP = 1 << 0
MAT_T_WRAP = 1 << 1
MAT_TRANSLUCENT = 1 << 2
MAT_ADDITIVE = 1 << 3
MAT_SUBTRACTIVE = 1 << 4
MAT_SELF_ILLUMINATING = 1 << 5
MAT_NEVER_ENV_MAP = 1 << 6
MAT_NO_MIP_MAP = 1 << 7
MAT_MIP_MAP_ZERO_BORDER = 1 << 8
MAT_IFL_MATERIAL = 1 << 27
MAT_IFL_FRAME = 1 << 28
MAT_DETAIL_MAP_ONLY = 1 << 29
MAT_BUMP_MAP_ONLY = 1 << 30
MAT_REFLECTANCE_MAP_ONLY = 1 << 31

NO_MAP = 0xFFFFFFFF

DTS_EXPORTER_CURRENT_VERSION = 124


@dataclass
class Node:
    name_index: int
    parent_index: int
    # runtime: firstObject, firstChild, nextSibling
    runtime: tuple[int, int, int] = (-1, -1, -1)


@dataclass
class Object:
    name_index: int
    num_meshes: int
    start_mesh_index: int
    node_index: int
    # runtime: nextSibling, firstDecal
    runtime: tuple[int, int] = (-1, -1)


@dataclass
class Decal:
    """Preserved opaquely: nameIndex, numMeshes, startMeshIndex, objectIndex, nextSibling."""

    raw: tuple[int, int, int, int, int]


@dataclass
class IflMaterial:
    """nameIndex, materialSlot, firstFrame, firstFrameOffTimeIndex, numFrames."""

    raw: tuple[int, int, int, int, int]


@dataclass
class Detail:
    name_index: int
    sub_shape_num: int
    object_detail_num: int
    size: float
    average_error: float = -1.0
    max_error: float = -1.0
    poly_count: int = 0


@dataclass
class ObjectState:
    vis: float
    frame_index: int
    mat_frame_index: int


@dataclass
class Trigger:
    state: int  # U32: state bit | StateOn | InvertOnReverse
    pos: float


@dataclass
class Primitive:
    start: int
    num_elements: int
    mat_index: int  # material index | type bits


@dataclass
class SortedData:
    """TSSortedMesh extension payload (tsSortedMesh.cc), preserved for re-emission."""

    clusters: list[tuple] = field(default_factory=list)  # 8 words each:
    # (startPrimitive, endPrimitive, normal x/y/z (f), k (f), frontCluster, backCluster)
    start_cluster: list[int] = field(default_factory=list)
    first_verts: list[int] = field(default_factory=list)
    num_verts: list[int] = field(default_factory=list)
    first_tverts: list[int] = field(default_factory=list)
    always_write_depth: int = 0


@dataclass
class DecalMeshData:
    """TSDecalMesh payload (tsDecal.cc, v>=20 layout), preserved for re-emission."""

    primitives: list[Primitive] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    start_primitive: list[int] = field(default_factory=list)
    texgen_s: list[tuple[float, float, float, float]] = field(default_factory=list)
    texgen_t: list[tuple[float, float, float, float]] = field(default_factory=list)
    material_index: int = 0


@dataclass
class Mesh:
    mesh_type: int = STANDARD_MESH
    num_frames: int = 1
    num_mat_frames: int = 1
    parent_mesh: int = -1
    bounds: tuple = (0.0,) * 6  # min xyz, max xyz
    center: tuple = (0.0, 0.0, 0.0)
    radius_int: int = 0  # mesh radius is stored as (S32)mRadius — an integer!
    verts: list[tuple[float, float, float]] = field(default_factory=list)
    tverts: list[tuple[float, float]] = field(default_factory=list)
    norms: list[tuple[float, float, float]] = field(default_factory=list)
    encoded_norms: bytes = b""  # one U8 per vert, present on disk for v>21
    primitives: list[Primitive] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    merge_indices: list[int] = field(default_factory=list)
    verts_per_frame: int = 0
    flags: int = 0  # raw trailing flags word (includes type bits as stored)
    # skin extension (mesh_type == SKIN_MESH)
    initial_verts: list[tuple[float, float, float]] = field(default_factory=list)
    initial_norms: list[tuple[float, float, float]] = field(default_factory=list)
    initial_encoded_norms: bytes = b""
    initial_transforms: list[tuple] = field(default_factory=list)  # 16 floats each
    vertex_index: list[int] = field(default_factory=list)
    bone_index: list[int] = field(default_factory=list)
    weight: list[float] = field(default_factory=list)
    node_index: list[int] = field(default_factory=list)
    # decal / sorted extensions
    sorted_data: SortedData | None = None
    decal_data: DecalMeshData | None = None


@dataclass
class Sequence:
    name_index: int = -1
    flags: int = 0
    num_keyframes: int = 0
    duration: float = 0.0
    priority: int = 0
    first_ground_frame: int = 0
    num_ground_frames: int = 0
    base_rotation: int = 0
    base_translation: int = 0
    base_scale: int = 0
    base_object_state: int = 0
    base_decal_state: int = 0
    first_trigger: int = 0
    num_triggers: int = 0
    tool_begin: float = 0.0
    rotation_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    translation_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    scale_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    decal_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    ifl_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    vis_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    frame_matters: TSIntegerSet = field(default_factory=TSIntegerSet)
    mat_frame_matters: TSIntegerSet = field(default_factory=TSIntegerSet)

    def animates_uniform_scale(self) -> bool:
        return bool(self.flags & SEQ_UNIFORM_SCALE)

    def animates_aligned_scale(self) -> bool:
        return bool(self.flags & SEQ_ALIGNED_SCALE)

    def animates_arbitrary_scale(self) -> bool:
        return bool(self.flags & SEQ_ARBITRARY_SCALE)

    def is_cyclic(self) -> bool:
        return bool(self.flags & SEQ_CYCLIC)

    def is_blend(self) -> bool:
        return bool(self.flags & SEQ_BLEND)


@dataclass
class Material:
    name: str  # as stored (may contain a legacy path prefix)
    flags: int = MAT_S_WRAP | MAT_T_WRAP
    reflectance_map: int = NO_MAP
    bump_map: int = NO_MAP
    detail_map: int = NO_MAP
    detail_scale: float = 1.0
    reflection_amount: float = 1.0

    @property
    def basename(self) -> str:
        """Material name with any legacy Tribes-tool path stripped (engine behavior)."""
        n = self.name
        for sep in ("/", "\\"):
            if sep in n:
                n = n.rsplit(sep, 1)[1]
        return n


@dataclass
class Shape:
    # header-ish
    smallest_visible_size: float = 0.0  # stored as (S32) cast — integral
    smallest_visible_dl: int = 0
    radius: float = 0.0
    tube_radius: float = 0.0
    center: tuple = (0.0, 0.0, 0.0)
    bounds: tuple = (0.0,) * 6
    # tables
    names: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    objects: list[Object] = field(default_factory=list)
    decals: list[Decal] = field(default_factory=list)
    ifl_materials: list[IflMaterial] = field(default_factory=list)
    details: list[Detail] = field(default_factory=list)
    sub_shape_first_node: list[int] = field(default_factory=list)
    sub_shape_first_object: list[int] = field(default_factory=list)
    sub_shape_first_decal: list[int] = field(default_factory=list)
    sub_shape_num_nodes: list[int] = field(default_factory=list)
    sub_shape_num_objects: list[int] = field(default_factory=list)
    sub_shape_num_decals: list[int] = field(default_factory=list)
    default_rotations: list[Quat16] = field(default_factory=list)
    default_translations: list[tuple] = field(default_factory=list)
    node_rotations: list[Quat16] = field(default_factory=list)
    node_translations: list[tuple] = field(default_factory=list)
    node_uniform_scales: list[float] = field(default_factory=list)
    node_aligned_scales: list[tuple] = field(default_factory=list)
    node_arbitrary_scale_factors: list[tuple] = field(default_factory=list)
    node_arbitrary_scale_rots: list[Quat16] = field(default_factory=list)
    ground_translations: list[tuple] = field(default_factory=list)
    ground_rotations: list[Quat16] = field(default_factory=list)
    object_states: list[ObjectState] = field(default_factory=list)
    decal_states: list[int] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    meshes: list[Mesh | None] = field(default_factory=list)  # None == null mesh
    sequences: list[Sequence] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    # provenance
    source_version: int | None = None
    exporter_version: int = DTS_EXPORTER_CURRENT_VERSION
    # original files pad the 16-/8-bit buffers to dwords with uninitialized
    # bytes; captured at read time so rewrites are byte-identical
    pad16: bytes = b""
    pad8: bytes = b""

    # -- name helpers -------------------------------------------------
    def name(self, i: int) -> str:
        return self.names[i] if 0 <= i < len(self.names) else ""

    def find_name(self, s: str) -> int:
        """Case-insensitive first match, like TSShape::findName."""
        low = s.lower()
        for i, n in enumerate(self.names):
            if n.lower() == low:
                return i
        return -1

    def add_name(self, s: str) -> int:
        i = self.find_name(s)
        if i < 0:
            i = len(self.names)
            self.names.append(s)
        return i

    def find_node(self, s: str) -> int:
        idx = self.find_name(s)
        if idx < 0:
            return -1
        for i, n in enumerate(self.nodes):
            if n.name_index == idx:
                return i
        return -1

    def find_sequence(self, s: str) -> int:
        idx = self.find_name(s)
        if idx < 0:
            return -1
        for i, seq in enumerate(self.sequences):
            if seq.name_index == idx:
                return i
        return -1

    def node_name(self, node_index: int) -> str:
        return self.name(self.nodes[node_index].name_index)


@dataclass
class DsqFile:
    """A standalone sequence file (TSShape::exportSequences layout)."""

    version: int = 24
    node_names: list[str] = field(default_factory=list)
    num_source_objects: int = 0  # source shape's object count (rebasing aid)
    node_rotations: list[Quat16] = field(default_factory=list)
    node_translations: list[tuple] = field(default_factory=list)
    node_uniform_scales: list[float] = field(default_factory=list)
    node_aligned_scales: list[tuple] = field(default_factory=list)
    node_arbitrary_scale_rots: list[Quat16] = field(default_factory=list)
    node_arbitrary_scale_factors: list[tuple] = field(default_factory=list)
    ground_translations: list[tuple] = field(default_factory=list)
    ground_rotations: list[Quat16] = field(default_factory=list)
    sequences: list[Sequence] = field(default_factory=list)
    sequence_names: list[str] = field(default_factory=list)  # parallel to sequences
    triggers: list[Trigger] = field(default_factory=list)
