"""Splitting and merging the RGBA buffer of a DTS reflectance texture.

A DTS material entry names one texture, and its ``reflectance_map`` slot names
the texture whose *alpha channel* masks the engine's environment map per texel.
Every one of the 3185 materials in the 852-file corpus points that slot at
itself, so in practice one RGBA file carries both maps: colour is the diffuse,
alpha is the reflectance.

Blender has no way to show that as two things, so the importer takes it apart
and a checkbox puts it back together.  Both directions live here.

No ``bpy``: these take and return flat float buffers, which is exactly what
``Image.pixels.foreach_get`` fills and ``foreach_set`` consumes.  Keeping them
bpy-free is what lets the fast pytest loop reach them -- ``mapping/materials.py``
imports bpy at module scope and never will be reachable.

Everything is strided slice assignment over ``array("f")``, which runs at C
speed and returns an ``array``; there is no per-pixel Python loop and no numpy.
The add-on has no third-party dependencies and this does not introduce one.
"""

from __future__ import annotations

from array import array
from pathlib import Path

# Image suffixes the add-on will load.  Lives here rather than in materials.py
# so the naming rules below can be tested without Blender.
TEXTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds")

REFLECTANCE_SUFFIX = "_refl"


def _check_rgba(buf: array) -> None:
    if len(buf) % 4:
        raise ValueError(f"buffer of {len(buf)} floats is not whole RGBA pixels")


def alpha_range(rgba: array) -> tuple[float, float]:
    """(lowest, highest) alpha in the buffer.  (1.0, 1.0) for an empty one."""
    _check_rgba(rgba)
    alpha = rgba[3::4]
    if not alpha:
        return 1.0, 1.0
    return min(alpha), max(alpha)


def alpha_is_uniform(rgba: array, eps: float = 1e-4) -> bool:
    """True when the alpha channel carries no per-texel information.

    This is the gate on the whole re-encode, and it is not a formality: nearly
    all game art is RGBA with a uniformly opaque alpha, so "has an alpha
    channel" is true of almost every texture and means almost nothing.  A flat
    alpha is a constant reflectivity, which is what ``reflection_amount``
    already says -- splitting it out would replace an image linked to a file on
    disk with a packed copy carrying no information the shape did not have.
    """
    low, high = alpha_range(rgba)
    return high - low <= eps


def split_rgba(rgba: array) -> tuple[array, array]:
    """One RGBA buffer -> (opaque RGB diffuse, greyscale reflectance).

    The diffuse keeps its colour with alpha forced to 1.0, so it does not read
    as transparent in Blender; the reflectance is the alpha broadcast to R, G
    and B, so it shows as the grey mask it is.
    """
    _check_rgba(rgba)
    alpha = rgba[3::4]
    ones = array("f", [1.0]) * len(alpha)

    diffuse = array("f", rgba)
    diffuse[3::4] = ones

    reflectance = array("f", [0.0]) * len(rgba)
    reflectance[0::4] = alpha
    reflectance[1::4] = alpha
    reflectance[2::4] = alpha
    reflectance[3::4] = ones
    return diffuse, reflectance


def merge_rgba(diffuse: array, reflectance: array) -> array:
    """The inverse of :func:`split_rgba`: reflectance grey back into alpha.

    Reads the reflectance's red channel, since :func:`split_rgba` writes the
    same value to all three and a user editing the mask in Blender edits a
    greyscale image.
    """
    _check_rgba(diffuse)
    _check_rgba(reflectance)
    if len(diffuse) != len(reflectance):
        raise ValueError(
            f"diffuse is {len(diffuse) // 4} pixels and reflectance is "
            f"{len(reflectance) // 4}; they must be the same size"
        )
    merged = array("f", diffuse)
    merged[3::4] = reflectance[0::4]
    return merged


def strip_texture_extension(name: str) -> str:
    """Drop a trailing image extension, and only an image extension.

    Material names routinely carry dots that are not extensions -- "base.lmale",
    "armor.damage.1" -- so ``Path(name).stem`` would truncate them.  Same rule
    as ``materials._match_keys``.
    """
    if Path(name).suffix.lower() in TEXTURE_EXTENSIONS:
        return name[: -len(Path(name).suffix)]
    return name


def reflectance_material_name(diffuse_name: str, on_disk_stem: str | None = None) -> str:
    r"""What to call the material-list entry holding a reflectance texture.

    When the image has a file behind it, its stem *is* the name -- that is the
    texture the engine will find, and export writes no file for it.  Otherwise
    the name is derived from the diffuse's, keeping the path prefix, because
    that prefix is a real directory under ``textures/``:
    ``skins\pack_cloak`` -> ``skins\pack_cloak_refl``.
    """
    if on_disk_stem:
        return on_disk_stem
    return strip_texture_extension(diffuse_name) + REFLECTANCE_SUFFIX
