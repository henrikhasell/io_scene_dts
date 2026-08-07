"""Blender material <-> dtslib.Material translation.

The dts_* custom properties on the Blender material are the round-trip source
of truth for the bump and detail slots and the flag bits; viewport/shader
settings are cosmetic.  Two things are the other way round -- the blend bits
(see :func:`blend_flags_from_material`) and the reflectance map -- because a
shader is the only place they can be edited.

The reflectance/bump/detail map slots hold *indices into the material list*;
they are stored on the Blender material as name references ("self", "none",
or the referenced material's DTS name) so they survive reordering, and are
resolved back to indices in a second pass on export.

The reflectance map is the one the file packs strangely: its slot names another
entry in the same material list, and the *alpha channel* of that entry's texture
is the per-texel environment-map mask.  Every material in the corpus points the
slot at itself, so one RGBA file is really two maps.  On import that file comes
apart into an RGB diffuse and a greyscale mask (``mapping/texture_split.py``),
and the material's Combine checkbox puts it back together on the way out.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from .texture_split import (
    TEXTURE_EXTENSIONS,
    alpha_is_uniform,
    merge_rgba,
    reflectance_material_name,
    split_rgba,
)
from ..dtslib.types import (
    IflMaterial,
    MAT_ADDITIVE,
    MAT_BUMP_MAP_ONLY,
    MAT_DETAIL_MAP_ONLY,
    MAT_IFL_FRAME,
    MAT_IFL_MATERIAL,
    MAT_MIP_MAP_ZERO_BORDER,
    MAT_NEVER_ENV_MAP,
    MAT_NO_MIP_MAP,
    MAT_REFLECTANCE_MAP_ONLY,
    MAT_S_WRAP,
    MAT_SELF_ILLUMINATING,
    MAT_SUBTRACTIVE,
    MAT_T_WRAP,
    MAT_TRANSLUCENT,
    NO_MAP,
    Material,
)

_TEXTURE_EXTENSIONS = TEXTURE_EXTENSIONS
IFL_EXTENSIONS = (".ifl",)

# Every bit of the material flags word the props own, each with its own
# checkbox.  Four bits are *not* here.  The three blend bits are read off the
# node graph by blend_flags_from_material, and MAT_IFL_MATERIAL is read off
# `dts_material.is_ifl`; a stored copy beside either would be a second source
# for one value.
#
# There used to be a packed `dts_flags` beside these holding whatever had no
# named property -- four bits nobody had named, plus a copy of the ten that
# were.  That was the same bug, and it came with one of its own: Blender's
# integer ID-properties are a C int, so MAT_REFLECTANCE_MAP_ONLY (1 << 31)
# could not be stored in it at all and needed a special case.  With every bit
# either named or derived, the word is assembled on export and nothing is
# packed.
_FLAG_PROPS = {
    "dts_s_wrap": MAT_S_WRAP,
    "dts_t_wrap": MAT_T_WRAP,
    "dts_self_illuminating": MAT_SELF_ILLUMINATING,
    "dts_never_env_map": MAT_NEVER_ENV_MAP,
    "dts_no_mip_map": MAT_NO_MIP_MAP,
    "dts_mip_map_zero_border": MAT_MIP_MAP_ZERO_BORDER,
    "dts_ifl_frame": MAT_IFL_FRAME,
    "dts_detail_map_only": MAT_DETAIL_MAP_ONLY,
    "dts_bump_map_only": MAT_BUMP_MAP_ONLY,
    "dts_reflectance_map_only": MAT_REFLECTANCE_MAP_ONLY,
}

_MAP_PROPS = ("dts_reflectance_map", "dts_bump_map", "dts_detail_map")


# resolved textures/ dir -> {lowercase stem: path}, rebuilt per import
_texture_index: dict[tuple, dict[str, Path]] = {}

# source texture path -> (diffuse image, reflectance image).  Real shapes reuse
# one texture across many materials -- 23 in the worst corpus case -- and each
# would otherwise re-encode it into its own pair of datablocks.
_split_images: dict[str, tuple[bpy.types.Image, bpy.types.Image]] = {}

def reset_texture_cache() -> None:
    """Drop the sibling-textures index so a re-import picks up new files."""
    _texture_index.clear()
    _split_images.clear()


def sibling_texture_dir(search_dir: Path) -> Path | None:
    """The ``textures/`` dir beside a ``shapes/`` dir, if that is the layout.

    Tribes 2 and friends keep ``shapes/`` and ``textures/`` as siblings under
    the mod root, so a shape's textures are never next to the .dts itself.
    Gated on the directory actually being named "shapes" -- without that check
    this would start guessing at sibling directories for arbitrary layouts.
    """
    if search_dir.name.lower() != "shapes":
        return None
    tex = search_dir.parent / "textures"
    return tex if tex.is_dir() else None


def _split_material_name(name: str) -> tuple[str, str]:
    r"""Split a stored material name into (path prefix, bare name).

    Names keep the authoring tool's path, e.g. ``skins\base.lmale``.  That
    prefix is a real directory under ``textures/``, so it is worth keeping
    rather than discarding -- it is the only thing distinguishing
    ``textures/skins/base.lmale.png`` from a stale ``textures/base.lmale.png``.
    """
    norm = name.replace("\\", "/")
    if "/" in norm:
        prefix, _, bare = norm.rpartition("/")
        return prefix, bare
    return "", norm


def _prefixed_dir(tex_dir: Path, prefix: str) -> Path | None:
    """``tex_dir/prefix`` if it exists and stays inside the texture tree."""
    if not prefix:
        return None
    try:
        resolved = (tex_dir / prefix).resolve()
        root = tex_dir.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    # the prefix comes out of a file, so refuse anything that escapes the tree
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _indexed(root: Path, extensions=_TEXTURE_EXTENSIONS) -> dict[str, Path]:
    """Case-insensitive stem -> path over a texture tree, cached.

    Textures sit in subdirectories (``textures/skins/foo.png``), so the index
    is keyed on the bare stem and built recursively; it is the fallback for
    when a material's own path prefix does not locate the file.

    The extension set is a parameter because ``.ifl`` sidecars live in the same
    trees and are found the same way -- all 35 the corpus names resolve under a
    ``textures/skins/`` directory, exactly where the material's path prefix
    says to look.
    """
    key = (root.resolve(), extensions)
    cached = _texture_index.get(key)
    if cached is not None:
        return cached
    index: dict[str, Path] = {}
    # sort so a stem present more than once (case-variant duplicates exist in
    # real game data) always resolves to the same file
    for p in sorted(root.rglob("*"), key=lambda q: (len(q.parts), str(q).lower())):
        if p.suffix.lower() in extensions and p.is_file():
            index.setdefault(p.stem.lower(), p)
    _texture_index[key] = index
    return index


def _match_keys(name: str, extensions=_TEXTURE_EXTENSIONS) -> list[str]:
    """Index keys to try for a material name, best first.

    Material names routinely contain dots that are *not* extensions
    ("base.lmale", "armor.damage.1"), so the whole name is the primary key --
    ``Path(name).stem`` would truncate those to "base" / "armor.damage".  The
    stem is kept as a fallback for the rare name that does carry an image
    extension.
    """
    keys = [name.lower()]
    if Path(name).suffix.lower() in extensions:
        keys.append(Path(name).stem.lower())
    return keys


def find_texture(name: str, search_dir: Path | None) -> Path | None:
    r"""Find an image for a material name (case-insensitive).

    Resolution order:

    1. next to the .dts -- a texture dropped beside a shape is an override,
       so it wins regardless of what the name's path prefix says;
    2. the prefix directory under a sibling ``textures/`` tree, so
       ``skins\base.lmale`` prefers ``textures/skins/base.lmale.png``;
    3. anywhere in that tree, matched on the bare name.

    The sibling hop only happens when the shape lives in a directory named
    ``shapes``.
    """
    return _find_beside(name, search_dir, _TEXTURE_EXTENSIONS)


def find_ifl(name: str, search_dir: Path | None) -> Path | None:
    r"""Find the ``.ifl`` an IFL material names, by the same rules.

    Same search as :func:`find_texture` and for the same reason: the sidecars
    sit in the very trees the textures do.  All 35 the corpus references
    resolve -- usually at ``textures/skins/<name>.ifl``, which the material's
    own ``skins\`` prefix points straight at, and sometimes flat beside the
    ``.dts``, which the local-directory-first rule catches.
    """
    return _find_beside(name, search_dir, IFL_EXTENSIONS)


def _find_beside(name: str, search_dir: Path | None, extensions) -> Path | None:
    if search_dir is None or not search_dir.is_dir():
        return None
    prefix, bare = _split_material_name(name)
    keys = _match_keys(bare, extensions)
    local = {
        p.stem.lower(): p
        for p in sorted(search_dir.iterdir(), key=lambda q: str(q).lower())
        if p.suffix.lower() in extensions
    }
    for key in keys:
        if key in local:
            return local[key]

    roots = []
    # the name's own prefix, relative to the shape's directory.  Tribes 2 keeps
    # shapes/ and textures/ apart, but an exported shape often ships with its
    # own skins/ beside it, and `skins\foo` points straight at it
    beside = _prefixed_dir(search_dir, prefix)
    if beside is not None:
        roots.append(beside)
    tex_dir = sibling_texture_dir(search_dir)
    if tex_dir is not None:
        sub = _prefixed_dir(tex_dir, prefix)
        if sub is not None:
            roots.append(sub)
        roots.append(tex_dir)

    for root in roots:
        index = _indexed(root, extensions)
        for key in keys:
            if key in index:
                return index[key]
    return None


def _output_node(nt):
    return next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)


def _build_add_shader(bmat, tex, subtractive: bool) -> None:
    """Replace the Principled surface with the additive/subtractive encoding.

    ``Transparent BSDF + Emission -> Add Shader`` is how EEVEE renders additive
    blending, and it is the graph :func:`blend_flags_from_material` reads back,
    so it is the *storage* for the flag as much as the preview.  Subtractive
    has no EEVEE equivalent at all -- it is the same graph with the emission
    colour inverted, which is this add-on's own convention and previews only
    approximately.  No shape in the corpus uses it.

    The Principled node is removed rather than left dangling: the visibility
    and decal wiring both look for one, and a disconnected node would make them
    fade a surface that no longer reaches the output.
    """
    nt = bmat.node_tree
    out = _output_node(nt)
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    base_color = (1.0, 1.0, 1.0, 1.0)
    if bsdf is not None:
        base_color = tuple(bsdf.inputs["Base Color"].default_value)
        nt.nodes.remove(bsdf)

    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, 460)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-200, 240)
    emission.inputs["Color"].default_value = base_color
    add = nt.nodes.new("ShaderNodeAddShader")
    add.location = (60, 360)

    source = tex.outputs["Color"] if tex is not None else None
    if subtractive:
        invert = nt.nodes.new("ShaderNodeInvert")
        invert.location = (-420, 240)
        invert.inputs["Factor"].default_value = 1.0
        if source is not None:
            nt.links.new(source, invert.inputs["Color"])
        else:
            invert.inputs["Color"].default_value = base_color
        source = invert.outputs["Color"]
    if source is not None:
        nt.links.new(source, emission.inputs["Color"])

    nt.links.new(transparent.outputs["BSDF"], add.inputs[0])
    nt.links.new(emission.outputs["Emission"], add.inputs[1])
    if out is not None:
        nt.links.new(add.outputs["Shader"], out.inputs["Surface"])


def emission_of_add_shader(nt):
    """The Emission node of the additive/subtractive graph, or None.

    Callers that fade a material need this because such a material has no
    Principled BSDF to push alpha into — the fade scales emission instead.
    """
    if nt is None:
        return None
    add = next((n for n in nt.nodes if n.type == "ADD_SHADER"), None)
    if add is None:
        return None
    for socket in add.inputs:
        for link in socket.links:
            if link.from_node.type == "EMISSION":
                return link.from_node
    return None


def fade_emission(nt, emission) -> bool:
    """Multiply object alpha into an emission strength.

    The alpha-into-Principled fade has nothing to grab on an additive material
    -- there is no Principled node and no alpha to speak of -- so a fade scales
    how much light it adds instead, which is what fading a glow means.
    """
    info = nt.nodes.new("ShaderNodeObjectInfo")
    info.location = (emission.location.x - 600, emission.location.y - 200)
    if "Alpha" not in info.outputs:
        nt.nodes.remove(info)
        return False
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.location = (emission.location.x - 300, emission.location.y - 200)
    strength = emission.inputs["Strength"]
    if strength.is_linked:
        nt.links.new(strength.links[0].from_socket, mul.inputs[0])
    else:
        mul.inputs[0].default_value = strength.default_value
    nt.links.new(info.outputs["Alpha"], mul.inputs[1])
    nt.links.new(mul.outputs[0], strength)
    return True


def remember_blend_state(mat) -> None:
    """Record the material's blend state before a fade forces blending.

    Export reads `MAT_TRANSLUCENT` off the material, and both the visibility
    and decal wiring set the render method to BLENDED whatever the shape said.
    Without this an opaque material that merely fades would come back
    translucent.  Written once, so re-wiring cannot overwrite the true value.
    """
    if "dts_blend_before_fade" in mat:
        return
    mat["dts_blend_before_fade"] = getattr(mat, "surface_render_method", None) or getattr(
        mat, "blend_method", "OPAQUE"
    )


def _set_blended(bmat) -> None:
    # Blender 4.2+ dropped blend_method from EEVEE Next; guard for both
    if hasattr(bmat, "blend_method"):
        bmat.blend_method = "BLEND"
    if hasattr(bmat, "surface_render_method"):
        bmat.surface_render_method = "BLENDED"


def blend_flags_from_material(bmat) -> int:
    """The translucent/additive/subtractive bits the material itself encodes.

    The material wins over the `dts_*` props for these three flags: editing the
    shader is how you change them.  Additive and subtractive both blend against
    the frame buffer, and every one of the 234 additive materials in the corpus
    also carries `MAT_TRANSLUCENT`, so the graph implies it.
    """
    emission = emission_of_add_shader(getattr(bmat, "node_tree", None))
    if emission is not None:
        color = emission.inputs["Color"]
        inverted = color.is_linked and color.links[0].from_node.type == "INVERT"
        return MAT_TRANSLUCENT | (MAT_SUBTRACTIVE if inverted else MAT_ADDITIVE)
    method = bmat.get("dts_blend_before_fade")
    if method is None:
        method = getattr(bmat, "surface_render_method", None) or getattr(
            bmat, "blend_method", "OPAQUE"
        )
    return MAT_TRANSLUCENT if method in ("BLENDED", "BLEND") else 0


# ----------------------------------------------------------------------
# the reflectance map: one RGBA file, two maps
# ----------------------------------------------------------------------


def _pixels_of(img) -> array:
    """An image's buffer as a flat array('f'), one memcpy."""
    buf = array("f", [0.0]) * (img.size[0] * img.size[1] * img.channels)
    img.pixels.foreach_get(buf)
    return buf


