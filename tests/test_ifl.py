"""The .ifl parser, against every hazard the shipped files actually contain.

Each case here is something one of the 35 .ifl files the corpus references
really does -- they are not invented edge cases.  mapping/ifl.py is bpy-free so
this runs in the fast loop; the Blender side is covered by
tests/blender/test_operators.py.
"""

from mapping.ifl import (
    format_ifl,
    frame_schedule,
    ifl_name_for,
    material_name_for,
    parse_ifl,
    total_ticks,
)


def test_a_plain_list_parses_in_order():
    text = "plasma01.png 1\r\nplasma02.png 1\r\nplasma03.png 1\r\n"
    assert parse_ifl(text) == [("plasma01.png", 1), ("plasma02.png", 1), ("plasma03.png", 1)]


def test_blank_lines_inside_the_list_are_not_terminators():
    """jetflare00.ifl puts a blank line after every 6-frame block -- 36 of them.
    Stopping at the first would read 6 frames of 210."""
    text = "a.png 1\r\nb.png 1\r\n\r\na.png 1\r\nb.png 1\r\n\r\n"
    assert parse_ifl(text) == [("a.png", 1), ("b.png", 1), ("a.png", 1), ("b.png", 1)]


def test_trailing_whitespace_before_the_newline():
    """plasma.ifl's last line is "plasma10.png 1 \r\n"."""
    assert parse_ifl("plasma10.png 1 \r\n") == [("plasma10.png", 1)]


def test_durations_are_not_always_one():
    """blue_blink.ifl opens with a 120-tick hold, then ping-pongs at 1."""
    text = "blue_blink0.PNG 120\r\nblue_blink1.PNG 1\r\n"
    assert parse_ifl(text) == [("blue_blink0.PNG", 120), ("blue_blink1.PNG", 1)]


def test_a_missing_duration_reads_as_one_frame():
    assert parse_ifl("lonely.png\r\n") == [("lonely.png", 1)]


def test_a_bad_duration_skips_the_line_rather_than_failing():
    """One malformed row must not lose the other 209."""
    assert parse_ifl("good.png 1\r\nbad.png xyz\r\nalso.png 2\r\n") == [
        ("good.png", 1), ("also.png", 2)
    ]


def test_frames_repeat_and_run_out_of_order():
    """jetflare00.ifl is 210 lines over 6 textures, ordered 00,03,01,04,02,05.
    The list is a sequence, not a set, and sorting it would change the
    animation."""
    block = ["jetflare00.png", "jetflare03.png", "jetflare01.png",
             "jetflare04.png", "jetflare02.png", "jetflare05.png"]
    text = "".join(f"{n} 1\r\n" for n in block * 35)
    frames = parse_ifl(text)
    assert len(frames) == 210
    assert len({n for n, _ in frames}) == 6
    assert [n for n, _ in frames[:6]] == block


def test_lf_only_input_still_parses():
    """Nothing in the corpus is LF, but a file a user edits on Linux will be."""
    assert parse_ifl("a.png 1\nb.png 2\n") == [("a.png", 1), ("b.png", 2)]


def test_format_round_trips_a_normalised_list():
    frames = [("a.png", 1), ("b.png", 120), ("a.png", 3)]
    text = format_ifl(frames)
    assert text.endswith("\r\n") and "\r\n\r\n" not in text
    assert parse_ifl(text) == frames


def test_the_ifl_name_is_the_material_name_plus_a_suffix():
    r"""Exact for all 64 corpus entries, path prefix included."""
    assert ifl_name_for("skins\\jetflare00") == "skins\\jetflare00.ifl"
    assert material_name_for("skins\\jetflare00.ifl") == "skins\\jetflare00"
    # idempotent, so a name that already carries the suffix is not doubled
    assert ifl_name_for("flame.ifl") == "flame.ifl"
    assert material_name_for("flame") == "flame"


def test_the_schedule_is_the_running_total_of_the_durations():
    frames = [("a.png", 120), ("b.png", 1), ("c.png", 2)]
    assert frame_schedule(frames) == [(0, 0), (120, 1), (121, 2)]
    assert total_ticks(frames) == 123


def test_an_empty_ifl_is_not_an_error():
    assert parse_ifl("") == []
    assert parse_ifl("\r\n\r\n") == []
    assert total_ticks([]) == 0
    assert format_ifl([]) == ""
