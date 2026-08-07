"""The ``.ifl`` sidecar: a material's flipbook, one frame per line.

An IFL material does not name a texture, it names a ``.ifl`` file, and that
file is the animation::

    jetflare00.png 1
    jetflare03.png 1
    jetflare01.png 1

Each line is a texture and how long to hold it.  The shape's own IFL table says
only *which material* flips; everything about *how* lives here, which is why a
shape whose ``.ifl`` is missing has an animation nobody can see or reproduce.

No ``bpy``: this is string handling, and keeping it bpy-free is what lets the
fast pytest loop reach it -- ``mapping/materials.py`` imports bpy at module
scope and never will be reachable.

Every rule below was read off the 35 ``.ifl`` files the corpus references, not
guessed:

- **CRLF throughout.**  Every line of every file.
- **Blank lines happen inside the list**, not only at the end -- 36 of them in
  ``jetflare00.ifl``, one after each 6-frame block.  A parser that stops at the
  first blank line reads 6 frames of 210.
- **Durations vary**, 1 to 120, in 27 of the 35 files.  A frame is not one tick.
- **Frames repeat and run out of order.**  ``jetflare00.ifl`` is 210 lines over
  6 textures in the order 00, 03, 01, 04, 02, 05; several ping-pong.  The list
  is a sequence, not a set, and it is not sorted.
- **Trailing whitespace before the newline** (``plasma.ifl``'s last line).
"""

from __future__ import annotations

IFL_SUFFIX = ".ifl"
# what the corpus files use, so a rewritten one is not gratuitously different
LINE_END = "\r\n"
DEFAULT_DURATION = 1


def parse_ifl(text: str) -> list[tuple[str, int]]:
    """``.ifl`` text -> [(texture name, duration)], in file order.

    Order is preserved exactly: it is the animation.  A line with no duration
    gets 1 rather than being refused -- the field is a hold count and its
    absence reads naturally as "one frame" -- and a line whose duration is not
    a number is skipped rather than crashing an import on one bad row.
    """
    frames: list[tuple[str, int]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue  # blank lines separate blocks; they are not terminators
        name = parts[0]
        if len(parts) == 1:
            frames.append((name, DEFAULT_DURATION))
            continue
        try:
            duration = int(parts[1])
        except ValueError:
            continue
        frames.append((name, max(1, duration)))
    return frames


def format_ifl(frames) -> str:
    """[(texture name, duration)] -> ``.ifl`` text.

    CRLF and a trailing newline, which is what every corpus file has.  No blank
    lines: they carry nothing, and the ones in the shipped files look like an
    authoring tool's block separators rather than data.
    """
    return "".join(f"{name} {int(duration)}{LINE_END}" for name, duration in frames)


def ifl_name_for(material_name: str) -> str:
    r"""The ``.ifl`` a material of this name flips.

    Suffixing the material's own name is not a convention this add-on invented
    -- it holds byte-for-byte, path prefix included, for all 64 IFL entries in
    the corpus: material ``skins\jetflare00`` <-> ``skins\jetflare00.ifl``.
    That exactness is what lets the shape's IFL table be derived rather than
    stored beside the material that already implies it.
    """
    if material_name.lower().endswith(IFL_SUFFIX):
        return material_name
    return material_name + IFL_SUFFIX


def material_name_for(ifl_name: str) -> str:
    """The inverse of :func:`ifl_name_for`."""
    if ifl_name.lower().endswith(IFL_SUFFIX):
        return ifl_name[: -len(IFL_SUFFIX)]
    return ifl_name


def frame_schedule(frames) -> list[tuple[int, int]]:
    """[(start tick, frame index)] from the durations, for the preview.

    Derived, never stored: the durations are the authored thing and this is
    just their running total.  Returned as frame *indices* so the caller can
    map them onto whichever images it built.
    """
    schedule = []
    tick = 0
    for index, (_name, duration) in enumerate(frames):
        schedule.append((tick, index))
        tick += max(1, int(duration))
    return schedule


def total_ticks(frames) -> int:
    """How long one pass through the flipbook lasts."""
    return sum(max(1, int(duration)) for _name, duration in frames)
