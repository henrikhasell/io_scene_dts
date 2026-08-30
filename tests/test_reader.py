import pytest

from dtslib import (
    SKIN_MESH,
    SORTED_MESH,
    DtsError,
    DtsUnsupportedVersion,
    read_header,
    read_shape,
    read_shape_file,
)
from tests.conftest import fixture, fixture_bytes


class TestHeader:
    def test_versions(self):
        assert read_header(fixture_bytes("v19_test_crate.dts"))[0] == 19
        assert read_header(fixture_bytes("v22_test_crate.dts"))[0] == 22
        assert read_header(fixture_bytes("v23_crt_monitor.dts"))[0] == 23
        ver, exporter = read_header(fixture_bytes("v24_test_crate.dts"))
        assert ver == 24
        assert exporter > 0

    def test_too_short(self):
        with pytest.raises(DtsError):
            read_header(b"ab")


class TestKeyframeEra:
    """v15-16 store animation keyframe-major, indexed through a table the
    engine reads and throws away.  Getting the transpose wrong does not fail
    loudly, so these check the shape of the result rather than just that it
    parsed."""

    def test_v15_parses(self):
        shape = read_shape(fixture_bytes("v15_sequence_triggers.dts"))
        assert shape.source_version == 15
        assert len(shape.nodes) == 2
        assert len(shape.sequences) == 1
        seq = shape.sequences[0]
        # the base indices are not in a v15 sequence record: they came out of
        # the keyframe table, rebased past the default node transforms
        assert seq.num_keyframes == 12
        assert seq.base_rotation == seq.base_translation == 0
        # one rotation and one translation per animated node per keyframe
        assert len(shape.node_rotations) == seq.num_keyframes * seq.rotation_matters.count()
        assert len(shape.node_rotations) == len(shape.node_translations)

    def test_v16_null_mesh_type_words(self):
        """v16 marks an empty mesh slot with a type word rather than leaving it
        out of a mesh index list, which is the difference from v15."""
        shape = read_shape(fixture_bytes("v16_detail_levels.dts"))
        assert shape.source_version == 16
        assert len(shape.details) == 5
        assert any(m is None for m in shape.meshes)
        assert any(m is not None for m in shape.meshes)

    def test_v16_sorted_mesh(self):
        shape = read_shape(fixture_bytes("v16_sorted_foliage.dts"))
        assert shape.source_version == 16
        assert [m is not None for m in shape.meshes].count(True) == len(shape.meshes)
        assert any(m.mesh_type == SORTED_MESH for m in shape.meshes)

    def test_v15_mesh_index_list(self):
        """v15 lists its mesh slots separately; every slot in this one is filled,
        and the object mesh ranges have to land on real meshes."""
        shape = read_shape(fixture_bytes("v15_sequence_triggers.dts"))
        for obj in shape.objects:
            for i in range(obj.num_meshes):
                assert shape.meshes[obj.start_mesh_index + i] is not None


class TestUnsupportedVersions:
    def test_pre_keyframe_era_refused(self):
        data = bytearray(fixture_bytes("v15_sequence_triggers.dts"))
        data[0] = 14
        with pytest.raises(DtsUnsupportedVersion) as e:
            read_shape(bytes(data))
        assert e.value.version == 14

    def test_v18_old_format_parses(self):
        shape = read_shape(fixture_bytes("v18_test_crate.dts"))
        assert shape.source_version == 18
        assert shape.nodes
        assert shape.meshes
        mesh = next(m for m in shape.meshes if m is not None)
        assert mesh.verts
        # bounds were recomputed (old files carry none)
        assert mesh.bounds != (0.0,) * 6

    def test_future_version_refused(self):
        data = bytearray(fixture_bytes("v24_test_crate.dts"))
        data[0] = 25
        with pytest.raises(DtsUnsupportedVersion):
            read_shape(bytes(data))

    def test_truncated(self):
        with pytest.raises(DtsError):
            read_shape(fixture_bytes("v24_test_crate.dts")[:100])

    def test_empty(self):
        with pytest.raises(DtsError):
            read_shape(b"")


