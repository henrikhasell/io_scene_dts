"""Build shapes from scratch in code, write both versions, and read them back.

This is the path a Blender export takes, so it must work without any raw
fields captured from an existing file.
"""

import pytest

from dtslib import (
    PRIM_INDEXED,
    PRIM_TRIANGLES,
    SKIN_MESH,
    Detail,
    DsqFile,
    Material,
    Mesh,
    Node,
    Object,
    ObjectState,
    Primitive,
    Quat16,
    Sequence,
    Shape,
    TSIntegerSet,
    Trigger,
    read_dsq,
    read_shape,
    write_dsq,
    write_shape,
)
from tests.util import assert_shapes_equal


def make_triangle_shape() -> Shape:
    shape = Shape()
    shape.names = ["base", "detail2", "shape", "mat"]
    shape.nodes = [Node(name_index=0, parent_index=-1)]
    shape.default_rotations = [Quat16.identity()]
    shape.default_translations = [(0.0, 0.0, 0.0)]
    mesh = Mesh()
    mesh.verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    mesh.tverts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    mesh.norms = [(0.0, 0.0, 1.0)] * 3
    mesh.primitives = [Primitive(0, 3, 0 | PRIM_TRIANGLES | PRIM_INDEXED)]
    mesh.indices = [0, 1, 2]
    mesh.verts_per_frame = 3
    mesh.bounds = (0.0, 0.0, 0.0, 1.0, 1.0, 0.0)
    mesh.center = (0.5, 0.5, 0.0)
    mesh.radius_int = 1
    shape.meshes = [mesh]
    shape.objects = [Object(name_index=2, num_meshes=1, start_mesh_index=0, node_index=0)]
    shape.object_states = [ObjectState(1.0, 0, 0)]
    shape.details = [Detail(name_index=1, sub_shape_num=0, object_detail_num=0, size=2.0)]
    shape.sub_shape_first_node = [0]
    shape.sub_shape_first_object = [0]
    shape.sub_shape_first_decal = [0]
    shape.sub_shape_num_nodes = [1]
    shape.sub_shape_num_objects = [1]
    shape.sub_shape_num_decals = [0]
    shape.materials = [Material(name="mat")]
    shape.smallest_visible_size = 2.0
    shape.smallest_visible_dl = 0
    shape.radius = 1.0
    shape.tube_radius = 1.0
    shape.center = (0.5, 0.5, 0.0)
    shape.bounds = (0.0, 0.0, 0.0, 1.0, 1.0, 0.0)
    return shape


def make_skinned_shape() -> Shape:
    shape = make_triangle_shape()
    shape.names.append("bone")
    shape.nodes.append(Node(name_index=4, parent_index=0))
    shape.default_rotations.append(Quat16.identity())
    shape.default_translations.append((0.0, 0.0, 1.0))
    shape.sub_shape_num_nodes = [2]
    mesh = shape.meshes[0]
    mesh.mesh_type = SKIN_MESH
    mesh.initial_verts = list(mesh.verts)
    mesh.initial_norms = list(mesh.norms)
    identity16 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    mesh.initial_transforms = [identity16, identity16]
    mesh.vertex_index = [0, 1, 2]
    mesh.bone_index = [0, 0, 1]
    mesh.weight = [1.0, 1.0, 1.0]
    mesh.node_index = [0, 1]
    return shape


def make_animated_shape(with_ground=True) -> Shape:
    shape = make_skinned_shape()
    shape.names.append("walk")
    seq = Sequence(name_index=5)
    seq.num_keyframes = 2
    seq.duration = 1.0
    seq.rotation_matters = TSIntegerSet(0b11)
    seq.translation_matters = TSIntegerSet(0b01)
    seq.base_rotation = 0
    seq.base_translation = 0
    seq.first_trigger = 0
    seq.num_triggers = 1
    shape.node_rotations = [Quat16.identity()] * 4  # 2 nodes x 2 keyframes
    shape.node_translations = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]  # 1 node x 2 kf
    shape.triggers = [Trigger(state=(1 << 31) | 1, pos=0.5)]
    if with_ground:
        seq.first_ground_frame = 0
        seq.num_ground_frames = 2
        shape.ground_translations = [(0.0, 0.5, 0.0), (0.0, 1.0, 0.0)]
        shape.ground_rotations = [Quat16.identity()] * 2
    shape.sequences = [seq]
    return shape


class TestSynthetic:
    @pytest.mark.parametrize("version", [23, 24])
    def test_triangle(self, version):
        shape = make_triangle_shape()
        again = read_shape(write_shape(shape, version))
        assert_shapes_equal(shape, again)
        assert again.source_version == version

    @pytest.mark.parametrize("version", [23, 24])
    def test_skinned(self, version):
        shape = make_skinned_shape()
        again = read_shape(write_shape(shape, version))
        assert_shapes_equal(shape, again)

    def test_animated_with_ground_v24(self):
        shape = make_animated_shape(with_ground=True)
        again = read_shape(write_shape(shape, 24))
        assert_shapes_equal(shape, again)
        assert again.sequences[0].num_ground_frames == 2

    def test_animated_no_ground_v23(self):
        shape = make_animated_shape(with_ground=False)
        again = read_shape(write_shape(shape, 23))
        assert_shapes_equal(shape, again)

    def test_write_is_deterministic(self):
        shape = make_animated_shape()
        assert write_shape(shape, 24) == write_shape(shape, 24)

    def test_synthetic_dsq(self):
        dsq = DsqFile()
        dsq.node_names = ["base", "bone"]
        dsq.num_source_objects = 1
        seq = Sequence()
        seq.num_keyframes = 2
        seq.duration = 0.5
        seq.rotation_matters = TSIntegerSet(0b11)
        seq.translation_matters = TSIntegerSet(0b11)
        dsq.sequences = [seq]
        dsq.sequence_names = ["wave"]
        dsq.node_rotations = [Quat16.identity()] * 4
        dsq.node_translations = [(0.0, 0.0, float(i)) for i in range(4)]
        again = read_dsq(write_dsq(dsq))
        assert again.node_names == dsq.node_names
        assert again.sequences == dsq.sequences
        assert again.sequence_names == dsq.sequence_names
        assert again.node_rotations == dsq.node_rotations
        assert again.node_translations == dsq.node_translations