def _new_image(name: str, width: int, height: int, buf: array, *, colorspace: str, is_float: bool):
    """A packed image datablock from a flat float buffer.

    Packed, not saved: it has no file, and an unpacked generated image is lost
    when the .blend is saved.  ``examples/build_examples.py`` writes one instead
    because it is producing game data; this is scene data.

    Colour management is the trap here.  ``pixels`` is not colour-converted on
    the way in or out, so copying a buffer between two images of the same bit
    depth is exact *provided the colorspace matches* -- which is why the caller
    passes the source's rather than taking the default.
    """
    img = bpy.data.images.new(
        name, width=width, height=height, alpha=True, float_buffer=is_float
    )
    img.colorspace_settings.name = colorspace
    img.pixels.foreach_set(buf)
    img.update()
    img.pack()
    return img


def split_diffuse_and_reflectance(img):
    """Re-encode one RGBA texture as an RGB diffuse and a greyscale mask.

    None when there is nothing to split: no alpha channel, or an alpha that
    does not vary.  That second case is the common one, not the exotic one --
    practically all game art is RGBA and most of it is uniformly opaque, so
    "has an alpha channel" is nearly always true and nearly always means
    nothing.  Returning None there keeps the diffuse pointing at its file on
    disk instead of replacing it with a packed copy of itself.

    The mask is Non-Color: an alpha channel is data, and putting it through an
    sRGB curve would change the numbers the engine reads back.
    """
    key = img.filepath or img.name
    cached = _split_images.get(key)
    if cached is not None:
        return cached
    if img.channels != 4 or not img.has_data:
        return None
    buf = _pixels_of(img)
    if alpha_is_uniform(buf):
        return None

    diffuse_buf, refl_buf = split_rgba(buf)
    width, height = img.size
    stem = Path(img.name).stem or img.name
    diffuse = _new_image(
        f"{stem}.diffuse", width, height, diffuse_buf,
        colorspace=img.colorspace_settings.name, is_float=img.is_float,
    )
    reflectance = _new_image(
        f"{stem}.reflectance", width, height, refl_buf,
        colorspace="Non-Color", is_float=img.is_float,
    )
    _split_images[key] = (diffuse, reflectance)
    return diffuse, reflectance


