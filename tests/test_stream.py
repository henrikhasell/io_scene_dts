import pytest
from hypothesis import given
from hypothesis import strategies as st

from dtslib.errors import DtsError, DtsGuardMismatch
from dtslib.stream import ReadAlloc, StreamReader, StreamWriter, WriteAlloc


def roundtrip_alloc(write_fn):
    """Run write_fn(WriteAlloc), frame it, and return a ReadAlloc over it."""
    w = WriteAlloc()
    write_fn(w)
    block, size, start16, start8 = w.to_memory_block()
    assert len(block) == size * 4
    return ReadAlloc(block[: start16 * 4], block[start16 * 4 : start8 * 4], block[start8 * 4 :])


class TestAllocRoundtrip:
    def test_basic_types(self):
        def write(w):
            w.set32(-5)
            w.setu32(0xDEADBEEF)
            w.set32f(2.5)
            w.set16(-7)
            w.set8(-3)
            w.set_cstring8("hello")

        r = roundtrip_alloc(write)
        assert r.get32() == -5
        assert r.getu32() == 0xDEADBEEF
        assert r.get32f() == 2.5
        assert r.get16() == -7
        assert r.get8() == -3
        assert r.get_cstring8() == "hello"

    def test_guards_in_step(self):
        def write(w):
            for i in range(10):
                w.set32(i)
                w.guard()

        r = roundtrip_alloc(write)
        for i in range(10):
            assert r.get32() == i
            r.guard()

    def test_guard_mismatch_raises(self):
        def write(w):
            w.guard()
            w.set32(5)  # reader will consume this as the second guard's 32-bit word
            w.set16(5)
            w.set8(5)

        r = roundtrip_alloc(write)
        r.guard()
        with pytest.raises(DtsGuardMismatch):
            r.guard()

    def test_desync_detected(self):
        # writing one fewer 16-bit value than the reader consumes shifts the
        # 16-bit guard and must be caught at the next checkpoint
        def write(w):
            w.set16n([1, 2, 3])
            w.guard()

        r = roundtrip_alloc(write)
        r.get16n(2)  # under-consume
        with pytest.raises(DtsGuardMismatch):
            r.guard()

    def test_padding(self):
        def write(w):
            w.set16(1)  # 2 bytes -> padded to 4
            w.set8(1)  # 1 byte -> padded to 4

        w = WriteAlloc()
        write(w)
        block, size, start16, start8 = w.to_memory_block()
        assert size == 2 and start16 == 0 and start8 == 1
        assert block[2:4] == b"\x00\x00"
        assert block[5:8] == b"\x00\x00\x00"

    def test_custom_padding_bytes(self):
        w = WriteAlloc()
        w.set8(1)
        block, *_ = w.to_memory_block(pad8=b"\xaa\xbb\xcc")
        assert block[1:4] == b"\xaa\xbb\xcc"
        # wrong-length pad falls back to zeros
        w2 = WriteAlloc()
        w2.set8(1)
        block2, *_ = w2.to_memory_block(pad8=b"\xaa")
        assert block2[1:4] == b"\x00\x00\x00"

    def test_8bit_underrun(self):
        r = ReadAlloc(b"", b"", b"ab")
        with pytest.raises(DtsError):
            r.get8n(3)

    @given(
        st.lists(st.integers(-(2**31), 2**31 - 1), max_size=20),
        st.lists(st.integers(-(2**15), 2**15 - 1), max_size=20),
        st.lists(st.integers(-128, 127), max_size=20),
    )
    def test_property_roundtrip(self, v32, v16, v8):
        def write(w):
            w.set32n(v32)
            w.guard()
            w.set16n(v16)
            w.set8bytes(bytes(b & 0xFF for b in v8))
            w.guard()

        r = roundtrip_alloc(write)
        assert list(r.get32n(len(v32))) == v32
        r.guard()
        assert list(r.get16n(len(v16))) == v16
        assert r.get8n(len(v8)) == bytes(b & 0xFF for b in v8)
        r.guard()


class TestFlatStream:
    def test_scalars(self):
        w = StreamWriter()
        w.s32(-1)
        w.u32(0xFFFFFFFF)
        w.f32(1.5)
        w.s16(-2)
        w.u8(255)
        r = StreamReader(w.getvalue())
        assert r.s32() == -1
        assert r.u32() == 0xFFFFFFFF
        assert r.f32() == 1.5
        assert r.s16() == -2
        assert r.u8() == 255
        assert r.at_end()

    def test_strings(self):
        w = StreamWriter()
        w.pascal_string("material.png")
        w.s32_string("NodeName")
        w.s32_string("")
        r = StreamReader(w.getvalue())
        assert r.pascal_string() == "material.png"
        assert r.s32_string() == "NodeName"
        assert r.s32_string() == ""

    def test_pascal_string_truncates_at_255(self):
        w = StreamWriter()
        w.pascal_string("x" * 300)
        r = StreamReader(w.getvalue())
        assert r.pascal_string() == "x" * 255

    def test_raw_underrun(self):
        r = StreamReader(b"ab")
        with pytest.raises(DtsError):
            r.raw(3)

    def test_bool8(self):
        w = StreamWriter()
        w.u8(0)
        w.u8(1)
        r = StreamReader(w.getvalue())
        assert r.bool8() is False
        assert r.bool8() is True
