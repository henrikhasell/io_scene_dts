"""Reading the JSON blobs older versions of the add-on wrote.

Pure parsing: no ``bpy`` anywhere, so it is unit-testable in the fast loop
(tests/test_migrate.py).  ``props/migrate.py`` does the applying, which needs
datablocks; keeping the two apart is what makes the parsing testable at all.

The blobs were plain data, so the conversions are mechanical.  The one piece of
real decoding is the trigger word, which packs three meanings into a U32.
"""

from __future__ import annotations

import json

# blobs and packed words the current code no longer reads
LEGACY_SHAPE_KEYS = (
    "dts_names_order",
    "dts_node_transforms",
    "dts_details",
    "dts_materials_order",
    "dts_ifl_materials",
)
LEGACY_ACTION_KEYS = (
    "dts_ground",
    "dts_triggers",
    "dts_ifl_matters",
    "dts_object_anim",
    "dts_decal_anim",
    "dts_scale_anim",
    "dts_rot_matters",
    "dts_trans_matters",
    "dts_keyframes",
)
# per-mesh flags, when they were ID properties present only if set
LEGACY_MESH_KEYS = (
    "dts_billboard",
    "dts_billboard_z",
    "dts_has_detail_texture",
    "dts_use_encoded_normals",
    "dts_echo_type_bits",
    "dts_sorted_mode",
    "dts_sorted_depth",
    "dts_always_write_depth",
)

# the mesh payload: deliberately not converted, see note() below
LEGACY_PAYLOAD_KEYS = (
    "dts_source_payload",
    "dts_source_digest",
    "dts_frozen_payload",
    "dts_frozen_digest",
    "dts_strict_freeze",
)


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def parse_details(blob) -> list[dict]:
    """The dts_details JSON into detail records.

    Earlier versions stored only the first four fields, so the trailing LOD
    metrics are optional.
    """
    out = []
    for entry in _loads(blob, []):
        if len(entry) < 4:
            continue
        record = {
            "name": str(entry[0]),
            "sub_shape_num": int(entry[1]),
            "object_detail_num": int(entry[2]),
            "size": float(entry[3]),
        }
        if len(entry) >= 7:
            record["average_error"] = float(entry[4])
            record["max_error"] = float(entry[5])
            record["poly_count"] = int(entry[6])
        out.append(record)
    return out


def parse_ifl(blob) -> list[dict]:
    out = []
    for entry in _loads(blob, []):
        raw = list(entry.get("raw", []))
        if len(raw) < 5:
            continue
        out.append(
            {
                "name": str(entry.get("name", "")),
                "material_slot": int(raw[1]),
                "first_frame": int(raw[2]),
                "first_frame_off_time": int(raw[3]),
                "num_frames": int(raw[4]),
            }
        )
    return out


def parse_ground(blob) -> list[dict]:
    out = []
    for entry in _loads(blob, []):
        if len(entry) < 2 or len(entry[0]) < 3 or len(entry[1]) < 4:
            continue
        out.append(
            {
                "translation": [float(c) for c in entry[0]],
                "rotation": [int(c) for c in entry[1]],
            }
        )
    return out


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


def parse_triggers(blob) -> list[dict]:
    out = []
    for entry in _loads(blob, []):
        if len(entry) < 2:
            continue
        record = parse_trigger_state(int(entry[0]))
        record["pos"] = float(entry[1])
        out.append(record)
    return out


def parse_node_transforms(blob) -> dict[str, dict]:
    out = {}
    for name, entry in _loads(blob, {}).items():
        if len(entry) < 2 or len(entry[0]) < 4 or len(entry[1]) < 3:
            continue
        out[str(name)] = {
            "stored_rotation": [int(c) for c in entry[0]],
            "stored_translation": [float(c) for c in entry[1]],
        }
    return out


def pack_trigger(state: int, on: bool, invert_on_reverse: bool) -> int:
    """The inverse of parse_triggers, for the export path."""
    packed = 1 << max(0, state - 1)
    if on:
        packed |= 1 << 31
    if invert_on_reverse:
        packed |= 1 << 30
    return packed & 0xFFFFFFFF