def merged_reflectance_image(diffuse_img, reflectance_img):
    """The inverse of :func:`split_diffuse_and_reflectance`, for export.

    None on a size mismatch -- two textures of different resolutions have no
    single RGBA file, and the caller warns rather than silently resampling.
    """
    if diffuse_img is None or reflectance_img is None:
        return None
    try:
        merged = merge_rgba(_pixels_of(diffuse_img), _pixels_of(reflectance_img))
    except ValueError:
        return None
    width, height = diffuse_img.size
    stem = Path(diffuse_img.name).stem or diffuse_img.name
    return _new_image(
        f"{stem}.combined", width, height, merged,
        colorspace=diffuse_img.colorspace_settings.name,
        is_float=diffuse_img.is_float,
    )


def _image_node_feeding(bmat, socket_name: str):
    """The Image Texture node reaching a Principled input, or None.

    Follows a chain of single-input nodes, so a hand-built graph that runs the
    map through an Invert or a Math node still reads back as that map.  There
    is no Principled node on an additive material (:func:`_build_add_shader`
    removes it), which is why those have no reflectance.
    """
    nt = getattr(bmat, "node_tree", None)
    if nt is None:
        return None
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or socket_name not in bsdf.inputs:
        return None
    socket = bsdf.inputs[socket_name]
    seen = set()
    while socket.is_linked:
        node = socket.links[0].from_node
        if node.type == "TEX_IMAGE":
            return node if node.image is not None else None
        if node.name in seen:
            return None
        seen.add(node.name)
        upstream = next((i for i in node.inputs if i.is_linked), None)
        if upstream is None:
            return None
        socket = upstream
    return None


