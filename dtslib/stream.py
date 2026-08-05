"""Stream layer for DTS files.

Two kinds of streams:

- ReadAlloc/WriteAlloc: the DTS "memory block" — three back-to-back buffers
  (32-bit, 16-bit, 8-bit) walked simultaneously, with guard checkpoints that
  write/check one incrementing counter into all three buffers at once.
  Port of engine/ts/tsShapeAlloc.{h,cc}.  Note that TSShapeAlloc::align32 in
  read mode only pads the engine's *destination* copy buffer, never the file
  buffers — the three file buffers are packed contiguously with no internal
  alignment.  Only the 16- and 8-bit buffers' *totals* are padded up to whole
  dwords when the block is framed.

- StreamReader/StreamWriter: the flat little-endian stream used for the file
  header, sequences, material list, and the whole of a DSQ file.
"""

from __future__ import annotations

import struct

from .errors import DtsError, DtsGuardMismatch


class ReadAlloc:
    """Reader over the three-buffer memory block."""

    def __init__(self, buf32: bytes, buf16: bytes, buf8: bytes):
        self.b32 = buf32
        self.b16 = buf16
        self.b8 = buf8
        self.p32 = 0  # byte positions
        self.p16 = 0
        self.p8 = 0
        self.guard_count = 0

    # -- 32-bit buffer ------------------------------------------------
    def get32(self) -> int:
        v = struct.unpack_from("<i", self.b32, self.p32)[0]
        self.p32 += 4
        return v

    def getu32(self) -> int:
        v = struct.unpack_from("<I", self.b32, self.p32)[0]
        self.p32 += 4
        return v

    def get32f(self) -> float:
        v = struct.unpack_from("<f", self.b32, self.p32)[0]
        self.p32 += 4
        return v

    def get32n(self, n: int) -> tuple:
        v = struct.unpack_from(f"<{n}i", self.b32, self.p32)
        self.p32 += 4 * n
        return v

    def getu32n(self, n: int) -> tuple:
        v = struct.unpack_from(f"<{n}I", self.b32, self.p32)
        self.p32 += 4 * n
        return v

    def get32fn(self, n: int) -> tuple:
        v = struct.unpack_from(f"<{n}f", self.b32, self.p32)
        self.p32 += 4 * n
        return v

    # -- 16-bit buffer ------------------------------------------------
    def get16(self) -> int:
        v = struct.unpack_from("<h", self.b16, self.p16)[0]
        self.p16 += 2
        return v

    def get16n(self, n: int) -> tuple:
        v = struct.unpack_from(f"<{n}h", self.b16, self.p16)
        self.p16 += 2 * n
        return v

    def getu16n(self, n: int) -> tuple:
        v = struct.unpack_from(f"<{n}H", self.b16, self.p16)
        self.p16 += 2 * n
        return v

    # -- 8-bit buffer -------------------------------------------------
    def get8(self) -> int:
        v = struct.unpack_from("<b", self.b8, self.p8)[0]
        self.p8 += 1
        return v

    def get8n(self, n: int) -> bytes:
        v = self.b8[self.p8 : self.p8 + n]
        if len(v) != n:
            raise DtsError(f"8-bit buffer underrun: wanted {n} at {self.p8}")
        self.p8 += n
        return bytes(v)

    def get_cstring8(self) -> str:
        end = self.b8.index(b"\x00", self.p8)
        s = bytes(self.b8[self.p8 : end]).decode("latin-1")
        self.p8 = end + 1
        return s

    # -- guards -------------------------------------------------------
    def guard(self) -> None:
        offsets = (self.p32, self.p16, self.p8)
        g32 = self.get32()
        g16 = self.get16()
        g8 = self.get8()
        expected = self.guard_count
        # the engine's guards are stored as S32/S16/S8; the 8-bit one wraps
        e16 = struct.unpack("<h", struct.pack("<H", expected & 0xFFFF))[0]
        e8 = struct.unpack("<b", struct.pack("<B", expected & 0xFF))[0]
        if g32 != expected or g16 != e16 or g8 != e8:
            raise DtsGuardMismatch(expected, g32, g16, g8, offsets)
        self.guard_count += 1


