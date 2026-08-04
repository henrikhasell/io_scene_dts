import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dtslib.primitives import (
    MAX_TS_SET_DWORDS,
    MAX_TS_SET_SIZE,
    Quat16,
    TSIntegerSet,
    bits_to_f32,
    f32_to_bits,
    to_signed16,
    to_signed32,
)


class TestBitCast:
    def test_f32_roundtrip(self):
        for f in (0.0, 1.0, -1.0, 3.14159, 1e30, -1e-30):
            assert bits_to_f32(f32_to_bits(f)) == struct.unpack("<f", struct.pack("<f", f))[0]

    def test_known_values(self):
        assert f32_to_bits(1.0) == 0x3F800000
        assert bits_to_f32(0x3F800000) == 1.0
        assert f32_to_bits(-1.0) == to_signed32(0xBF800000)

    @given(st.integers(0, 0xFFFFFFFF))
    def test_bits_roundtrip(self, i):
        f = bits_to_f32(i)
        if f == f:  # skip NaN payload variations
            assert f32_to_bits(f) & 0xFFFFFFFF == i

    def test_to_signed(self):
        assert to_signed32(0xFFFFFFFF) == -1
        assert to_signed32(0x7FFFFFFF) == 0x7FFFFFFF
        assert to_signed16(0xFFFF) == -1
        assert to_signed16(0x7FFF) == 0x7FFF


class TestQuat16:
    def test_identity(self):
        q = Quat16.identity()
        assert q.to_floats() == (0.0, 0.0, 0.0, 1.0)

    def test_truncation_not_rounding(self):
        # the engine casts (S16)(f * 0x7fff), truncating toward zero
        q = Quat16.from_floats(0.99999, 0, 0, -0.99999)
        assert q.x == int(0.99999 * 0x7FFF)
        assert q.w == int(-0.99999 * 0x7FFF)

    def test_extremes(self):
        q = Quat16.from_floats(1.0, -1.0, 0.0, 0.0)
        assert q.x == 0x7FFF
        assert q.y == -0x7FFF

    @given(st.floats(-1, 1), st.floats(-1, 1), st.floats(-1, 1), st.floats(-1, 1))
    def test_roundtrip_error_bounded(self, x, y, z, w):
        q = Quat16.from_floats(x, y, z, w)
        fx, fy, fz, fw = q.to_floats()
        for a, b in zip((x, y, z, w), (fx, fy, fz, fw)):
            assert abs(a - b) < 1.0 / 0x7FFF + 1e-9

    def test_normalized_floats_zero(self):
        assert Quat16(0, 0, 0, 0).normalized_floats() == (0.0, 0.0, 0.0, 1.0)

    def test_normalized_floats(self):
        x, y, z, w = Quat16.from_floats(0.5, 0.5, 0.5, 0.5).normalized_floats()
        assert abs(x * x + y * y + z * z + w * w - 1.0) < 1e-6


class TestTSIntegerSet:
    def test_empty(self):
        s = TSIntegerSet()
        assert s.count() == 0
        assert s.trimmed_dwords() == 0
        assert list(s.indices()) == []

    def test_set_test_clear(self):
        s = TSIntegerSet()
        s.set(0)
        s.set(31)
        s.set(32)
        s.set(191)
        assert s.test(0) and s.test(31) and s.test(32) and s.test(191)
        assert not s.test(1)
        assert s.count() == 4
        s.clear(31)
        assert not s.test(31)
        assert s.count() == 3

    def test_cap(self):
        s = TSIntegerSet()
        s.set(MAX_TS_SET_SIZE - 1)
        with pytest.raises(ValueError):
            s.set(MAX_TS_SET_SIZE)

    def test_trimmed_dwords(self):
        s = TSIntegerSet()
        assert s.trimmed_dwords() == 0
        s.set(0)
        assert s.trimmed_dwords() == 1
        s.set(32)
        assert s.trimmed_dwords() == 2
        s.clear(32)
        assert s.trimmed_dwords() == 1
        s.set(191)
        assert s.trimmed_dwords() == MAX_TS_SET_DWORDS

    def test_ordinal_of(self):
        s = TSIntegerSet()
        for i in (3, 7, 40):
            s.set(i)
        assert s.ordinal_of(3) == 0
        assert s.ordinal_of(7) == 1
        assert s.ordinal_of(40) == 2
        with pytest.raises(ValueError):
            s.ordinal_of(5)

    def test_words(self):
        s = TSIntegerSet()
        s.set(0)
        s.set(33)
        assert s.words(2) == [1, 2]

    def test_eq_ignores_encoding_metadata(self):
        a = TSIntegerSet(0b101, legacy=7, stored_dwords=3)
        b = TSIntegerSet(0b101)
        assert a == b
        assert a != TSIntegerSet(0b100)

    def test_copy(self):
        a = TSIntegerSet(0b11, legacy=5, stored_dwords=2)
        b = a.copy()
        assert b.mask == a.mask and b.legacy == 5 and b.stored_dwords == 2
        b.set(90)
        assert not a.test(90)