def diffuse_image_node(bmat):
    """The node feeding Base Color -- the material's own texture."""
    return _image_node_feeding(bmat, "Base Color")


def reflectance_image_node(bmat):
    """The node feeding Metallic -- the material's reflectance map.

    The *node*, not the image, because export has to tell "one node feeds both
    sockets" (already combined) from "a second node" (separable).
    """
    return _image_node_feeding(bmat, "Metallic")


def is_translucent(bobj) -> bool:
    """Whether any material on an object blends.

    Read off the shader through :func:`blend_flags_from_material`, not off a
    stored prop, because the shader is what export writes -- a material
    switched to OPAQUE in the node editor is not translucent whatever anything
    else says.  Additive and subtractive count: both carry `MAT_TRANSLUCENT`.

    Any slot, not all of them: one blended surface is enough to put a mesh's
    triangles in the wrong order.  Both the export-side promotion to a sorted
    mesh and the importer's decision not to record that promotion ask this.
    """
    return any(
        slot.material is not None and blend_flags_from_material(slot.material) & MAT_TRANSLUCENT
        for slot in bobj.material_slots
    )


def _map_ref(value: int, own_index: int, all_mats: list[Material]) -> str:
    if value == NO_MAP:
        return "none"
    if value == own_index:
        return "self"
    if 0 <= value < len(all_mats):
        return all_mats[value].name
    return "none"


def _texture_node(bmat, name: str, search_dir: Path | None, location):
    """An Image Texture node for a DTS material name, or None if not on disk."""
    path = find_texture(name, search_dir)
    if path is None:
        return None
    node = bmat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = bpy.data.images.load(str(path), check_existing=True)
    node.location = location
    return node


