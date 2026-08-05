"""Low-level DTS primitives: Quat16, TSIntegerSet, bit-cast helpers.

Ported from engine/ts/tsTransform.h, tsIntegerSet.cc, math/mQuat.cc in the
agentic-torque Torque Game Engine 1.5 source.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

MAX_TS_SET_DWORDS = 6  # tsIntegerSet.h: 192 nodes/objects max
MAX_TS_SET_SIZE = 32 * MAX_TS_SET_DWORDS

_F32 = struct.Struct("<f")
_S32 = struct.Struct("<i")
_U32 = struct.Struct("<I")


def f32_to_bits(f: float) -> int:
    """Bit-cast a float to its S32 representation (little-endian)."""
    return _S32.unpack(_F32.pack(f))[0]


def bits_to_f32(i: int) -> float:
    """Bit-cast an S32/U32 to a float."""
    return _F32.unpack(_S32.pack(i) if i < 0x80000000 else _U32.pack(i))[0]


def to_signed32(i: int) -> int:
    i &= 0xFFFFFFFF
    return i - 0x100000000 if i >= 0x80000000 else i


def to_signed16(i: int) -> int:
    i &= 0xFFFF
    return i - 0x10000 if i >= 0x8000 else i


@dataclass(frozen=True)
class Quat16:
    """Compressed quaternion: four S16 components scaled by 0x7fff.

    NOTE on convention: DTS stores the quaternion that Torque's
    QuatF::setMatrix() rebuilds a node matrix from, and m_quatF_set_matF
    writes the *transpose* of the textbook rotation matrix.  So the stored
    quaternion is the conjugate of a standard-convention quaternion for the
    same rotation.  This class stores/returns raw components; conjugation is
    the mapping layer's job at the dtslib<->Blender boundary.
    """

    MAX_VAL = 0x7FFF

    x: int
    y: int
    z: int
    w: int

    def to_floats(self) -> tuple[float, float, float, float]:
        """Quat16::getQuatF — divide by 0x7fff."""
        s = 1.0 / self.MAX_VAL
        return (self.x * s, self.y * s, self.z * s, self.w * s)

    @classmethod
    def from_floats(cls, x: float, y: float, z: float, w: float) -> "Quat16":
        """Quat16::set — the engine *truncates* toward zero, it does not round.

        (mQuat.cc: x = (S16)(q.x * MAX_VAL) — a C float->int cast.)
        """
        m = cls.MAX_VAL
        return cls(int(x * m), int(y * m), int(z * m), int(w * m))

    @classmethod
    def identity(cls) -> "Quat16":
        return cls(0, 0, 0, cls.MAX_VAL)

    def normalized_floats(self) -> tuple[float, float, float, float]:
        x, y, z, w = self.to_floats()
        n = math.sqrt(x * x + y * y + z * z + w * w)
        if n == 0.0:
            return (0.0, 0.0, 0.0, 1.0)
        return (x / n, y / n, z / n, w / n)


class TSIntegerSet:
    """Bitset over node/object indices (max 192 bits outside TORQUE_LIB).

    File form: S32 legacy word (ignored by the engine but carried through
    rather than recomputed), S32 dword count, then that many U32 words.
    The engine's writer trims trailing zero dwords.
    """

    __slots__ = ("mask", "legacy", "stored_dwords")

    def __init__(self, mask: int = 0, legacy: int = 0, stored_dwords: int | None = None):
        self.mask = mask
        self.legacy = legacy  # first S32 in the file ("don't care" to the engine)
        # dword count as found in the file; None -> compute trimmed on write
        self.stored_dwords = stored_dwords

    def test(self, i: int) -> bool:
        return bool(self.mask >> i & 1)

    def set(self, i: int) -> None:
        if i >= MAX_TS_SET_SIZE:
            raise ValueError(f"TSIntegerSet index {i} exceeds {MAX_TS_SET_SIZE - 1}")
        self.mask |= 1 << i

    def clear(self, i: int) -> None:
        self.mask &= ~(1 << i)

    def count(self) -> int:
        return self.mask.bit_count()

    def indices(self):
        m, i = self.mask, 0
        while m:
            if m & 1:
                yield i
            m >>= 1
            i += 1

    def ordinal_of(self, i: int) -> int:
        """Position of index i among the set members (channel-major addressing)."""
        if not self.test(i):
            raise ValueError(f"index {i} not in set")
        return (self.mask & ((1 << i) - 1)).bit_count()

    def trimmed_dwords(self) -> int:
        """Index of the highest non-zero dword + 1 (the engine's write rule)."""
        sz = 0
        for i in range(MAX_TS_SET_DWORDS):
            if self.mask >> (32 * i) & 0xFFFFFFFF:
                sz = i + 1
        return sz

    def words(self, sz: int) -> list[int]:
        return [self.mask >> (32 * i) & 0xFFFFFFFF for i in range(sz)]

    def __eq__(self, other) -> bool:
        return isinstance(other, TSIntegerSet) and self.mask == other.mask

    def __repr__(self) -> str:
        return f"TSIntegerSet({sorted(self.indices())})"

    def copy(self) -> "TSIntegerSet":
        return TSIntegerSet(self.mask, self.legacy, self.stored_dwords)
