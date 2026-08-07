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

from pathlib import Path


def write_textures(writes, texture_dir: Path | None, warnings: list[str],
                   include_images: bool = True) -> int:
    """Save each :class:`materials.TextureWrite` beside the .dts.

    ``include_images`` is the export dialog's Export Textures checkbox.  It
    gates *images* only: a generated sidecar like an ``.ifl`` is the shape's own
    animation data rather than art, and the .dts names it, so suppressing it
    would leave a material pointing at a flipbook that does not exist.

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
        try:
            if isinstance(write.image, str):
                target.write_text(write.image, newline="")
            else:
                # restored afterwards: export must not leave the .blend
                # different from how it found it, and file_format is a property
                # of the datablock rather than of this one save
                previous_format = write.image.file_format
                try:
                    write.image.file_format = "PNG"
                    # the filepath argument saves a copy without claiming the
                    # path on the datablock, so a packed scene image does not
                    # quietly become a file-backed one, and an image loaded
                    # from a game tree goes on pointing at where it came from
                    write.image.save(filepath=str(target))
                finally:
                    write.image.file_format = previous_format
        except (OSError, RuntimeError) as e:
            warnings.append(f"material {write.owner!r}: could not write {target}: {e}")
            continue
        written += 1

    return written
