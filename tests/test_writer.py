import pytest

from dtslib import (
    SEQ_UNIFORM_SCALE,
    SKIN_MESH,
    DtsWriteError,
    Quat16,
    fit_to_version,
    read_header,
    read_shape,
    strip_ground_frames,
    write_shape,
)
from tests.conftest import fixture_bytes
from tests.util import assert_shapes_equal


ALL_VERSIONS = tuple(range(15, 25))


class TestVersionRefusals:
    def test_bad_versions(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        for v in (0, 14, 25, 26):
            with pytest.raises(DtsWriteError):
                write_shape(shape, v)

    @pytest.mark.parametrize("version", (22, 23))
    def test_ground_frames_refused_where_there_is_no_room(self, version):
        """v22 and v23 are the two with no ground storage at all."""
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        shape.ground_translations = [(0.0, 1.0, 0.0)]
        shape.ground_rotations = [Quat16.identity()]
        with pytest.raises(DtsWriteError) as e:
            write_shape(shape, version)
        assert "ground frame" in str(e.value)

    def test_v24_with_ground_frames_ok(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        shape.ground_translations = [(0.0, 1.0, 0.0)]
        shape.ground_rotations = [Quat16.identity()]
        data = write_shape(shape, 24)
        again = read_shape(data)
        assert again.ground_translations == [(0.0, 1.0, 0.0)]

    def test_strip_ground_frames(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        shape.ground_translations = [(0.0, 1.0, 0.0)]
        shape.ground_rotations = [Quat16.identity()]
        if shape.sequences:
            shape.sequences[0].num_ground_frames = 1
        strip_ground_frames(shape)
        assert not shape.ground_translations
        assert all(s.num_ground_frames == 0 for s in shape.sequences)
        write_shape(shape, 23)  # now succeeds


class TestFitToVersion:
    """The exporter's half of the refusal: drop what the version cannot hold,
    and hand back something to put in front of the user."""

    def _with_ground(self, n=2):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        shape.ground_translations = [(0.0, float(i), 0.0) for i in range(n)]
        shape.ground_rotations = [Quat16.identity()] * n
        for seq in shape.sequences:
            seq.num_ground_frames = n
        return shape

    def test_v23_drops_ground_frames_and_says_how_many(self):
        shape = self._with_ground(2)
        warnings = fit_to_version(shape, 23)
        assert len(warnings) == 1
        assert "2 ground frame(s)" in warnings[0]
        assert "v24" in warnings[0]  # tells the user what would keep them
        assert not shape.ground_translations
        assert all(s.num_ground_frames == 0 for s in shape.sequences)
        write_shape(shape, 23)  # and the shape now fits

    def test_v24_keeps_them_and_says_nothing(self):
        shape = self._with_ground(2)
        assert fit_to_version(shape, 24) == []
        assert len(shape.ground_translations) == 2

    def test_a_shape_without_ground_frames_is_left_alone(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        assert not shape.ground_translations
        assert fit_to_version(shape, 23) == []

    def _animated_with_ground(self, n=2):
        """Ground frames only survive a round trip if a sequence claims them --
        the pre-v22 layout has no way to name an orphan."""
        shape = read_shape(fixture_bytes("v24_woodDoor01.dts"))
        assert shape.sequences
        shape.ground_translations = [(0.0, float(i), 0.0) for i in range(n)]
        shape.ground_rotations = [Quat16.identity()] * n
        shape.sequences[0].first_ground_frame = 0
        shape.sequences[0].num_ground_frames = n
        return shape

    def test_v21_keeps_ground_frames_in_the_node_array(self):
        """The layout v22 and v23 lost: ground frames at the end of the node
        array, addressed past the shape's default transforms."""
        shape = self._animated_with_ground(2)
        assert fit_to_version(shape, 21) == []
        assert len(shape.ground_translations) == 2
        again = read_shape(write_shape(shape, 21))
        assert again.ground_translations == shape.ground_translations
        assert again.ground_rotations == shape.ground_rotations
        assert [s.num_ground_frames for s in again.sequences] == [
            s.num_ground_frames for s in shape.sequences
        ]

    def test_v21_drops_scale_animation(self):
        shape = read_shape(fixture_bytes("v24_woodDoor01.dts"))
        shape.node_uniform_scales = [1.0, 2.0]
        shape.sequences[0].flags |= SEQ_UNIFORM_SCALE
        shape.sequences[0].scale_matters.set(0)
        with pytest.raises(DtsWriteError) as e:
            write_shape(shape, 21)
        assert "scale" in str(e.value)
        warnings = fit_to_version(shape, 21)
        assert len(warnings) == 1 and "scale key(s)" in warnings[0]
        assert not shape.node_uniform_scales
        assert not shape.sequences[0].flags & SEQ_UNIFORM_SCALE
        write_shape(shape, 21)

    def test_v21_pairs_node_tracks_without_losing_animation(self):
        """Pre-v22 a node state is a rotation *and* a translation.  A shape that
        animates them for different nodes gets the redundancy filled in from the
        rest pose, so every original track survives at its original ordinal."""
        shape = read_shape(fixture_bytes("v22_station_teleport.dts"))
        seq = next(s for s in shape.sequences if s.translation_matters.count())
        before = {
            node: shape.node_translations[
                seq.base_translation + o * seq.num_keyframes :
                seq.base_translation + (o + 1) * seq.num_keyframes
            ]
            for o, node in enumerate(seq.translation_matters.indices())
        }
        assert seq.rotation_matters != seq.translation_matters
        assert fit_to_version(shape, 21) == []
        assert seq.rotation_matters == seq.translation_matters
        for o, node in enumerate(seq.translation_matters.indices()):
            if node not in before:
                continue
            start = seq.base_translation + o * seq.num_keyframes
            assert shape.node_translations[start : start + seq.num_keyframes] == before[node]

    def test_v18_drops_merge_indices(self):
        shape = read_shape(fixture_bytes("v23_bioderm_light.dts"))
        assert any(m.merge_indices for m in shape.meshes if m is not None)
        with pytest.raises(DtsWriteError) as e:
            write_shape(shape, 18)
        assert "merge-index" in str(e.value)
        warnings = fit_to_version(shape, 18)
        assert any("merge-index" in w for w in warnings)
        assert not any(m.merge_indices for m in shape.meshes if m is not None)

    def test_v18_drops_decal_texgens(self):
        shape = read_shape(fixture_bytes("v22_turret_belly_barrell.dts"))
        assert any(m.decal_data and m.decal_data.texgen_s for m in shape.meshes if m)
        warnings = fit_to_version(shape, 18)
        assert any("texture-generation" in w for w in warnings)
        # ...and v19, one version up, keeps them
        shape = read_shape(fixture_bytes("v22_turret_belly_barrell.dts"))
        assert not any("texture-generation" in w for w in fit_to_version(shape, 19))

    def test_fit_is_idempotent(self):
        """Fitting twice must not double up the ground frames it folds into the
        node array, or shift a base index a second time."""
        for version in ALL_VERSIONS:
            once = read_shape(fixture_bytes("v24_woodDoor01.dts"))
            fit_to_version(once, version)
            twice = read_shape(fixture_bytes("v24_woodDoor01.dts"))
            fit_to_version(twice, version)
            fit_to_version(twice, version)
            assert_shapes_equal(once, twice)


class TestEveryVersion:
    """Every version this library writes, from every fixture: what comes back
    out is what fit_to_version said would go in."""

    NAMES = [
        "v15_chaingun_shot.dts",
        "v16_borg11.dts",
        "v18_octahedron.dts",
        "v19_turret_muzzlepoint.dts",
        "v19_vehicle_air_scout_wreck.dts",
        "v19_xorg20.dts",
        "v21_weapon_energy.dts",
        "v22_energy_explosion.dts",
        "v22_station_teleport.dts",
        "v22_turret_belly_barrell.dts",
        "v23_bioderm_light.dts",
        "v24_ammo.dts",
        "v24_w_sqknest.dts",
        "v24_woodDoor01.dts",
    ]

    @pytest.mark.parametrize("version", ALL_VERSIONS)
    @pytest.mark.parametrize("name", NAMES)
    def test_roundtrip(self, name, version):
        shape = read_shape(fixture_bytes(name))
        fit_to_version(shape, version)
        data = write_shape(shape, version)
        assert read_header(data)[0] == version
        assert_shapes_equal(shape, read_shape(data))

    @pytest.mark.parametrize("version", ALL_VERSIONS)
    def test_skins_survive_every_version(self, version):
        """Pre-v23 files keep skins in a section of their own, which has nowhere
        for the object's name or node.  Writing them into the mesh list instead
        is read correctly by every version and keeps both."""
        shape = read_shape(fixture_bytes("v24_w_sqknest.dts"))
        fit_to_version(shape, version)
        skinned = [
            (i, shape.name(o.name_index), o.node_index)
            for i, o in enumerate(shape.objects)
            if any(
                (m := shape.meshes[o.start_mesh_index + k]) is not None
                and m.mesh_type == SKIN_MESH
                for k in range(o.num_meshes)
            )
        ]
        assert skinned, "fixture has no skinned object"
        again = read_shape(write_shape(shape, version))
        assert len(again.objects) == len(shape.objects)
        for i, name, node in skinned:
            assert again.name(again.objects[i].name_index) == name
            assert again.objects[i].node_index == node


class TestFixtureRoundtrips:
    @pytest.mark.parametrize(
        "name,version",
        [
            ("v18_octahedron.dts", 18),
            ("v19_turret_muzzlepoint.dts", 19),
            ("v19_xorg20.dts", 19),
            ("v21_weapon_energy.dts", 21),
            ("v21_xorg21.dts", 21),
            ("v22_porg1.dts", 22),
            ("v22_energy_explosion.dts", 22),
            ("v22_station_teleport.dts", 22),
            ("v22_turret_belly_barrell.dts", 22),
            ("v23_weapon_energy_vehicle.dts", 23),
            ("v23_pack_upgrade_shield.dts", 23),
            ("v23_bioderm_light.dts", 23),
            ("v24_octahedron.dts", 24),
            ("v24_woodDoor01.dts", 24),
            ("v24_shrub.dts", 24),
            ("v24_ammo.dts", 24),
            ("v24_w_sqknest.dts", 24),
        ],
    )
    def test_byte_identical(self, name, version):
        data = fixture_bytes(name)
        shape = read_shape(data)
        assert write_shape(shape, version, shape.exporter_version) == data

    @pytest.mark.parametrize(
        "name",
        [
            "v19_turret_muzzlepoint.dts",
            "v19_weapon_chaingun_ammocasing.dts",
            "v19_xorg20.dts",
            "v21_xorg21.dts",
            "v22_porg1.dts",
            "v22_porg5.dts",
            "v22_energy_explosion.dts",
            "v22_turret_belly_barrell.dts",
        ],
    )
    def test_old_version_structural(self, name):
        shape = read_shape(fixture_bytes(name))
        target = 24 if shape.ground_translations else 23
        out = write_shape(shape, target)
        assert_shapes_equal(shape, read_shape(out))

    def test_upconvert_v23_to_v24(self):
        shape = read_shape(fixture_bytes("v23_pack_upgrade_shield.dts"))
        out = write_shape(shape, 24)
        again = read_shape(out)
        assert again.source_version == 24
        assert_shapes_equal(shape, again)

    def test_exporter_version_stamped(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        out = write_shape(shape, 24, exporter_version=99)
        assert read_shape(out).exporter_version == 99
