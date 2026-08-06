"""The bpy-free half of the reflectance re-encode.

mapping/materials.py imports bpy and cannot be reached from here, so the pixel
arithmetic and the naming rules live in mapping/texture_split.py and are tested
in the fast loop.  The Blender-side plumbing is covered by
tests/blender/test_operators.py.
"""

from array import array

import pytest

from mapping.texture_split import (
    REFLECTANCE_SUFFIX,
    alpha_is_uniform,
    alpha_range,
    merge_rgba,
    reflectance_material_name,
    split_rgba,
    strip_texture_extension,
)


def _ramp(pixels=4):
    """RGBA with a distinct alpha per pixel, so a split has something to find."""
    buf = array("f")
    for i in range(pixels):
        buf.extend((0.25, 0.5, 0.75, i / (pixels - 1)))
    return buf


def test_split_puts_alpha_in_every_colour_channel():
    diffuse, reflectance = split_rgba(_ramp())
    assert list(diffuse[3::4]) == [1.0] * 4, "diffuse must not read as transparent"
    assert list(diffuse[0::4]) == [0.25] * 4, "colour is untouched"
    for channel in (0, 1, 2):
        assert list(reflectance[channel::4]) == pytest.approx([0.0, 1 / 3, 2 / 3, 1.0])
    assert list(reflectance[3::4]) == [1.0] * 4


def test_merge_is_the_inverse_of_split():
    source = _ramp(8)
    assert merge_rgba(*split_rgba(source)) == source


def test_merge_reads_the_reflectance_red_channel():
    """A user editing the mask in Blender edits a greyscale image; red is the
    channel split_rgba writes first and the one merge must agree on."""
    diffuse = array("f", [0.1, 0.2, 0.3, 1.0])
    reflectance = array("f", [0.6, 0.0, 0.0, 1.0])
    assert list(merge_rgba(diffuse, reflectance)) == pytest.approx([0.1, 0.2, 0.3, 0.6])


def test_merge_refuses_a_size_mismatch():
    with pytest.raises(ValueError, match="same size"):
        merge_rgba(_ramp(4), _ramp(8))


def test_a_ragged_buffer_is_refused():
    with pytest.raises(ValueError, match="whole RGBA pixels"):
        split_rgba(array("f", [1.0, 1.0, 1.0]))


def test_a_flat_alpha_carries_nothing():
    """The gate on the whole re-encode.  Nearly all game art is RGBA with a
    uniformly opaque alpha, so 'has an alpha channel' is not the question."""
    opaque = array("f", [0.5, 0.5, 0.5, 1.0] * 4)
    half = array("f", [0.5, 0.5, 0.5, 0.5] * 4)
    assert alpha_is_uniform(opaque)
    assert alpha_is_uniform(half), "a constant alpha is reflection_amount, not a mask"
    assert not alpha_is_uniform(_ramp())


def test_alpha_range_of_an_empty_buffer_is_opaque():
    assert alpha_range(array("f")) == (1.0, 1.0)
    assert alpha_range(_ramp()) == (0.0, 1.0)


def test_only_a_real_image_extension_is_stripped():
    # material names carry dots that are not extensions
    assert strip_texture_extension("base.lmale") == "base.lmale"
    assert strip_texture_extension("armor.damage.1") == "armor.damage.1"
    assert strip_texture_extension("hull.PNG") == "hull"
    assert strip_texture_extension("hull") == "hull"


def test_a_reflectance_name_keeps_the_path_prefix():
    assert reflectance_material_name("skins\\pack_cloak") == "skins\\pack_cloak" + REFLECTANCE_SUFFIX
    assert reflectance_material_name("hull") == "hull_refl"
    assert reflectance_material_name("hull.tga") == "hull_refl"


def test_an_on_disk_image_names_itself():
    """Its stem is the texture the engine will find, and export writes no file
    for it, so inventing a name would point the shape at nothing."""
    assert reflectance_material_name("hull", "shiny") == "shiny"