def _wire_textures(bmat, bsdf, mat, index, all_mats, search_dir, warnings):
    """Build the diffuse and reflectance image nodes; return the diffuse one.

    The alpha channel of a DTS texture means two different things and the file
    says which only by implication.  When the material is env-mapped it is the
    reflectance mask; otherwise it is transparency.  That is not a guess: the
    engine's own upgrade rule for shapes older than v16 forces
    ``MAT_NEVER_ENV_MAP`` onto every translucent material
    (``dtslib/matlist.py``), which is the format saying the two readings are
    exclusive.  270 of the 3185 materials in the corpus are env-mapped and 6 of
    those are also translucent.
    """
    links = bmat.node_tree.links
    props = getattr(bmat, "dts_material", None)

    # the full name, not basename: the path prefix is the lookup hint
    diffuse = _texture_node(bmat, mat.name, search_dir, (-350, 300))
    if diffuse is None:
        return None
    links.new(diffuse.outputs["Color"], bsdf.inputs["Base Color"])

    translucent = bool(mat.flags & MAT_TRANSLUCENT)
    if translucent:
        links.new(diffuse.outputs["Alpha"], bsdf.inputs["Alpha"])

    # additive and subtractive materials lose their Principled node entirely
    # (see _build_add_shader), so there is no Metallic input to reach.  None in
    # the corpus is both additive and env-mapped.
    if mat.flags & MAT_NEVER_ENV_MAP or mat.flags & (MAT_ADDITIVE | MAT_SUBTRACTIVE):
        return diffuse

    if mat.reflectance_map == index:
        if translucent:
            # one alpha, two meanings, and this material claims both.
            # Transparency wins -- 2015 materials depend on that reading and 6
            # on this one -- but the mask is still previewed off the same node,
            # and export sees one node feeding both sockets and keeps the
            # combined packing.
            links.new(diffuse.outputs["Alpha"], bsdf.inputs["Metallic"])
            warnings.append(
                f"material {mat.name!r} is both env-mapped and translucent: its "
                f"alpha is read as transparency, and as the reflectance mask only "
                f"in preview"
            )
            return diffuse
        split = split_diffuse_and_reflectance(diffuse.image)
        if split is None:
            return diffuse
        diffuse.image, reflectance_img = split
        reflectance = bmat.node_tree.nodes.new("ShaderNodeTexImage")
        reflectance.image = reflectance_img
        reflectance.location = (-350, -80)
        links.new(reflectance.outputs["Color"], bsdf.inputs["Metallic"])
        if props is not None:
            props.combine_reflectance = True
        return diffuse

    if 0 <= mat.reflectance_map < len(all_mats):
        reflectance = _texture_node(
            bmat, all_mats[mat.reflectance_map].name, search_dir, (-350, -80)
        )
        if reflectance is not None:
            links.new(reflectance.outputs["Color"], bsdf.inputs["Metallic"])
            if props is not None:
                props.combine_reflectance = False
    return diffuse


# ----------------------------------------------------------------------
# IFL: the material's own flipbook
# ----------------------------------------------------------------------

IFL_VALUE_NODE = "dts_ifl_frame"
IFL_NODE_LABEL = "dts_ifl"


def load_ifl_frames(bmat, dts_name: str, search_dir: Path | None, warnings) -> int:
    """Read the material's ``.ifl`` into its frame list.  Returns frame count.

    The frames are the authored data -- the file's own IFL table says only
    *which* material flips -- so they come in as a typed collection the user
    can edit, and export writes them back out as the ``.ifl``.

    A sidecar that cannot be found is a warning, not a failure: the material is
    still an IFL material and still needs its table entry, it just has no
    frames to show.  All 35 the corpus names do resolve.
    """
    from .ifl import ifl_name_for, parse_ifl

    props = getattr(bmat, "dts_material", None)
    if props is None:
        return 0
    props.is_ifl = True
    props.ifl_frames.clear()

    path = find_ifl(ifl_name_for(dts_name), search_dir)
    if path is None:
        warnings.append(
            f"material {dts_name!r} is an IFL material but {ifl_name_for(dts_name)!r} "
            f"was not found; its frames are missing and export will write none"
        )
        return 0
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        warnings.append(f"material {dts_name!r}: could not read {path}: {e}")
        return 0

    missing = 0
    for name, duration in parse_ifl(text):
        frame = props.ifl_frames.add()
        frame.duration = duration
        # beside the .ifl first: a flipbook's frames usually ship with the list
        # that names them, which is not always where the shape's own texture is
        tex = find_texture(name, path.parent) or find_texture(name, search_dir)
        if tex is None:
            missing += 1
            continue
        frame.image = bpy.data.images.load(str(tex), check_existing=True)
    if missing:
        warnings.append(
            f"material {dts_name!r}: {missing} of {len(props.ifl_frames)} IFL frame "
            f"texture(s) were not found beside the shape"
        )
    return len(props.ifl_frames)


def _clear_ifl_preview(nt) -> None:
    """Drop a previous preview, so a re-import does not stack graphs."""
    for node in [n for n in nt.nodes if n.label == IFL_NODE_LABEL]:
        nt.nodes.remove(node)