class WriteAlloc:
    """Writer building the three-buffer memory block."""

    def __init__(self):
        self.b32 = bytearray()
        self.b16 = bytearray()
        self.b8 = bytearray()
        self.guard_count = 0

    # -- 32-bit -------------------------------------------------------
    def set32(self, v: int) -> None:
        self.b32 += struct.pack("<i", v)

    def setu32(self, v: int) -> None:
        self.b32 += struct.pack("<I", v & 0xFFFFFFFF)

    def set32f(self, v: float) -> None:
        self.b32 += struct.pack("<f", v)

    def set32n(self, vals) -> None:
        self.b32 += struct.pack(f"<{len(vals)}i", *vals)

    def setu32n(self, vals) -> None:
        self.b32 += struct.pack(f"<{len(vals)}I", *[v & 0xFFFFFFFF for v in vals])

    def set32fn(self, vals) -> None:
        self.b32 += struct.pack(f"<{len(vals)}f", *vals)

    # -- 16-bit -------------------------------------------------------
    def set16(self, v: int) -> None:
        self.b16 += struct.pack("<h", v)

    def set16n(self, vals) -> None:
        self.b16 += struct.pack(f"<{len(vals)}h", *vals)

    def setu16n(self, vals) -> None:
        self.b16 += struct.pack(f"<{len(vals)}H", *[v & 0xFFFF for v in vals])

    # -- 8-bit --------------------------------------------------------
    def set8(self, v: int) -> None:
        self.b8 += struct.pack("<b", v)

    def set8bytes(self, data: bytes) -> None:
        self.b8 += data

    def set_cstring8(self, s: str) -> None:
        self.b8 += s.encode("latin-1") + b"\x00"

    # -- guards -------------------------------------------------------
    def guard(self) -> None:
        g = self.guard_count
        self.set32(g)
        self.b16 += struct.pack("<H", g & 0xFFFF)
        self.b8 += struct.pack("<B", g & 0xFF)
        self.guard_count += 1

    # -- framing ------------------------------------------------------
    def to_memory_block(self, pad16: bytes = b"", pad8: bytes = b"") -> tuple[bytes, int, int, int]:
        """Return (block bytes, sizeMemBuffer, start16, start8) — all in dwords.

        The 16- and 8-bit buffers are padded up to whole dwords, exactly like
        TSShape::write (tsShape.cc:1252-1263).  The engine's pad bytes are
        uninitialized memory; pass pad16/pad8 captured at read time to reuse
        the source's bytes (zeros otherwise).
        """
        size32 = len(self.b32) // 4
        b16 = bytes(self.b16)
        if len(b16) % 4:
            need = 4 - len(b16) % 4
            b16 += pad16 if len(pad16) == need else b"\x00" * need
        b8 = bytes(self.b8)
        if len(b8) % 4:
            need = 4 - len(b8) % 4
            b8 += pad8 if len(pad8) == need else b"\x00" * need
        size16 = len(b16) // 4
        size8 = len(b8) // 4
        start16 = size32
        start8 = start16 + size16
        return bytes(self.b32) + b16 + b8, size32 + size16 + size8, start16, start8


class StreamReader:
    """Flat little-endian stream (file header, sequences, material list, DSQ)."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def s32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def s16(self) -> int:
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def bool8(self) -> bool:
        return self.u8() != 0

    def raw(self, n: int) -> bytes:
        v = self.data[self.pos : self.pos + n]
        if len(v) != n:
            raise DtsError(f"stream underrun: wanted {n} bytes at {self.pos}")
        self.pos += n
        return bytes(v)

    def pascal_string(self) -> str:
        """Stream::readString — U8 length + chars, no terminator."""
        n = self.u8()
        return self.raw(n).decode("latin-1")

    def s32_string(self) -> str:
        """TSShape::readName — S32 length + chars, no terminator."""
        n = self.s32()
        return self.raw(n).decode("latin-1")

    def at_end(self) -> bool:
        return self.pos >= len(self.data)


class StreamWriter:
    def __init__(self):
        self.buf = bytearray()

    def s32(self, v: int) -> None:
        self.buf += struct.pack("<i", v)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def f32(self, v: float) -> None:
        self.buf += struct.pack("<f", v)

    def s16(self, v: int) -> None:
        self.buf += struct.pack("<h", v)

    def u8(self, v: int) -> None:
        self.buf += struct.pack("<B", v & 0xFF)

    def raw(self, data: bytes) -> None:
        self.buf += data

    def pascal_string(self, s: str) -> None:
        data = s.encode("latin-1")[:255]
        self.u8(len(data))
        self.raw(data)

    def s32_string(self, s: str) -> None:
        data = s.encode("latin-1")
        self.s32(len(data))
        self.raw(data)

    def getvalue(self) -> bytes:
        return bytes(self.buf)
