"""Integration scenarios run inside Blender by run_blender_tests.py."""

import json
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"

import sys

sys.path.insert(0, str(REPO.parent))
from io_scene_dts.dtslib import SKIN_MESH, read_dsq, read_shape_file  # noqa: E402


def _reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def _tmp(suffix):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    return f.name


def _armature():
    return next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")


def _import_dts(name):
    res = bpy.ops.io_scene_dts.import_dts(filepath=str(FIXTURES / name))
    assert res == {"FINISHED"}, res
    return _armature()


def test_import_static_v24():
    _reset()
    arm = _import_dts("v24_octahedron.dts")
    src = read_shape_file(FIXTURES / "v24_octahedron.dts")
    assert len(arm.data.bones) == len(src.nodes)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert meshes
    total_tris = sum(len(o.data.polygons) for o in meshes)
    assert total_tris > 0


def test_static_roundtrip_v24():
    _reset()
    _import_dts("v24_ammo.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
    assert res == {"FINISHED"}, res
    src = read_shape_file(FIXTURES / "v24_ammo.dts")
    dst = read_shape_file(out)
    assert dst.source_version == 24
    assert len(dst.nodes) == len(src.nodes)
    assert len(dst.objects) == len(src.objects)
    assert len(dst.details) == len(src.details)
    assert {d.size for d in dst.details} == {d.size for d in src.details}
    # node names survive
    src_names = {src.node_name(i).lower() for i in range(len(src.nodes))}
    dst_names = {dst.node_name(i).lower() for i in range(len(dst.nodes))}
    assert src_names == dst_names
    # geometry mass conservation: same triangle count on the biggest detail
    def tri_count(shape):
        n = 0
        for m in shape.meshes:
            if m is not None and m.mesh_type in (0, 1):
                from io_scene_dts.mapping.shape_to_blender import decode_primitives

                n += len(decode_primitives(m))
        return n

    assert tri_count(dst) == tri_count(src)


def test_skinned_roundtrip_v24():
    _reset()
    _import_dts("v24_w_sqknest.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
    assert res == {"FINISHED"}, res
    src = read_shape_file(FIXTURES / "v24_w_sqknest.dts")
    dst = read_shape_file(out)
    assert len(dst.nodes) == len(src.nodes)
    src_skins = [m for m in src.meshes if m and m.mesh_type == SKIN_MESH]
    dst_skins = [m for m in dst.meshes if m and m.mesh_type == SKIN_MESH]
    assert len(dst_skins) >= 1
    # weights survive: every dst skin vertex is weighted
    for skin in dst_skins:
        assert skin.vertex_index
        assert len(skin.vertex_index) == len(skin.weight)
        assert all(w > 0 for w in skin.weight)
        assert len(skin.initial_transforms) == len(skin.node_index)


def test_animated_roundtrip_v23():
    _reset()
    _import_dts("v23_pack_upgrade_shield.dts")
    src = read_shape_file(FIXTURES / "v23_pack_upgrade_shield.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert dst.source_version == 23
    src_seqs = {src.name(s.name_index).lower(): s for s in src.sequences}
    dst_seqs = {dst.name(s.name_index).lower(): s for s in dst.sequences}
    assert set(dst_seqs) == set(src_seqs)
    for name, s_src in src_seqs.items():
        s_dst = dst_seqs[name]
        assert s_dst.num_keyframes == s_src.num_keyframes, name
        assert s_dst.is_cyclic() == s_src.is_cyclic(), name
        assert s_dst.num_triggers == s_src.num_triggers, name
        assert s_dst.rotation_matters.count() >= 1 or s_src.rotation_matters.count() == 0
        assert abs(s_dst.duration - s_src.duration) < 1e-5


def test_animation_values_survive():
    """Sampled keyframe transforms must survive import -> export within
    Quat16 quantization tolerance."""
    _reset()
    _import_dts("v23_pack_upgrade_shield.dts")
    src = read_shape_file(FIXTURES / "v23_pack_upgrade_shield.dts")
    out = _tmp(".dts")
    bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    dst = read_shape_file(out)

    for s_src in src.sequences:
        name = src.name(s_src.name_index).lower()
        s_dst = next(s for s in dst.sequences if dst.name(s.name_index).lower() == name)
        n = s_src.num_keyframes
        for node_src in s_src.rotation_matters.indices():
            node_name = src.node_name(node_src).lower()
            node_dst = next(
                i for i in range(len(dst.nodes)) if dst.node_name(i).lower() == node_name
            )
            if not s_dst.rotation_matters.test(node_dst):
                continue
            for kf in range(0, n, max(1, n // 4)):
                q_src = src.node_rotations[
                    s_src.base_rotation + s_src.rotation_matters.ordinal_of(node_src) * n + kf
                ].normalized_floats()
                q_dst = dst.node_rotations[
                    s_dst.base_rotation + s_dst.rotation_matters.ordinal_of(node_dst) * n + kf
                ].normalized_floats()
                dot = abs(sum(a * b for a, b in zip(q_src, q_dst)))
                assert dot > 0.9999, (name, node_name, kf, q_src, q_dst, dot)


def test_v23_ground_frame_refusal():
    _reset()
    # find a fixture-adjacent corpus shape with ground frames: HL exports have
    # them; skip if the import has none
    _import_dts("v24_w_sqknest.dts")
    src = read_shape_file(FIXTURES / "v24_w_sqknest.dts")
    out = _tmp(".dts")
    if src.ground_translations:
        try:
            res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23", drop_ground_frames=False)
            assert res == {"CANCELLED"}, res
        except RuntimeError as e:
            assert "ground frame" in str(e)
        res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23", drop_ground_frames=True)
        assert res == {"FINISHED"}, res
        dst = read_shape_file(out)
        assert not dst.ground_translations
    else:
        # still verify a clean v23 export works
        res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
        assert res == {"FINISHED"}, res


def test_dsq_roundtrip():
    _reset()
    _import_dts("v24_w_sqknest.dts")
    arm = _armature()
    bpy.context.view_layer.objects.active = arm
    out = _tmp(".dsq")
    res = bpy.ops.io_scene_dts.export_dsq(filepath=out)
    assert res == {"FINISHED"}, res
    dsq = read_dsq(Path(out).read_bytes())
    src = read_shape_file(FIXTURES / "v24_w_sqknest.dts")
    assert len(dsq.sequences) == len(src.sequences)
    assert {n.lower() for n in dsq.sequence_names} == {
        src.name(s.name_index).lower() for s in src.sequences
    }
    # re-import the DSQ onto the same armature
    n_actions = len(bpy.data.actions)
    res = bpy.ops.io_scene_dts.import_dsq(filepath=out)
    assert res == {"FINISHED"}, res
    assert len(bpy.data.actions) == n_actions + len(dsq.sequences)


def test_synthetic_scene_export():
    """A scene built by hand (no import metadata at all) must export."""
    _reset()
    arm = bpy.data.armatures.new("rig")
    arm_obj = bpy.data.objects.new("rig", arm)
    bpy.context.scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones.new("base")
    eb.head = (0, 0, 0)
    eb.tail = (0, 0.25, 0)
    bpy.ops.object.mode_set(mode="OBJECT")

    mesh = bpy.data.meshes.new("box2")
    mesh.from_pydata(
        [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2), (0, 2, 3)]
    )
    mesh.update()
    bobj = bpy.data.objects.new("box2", mesh)
    bpy.context.scene.collection.objects.link(bobj)
    bobj.parent = arm_obj

    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
    assert res == {"FINISHED"}, res
    shape = read_shape_file(out)
    assert len(shape.nodes) == 1
    assert len(shape.objects) == 1
    assert shape.name(shape.objects[0].name_index) == "box"
    assert shape.details[0].size == 2.0
    mesh0 = shape.meshes[0]
    assert len(mesh0.verts) >= 4
    # re-import what we exported
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(filepath=out)
    assert res == {"FINISHED"}, res
    assert any(o.type == "MESH" for o in bpy.context.scene.objects)


def test_textures_and_materials():
    _reset()
    _import_dts("v24_w_sqknest.dts")
    mats = [m for m in bpy.data.materials if "dts_name" in m]
    assert mats
    # the fixture dir carries NSQK_Top1.png — at least one material found it
    teximages = [
        n.image
        for m in mats
        if m.use_nodes
        for n in m.node_tree.nodes
        if n.type == "TEX_IMAGE" and n.image is not None
    ]
    assert teximages, "no texture was loaded from next to the .dts"


def test_dsq_active_action_only():
    _reset()
    _import_dts("v24_w_sqknest.dts")
    arm = _armature()
    bpy.context.view_layer.objects.active = arm
    # "active" is now the single unmuted NLA track, not an assigned action
    assert arm.animation_data.action is None
    assert sum(0 if t.mute else 1 for t in arm.animation_data.nla_tracks) == 1
    out = _tmp(".dsq")
    res = bpy.ops.io_scene_dts.export_dsq(filepath=out, active_action_only=True)
    assert res == {"FINISHED"}, res
    dsq = read_dsq(Path(out).read_bytes())
    assert len(dsq.sequences) == 1


def test_import_t2_player():
    """bioderm_light: node-rigged v23 player with decals, sequences that
    claim ground frames the shape doesn't carry, and null meshes."""
    _reset()
    arm = _import_dts("v23_bioderm_light.dts")
    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    assert len(arm.data.bones) == len(src.nodes)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert meshes
    actions = [a for a in bpy.data.actions if a.get("dts_sequence")]
    assert len(actions) == len(src.sequences)


def test_material_fields_survive():
    """All six material-list fields survive import -> export."""
    _reset()
    _import_dts("v23_pack_upgrade_cloaking.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    src = read_shape_file(FIXTURES / "v23_pack_upgrade_cloaking.dts")
    dst = read_shape_file(out)
    src_by_name = {m.name.lower(): (i, m) for i, m in enumerate(src.materials)}
    assert len(dst.materials) == len(src.materials)

    def canon(mats, idx, own):
        if idx == 0xFFFFFFFF:
            return "none"
        if idx == own:
            return "self"
        return mats[idx].name.lower()

    for j, m_dst in enumerate(dst.materials):
        i, m_src = src_by_name[m_dst.name.lower()]
        assert m_dst.flags == m_src.flags, m_dst.name
        assert m_dst.detail_scale == m_src.detail_scale, m_dst.name
        assert m_dst.reflection_amount == m_src.reflection_amount, m_dst.name
        assert canon(dst.materials, m_dst.reflectance_map, j) == canon(src.materials, m_src.reflectance_map, i), m_dst.name
        assert canon(dst.materials, m_dst.bump_map, j) == canon(src.materials, m_src.bump_map, i), m_dst.name
        assert canon(dst.materials, m_dst.detail_map, j) == canon(src.materials, m_src.detail_map, i), m_dst.name


def test_material_cross_refs_survive():
    """Synthetic case absent from the corpus: reflectance pointing at another
    material, bump and detail maps set — must survive the Blender round-trip."""
    sys.path.insert(0, str(REPO))  # test_synthetic does `from dtslib import ...`
    sys.path.insert(0, str(REPO / "tests"))
    from test_synthetic import make_triangle_shape

    from io_scene_dts.dtslib import Material, write_shape_file

    shape = make_triangle_shape()
    shape.materials = [
        Material(name="base", flags=0x3, reflectance_map=1, bump_map=2, detail_map=3,
                 detail_scale=2.5, reflection_amount=0.25),
        Material(name="envmap", flags=0x3, reflectance_map=1),
        Material(name="bumpmap", flags=0x3, reflectance_map=2),
        Material(name="detailmap", flags=0x3, reflectance_map=3),
    ]
    src_path = _tmp(".dts")
    write_shape_file(shape, src_path, 24)

    _reset()
    res = bpy.ops.io_scene_dts.import_dts(filepath=src_path)
    assert res == {"FINISHED"}, res
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    by_name = {m.name: i for i, m in enumerate(dst.materials)}
    m = dst.materials[by_name["base"]]
    assert dst.materials[m.reflectance_map].name == "envmap"
    assert dst.materials[m.bump_map].name == "bumpmap"
    assert dst.materials[m.detail_map].name == "detailmap"
    assert m.detail_scale == 2.5
    assert abs(m.reflection_amount - 0.25) < 1e-6


def test_texture_pairing():
    """Every material whose texture exists next to the .dts gets its own image."""
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(filepath=str(FIXTURES / "gman" / "v24_gman.dts"))
    assert res == {"FINISHED"}, res
    on_disk = {p.stem.lower() for p in (FIXTURES / "gman").glob("*.png")}
    paired = 0
    for m in bpy.data.materials:
        if "dts_name" not in m or not m.use_nodes:
            continue
        images = [n.image for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
        stem = Path(str(m["dts_name"])).stem.lower()
        if stem in on_disk:
            assert images, f"material {m['dts_name']!r} has a texture on disk but none loaded"
            assert Path(images[0].filepath).stem.lower() == stem, (
                f"material {m['dts_name']!r} got wrong image {images[0].filepath!r}"
            )
            paired += 1
    assert paired >= 10, f"only {paired} materials paired with their textures"


def test_uv_and_alpha():
    _reset()
    _import_dts("v24_shrub.dts")
    src = read_shape_file(FIXTURES / "v24_shrub.dts")
    # uv set matches tverts (v flipped)
    src_uvs = {(round(u, 4), round(1.0 - v, 4)) for m in src.meshes if m for (u, v) in m.tverts}
    mesh_obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    uv = mesh_obj.data.uv_layers.active
    got = {(round(d.uv[0], 4), round(d.uv[1], 4)) for d in uv.data}
    assert got, "no UVs imported"
    assert got <= src_uvs, "imported UVs not a subset of source tverts"
    # translucent material got its alpha wired
    m = next(m for m in bpy.data.materials if m.get("dts_translucent"))
    links = [(l.from_node.type, l.to_socket.name) for l in m.node_tree.links]
    assert ("TEX_IMAGE", "Alpha") in links, links


def test_decals_preserved():
    _reset()
    _import_dts("v23_bioderm_light.dts")
    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert len(dst.decals) == len(src.decals) == 24
    src_names = [src.name(d.raw[0]) for d in src.decals]
    dst_names = [dst.name(d.raw[0]) for d in dst.decals]
    assert dst_names == src_names
    # decal mesh payloads survive verbatim
    for d_src, d_dst in zip(src.decals, dst.decals):
        for j in range(d_src.raw[1]):
            m_src = src.meshes[d_src.raw[2] + j]
            m_dst = dst.meshes[d_dst.raw[2] + j]
            assert (m_src is None) == (m_dst is None)
            if m_src is not None:
                assert m_dst.decal_data == m_src.decal_data
    # default decal states survive
    assert dst.decal_states[: len(dst.decals)] == src.decal_states[: len(src.decals)]
    # the Damage sequence's decal track survives
    s_src = next(s for s in src.sequences if src.name(s.name_index) == "Damage")
    s_dst = next(s for s in dst.sequences if dst.name(s.name_index) == "Damage")
    assert s_dst.decal_matters.count() == s_src.decal_matters.count() == 24
    n = s_src.num_keyframes
    src_track = src.decal_states[s_src.base_decal_state : s_src.base_decal_state + 24 * n]
    dst_track = dst.decal_states[s_dst.base_decal_state : s_dst.base_decal_state + 24 * n]
    assert dst_track == src_track


def test_multiframe_shape_keys():
    _reset()
    _import_dts("v22_disc.dts")
    src = read_shape_file(FIXTURES / "v22_disc.dts")
    keyed = [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and o.data.shape_keys is not None
    ]
    assert keyed, "no shape keys created for the multi-frame meshes"
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    src_mf = [m for m in src.meshes if m and m.num_frames > 1]
    dst_mf = [m for m in dst.meshes if m and m.num_frames > 1]
    assert len(dst_mf) == len(src_mf) == 2
    for m_src, m_dst in zip(src_mf, dst_mf):
        assert m_dst.num_frames == m_src.num_frames == 17
        assert len(m_dst.verts) == m_dst.num_frames * m_dst.verts_per_frame
        # frame geometry survives (dedup may reorder/split verts; compare
        # point sets with a small tolerance)
        def close(p, pts, tol=1e-3):
            return any(sum((a - b) ** 2 for a, b in zip(p, q)) < tol * tol for q in pts)

        for f in range(m_src.num_frames):
            src_frame = m_src.verts[f * m_src.verts_per_frame : (f + 1) * m_src.verts_per_frame]
            dst_frame = m_dst.verts[f * m_dst.verts_per_frame : (f + 1) * m_dst.verts_per_frame]
            assert all(close(p, dst_frame) for p in src_frame), f"frame {f}: src point missing"
            assert all(close(p, src_frame) for p in dst_frame), f"frame {f}: dst point extra"


def test_ifl_preserved():
    _reset()
    _import_dts("v22_energy_explosion.dts")
    src = read_shape_file(FIXTURES / "v22_energy_explosion.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert len(dst.ifl_materials) == len(src.ifl_materials) == 1
    assert dst.name(dst.ifl_materials[0].raw[0]) == src.name(src.ifl_materials[0].raw[0])
    assert dst.ifl_materials[0].raw[1:] == src.ifl_materials[0].raw[1:]
    assert [m.name for m in dst.materials] == [m.name for m in src.materials]


def test_sorted_edit_refused():
    _reset()
    _import_dts("v19_xorg20.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res  # unedited: exports fine

    # every mesh carries a source payload now; only sorted/multi-matframe ones
    # are strict, i.e. refuse to export when edited instead of re-deriving
    frozen = next(o for o in bpy.context.scene.objects if o.get("dts_strict_freeze"))
    frozen.data.vertices[0].co.x += 1.0
    try:
        res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
        assert res == {"CANCELLED"}, res
    except RuntimeError as e:
        assert "round-trips verbatim" in str(e), str(e)


def test_import_v18_old_format():
    _reset()
    arm = _import_dts("v18_octahedron.dts")
    src = read_shape_file(FIXTURES / "v18_octahedron.dts")
    assert src.source_version == 18
    assert len(arm.data.bones) == len(src.nodes)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert meshes
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert dst.source_version == 24
    assert len(dst.nodes) == len(src.nodes)


def test_sibling_textures_under_shapes_dir():
    """shapes/ and textures/ are siblings in Tribes 2 layouts, so a shape's
    textures are never next to the .dts itself."""
    import tempfile

    from io_scene_dts.mapping.materials import find_texture, reset_texture_cache

    root = Path(tempfile.mkdtemp())
    (root / "shapes").mkdir()
    skins = root / "textures" / "skins"
    skins.mkdir(parents=True)
    body = skins / "base.lmale.png"
    dotted = skins / "armor.damage.1.png"
    for p in (body, dotted):
        p.write_bytes(b"\x89PNG\r\n\x1a\n")

    reset_texture_cache()
    assert find_texture("base.lmale", root / "shapes") == body
    # dots in a material name are not extensions: Path(...).stem would
    # truncate these to "base" and "armor.damage" and find nothing
    assert find_texture("armor.damage.1", root / "shapes") == dotted

    # the sibling hop is gated on the directory actually being named "shapes"
    (root / "models").mkdir()
    reset_texture_cache()
    assert find_texture("base.lmale", root / "models") is None


def test_texture_next_to_dts_wins():
    """A texture beside the .dts takes precedence over the sibling tree."""
    import tempfile

    from io_scene_dts.mapping.materials import find_texture, reset_texture_cache

    root = Path(tempfile.mkdtemp())
    shapes = root / "shapes"
    shapes.mkdir()
    skins = root / "textures" / "skins"
    skins.mkdir(parents=True)
    (skins / "hull.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    local = shapes / "hull.png"
    local.write_bytes(b"\x89PNG\r\n\x1a\n")

    reset_texture_cache()
    assert find_texture("hull", shapes) == local


def test_material_path_prefix_picks_subdirectory():
    r"""A material named "skins\foo" means textures/skins/foo, not textures/foo."""
    import tempfile

    from io_scene_dts.mapping.materials import find_texture, reset_texture_cache

    root = Path(tempfile.mkdtemp())
    shapes = root / "shapes"
    shapes.mkdir()
    textures = root / "textures"
    skins = textures / "skins"
    skins.mkdir(parents=True)
    shallow = textures / "base.lmale.png"
    nested = skins / "base.lmale.png"
    for p in (shallow, nested):
        p.write_bytes(b"\x89PNG\r\n\x1a\n")

    reset_texture_cache()
    assert find_texture("skins\\base.lmale", shapes) == nested
    # forward slashes too, and no prefix falls back to the flat search
    assert find_texture("skins/base.lmale", shapes) == nested
    assert find_texture("base.lmale", shapes) == shallow

    # a prefix that names no directory still resolves via the flat index
    assert find_texture("nosuchdir\\base.lmale", shapes) == shallow


def test_material_prefix_cannot_escape_textures_tree():
    import tempfile

    from io_scene_dts.mapping.materials import find_texture, reset_texture_cache

    root = Path(tempfile.mkdtemp())
    shapes = root / "shapes"
    shapes.mkdir()
    (root / "textures").mkdir()
    outside = root / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")

    reset_texture_cache()
    assert find_texture("..\\secret", shapes) is None


# ----------------------------------------------------------------------
# DSQ partial-channel regressions
#
# A sequence names the nodes it animates in rotation_matters /
# translation_matters, and the two sets rarely agree: most nodes only rotate.
# A DSQ carries no default transforms to fill the gap, so the importer has to
# take the missing channel from the armature's rest pose (which was built from
# the shape's defaults).  It used to substitute zero instead, collapsing every
# rotation-only bone onto its parent.
# ----------------------------------------------------------------------


def _rest_local(bone):
    if bone.parent:
        return bone.parent.matrix_local.inverted() @ bone.matrix_local
    return bone.matrix_local.copy()


def _pose_local(pose_bone):
    if pose_bone.parent:
        return pose_bone.parent.matrix.inverted() @ pose_bone.matrix
    return pose_bone.matrix.copy()


def _offset_bone(arm):
    """The bone whose rest offset from its parent is largest — the one where a
    collapsed translation is most visible."""
    best = max(
        ((i, b) for i, b in enumerate(arm.data.bones)),
        key=lambda ib: _rest_local(ib[1]).to_translation().length,
    )
    assert _rest_local(best[1]).to_translation().length > 0.05, "fixture has no offset bones"
    return best


def _probe_dsq(arm, node, *, rotate, translate, name="Probe"):
    """A one-sequence DSQ over arm's bones that animates a single node with
    only the requested channels present."""
    from io_scene_dts.dtslib import DsqFile, Quat16, Sequence, TSIntegerSet, write_dsq

    dsq = DsqFile(version=24)
    dsq.node_names = [b.get("dts_name") or b.name for b in arm.data.bones]
    seq = Sequence()
    seq.num_keyframes = 2
    seq.duration = 0.5
    if rotate:
        seq.rotation_matters = TSIntegerSet()
        seq.rotation_matters.set(node)
        dsq.node_rotations = [Quat16.identity()] * seq.num_keyframes
    if translate is not None:
        seq.translation_matters = TSIntegerSet()
        seq.translation_matters.set(node)
        dsq.node_translations = [tuple(translate)] * seq.num_keyframes
    dsq.sequences = [seq]
    dsq.sequence_names = [name]
    return write_dsq(dsq, 24)


def _import_probe(arm, payload):
    """Import a probe DSQ and evaluate it.  Sequences arrive as NLA strips, so
    the pose comes from the strip, not from an assigned action."""
    out = _tmp(".dsq")
    Path(out).write_bytes(payload)
    bpy.context.view_layer.objects.active = arm
    res = bpy.ops.io_scene_dts.import_dsq(filepath=out)
    assert res == {"FINISHED"}, res
    action = bpy.data.actions["Probe"]
    live = [t for t in arm.animation_data.nla_tracks if not t.mute]
    assert [t.name for t in live] == ["Probe"], [t.name for t in live]
    assert arm.animation_data.action is None
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    return action


def test_dsq_rotation_only_node_keeps_rest_translation():
    """Regression: a node in rotation_matters but not translation_matters used
    to get translation (0,0,0), teleporting the bone onto its parent."""
    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    node, bone = _offset_bone(arm)
    rest_t = _rest_local(bone).to_translation()

    _import_probe(arm, _probe_dsq(arm, node, rotate=True, translate=None))

    posed_t = _pose_local(arm.pose.bones[bone.name]).to_translation()
    assert (posed_t - rest_t).length < 1e-5, (
        f"{bone.name}: rotation-only node moved to {posed_t[:]} instead of "
        f"keeping its rest offset {rest_t[:]}"
    )
    # the pose channel itself must be neutral, not a counter-offset
    assert arm.pose.bones[bone.name].location.length < 1e-5


def test_dsq_translation_only_node_keeps_rest_rotation():
    """Regression: a node in translation_matters but not rotation_matters had
    the rest matrix composed on the wrong side, applying the translation in the
    bone's rotated frame."""
    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    node, bone = _offset_bone(arm)
    rest_q = _rest_local(bone).to_quaternion()
    target = (0.25, -0.5, 0.75)

    _import_probe(arm, _probe_dsq(arm, node, rotate=False, translate=target))

    posed = _pose_local(arm.pose.bones[bone.name])
    assert (posed.to_translation() - Vector(target)).length < 1e-5, (
        f"{bone.name}: translation-only node landed at {posed.to_translation()[:]} "
        f"instead of {target}"
    )
    assert posed.to_quaternion().rotation_difference(rest_q).angle < 1e-4, (
        f"{bone.name}: translation-only node should keep its rest orientation"
    )


def _fcurves(action):
    if getattr(action, "layers", None):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


def test_import_dts_with_dsq_companions():
    """A shape ships its animations as many separate .dsq files — light_male
    has about forty — so the importer takes any number of them at once."""
    import shutil
    import tempfile

    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    node, bone = _offset_bone(arm)
    probes = ["ProbeA", "ProbeB", "ProbeC", "ProbeD", "ProbeE"]

    tmp = Path(tempfile.mkdtemp())
    shutil.copy(FIXTURES / "v24_w_sqknest.dts", tmp / "shape.dts")
    for seq in probes:
        (tmp / f"{seq}.dsq").write_bytes(
            _probe_dsq(arm, node, rotate=True, translate=None, name=seq)
        )

    _reset()
    res = bpy.ops.io_scene_dts.import_dts(
        directory=str(tmp),
        files=[{"name": "shape.dts"}] + [{"name": f"{s}.dsq"} for s in probes],
    )
    assert res == {"FINISHED"}, res

    arm = _armature()
    for seq in probes:
        action = bpy.data.actions.get(seq)
        assert action is not None, f"{seq} missing from {list(bpy.data.actions.keys())}"
        # bound to this armature's bones, not merely created
        assert any(bone.name in fc.data_path for fc in _fcurves(action))
        # nothing assigns DSQ actions, so they need a fake user to survive a save
        assert action.use_fake_user
    # every companion landed, and none clobbered the shape's own sequences
    assert len(bpy.data.actions) == len(read_shape_file(tmp / "shape.dts").sequences) + len(probes)


def test_import_dts_rejects_more_than_one_shape():
    _reset()
    try:
        bpy.ops.io_scene_dts.import_dts(
            directory=str(FIXTURES),
            files=[{"name": "v24_ammo.dts"}, {"name": "v24_octahedron.dts"}],
        )
    except RuntimeError as e:
        assert "exactly one .dts" in str(e), str(e)
    else:
        raise AssertionError("expected two .dts selections to be rejected")


def _bone_driving_actions(arm):
    return [
        a for a in bpy.data.actions
        if a.get("dts_sequence")
        and any(fc.data_path.startswith('pose.bones["') for fc in _fcurves(a))
    ]


def test_dts_import_stacks_sequences_as_nla_strips():
    """Importing is the only way sequences arrive, and they arrive retimed: one
    scene fps cannot serve sequences authored at 15 and 30, a per-strip scale
    can.  Each strip must span its sequence's real-world duration."""
    from io_scene_dts.mapping.nla import strip_scale

    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base

    tracks = arm.animation_data.nla_tracks
    assert len(tracks) == len(_bone_driving_actions(arm))

    checked = 0
    for track in tracks:
        strip = track.strips[0]
        action = strip.action
        assert not strip.use_sync_length, track.name
        assert abs(strip.scale - strip_scale(action, fps)) < 1e-5, track.name
        n = int(action.get("dts_keyframes") or 0)
        duration = float(action.get("dts_duration") or 0.0)
        if n > 1 and duration > 0.0:
            seconds = (strip.frame_end - strip.frame_start) / fps
            assert abs(seconds - duration) < 1e-3, (track.name, seconds, duration)
            checked += 1
    assert checked, "fixture carries no timed sequences"

    # a library of clips, not seventeen animations blended at once
    assert sum(0 if t.mute else 1 for t in tracks) == 1
    # an assigned action would evaluate on top of the stack, at scene fps
    assert arm.animation_data.action is None
    # deleting a track must not take the sequence with it
    assert all(a.use_fake_user for a in _bone_driving_actions(arm))


def test_object_visibility_animates_through_the_strip():
    """A sequence's vis track is keyframed on the armature and fanned out to
    every LOD copy by a driver, so one strip drives pose and visibility."""
    from io_scene_dts.mapping.visibility import vis_prop

    _reset()
    arm = _import_dts("v23_pack_upgrade_cloaking.dts")
    animated = ["Main_light", "Hand_bottom_light", "Hand_right_light", "Hand_left_light"]

    # the value lives on the armature, in the bones' own slot
    for name in animated:
        assert vis_prop(name) in arm.keys(), name
    assert any(
        fc.data_path == f'["{vis_prop("Main_light")}"]'
        for fc in _fcurves(bpy.data.actions["ambient"])
    )

    # every mesh built from an animated object is driven; a DTS object is one
    # mesh per detail level, so this is 1:N
    driven = [o for o in bpy.data.objects
              if o.type == "MESH" and o.get("dts_object_name") in animated]
    assert driven
    for o in driven:
        paths = {d.data_path for d in o.animation_data.drivers}
        assert {"color", "hide_viewport", "hide_render"} <= paths, (o.name, paths)
        for d in o.animation_data.drivers:
            assert d.driver.variables[0].targets[0].id is arm

    # a vis-only sequence must still get a strip — it used to be skipped for
    # having no bone channels, which left the visibility inert
    tracks = {t.name for t in arm.animation_data.nla_tracks}
    assert "ambient" in tracks, tracks

    # and it must actually move
    for t in arm.animation_data.nla_tracks:
        t.mute = t.name != "ambient"
    n = int(bpy.data.actions["ambient"]["dts_keyframes"])
    seen = set()
    for f in range(1, n + 1):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        seen.add(round(arm[vis_prop("Main_light")], 3))
    assert len(seen) > 1, f"visibility never changed: {seen}"


def test_visibility_starts_at_the_shapes_default_state():
    """Defaulting every vis property to 1.0 shows meshes the shape hides at
    rest — station_generator_large's destroyed hulk swallowed the machine it
    replaces, because its default vis is 0."""
    from io_scene_dts.mapping.visibility import vis_prop

    _reset()
    arm = _import_dts("v22_disc.dts")
    hidden = ["leadingEdgeAct", "leadingEdgeMaint", "trailAct", "trailMaint"]

    # mute everything so nothing overrides the resting value
    for t in arm.animation_data.nla_tracks:
        t.mute = True
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()

    for name in hidden:
        assert arm[vis_prop(name)] == 0.0, (name, arm[vis_prop(name)])
        mesh = next(o for o in bpy.data.objects
                    if o.type == "MESH" and o.get("dts_object_name") == name)
        ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert ev.hide_viewport, f"{name} should be hidden at rest"


def test_only_fractional_tracks_get_alpha_materials():
    """A binary vis track is a swap the hide drivers already cover; making its
    material transparent would cost sorting for no gain."""
    from io_scene_dts.mapping.visibility import fractional_object_names

    _reset()
    _import_dts("v22_disc.dts")
    actions = [a for a in bpy.data.actions if a.get("dts_sequence")]
    fades = fractional_object_names(actions)

    for action in actions:
        for name, tracks in json.loads(action.get("dts_object_anim", "{}") or "{}").items():
            vis = tracks.get("vis") or []
            if vis and all(v in (0.0, 1.0) for v in vis) and name not in fades:
                meshes = [o for o in bpy.data.objects
                          if o.type == "MESH" and o.get("dts_object_name") == name]
                for m in meshes:
                    for slot in m.material_slots:
                        if slot.material and slot.material.node_tree:
                            assert not any(n.type == "OBJECT_INFO"
                                           for n in slot.material.node_tree.nodes), \
                                f"{name} is binary; {slot.material.name} should stay opaque"


def test_visibility_drivers_are_not_duplicated_on_reimport():
    _reset()
    arm = _import_dts("v23_pack_upgrade_shield.dts")
    driven = next(o for o in bpy.data.objects
                  if o.type == "MESH" and o.get("dts_object_name") == "CenterFace_ambient")
    before = len(driven.animation_data.drivers)

    from io_scene_dts.mapping.visibility import wire_drivers
    wire_drivers(arm, {"CenterFace_ambient"}, [])
    assert len(driven.animation_data.drivers) == before


def test_no_standalone_nla_stacker():
    """Stacking is not a separate step a user can forget to run.

    bpy.ops namespaces resolve lazily, so hasattr always succeeds — check what
    is actually registered.
    """
    assert "stack_sequences_nla" not in dir(bpy.ops.io_scene_dts)
    assert not hasattr(bpy.types, "IO_SCENE_DTS_OT_stack_sequences_nla")


def test_nla_strips_do_not_reach_the_exported_file():
    """dts_duration stays the single source of truth, so retiming a strip
    cannot change what gets written."""
    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    bpy.context.view_layer.objects.active = arm

    for track in arm.animation_data.nla_tracks:
        track.strips[0].scale *= 3.0  # deliberately wreck the display timing

    out = _tmp(".dsq")
    assert bpy.ops.io_scene_dts.export_dsq(filepath=out) == {"FINISHED"}
    dsq = read_dsq(Path(out).read_bytes())
    written = {n.lower(): s for n, s in zip(dsq.sequence_names, dsq.sequences)}
    src = read_shape_file(FIXTURES / "v24_w_sqknest.dts")
    for s in src.sequences:
        name = src.name(s.name_index).lower()
        if name not in written:
            continue
        assert written[name].num_keyframes == s.num_keyframes, name
        assert abs(written[name].duration - s.duration) < 1e-4, name


def test_import_hides_non_default_detail_levels():
    """Every LOD sits at the same origin; only the default (largest size) one
    is visible after import."""
    _reset()
    _import_dts("v23_bioderm_light.dts")
    vl = bpy.context.view_layer
    lods = [
        lc for lc in vl.layer_collection.children
        if any("dts_detail_size" in o for o in lc.collection.objects)
    ]
    assert len(lods) > 1, "fixture has only one detail level"

    def size_of(lc):
        return max(o["dts_detail_size"] for o in lc.collection.objects)

    visible = [lc for lc in lods if not lc.hide_viewport]
    assert len(visible) == 1, [lc.name for lc in visible]
    assert size_of(visible[0]) == max(size_of(lc) for lc in lods)
    # hidden, not deleted, and still renderable
    assert all(lc.collection.objects for lc in lods)
    assert not any(lc.collection.hide_render for lc in lods)
