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
    MAT_IFL_MATERIAL,
    MAT_NEVER_ENV_MAP,
    MAT_NO_MIP_MAP,
    MAT_S_WRAP,
    MAT_SELF_ILLUMINATING,
    MAT_SUBTRACTIVE,
    MAT_T_WRAP,
    MAT_TRANSLUCENT,
    NO_MAP,
    Material,
)

_TEXTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds")

_FLAG_PROPS = {
    "dts_s_wrap": MAT_S_WRAP,
    "dts_t_wrap": MAT_T_WRAP,
    "dts_translucent": MAT_TRANSLUCENT,
    "dts_additive": MAT_ADDITIVE,
    "dts_subtractive": MAT_SUBTRACTIVE,
    "dts_self_illuminating": MAT_SELF_ILLUMINATING,
    "dts_never_env_map": MAT_NEVER_ENV_MAP,
    "dts_no_mip_map": MAT_NO_MIP_MAP,
    "dts_ifl_material": MAT_IFL_MATERIAL,
}

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


def _indexed(root: Path) -> dict[str, Path]:
    """Case-insensitive stem -> path over a texture tree, cached.

    Textures sit in subdirectories (``textures/skins/foo.png``) and material
    names carry at most a legacy prefix that ``Material.basename`` already
    strips, so the index is keyed on the bare stem and built recursively.
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
    """Find an image for a material name (case-insensitive).

    Looks next to the .dts first, then in a sibling ``textures/`` tree when the
    shape lives in a directory named ``shapes``.
    """
    if search_dir is None or not search_dir.is_dir():
        return None
    keys = _match_keys(name)
    local = {
        p.stem.lower(): p
        for p in sorted(search_dir.iterdir(), key=lambda q: str(q).lower())
        if p.suffix.lower() in _TEXTURE_EXTENSIONS
    }
    for key in keys:
        if key in local:
            return local[key]
    tex_dir = sibling_texture_dir(search_dir)
    if tex_dir is not None:
        index = _indexed(tex_dir)
        for key in keys:
            if key in index:
                return index[key]
    return None


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
    bmat["dts_flags"] = mat.flags
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

    tex_path = find_texture(mat.basename, search_dir)
    if tex_path is not None:
        img = bpy.data.images.load(str(tex_path), check_existing=True)
        tex = bmat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-350, 300)
        bmat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if mat.flags & MAT_TRANSLUCENT:
            bmat.node_tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])

    if mat.flags & MAT_TRANSLUCENT:
        # Blender 4.2+ dropped blend_method from EEVEE Next; guard for both
        if hasattr(bmat, "blend_method"):
            bmat.blend_method = "BLEND"
        if hasattr(bmat, "surface_render_method"):
            bmat.surface_render_method = "BLENDED"
    return bmat


def _flags_from_blender(bmat: bpy.types.Material) -> int:
    if "dts_flags" in bmat:
        flags = int(bmat["dts_flags"])
        for prop, bit in _FLAG_PROPS.items():
            if prop in bmat:  # checkbox props override the packed word
                flags = (flags | bit) if bmat[prop] else (flags & ~bit)
        return flags
    flags = MAT_S_WRAP | MAT_T_WRAP
    for prop, bit in _FLAG_PROPS.items():
        if bmat.get(prop):
            flags |= bit
    return flags


def materials_from_blender(bmats: list[bpy.types.Material]) -> tuple[list[Material], list[str]]:
    """Two-pass conversion: build the list, then resolve map references."""
    warnings: list[str] = []
    mats: list[Material] = []
    for bmat in bmats:
        name = str(bmat.get("dts_name") or bmat.name)
        mats.append(
            Material(
                name=name,
                flags=_flags_from_blender(bmat),
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
