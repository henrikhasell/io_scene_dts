import pytest

from dtslib import DtsError, DtsUnsupportedVersion, read_dsq, write_dsq
from tests.conftest import fixture_bytes


class TestDsqRead:
    def test_v24(self):
        dsq = read_dsq(fixture_bytes("v24_dsq_animation.dsq"))
        assert dsq.version == 24
        assert dsq.node_names
        assert len(dsq.sequences) == 1
        assert dsq.sequence_names == ["Swing"]
        seq = dsq.sequences[0]
        assert seq.num_keyframes > 0
        assert seq.duration > 0

    def test_v22_old_layout(self):
        dsq = read_dsq(fixture_bytes("v22_dsq_animation.dsq"))
        assert dsq.version == 22
        assert dsq.sequence_names == ["Swing"]
        assert len(dsq.node_rotations) >= dsq.sequences[0].num_keyframes

    def test_large_v24(self):
        """Twelve sequences over 45 nodes: the case where the per-sequence
        matters sets and the shared node track actually have to be indexed."""
        dsq = read_dsq(fixture_bytes("v24_tutorial_player.dsq"))
        assert len(dsq.sequence_names) == 12
        assert dsq.sequence_names[:3] == ["Back", "Celwave", "Fall"]
        assert len(dsq.node_names) == 45

    def test_empty(self):
        with pytest.raises(DtsError):
            read_dsq(b"")

    def test_future_version(self):
        data = bytearray(fixture_bytes("v24_dsq_animation.dsq"))
        data[0] = 25
        with pytest.raises(DtsUnsupportedVersion):
            read_dsq(bytes(data))


class TestDsqRoundtrip:
    @pytest.mark.parametrize(
        "name",
        ["v24_dsq_animation.dsq", "v24_tutorial_player.dsq", "v22_dsq_animation.dsq"],
    )
    def test_byte_identical(self, name):
        data = fixture_bytes(name)
        dsq = read_dsq(data)
        if dsq.version < 22:
            pytest.skip("write_dsq only writes the modern layout")
        assert write_dsq(dsq, dsq.version) == data

    def test_old_layout_write_refused(self):
        dsq = read_dsq(fixture_bytes("v24_dsq_animation.dsq"))
        with pytest.raises(DtsError):
            write_dsq(dsq, 21)

    def test_v22_structural_reroundtrip(self):
        # a v22 DSQ rewritten as v24 must read back to the same content
        a = read_dsq(fixture_bytes("v22_dsq_animation.dsq"))
        b = read_dsq(write_dsq(a, 24))
        assert b.node_names == a.node_names
        assert b.node_rotations == a.node_rotations
        assert b.node_translations == a.node_translations
        assert b.ground_translations == a.ground_translations
        assert b.ground_rotations == a.ground_rotations
        assert b.sequence_names == a.sequence_names
        assert b.sequences == a.sequences
        assert b.triggers == a.triggers
