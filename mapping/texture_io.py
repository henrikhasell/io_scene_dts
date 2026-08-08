"""Putting exported textures on disk.

Kept apart from ``mapping/materials.py``, which is otherwise pure translation
between two in-memory forms and touches no files.

**Export writes a PNG for every texture the shape names**, whether the image
was made in Blender or loaded from a file, and it overwrites what is already
there.  The checkbox is the only thing that stops it.  The reasoning is that a
``.dts`` names its textures by bare filename and the engine looks for them
beside the shape, so an export that copies only the images with no file behind
them produces a shape that does not render anywhere but the machine it was
made on -- and a stale texture left in place beside a new ``.dts`` is a wrong
render that looks like a right one.

The cost is real and is the reason the rule used to be the other way: exporting
*into* a game's ``textures/`` tree now rewrites the art there, re-encoded as
PNG through Blender.  Export somewhere else and copy, or untick Export
Textures.  ``UNSUPPORTED.md`` §4 says so too.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy


def nearest_power_of_two(n: int) -> int:
    """The power of two closest to *n* in log space: 100 -> 128, 80 -> 64."""
    if n < 1:
        return 1
    return 1 << max(0, round(math.log2(n)))


def _power_of_two_copy(image):
    """A power-of-two-sized copy of *image*, or None if it already is one.

    A **copy**, because ``Image.scale`` resamples the datablock in place.  Doing
    that to the scene's own image would resize the texture the user paints on,
    the viewport would change behind them, and a second export would resample
    the already-resampled result.  The caller removes it again.
    """
    width, height = image.size
    target = (nearest_power_of_two(width), nearest_power_of_two(height))
    if target == (width, height):
        return None
    scaled = image.copy()
    scaled.scale(*target)
    return scaled


def write_textures(writes, texture_dir: Path | None, warnings: list[str],
                   include_images: bool = True,
                   power_of_two: bool = True) -> int:
    """Save each :class:`materials.TextureWrite` beside the .dts.

    ``include_images`` is the export dialog's Export Textures checkbox.  It
    gates *images* only: a generated sidecar like an ``.ifl`` is the shape's own
    animation data rather than art, and the .dts names it, so suppressing it
    would leave a material pointing at a flipbook that does not exist.

    ``power_of_two`` is the Scale Textures to Power of Two checkbox.  Torque
    renders through fixed-function OpenGL and its texture loader assumes
    power-of-two dimensions; a 100x60 PNG is why a material comes out white or
    garbled in-game while looking correct in Blender.  Scaling on the way out
    means the .blend keeps the art at whatever size it was authored at -- the
    scene is never modified, only what lands on disk.

    Returns how many files were written.  Never raises: a texture that cannot
    be written is a warning, because losing the .dts over it would be worse
    than shipping it without its art.
    """
    if texture_dir is None or not writes:
        return 0

    claimed: dict[str, str] = {}
    written = 0

    for write in writes:
        if not include_images and not isinstance(write.image, str):
            continue
        # still one write per filename *within one export*: two materials that
        # would land on the same file are a collision to report, not an
        # overwrite to perform, because neither of them is the stale one.
        # Material names are not unique in real shapes.
        previous = claimed.get(write.filename.lower())
        if previous is not None:
            warnings.append(
                f"material {write.owner!r} and {previous!r} both write "
                f"{write.filename}; only the first was saved"
            )
            continue
        claimed[write.filename.lower()] = write.owner

        target = texture_dir / write.filename
        scaled = None
        try:
            if isinstance(write.image, str):
                target.write_text(write.image, newline="")
            else:
                image = write.image
                if power_of_two:
                    was = tuple(image.size)
                    scaled = _power_of_two_copy(image)
                    if scaled is not None:
                        image = scaled
                        warnings.append(
                            f"material {write.owner!r}: {write.filename} scaled "
                            f"{was[0]}x{was[1]} -> {image.size[0]}x{image.size[1]} "
                            f"for the engine's power-of-two textures"
                        )
                # restored afterwards: export must not leave the .blend
                # different from how it found it, and file_format is a property
                # of the datablock rather than of this one save
                previous_format = image.file_format
                try:
                    image.file_format = "PNG"
                    # the filepath argument saves a copy without claiming the
                    # path on the datablock, so a packed scene image does not
                    # quietly become a file-backed one, and an image loaded
                    # from a game tree goes on pointing at where it came from
                    image.save(filepath=str(target))
                finally:
                    image.file_format = previous_format
        except (OSError, RuntimeError) as e:
            warnings.append(f"material {write.owner!r}: could not write {target}: {e}")
            continue
        finally:
            # the copy is export scratch; leaving it behind would put a second
            # datablock of every non-power-of-two texture in the .blend
            if scaled is not None:
                bpy.data.images.remove(scaled)
        written += 1

    return written
