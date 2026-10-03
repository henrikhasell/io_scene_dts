"""The packed trigger-state word (mapping/triggers.py).

Pure arithmetic -- the module imports no bpy, which is why these run in the
fast loop.  The round trip is what matters: an imported trigger has to export
as the same U32 it arrived as.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapping.triggers import pack_trigger, parse_trigger_state  # noqa: E402


class TestTriggerState:
    def test_the_state_is_one_hot(self):
        assert parse_trigger_state((1 << 2) | (1 << 31)) == {
            "state": 3,
            "on": True,
            "invert_on_reverse": False,
        }

    def test_off_and_inverted(self):
        fields = parse_trigger_state((1 << 0) | (1 << 30))
        assert fields["state"] == 1
        assert fields["on"] is False
        assert fields["invert_on_reverse"] is True

    def test_packing_is_the_inverse(self):
        for state in (1, 2, 15, 30):
            for on in (True, False):
                for invert in (True, False):
                    back = parse_trigger_state(pack_trigger(state, on, invert))
                    assert (back["state"], back["on"], back["invert_on_reverse"]) == (
                        state,
                        on,
                        invert,
                    )

    def test_a_zero_state_does_not_become_state_zero(self):
        """There is no state 0; the property is 1..30, so a corrupt word
        clamps rather than producing an unsettable value."""
        assert parse_trigger_state(0)["state"] == 1