class TestBasicStructure:
    def test_v24_test_crate(self):
        shape = read_shape_file(fixture("v24_test_crate.dts"))
        assert shape.source_version == 24
        assert len(shape.nodes) >= 1
        assert len(shape.objects) >= 1
        assert len(shape.details) >= 1
        assert len(shape.meshes) >= 1
        assert len(shape.names) > 0
        assert len(shape.default_rotations) == len(shape.nodes)
        assert len(shape.default_translations) == len(shape.nodes)
        # every object's name resolves
        for o in shape.objects:
            assert 0 <= o.name_index < len(shape.names)

    def test_v23_animated(self):
        shape = read_shape_file(fixture("v23_crt_monitor.dts"))
        assert shape.sequences
        for seq in shape.sequences:
            assert 0 <= seq.name_index < len(shape.names)
            assert seq.num_keyframes >= 0
            assert seq.duration >= 0

    def test_v24_skinned(self):
        shape = read_shape_file(fixture("v24_skin_animation.dts"))
        skins = [m for m in shape.meshes if m and m.mesh_type == SKIN_MESH]
        assert skins
        for skin in skins:
            if skin.parent_mesh < 0:
                assert len(skin.vertex_index) == len(skin.bone_index) == len(skin.weight)
                assert skin.node_index
                for ni in skin.node_index:
                    assert 0 <= ni < len(shape.nodes)
                for bi in skin.bone_index:
                    assert 0 <= bi < len(skin.node_index)

    def test_v23_decal_mesh_variety(self):
        # a decal-bearing shape is node-rigged: standard meshes and decal
        # meshes side by side in one mesh list, no skins
        shape = read_shape_file(fixture("v23_decals.dts"))
        types = {(-1 if m is None else m.mesh_type) for m in shape.meshes}
        assert types == {0, 2}
        assert shape.sequences
        assert any(m and m.decal_data for m in shape.meshes)

    def test_v19_sorted(self):
        shape = read_shape_file(fixture("v19_sorted_foliage.dts"))
        assert any(m and m.mesh_type == SORTED_MESH for m in shape.meshes)

    def test_v22_skin_section_normalized(self):
        # pre-v23 files keep skins in a separate section; after fixup the
        # object list must cover every mesh slot consistently
        shape = read_shape_file(fixture("v22_skin_animation.dts"))
        assert any(m and m.mesh_type == SKIN_MESH for m in shape.meshes)
        for o in shape.objects:
            assert o.start_mesh_index + o.num_meshes <= len(shape.meshes)

    def test_v22_decal(self):
        shape = read_shape_file(fixture("v22_decals.dts"))
        assert shape.decals or any(m and m.decal_data for m in shape.meshes)

    def test_materials(self):
        shape = read_shape_file(fixture("v24_crt_monitor.dts"))
        assert shape.materials
        # this one's names carry a directory prefix, which is what basename is for
        assert any("\\" in m.name for m in shape.materials)
        for m in shape.materials:
            assert m.name
            assert m.basename == m.name.replace("\\", "/").rsplit("/", 1)[-1]

    def test_name_helpers(self):
        shape = read_shape_file(fixture("v23_crt_monitor.dts"))
        first_node_name = shape.node_name(0)
        assert shape.find_node(first_node_name) == 0
        assert shape.find_node(first_node_name.upper()) == 0  # case-insensitive
        assert shape.find_node("no_such_node") == -1
        seq_name = shape.name(shape.sequences[0].name_index)
        assert shape.find_sequence(seq_name) == 0

    def test_add_name_dedup(self):
        shape = read_shape_file(fixture("v24_test_crate.dts"))
        n = len(shape.names)
        existing = shape.names[0]
        assert shape.add_name(existing.upper()) == 0
        assert len(shape.names) == n
        assert shape.add_name("brand_new_name") == n
        assert len(shape.names) == n + 1
