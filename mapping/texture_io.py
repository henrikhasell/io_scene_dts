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


MAX_TEXTURE_SIZE = 512


def nearest_power_of_two(n: int) -> int:
    """The power of two closest to *n* in log space: 100 -> 128, 80 -> 64."""
    if n < 1:
        return 1
    return 1 << max(0, round(math.log2(n)))


def export_size(size, power_of_two: bool, max_size: int | None):
    """The dimensions a texture should be written at, from the two checkboxes.

    One function rather than two passes, because two passes would resample
    twice -- a 1000x600 image rounded to 1024x512 and then fitted to 512 would
    go through the filter once for each, and a resample of a resample is
    visibly softer than one straight to the answer.

    Power-of-two first, then the cap, and the cap divides rather than clamps
    each side on its own: halving the *longest* side and scaling the other to
    match keeps the aspect ratio, where clamping only the side that is too big
    would stretch the art.  Rounding the fitted result back to powers of two
    cannot undo the cap, because the cap is itself a power of two and rounding
    a value at or below it can only reach it, never pass it.
    """
    width, height = size
    if power_of_two:
        width, height = nearest_power_of_two(width), nearest_power_of_two(height)
    if max_size and max(width, height) > max_size:
        factor = max_size / max(width, height)
        width, height = max(1, round(width * factor)), max(1, round(height * factor))
        if power_of_two:
            width, height = nearest_power_of_two(width), nearest_power_of_two(height)
    return width, height


def _resized_copy(image, target):
    """A copy of *image* at *target*, or None if it is already that size.

    A **copy**, because ``Image.scale`` resamples the datablock in place.  Doing
    that to the scene's own image would resize the texture the user paints on,
    the viewport would change behind them, and a second export would resample
    the already-resampled result.  The caller removes it again.
    """
    if tuple(target) == tuple(image.size):
        return None
    scaled = image.copy()
    scaled.scale(*target)
    return scaled


def write_textures(writes, texture_dir: Path | None, warnings: list[str],
                   include_images: bool = True,
                   power_of_two: bool = True,
                   max_size: int | None = MAX_TEXTURE_SIZE) -> int:
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

    ``max_size`` is the Limit Textures to 512x512 checkbox, as a number rather
    than a flag so a caller can say what the limit is; None is unlimited.  It
    is a budget rather than a correctness rule -- an oversized texture renders,
    it just costs: the engine uploads the whole thing and mipmaps it, and the
    driver's own loader resamples anything past ``GL_MAX_TEXTURE_SIZE`` at load
    (``platformWin32/d3dgl.cc:9343``), so the download and the VRAM are paid
    for detail the card may then throw away.  512 is what the art this format
    ships with is built to: of the 1062 textures in the corpus's texture trees,
    3 are larger, and all 3 are from a modern tree rather than a game's.

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
                was = tuple(image.size)
                fitted = export_size(was, power_of_two, max_size)
                scaled = _resized_copy(image, fitted)
                if scaled is not None:
                    image = scaled
                    # one line whatever the two checkboxes each contributed:
                    # a texture resized for both reasons was resized once, and
                    # saying so twice would read as two resamples.  Which
                    # reasons applied is asked of the same function, so a
                    # 1024x1024 fitted to 512 is not also blamed on rounding
                    # it was already square with
                    rounded = export_size(was, power_of_two, None)
                    why = []
                    if rounded != was:
                        why.append("the engine's power-of-two textures")
                    if fitted != rounded:
                        why.append(f"the {max_size}x{max_size} limit")
                    warnings.append(
                        f"material {write.owner!r}: {write.filename} scaled "
                        f"{was[0]}x{was[1]} -> {fitted[0]}x{fitted[1]} for "
                        f"{' and '.join(why)}"
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
