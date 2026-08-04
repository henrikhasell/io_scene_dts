import pytest

from dtslib import (
    DtsWriteError,
    Quat16,
    read_shape,
    strip_ground_frames,
    write_shape,
)
from tests.conftest import fixture_bytes
from tests.util import assert_shapes_equal


class TestVersionRefusals:
    def test_bad_versions(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        for v in (0, 19, 22, 25, 26):
            with pytest.raises(DtsWriteError):
                write_shape(shape, v)

    def test_v23_with_ground_frames_refused(self):
        shape = read_shape(fixture_bytes("v24_octahedron.dts"))
        shape.ground_translations = [(0.0, 1.0, 0.0)]
        shape.ground_rotations = [Quat16.identity()]
        with pytest.raises(DtsWriteError) as e:
            write_shape(shape, 23)
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


class TestFixtureRoundtrips:
    @pytest.mark.parametrize(
        "name,version",
        [
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