def build_ifl_preview(bmat) -> int:
    """Show the flipbook: the frame list as a keyframed switch of images.

    Derived, and regenerated rather than stored -- the durations are what the
    user edits and this is only their running total, the same relationship the
    decal coverage attribute has to its projector.

    Keyframes rather than a driver.  A driver expression cannot look a schedule
    up, and Blender only evaluates *bare* comparisons natively, falling back to
    full Python -- which silently yields 0.0 without auto-run -- for anything
    richer (``mapping/decals.py``).  Blender's own image sequences cannot
    express this either: the order is not monotonic and the holds vary.

    One Image Texture node per *distinct* image, not per frame: the corpus's
    worst file is 210 frames over 6 textures.
    """
    from .ifl import frame_schedule

    props = getattr(bmat, "dts_material", None)
    nt = getattr(bmat, "node_tree", None)
    if props is None or nt is None:
        return 0
    _clear_ifl_preview(nt)
    frames = [f for f in props.ifl_frames if f.image is not None]
    # where the flipbook's colour goes.  An additive material has no Principled
    # node -- _build_add_shader removes it -- and its colour is the emission,
    # which is exactly what an IFL flare is: 46 of the 64 corpus IFL materials
    # are additive, so this is the common case rather than the exception.
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    emission = emission_of_add_shader(nt)
    if emission is not None:
        target = emission.inputs["Color"]
    elif bsdf is not None:
        target = bsdf.inputs["Base Color"]
        emission = bsdf  # only for laying the nodes out relative to something
    else:
        return 0
    if not frames:
        return 0
    bsdf = emission

    slots: list = []
    slot_of: dict[str, int] = {}
    for frame in frames:
        if frame.image.name not in slot_of:
            slot_of[frame.image.name] = len(slots)
            slots.append(frame.image)

    value = nt.nodes.new("ShaderNodeValue")
    value.name = value.label = IFL_VALUE_NODE
    value.location = (bsdf.location.x - 1200, bsdf.location.y + 400)

    def _texture(image, row):
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.label = IFL_NODE_LABEL
        node.location = (bsdf.location.x - 900, bsdf.location.y + 400 - row * 300)
        return node

    accumulated = _texture(slots[0], 0).outputs["Color"]
    for slot, image in enumerate(slots[1:], start=1):
        compare = nt.nodes.new("ShaderNodeMath")
        compare.operation = "COMPARE"
        compare.label = IFL_NODE_LABEL
        compare.location = (bsdf.location.x - 620, bsdf.location.y + 400 - slot * 300)
        nt.links.new(value.outputs[0], compare.inputs[0])
        compare.inputs[1].default_value = float(slot)
        # the index is an integer, so half a unit is the whole window
        compare.inputs[2].default_value = 0.5

        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.label = IFL_NODE_LABEL
        mix.location = (bsdf.location.x - 400, bsdf.location.y + 400 - slot * 300)
        sockets = [i for i in mix.inputs if i.enabled]
        nt.links.new(compare.outputs[0], sockets[0])
        nt.links.new(accumulated, sockets[1])
        nt.links.new(_texture(image, slot).outputs["Color"], sockets[2])
        accumulated = next(o for o in mix.outputs if o.enabled)

    nt.links.new(accumulated, target)

    value.label = IFL_VALUE_NODE  # not IFL_NODE_LABEL: the keyframes live here
    for tick, index in frame_schedule([(f.image.name, f.duration) for f in frames]):
        value.outputs[0].default_value = float(slot_of[frames[index].image.name])
        value.outputs[0].keyframe_insert("default_value", frame=tick + 1)
    _make_constant(nt)
    return len(frames)


def _make_constant(nt) -> None:
    """A frame index does not interpolate; a frame between two frames is not
    a frame."""
    from .sequences import _iter_fcurves

    action = getattr(getattr(nt, "animation_data", None), "action", None)
    if action is None:
        return
    for fcurve in _iter_fcurves(action):
        if IFL_VALUE_NODE not in fcurve.data_path:
            continue
        for point in fcurve.keyframe_points:
            point.interpolation = "CONSTANT"
        fcurve.update()


