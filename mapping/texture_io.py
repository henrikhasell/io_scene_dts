"""Putting exported textures on disk.

Kept apart from ``mapping/materials.py``, which is otherwise pure translation
between two in-memory forms and touches no files.

**Export writes a PNG only for an image that has no file behind it.**  Not a
path heuristic, not a comparison of directories -- just ``not image.filepath``.
Everything the importer loaded goes on pointing at its source and is never
rewritten, so "never overwrite the user's art in a game ``textures/`` tree" is
true by construction rather than by care.  For the great majority of shapes
that means export still writes exactly one file, the way it always did.
"""

from __future__ import annotations

from pathlib import Path

import bpy


def _source_texture_paths() -> set[Path]:
    """Every file some image datablock is loaded from."""
    paths: set[Path] = set()
    for img in bpy.data.images:
        if not img.filepath:
            continue
        try:
            paths.add(Path(bpy.path.abspath(img.filepath)).resolve())
        except (OSError, ValueError):
            continue
    return paths


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

    sources = _source_texture_paths()
    claimed: dict[str, str] = {}
    written = 0

    for write in writes:
        if not include_images and not isinstance(write.image, str):
            continue
        previous = claimed.get(write.filename.lower())
        if previous is not None:
            # material names are not unique in real shapes
            warnings.append(
                f"material {write.owner!r} and {previous!r} both write "
                f"{write.filename}; only the first was saved"
            )
            continue
        claimed[write.filename.lower()] = write.owner

        target = texture_dir / write.filename
        try:
            resolved = target.resolve()
        except (OSError, ValueError):
            resolved = target
        if resolved in sources:
            # this is art the scene loaded, not something we produced
            warnings.append(
                f"material {write.owner!r}: {target} is a texture this scene "
                f"loaded, so it was left alone and no texture was written"
            )
            continue

        try:
            if isinstance(write.image, str):
                # an .ifl: generated text with no other home, so it is always
                # written.  The rule above still protects real art beside it.
                target.write_text(write.image, newline="")
            else:
                write.image.file_format = "PNG"
                # the filepath argument saves a copy without claiming the path
                # on the datablock, so a packed scene image does not quietly
                # become a file-backed one and change what the next export
                # decides to write
                write.image.save(filepath=str(target))
        except (OSError, RuntimeError) as e:
            warnings.append(f"material {write.owner!r}: could not write {target}: {e}")
            continue
        written += 1

    return written
