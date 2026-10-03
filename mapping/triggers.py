"""The packed trigger-state word, in both directions.

A DTS trigger carries one U32 that packs three meanings: *which* object state
it refers to, whether it switches that state on or off, and whether the sense
inverts when the sequence plays backwards.  Importing a sequence has to take
that apart into the three properties a panel can show, and exporting one has to
put it back together byte-identically.

Pure arithmetic: no ``bpy`` here, so ``tests/test_triggers.py`` runs it in the
fast loop.
"""

from __future__ import annotations

__all__ = ["pack_trigger", "parse_trigger_state"]


def parse_trigger_state(packed: int) -> dict:
    """One packed U32 trigger state, into its three meanings.

    tsShape.h: the low 30 bits are a one-hot state number, bit 31 says the
    trigger switches it on rather than off, and bit 30 inverts the sense when
    the sequence plays backwards.
    """
    packed &= 0xFFFFFFFF
    bits = packed & 0x3FFFFFFF
    return {
        # there is no state 0, and the property is 1..30, so a corrupt word
        # clamps rather than producing a value the UI cannot show
        "state": max(1, bits.bit_length()),
        "on": bool(packed & (1 << 31)),
        "invert_on_reverse": bool(packed & (1 << 30)),
    }


def pack_trigger(state: int, on: bool, invert_on_reverse: bool) -> int:
    """The inverse of :func:`parse_trigger_state`, for the export path."""
    packed = 1 << max(0, state - 1)
    if on:
        packed |= 1 << 31
    if invert_on_reverse:
        packed |= 1 << 30
    return packed & 0xFFFFFFFF
