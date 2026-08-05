"""Blender material <-> dtslib.Material translation.

The dts_* custom properties on the Blender material are the round-trip source
of truth; viewport/shader settings are cosmetic.

The reflectance/bump/detail map slots hold *indices into the material list*;
they are stored on the Blender material as name references ("self", "none",
or the referenced material's DTS name) so they survive reordering, and are
resolved back to indices in a second pass on export.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..dtslib.types import (
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

_TEXTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds")

# Every defined bit of the material flags word, each with its own checkbox.
#
# There used to be a packed `dts_flags` beside these holding whatever had no
# named property -- four bits nobody had named, plus a copy of the ten that
# were.  That is two sources for one value, and it came with its own bug:
# Blender's integer ID-properties are a C int, so MAT_REFLECTANCE_MAP_ONLY
# (1 << 31) could not be stored in it at all and needed a special case.  With
# every bit named, the word is assembled on export and nothing is packed.
_FLAG_PROPS = {
    "dts_s_wrap": MAT_S_WRAP,
    "dts_t_wrap": MAT_T_WRAP,
    "dts_translucent": MAT_TRANSLUCENT,
    "dts_additive": MAT_ADDITIVE,
    "dts_subtractive": MAT_SUBTRACTIVE,
    "dts_self_illuminating": MAT_SELF_ILLUMINATING,
    "dts_never_env_map": MAT_NEVER_ENV_MAP,
    "dts_no_mip_map": MAT_NO_MIP_MAP,
    "dts_mip_map_zero_border": MAT_MIP_MAP_ZERO_BORDER,
    "dts_ifl_material": MAT_IFL_MATERIAL,
    "dts_ifl_frame": MAT_IFL_FRAME,
    "dts_detail_map_only": MAT_DETAIL_MAP_ONLY,
    "dts_bump_map_only": MAT_BUMP_MAP_ONLY,
    "dts_reflectance_map_only": MAT_REFLECTANCE_MAP_ONLY,
}

# derived from the shader, not the props -- see blend_flags_from_material
_BLEND_BITS = MAT_TRANSLUCENT | MAT_ADDITIVE | MAT_SUBTRACTIVE

_MAP_PROPS = ("dts_reflectance_map", "dts_bump_map", "dts_detail_map")


# resolved textures/ dir -> {lowercase stem: path}, rebuilt per import
_texture_index: dict[Path, dict[str, Path]] = {}


def reset_texture_cache() -> None:
    """Drop the sibling-textures index so a re-import picks up new files."""
    _texture_index.clear()


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


def _indexed(root: Path) -> dict[str, Path]:
    """Case-insensitive stem -> path over a texture tree, cached.

    Textures sit in subdirectories (``textures/skins/foo.png``), so the index
    is keyed on the bare stem and built recursively; it is the fallback for
    when a material's own path prefix does not locate the file.
    """
    key = root.resolve()
    cached = _texture_index.get(key)
    if cached is not None:
        return cached
    index: dict[str, Path] = {}
    # sort so a stem present more than once (case-variant duplicates exist in
    # real game data) always resolves to the same file
    for p in sorted(root.rglob("*"), key=lambda q: (len(q.parts), str(q).lower())):
        if p.suffix.lower() in _TEXTURE_EXTENSIONS and p.is_file():
            index.setdefault(p.stem.lower(), p)
    _texture_index[key] = index
    return index


def _match_keys(name: str) -> list[str]:
    """Index keys to try for a material name, best first.

    Material names routinely contain dots that are *not* extensions
    ("base.lmale", "armor.damage.1"), so the whole name is the primary key --
    ``Path(name).stem`` would truncate those to "base" / "armor.damage".  The
    stem is kept as a fallback for the rare name that does carry an image
    extension.
    """
    keys = [name.lower()]
    if Path(name).suffix.lower() in _TEXTURE_EXTENSIONS:
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
    if search_dir is None or not search_dir.is_dir():
        return None
    prefix, bare = _split_material_name(name)
    keys = _match_keys(bare)
    local = {
        p.stem.lower(): p
        for p in sorted(search_dir.iterdir(), key=lambda q: str(q).lower())
        if p.suffix.lower() in _TEXTURE_EXTENSIONS
    }
    for key in keys:
        if key in local:
            return local[key]
    tex_dir = sibling_texture_dir(search_dir)
    if tex_dir is None:
        return None
    sub = _prefixed_dir(tex_dir, prefix)
    for root in ([sub] if sub is not None else []) + [tex_dir]:
        index = _indexed(root)
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


def _map_ref(value: int, own_index: int, all_mats: list[Material]) -> str:
    if value == NO_MAP:
        return "none"
    if value == own_index:
        return "self"
    if 0 <= value < len(all_mats):
        return all_mats[value].name
    return "none"


def material_to_blender(
    mat: Material, index: int, all_mats: list[Material], search_dir: Path | None
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

    # the full name, not basename: the path prefix is the lookup hint
    tex = None
    tex_path = find_texture(mat.name, search_dir)
    if tex_path is not None:
        img = bpy.data.images.load(str(tex_path), check_existing=True)
        tex = bmat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-350, 300)
        bmat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if mat.flags & MAT_TRANSLUCENT:
            bmat.node_tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])

    # the shader *is* the storage for these three flags; export reads it back
    if mat.flags & (MAT_ADDITIVE | MAT_SUBTRACTIVE):
        _build_add_shader(bmat, tex, subtractive=bool(mat.flags & MAT_SUBTRACTIVE))
    if mat.flags & (MAT_TRANSLUCENT | MAT_ADDITIVE | MAT_SUBTRACTIVE):
        _set_blended(bmat)
    return bmat


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
    # ...and the material overrides both for the three blend bits
    return (flags & ~_BLEND_BITS) | blend_flags_from_material(bmat)


def _sync_blend_props(bmat, flags: int) -> None:
    """Write the derived blend bits back, so the props never contradict the
    shader they no longer control."""
    for prop, bit in (
        ("dts_translucent", MAT_TRANSLUCENT),
        ("dts_additive", MAT_ADDITIVE),
        ("dts_subtractive", MAT_SUBTRACTIVE),
    ):
        bmat[prop] = bool(flags & bit)


def materials_from_blender(bmats: list[bpy.types.Material]) -> tuple[list[Material], list[str]]:
    """Two-pass conversion: build the list, then resolve map references."""
    warnings: list[str] = []
    mats: list[Material] = []
    for bmat in bmats:
        name = str(bmat.get("dts_name") or bmat.name)
        flags = _flags_from_blender(bmat)
        _sync_blend_props(bmat, flags)
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
    return mats, warnings
