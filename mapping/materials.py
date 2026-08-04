"""Blender material <-> dtslib.Material translation.

The dts_* custom properties on the Blender material are the round-trip source
of truth; viewport/shader settings are cosmetic.
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


def find_texture(name: str, search_dir: Path | None) -> Path | None:
    """Find an image for a material name next to the .dts (case-insensitive)."""
    if search_dir is None or not search_dir.is_dir():
        return None
    stem = Path(name).stem.lower()
    for p in search_dir.iterdir():
        if p.suffix.lower() in _TEXTURE_EXTENSIONS and p.stem.lower() == stem:
            return p
    return None


def material_to_blender(mat: Material, search_dir: Path | None) -> bpy.types.Material:
    bmat = bpy.data.materials.new(name=mat.basename or "material")
    bmat["dts_name"] = mat.name
    bmat["dts_flags"] = mat.flags
    for prop, bit in _FLAG_PROPS.items():
        bmat[prop] = bool(mat.flags & bit)
    bmat["dts_detail_scale"] = mat.detail_scale
    bmat["dts_reflection_amount"] = mat.reflection_amount

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


def material_from_blender(bmat: bpy.types.Material, index: int) -> Material:
    name = bmat.get("dts_name") or bmat.name
    if "dts_flags" in bmat:
        flags = int(bmat["dts_flags"])
        for prop, bit in _FLAG_PROPS.items():
            if prop in bmat:  # checkbox props override the packed word
                flags = (flags | bit) if bmat[prop] else (flags & ~bit)
    else:
        flags = MAT_S_WRAP | MAT_T_WRAP
        if bmat.get("dts_translucent"):
            flags |= MAT_TRANSLUCENT

    mat = Material(name=name, flags=flags)
    # never write 0xFFFFFFFF reflectance: the engine NULL-derefs on env-mapped
    # render (tsMesh.cc:921) — point at self and keep env-mapping off instead
    mat.reflectance_map = index
    if not (flags & MAT_NEVER_ENV_MAP) and "dts_never_env_map" not in bmat:
        mat.flags |= MAT_NEVER_ENV_MAP
    mat.detail_scale = float(bmat.get("dts_detail_scale", 1.0))
    mat.reflection_amount = float(bmat.get("dts_reflection_amount", 1.0))
    return mat