def material_to_blender(
    mat: Material,
    index: int,
    all_mats: list[Material],
    search_dir: Path | None,
    warnings: list[str] | None = None,
) -> bpy.types.Material:
    bmat = bpy.data.materials.new(name=mat.basename or "material")
    bmat["dts_name"] = mat.name
    # names are not unique in real shapes (the same texture can appear twice
    # with different flags), so the index is the only reliable identity
    bmat["dts_material_index"] = index
    for prop, bit in _FLAG_PROPS.items():
        bmat[prop] = bool(mat.flags & bit)
    bmat["dts_detail_scale"] = mat.detail_scale
    bmat["dts_reflection_amount"] = mat.reflection_amount
    bmat["dts_reflectance_map"] = _map_ref(mat.reflectance_map, index, all_mats)
    bmat["dts_bump_map"] = _map_ref(mat.bump_map, index, all_mats)
    bmat["dts_detail_map"] = _map_ref(mat.detail_map, index, all_mats)

    bmat.use_nodes = True
    bsdf = next(n for n in bmat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 1.0

    tex = _wire_textures(
        bmat, bsdf, mat, index, all_mats, search_dir,
        warnings if warnings is not None else [],
    )

    # the shader *is* the storage for these three flags; export reads it back
    if mat.flags & (MAT_ADDITIVE | MAT_SUBTRACTIVE):
        _build_add_shader(bmat, tex, subtractive=bool(mat.flags & MAT_SUBTRACTIVE))
    if mat.flags & (MAT_TRANSLUCENT | MAT_ADDITIVE | MAT_SUBTRACTIVE):
        _set_blended(bmat)

    # an IFL material does not name a texture, it names a flipbook.  The
    # shape's table records only that this material flips; the frames are in
    # the .ifl beside it, and without them there is nothing to preview and
    # nothing a user could have authored.
    if mat.flags & MAT_IFL_MATERIAL:
        loaded = load_ifl_frames(
            bmat, mat.name, search_dir, warnings if warnings is not None else []
        )
        if loaded:
            build_ifl_preview(bmat)
    return bmat


def ifl_materials_from_blender(shape, bmats):
    """Derive the shape's IFL table from the materials that flip.

    The file's entry is (name, material slot, three ints).  Both real fields
    are implied by the material -- an entry exists *because* a material flips,
    and its name is that material's name plus ``.ifl``, which holds byte for
    byte across all 64 corpus entries -- so nothing about the table is stored
    beside the material that already says it.

    The last three ints are written as zero.  They are engine load-time scratch
    filled from the ``.ifl`` itself: 53 of the 64 corpus entries carry
    uninitialised memory there (``0xCDCDCDCD``, float bit patterns, a
    ``numFrames`` of -2147483648 for a 120-line file), the pre-v18 upgrade path
    already writes zeros, and 11 shipped entries are already zero.  ``numFrames``
    is the one that is real, and it is ``len(frames)``.

    Built in material-list order, because the sequences' ``ifl_matters`` bits
    are positional.

    Returns (entries, texture writes, warnings).
    """
    from .ifl import format_ifl, ifl_name_for

    entries: list[Material] = []
    writes: list[TextureWrite] = []
    warnings: list[str] = []
    for slot, bmat in enumerate(bmats):
        props = getattr(bmat, "dts_material", None)
        if props is None or not props.is_ifl:
            continue
        dts_name = str(bmat.get("dts_name") or bmat.name)
        # Bare, not prefixed.  The two engine loaders disagree about what a
        # material name's path prefix means: MaterialList strips it and opens
        # the texture beside the .dts ("Material paths are a legacy of Tribes
        # tools, strip them off" -- dgl/materialList.cc:231), while
        # readIflMaterials converts the backslashes and opens
        # shapePath/<name> verbatim (ts/tsShape.cc:1387).  A prefixed .ifl name
        # therefore sends the engine into a subdirectory the textures are not
        # in, and cannot be in.  The bare name is the one spelling both loaders
        # resolve to the same directory.
        ifl_name = ifl_name_for(Material(name=dts_name).basename)
        frames = list(props.ifl_frames)
        entries.append(
            IflMaterial((shape.add_name(ifl_name), slot, 0, 0, len(frames)))
        )
        if not frames:
            warnings.append(
                f"material {dts_name!r} is an IFL material with no frames; the "
                f"shape names {ifl_name!r} and export writes no such file"
            )
            continue

        lines = []
        seen_images: set[str] = set()
        for frame in frames:
            if frame.image is None:
                warnings.append(
                    f"material {dts_name!r}: an IFL frame has no image and was skipped"
                )
                continue
            lines.append((_ifl_frame_filename(frame.image), frame.duration))
            # once per *distinct* image: a flipbook repeats its frames -- 210
            # lines over 6 textures in the corpus's largest -- and registering
            # a write per line would warn about a collision on every repeat
            if frame.image.name not in seen_images:
                seen_images.add(frame.image.name)
                writes.append(
                    TextureWrite(frame.image, _ifl_frame_filename(frame.image), dts_name)
                )
        writes.append(
            TextureWrite(format_ifl(lines), Material(name=ifl_name).basename, dts_name)
        )
    return entries, writes, warnings


def ifl_filename(bmat) -> str:
    """The .ifl this material's frames are written to, bare filename."""
    from .ifl import ifl_name_for

    dts_name = str(bmat.get("dts_name") or bmat.name)
    return Material(name=ifl_name_for(dts_name)).basename


def _ifl_frame_filename(image) -> str:
    """What an IFL line calls a frame: a bare filename with an extension."""
    stem = Path(image.filepath).stem if image.filepath else Path(image.name).stem
    return f"{stem or image.name}.png"


def _flags_from_blender(bmat: bpy.types.Material) -> int:
    # a material created in Blender rather than imported has no flag props at
    # all; wrapping in both directions is the engine-safe default
    if not any(prop in bmat for prop in _FLAG_PROPS):
        flags = MAT_S_WRAP | MAT_T_WRAP
    else:
        flags = 0
        for prop, bit in _FLAG_PROPS.items():
            if bmat.get(prop):
                flags |= bit
    # ...and the derived bits, each owned by the one place it can be edited:
    # the blend bits by the node graph, MAT_IFL_MATERIAL by the checkbox that
    # also owns the frame list.  An ID property only the importer writes could
    # be cleared and never set, which is what made IFL unauthorable.
    props = getattr(bmat, "dts_material", None)
    if props is not None and props.is_ifl:
        flags |= MAT_IFL_MATERIAL
    return flags | blend_flags_from_material(bmat)


@dataclass
class TextureWrite:
    """Something export has to put on disk beside the .dts, and what to call it.

    ``image`` is an image datablock, or the text of a generated sidecar -- an
    ``.ifl`` has no datablock to be, and routing it through the same list is
    what gives it the same collision rule and the same refusal to overwrite
    art the scene loaded.
    """

    image: object
    filename: str
    owner: str  # the DTS material name, for the warning text


@dataclass
class _Synthetic:
    """A reflectance texture with no material-list entry of its own yet."""

    image: bpy.types.Image
    owners: list[int] = field(default_factory=list)


def _png_name(dts_name: str) -> str:
    """The file the engine will look for, given a material name."""
    from .texture_split import strip_texture_extension

    return strip_texture_extension(Material(name=dts_name).basename) + ".png"


def _resolve_reflectance_images(mats, bmats, diffuse_nodes, refl_nodes, warnings):
    """Point each reflectance slot at the image the material's shader shows.

    Runs after the name-based slots have been resolved, so nothing here can
    disturb them, and appends any entry it has to invent to the *end* of the
    list.  That is safe because every ``mat_index`` a primitive or decal holds
    was fixed before ``shape.materials`` was built at all
    (``mapping/blender_to_shape.py``), so entries past the used ones cannot
    move anything.

    Returns the textures the caller has to write.
    """
    writes: list[TextureWrite] = []

    # any material's own texture is a reflectance target for free
    owner_of: dict[str, int] = {}
    for i, node in enumerate(diffuse_nodes):
        if node is not None and node.image is not None:
            owner_of.setdefault(node.image.name, i)

    synthetic: dict[str, _Synthetic] = {}
    # materials whose own texture is already accounted for, either because a
    # combined image was registered under its name or because the file it came
    # from already holds both maps
    handled: set[int] = set()

    for i, bmat in enumerate(bmats):
        reflectance, diffuse, mat = refl_nodes[i], diffuse_nodes[i], mats[i]
        if reflectance is None:
            continue
        props = getattr(bmat, "dts_material", None)
        combine = True if props is None else bool(props.combine_reflectance)

        # the same image on both sockets is already the combined packing,
        # whatever the checkbox says -- there is nothing to separate.  `==`,
        # not `is`: bpy hands out a fresh Python wrapper per access, and only
        # `==` compares the datablock behind it
        if diffuse is not None and diffuse.image == reflectance.image:
            mat.reflectance_map = i
            continue

        if combine:
            mat.reflectance_map = i
            if diffuse is None:
                warnings.append(
                    f"material {mat.name!r}: reflectance is combined into the "
                    f"diffuse, but the material has no diffuse texture to hold it"
                )
                continue
            # an untouched split re-combines to the file it came from, so this
            # is a copy rather than a change -- which is the point, since the
            # engine needs that file beside the .dts either way
            merged = merged_reflectance_image(diffuse.image, reflectance.image)
            if merged is None:
                warnings.append(
                    f"material {mat.name!r}: diffuse is {tuple(diffuse.image.size)} "
                    f"and reflectance is {tuple(reflectance.image.size)}; they "
                    f"cannot share one texture, so the reflectance was dropped"
                )
                continue
            writes.append(TextureWrite(merged, _png_name(mat.name), mat.name))
            handled.add(i)
            continue

        owner = owner_of.get(reflectance.image.name)
        if owner is not None:
            mat.reflectance_map = owner
            continue
        # matched on the image datablock rather than a name, so this stays out
        # of the name-aliasing hazard the named map slots have
        synthetic.setdefault(
            reflectance.image.name, _Synthetic(reflectance.image)
        ).owners.append(i)

    for record in synthetic.values():
        index = len(mats)
        stem = Path(record.image.filepath).stem if record.image.filepath else None
        name = reflectance_material_name(mats[record.owners[0]].name, stem)
        mats.append(
            Material(
                name=name,
                # env-mapping off on the entry itself: it exists to be pointed
                # at, not drawn.  Self-index rather than NO_MAP for the reason
                # the fresh-material branch gives.
                flags=MAT_S_WRAP | MAT_T_WRAP | MAT_NEVER_ENV_MAP | MAT_REFLECTANCE_MAP_ONLY,
                reflectance_map=index,
                bump_map=NO_MAP,
                detail_map=NO_MAP,
            )
        )
        for owner in record.owners:
            mats[owner].reflectance_map = index
        writes.append(TextureWrite(record.image, _png_name(name), name))

    # and every ordinary texture, whether it was made in Blender or loaded from
    # somewhere else on disk.  A material names a texture the engine looks for
    # beside the .dts, and nothing else is going to put it there.
    for i, node in enumerate(diffuse_nodes):
        if i in handled or node is None or node.image is None:
            continue
        writes.append(TextureWrite(node.image, _png_name(mats[i].name), mats[i].name))

    return writes


def materials_from_blender(
    bmats: list[bpy.types.Material],
) -> tuple[list[Material], list[TextureWrite], list[str]]:
    """Build the material list, resolve map references, resolve reflectance.

    Four passes: build, resolve names, apply the stored slots, then let the
    shader override the reflectance slot for any material that shows one.
    """
    warnings: list[str] = []
    mats: list[Material] = []
    diffuse_nodes = [diffuse_image_node(b) for b in bmats]
    refl_nodes = [reflectance_image_node(b) for b in bmats]
    for bmat in bmats:
        name = str(bmat.get("dts_name") or bmat.name)
        flags = _flags_from_blender(bmat)
        mats.append(
            Material(
                name=name,
                flags=flags,
                detail_scale=float(bmat.get("dts_detail_scale", 1.0)),
                reflection_amount=float(bmat.get("dts_reflection_amount", 1.0)),
            )
        )

    index_by_name = {m.name.lower(): i for i, m in enumerate(mats)}

    def resolve(ref: str | None, own_index: int, default: int) -> int:
        if ref is None:
            return default
        if ref == "self":
            return own_index
        if ref == "none":
            return NO_MAP
        idx = index_by_name.get(str(ref).lower())
        if idx is None:
            warnings.append(
                f"material {mats[own_index].name!r}: map reference {ref!r} "
                f"not among exported materials; dropped"
            )
            return default
        return idx

    for i, (bmat, mat) in enumerate(zip(bmats, mats)):
        has_refs = _MAP_PROPS[0] in bmat
        if has_refs:
            # imported material: reproduce the stored slots verbatim
            mat.reflectance_map = resolve(bmat.get("dts_reflectance_map"), i, i)
            mat.bump_map = resolve(bmat.get("dts_bump_map"), i, NO_MAP)
            mat.detail_map = resolve(bmat.get("dts_detail_map"), i, NO_MAP)
        else:
            # new material: engine-safe defaults — reflectance points at self
            # and env-mapping is off.  Never write 0xFFFFFFFF reflectance for
            # a material that could env-map: the engine NULL-derefs on
            # env-mapped render (tsMesh.cc:921).
            mat.reflectance_map = i
            mat.bump_map = NO_MAP
            mat.detail_map = NO_MAP
            if not (mat.flags & MAT_NEVER_ENV_MAP) and "dts_never_env_map" not in bmat:
                mat.flags |= MAT_NEVER_ENV_MAP
        if refl_nodes[i] is not None:
            # A reflectance map the engine is told never to read is not a
            # feature, so showing one is how you ask for env-mapping -- and it
            # outranks both the checkbox and the default just above, which is
            # why that default needs no exception of its own.
            mat.flags &= ~MAT_NEVER_ENV_MAP

    writes = _resolve_reflectance_images(mats, bmats, diffuse_nodes, refl_nodes, warnings)
    return mats, writes, warnings
