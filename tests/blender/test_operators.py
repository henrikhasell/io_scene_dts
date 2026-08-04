"""Integration scenarios run inside Blender by run_blender_tests.py."""

import tempfile
from pathlib import Path

import bpy

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
    assert arm.animation_data and arm.animation_data.action
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
