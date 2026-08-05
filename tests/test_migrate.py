"""Reading the old JSON blobs (props/legacy.py).

Parsing only -- the module imports no bpy, which is why it is a separate file
from props/migrate.py and why these run in the fast loop.  The applying half is
covered by test_legacy_blend_migrates in the Blender suite.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from props.legacy import (  # noqa: E402
    pack_trigger,
    parse_details,
    parse_ground,
    parse_ifl,
    parse_node_transforms,
    parse_triggers,
)


class TestDetails:
    def test_the_seven_field_form(self):
        blob = json.dumps([["detail2", 0, 0, 2.0, 0.5, 1.5, 120]])
        assert parse_details(blob) == [
            {
                "name": "detail2",
                "sub_shape_num": 0,
                "object_detail_num": 0,
                "size": 2.0,
                "average_error": 0.5,
                "max_error": 1.5,
                "poly_count": 120,
            }
        ]

    def test_the_four_field_form_earlier_versions_wrote(self):
        record = parse_details(json.dumps([["collision-1", 0, 1, -1.0]]))[0]
        assert record["name"] == "collision-1"
        assert record["size"] == -1.0
        assert "poly_count" not in record  # left at the property default

    def test_a_short_entry_is_skipped_not_guessed(self):
        assert parse_details(json.dumps([["broken", 0]])) == []

    def test_absent_and_malformed_blobs(self):
        assert parse_details(None) == []
        assert parse_details("") == []
        assert parse_details("not json") == []


class TestIfl:
    def test_a_full_entry(self):
        blob = json.dumps([{"name": "flame.ifl", "raw": [7, 2, 10, 0, 6]}])
        assert parse_ifl(blob) == [
            {
                "name": "flame.ifl",
                "material_slot": 2,
                "first_frame": 10,
                "first_frame_off_time": 0,
                "num_frames": 6,
            }
        ]

    def test_the_name_index_in_raw0_is_not_carried(self):
        """raw[0] is an index into the file's name table, which is rebuilt on
        export; the name itself is what survives."""
        entry = parse_ifl(json.dumps([{"name": "x.ifl", "raw": [99, 0, 0, 0, 1]}]))[0]
        assert entry["name"] == "x.ifl"
        assert 99 not in entry.values()

    def test_a_truncated_raw_is_skipped(self):
        assert parse_ifl(json.dumps([{"name": "x", "raw": [1, 2]}])) == []


class TestGround:
    def test_pairs_become_records(self):
        blob = json.dumps([[[1.0, 2.0, 3.0], [0, 0, 0, 32767]]])
        assert parse_ground(blob) == [
            {"translation": [1.0, 2.0, 3.0], "rotation": [0, 0, 0, 32767]}
        ]

    def test_rotation_stays_integral(self):
        """Quat16 int16s, not floats: converting through a float is what loses
        the exact bits a ground frame round-trips on."""
        record = parse_ground(json.dumps([[[0, 0, 0], [-32768, 1, 2, 3]]]))[0]
        assert all(isinstance(c, int) for c in record["rotation"])

    def test_a_short_pair_is_skipped(self):
        assert parse_ground(json.dumps([[[1.0, 2.0], [0, 0, 0, 1]]])) == []


class TestTriggers:
    def test_the_packed_word_comes_apart(self):
        # state 3 (bit 2), switching on
        packed = (1 << 2) | (1 << 31)
        record = parse_triggers(json.dumps([[packed, 0.5]]))[0]
        assert record == {"state": 3, "on": True, "invert_on_reverse": False, "pos": 0.5}

    def test_off_and_inverted(self):
        packed = (1 << 0) | (1 << 30)
        record = parse_triggers(json.dumps([[packed, 0.0]]))[0]
        assert record["state"] == 1
        assert record["on"] is False
        assert record["invert_on_reverse"] is True

    def test_packing_is_the_inverse(self):
        for state in (1, 2, 15, 30):
            for on in (True, False):
                for invert in (True, False):
                    packed = pack_trigger(state, on, invert)
                    back = parse_triggers(json.dumps([[packed, 0.25]]))[0]
                    assert (back["state"], back["on"], back["invert_on_reverse"]) == (
                        state,
                        on,
                        invert,
                    )

    def test_a_zero_state_does_not_become_state_zero(self):
        """There is no state 0; the property is 1..30, so a corrupt word
        clamps rather than producing an unsettable value."""
        assert parse_triggers(json.dumps([[0, 0.0]]))[0]["state"] == 1


class TestNodeTransforms:
    def test_keyed_by_dts_node_name(self):
        blob = json.dumps({"Bip01": [[1, 2, 3, 4], [0.5, 0.0, -0.5]]})
        assert parse_node_transforms(blob) == {
            "Bip01": {"stored_rotation": [1, 2, 3, 4], "stored_translation": [0.5, 0.0, -0.5]}
        }

    def test_a_malformed_entry_is_skipped(self):
        assert parse_node_transforms(json.dumps({"x": [[1, 2], [0, 0, 0]]})) == {}
