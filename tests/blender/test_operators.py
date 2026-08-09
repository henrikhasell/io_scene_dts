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


def _keyframes_of(action):
    """A sequence's length is the keys it has -- dts_keyframes is gone, so that
    adding or removing one changes the exported length."""
    from io_scene_dts.mapping.sequences import _keyframe_count

    return _keyframe_count(action)


def _reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def _tmp(suffix):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    return f.name


def _armature():
    return next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")


def _import_dts(name):
    # every level: these are fidelity tests, and the operator now defaults
    # to the visible detail only
    res = bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / name), import_details=True
    )
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


def test_v23_drops_ground_frames():
    """An imported shape with ground frames still exports as v23 -- the frames
    go, because v23 has nowhere to put them, and v24 keeps them."""
    _reset()
    _import_dts("v24_w_sqknest.dts")
    src = read_shape_file(FIXTURES / "v24_w_sqknest.dts")
    out = _tmp(".dts")

    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert not dst.ground_translations
    assert all(s.num_ground_frames == 0 for s in dst.sequences)

    if src.ground_translations:
        out24 = _tmp(".dts")
        assert bpy.ops.io_scene_dts.export_dts(filepath=out24, version="24") == {"FINISHED"}
        assert read_shape_file(out24).ground_translations


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
    res = bpy.ops.io_scene_dts.import_dts(filepath=out, import_details=True)
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
    res = bpy.ops.io_scene_dts.import_dts(filepath=src_path, import_details=True)
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


def _env_mapped_fixture(*, translucent=False, name="shrub"):
    """A shape whose one material is env-mapped, beside a texture on disk.

    Synthesised rather than shipped: 270 of the corpus's 3185 materials are
    env-mapped, but none of the fixtures that carry their textures is one of
    them.  ``shrub.png`` is the fixture with a genuinely varying alpha, which
    is what a reflectance mask has to have to be worth splitting out.

    Returns (dts path, texture path) in a fresh directory.
    """
    import shutil

    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "tests"))
    from test_synthetic import make_triangle_shape

    from io_scene_dts.dtslib import Material, write_shape_file
    from io_scene_dts.dtslib.types import MAT_S_WRAP, MAT_T_WRAP, MAT_TRANSLUCENT

    flags = MAT_S_WRAP | MAT_T_WRAP | (MAT_TRANSLUCENT if translucent else 0)
    shape = make_triangle_shape()
    # reflectance pointing at itself with env-mapping on: the packing every
    # material in the shipped Tribes 2 shapes uses
    shape.materials = [Material(name=name, flags=flags, reflectance_map=0)]

    directory = Path(tempfile.mkdtemp())
    texture = directory / f"{name}.png"
    shutil.copy(FIXTURES / "shrub.png", texture)
    dts = directory / f"{name}.dts"
    write_shape_file(shape, str(dts), 24)
    return dts, texture


def _material_of(name):
    return next(m for m in bpy.data.materials if m.get("dts_name") == name)


def _node_feeding(mat, socket):
    from io_scene_dts.mapping.materials import _image_node_feeding

    return _image_node_feeding(mat, socket)


def test_a_self_reflectance_imports_as_two_images():
    """One RGBA file arrives as an RGB diffuse and a greyscale mask."""
    dts, texture = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}

    mat = _material_of("shrub")
    diffuse = _node_feeding(mat, "Base Color")
    reflectance = _node_feeding(mat, "Metallic")
    assert diffuse is not None and reflectance is not None, "both maps must arrive"
    assert diffuse.image is not reflectance.image
    assert diffuse.image.packed_file and reflectance.image.packed_file, (
        "a re-encode has no file, so it has to be packed or the .blend loses it"
    )
    assert reflectance.image.colorspace_settings.name == "Non-Color", (
        "an alpha channel is data; an sRGB curve would change what the engine reads"
    )

    source = bpy.data.images.load(str(texture))
    assert list(reflectance.image.pixels)[0::4] == list(source.pixels)[3::4]
    assert set(list(diffuse.image.pixels)[3::4]) == {1.0}, "diffuse must read as opaque"


def test_a_reflectance_round_trips_byte_identically():
    """Taking the texture apart and putting it back must change nothing.

    Combine defaults on, which is the packing the file already used, so the
    material list has to come back field for field, and the recombined texture
    is written beside the new .dts -- the shape names it, and the engine has
    nowhere else to look for it.
    """
    dts, _ = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}

    out = Path(tempfile.mkdtemp()) / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}

    src = read_shape_file(dts)
    dst = read_shape_file(out)
    assert len(dst.materials) == len(src.materials) == 1
    for field in ("name", "flags", "reflectance_map", "bump_map", "detail_map"):
        assert getattr(dst.materials[0], field) == getattr(src.materials[0], field), field
    assert [p.name for p in out.parent.glob("*.png")] == ["shrub.png"], (
        "the material names shrub, so shrub.png has to be beside the shape"
    )


def test_unticking_combine_splits_the_material_list():
    """Off, the mask becomes its own texture and its own material entry.

    Unticked on the *export dialog*, and the material is left alone: an
    imported material follows that box, which is the whole reason the importer
    records nothing about the packing it found.

    And doing it again must not grow the list further: the second import finds
    the reflectance entry as an ordinary material, so the third export points
    at that rather than inventing another -- with the box back on, because the
    material now says SEPARATE for itself.
    """
    dts, _ = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}
    assert _material_of("shrub").dts_material.reflectance_packing == "DEFAULT"

    out = Path(tempfile.mkdtemp()) / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(
        filepath=str(out), version="24", combine_reflectance=False
    ) == {"FINISHED"}
    dst = read_shape_file(out)
    assert len(dst.materials) == 2, [m.name for m in dst.materials]
    assert dst.materials[0].reflectance_map == 1
    from io_scene_dts.dtslib.types import MAT_REFLECTANCE_MAP_ONLY

    assert dst.materials[1].flags & MAT_REFLECTANCE_MAP_ONLY
    written = sorted(p.name for p in out.parent.glob("*.png"))
    assert len(written) == 2, written

    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(out), import_details=True) == {"FINISHED"}
    again = Path(tempfile.mkdtemp()) / "again.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(again), version="24") == {"FINISHED"}
    assert len(read_shape_file(again).materials) == 2, "the list must not keep growing"


def test_an_env_mapped_translucent_material_keeps_its_transparency():
    """One alpha channel, two meanings, and this material claims both.

    Transparency wins -- 2015 corpus materials read the alpha that way and 6
    read it the other -- so the texture is not split, and the mask is previewed
    off the same node rather than a new one.
    """
    dts, _ = _env_mapped_fixture(translucent=True)
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}

    mat = _material_of("shrub")
    links = [(l.from_socket.name, l.to_socket.name) for l in mat.node_tree.links]
    assert ("Alpha", "Alpha") in links, links
    assert ("Alpha", "Metallic") in links, links
    assert _node_feeding(mat, "Base Color") == _node_feeding(mat, "Metallic"), (
        "one node feeds both, so export keeps the combined packing"
    )
    assert len(read_shape_file(dts).materials) == 1
    out = Path(tempfile.mkdtemp()) / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}
    assert len(read_shape_file(out).materials) == 1, "nothing to separate"


def test_a_cross_referenced_reflectance_imports_as_the_other_materials_texture():
    """A reflectance slot naming another entry loads that entry's texture."""
    import shutil

    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "tests"))
    from test_synthetic import make_triangle_shape

    from io_scene_dts.dtslib import Material, write_shape_file
    from io_scene_dts.dtslib.types import MAT_NEVER_ENV_MAP, MAT_S_WRAP, MAT_T_WRAP

    wrap = MAT_S_WRAP | MAT_T_WRAP
    shape = make_triangle_shape()
    shape.materials = [
        Material(name="shrub", flags=wrap, reflectance_map=1),
        # the target is not itself env-mapped -- it is there to be pointed at,
        # so its own texture stays whole
        Material(name="wall", flags=wrap | MAT_NEVER_ENV_MAP, reflectance_map=1),
    ]
    directory = Path(tempfile.mkdtemp())
    for name in ("shrub", "wall"):
        shutil.copy(FIXTURES / "shrub.png", directory / f"{name}.png")
    dts = directory / "cross.dts"
    write_shape_file(shape, str(dts), 24)

    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}
    base, other = _material_of("shrub"), _material_of("wall")
    reflectance = _node_feeding(base, "Metallic")
    assert reflectance is not None, "the referenced texture must be loaded"
    assert reflectance.image == _node_feeding(other, "Base Color").image, (
        "it is the other material's own texture, not a copy"
    )
    # SEPARATE, not DEFAULT: the mask is another entry in the material list that
    # both materials point at, and combining would duplicate a shared texture.
    # So it has to survive an export with the Combine box left on, below.
    assert base.dts_material.reflectance_packing == "SEPARATE"

    out = directory / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    assert dst.materials[dst.materials[0].reflectance_map].name == "wall"
    assert len(dst.materials) == 2, "the entry already exists; do not invent another"


def test_export_copies_an_imported_texture_beside_the_dts():
    """A shape exported somewhere new takes its textures with it.

    The imported material's image is file-backed and was never painted on, so
    every rule this add-on used to have said 'reference it, do not copy it'.
    That produced a .dts that only rendered on the machine it was made on: the
    engine looks for the texture beside the shape, by bare filename, and there
    was nothing there.
    """
    dts, texture = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}

    # somewhere else entirely, the way an export into a mod tree is
    destination = Path(tempfile.mkdtemp())
    out = destination / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}
    assert (destination / "shrub.png").is_file(), sorted(
        p.name for p in destination.iterdir()
    )


def test_export_overwrites_a_source_texture():
    """...and it overwrites, including back into the tree it was read from.

    This is a deliberate trade rather than an oversight, and the expensive half
    of it: exporting into a game's textures/ tree rewrites the art there, since
    the add-on cannot tell that directory from a mod's own.  Leaving a stale
    texture beside a new .dts is the failure this is chosen over -- a wrong
    render that looks like a right one.
    """
    dts, texture = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}
    # paint on the mask, so the bytes that come back are provably ours and not
    # a byte-identical re-encode of the file already there
    mat = _material_of("shrub")
    reflectance = _node_feeding(mat, "Metallic")
    reflectance.image.pixels = [0.5] * len(reflectance.image.pixels)
    reflectance.image.update()

    before = texture.read_bytes()
    # export back into the directory the source texture lives in
    out = texture.parent / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}
    assert texture.read_bytes() != before, "the source texture was left alone"


def test_export_textures_unticked_leaves_a_source_texture_alone():
    """The checkbox is the whole of the protection, so it has to be real."""
    dts, texture = _env_mapped_fixture()
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(dts), import_details=True) == {"FINISHED"}
    mat = _material_of("shrub")
    reflectance = _node_feeding(mat, "Metallic")
    reflectance.image.pixels = [0.5] * len(reflectance.image.pixels)
    reflectance.image.update()

    before = texture.read_bytes()
    out = texture.parent / "out.dts"
    assert bpy.ops.io_scene_dts.export_dts(
        filepath=str(out), version="24", export_textures=False
    ) == {"FINISHED"}
    assert texture.read_bytes() == before, "unticked, and it wrote anyway"


def test_texture_pairing():
    """Every material whose texture exists next to the .dts gets its own image."""
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / "gman" / "v24_gman.dts"), import_details=True
    )
    assert res == {"FINISHED"}, res
    on_disk = {p.stem.lower() for p in (FIXTURES / "gman").glob("*.png")}
    paired = 0
    for m in bpy.data.materials:
        if "dts_name" not in m or not m.use_nodes:
            continue
        # the node feeding Base Color, not merely the first image node: a
        # material with a reflectance map has two, and their order in the node
        # list is not a fact about which is the diffuse
        node = _node_feeding(m, "Base Color")
        stem = Path(str(m["dts_name"])).stem.lower()
        if stem in on_disk:
            assert node is not None, (
                f"material {m['dts_name']!r} has a texture on disk but none loaded"
            )
            assert Path(node.image.filepath).stem.lower() == stem, (
                f"material {m['dts_name']!r} got wrong image {node.image.filepath!r}"
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
    # translucent material got its alpha wired.  Found through the shader,
    # since translucency is not stored as a prop at all
    from io_scene_dts.mapping.materials import blend_flags_from_material

    m = next(
        m for m in bpy.data.materials
        if "dts_name" in m and blend_flags_from_material(m) & 0x4
    )
    links = [(l.from_node.type, l.to_socket.name) for l in m.node_tree.links]
    assert ("TEX_IMAGE", "Alpha") in links, links
    # ...and only as transparency.  This material sets MAT_NEVER_ENV_MAP, so
    # its alpha is not a reflectance mask and nothing should reach Metallic
    assert ("TEX_IMAGE", "Metallic") not in links, links


def test_reflectance_map_only_survives_the_int_prop_limit():
    """MAT_REFLECTANCE_MAP_ONLY is bit 31, past what a Blender int prop holds.

    No corpus shape sets it, so the fixture is synthesised: assigning the raw
    flags word used to raise OverflowError out of the import operator and leave
    a half-built scene.  With every flag bit named, there is no packed word for
    it to overflow -- the checkbox is the only storage, and the limit is gone
    rather than worked around.
    """
    from io_scene_dts.dtslib.types import MAT_REFLECTANCE_MAP_ONLY
    from io_scene_dts.dtslib.writer import write_shape_file

    _reset()
    src = read_shape_file(FIXTURES / "v24_shrub.dts")
    src.materials[0].flags |= MAT_REFLECTANCE_MAP_ONLY
    src_flags = src.materials[0].flags
    assert src_flags > 0x7FFFFFFF, hex(src_flags)
    seeded = _tmp(".dts")
    write_shape_file(src, seeded, version=24)

    res = bpy.ops.io_scene_dts.import_dts(filepath=seeded)
    assert res == {"FINISHED"}, res

    bmat = next(m for m in bpy.data.materials if m.get("dts_name") == src.materials[0].name)
    assert bmat["dts_reflectance_map_only"], "bit 31 lost on import"
    assert "dts_flags" not in bmat.keys(), "the packed word is gone"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    assert read_shape_file(out).materials[0].flags == src_flags, "bit 31 lost on export"


def test_every_material_flag_bit_has_a_checkbox():
    """Four bits used to survive only inside the packed dts_flags word:
    MIP_MAP_ZERO_BORDER, IFL_FRAME, DETAIL_MAP_ONLY and BUMP_MAP_ONLY.  None
    occurs in the corpus, so setting one meant computing an integer by hand."""
    from io_scene_dts.dtslib.types import (
        MAT_BUMP_MAP_ONLY,
        MAT_DETAIL_MAP_ONLY,
        MAT_IFL_FRAME,
        MAT_MIP_MAP_ZERO_BORDER,
    )
    from io_scene_dts.dtslib.writer import write_shape_file

    bits = {
        "dts_mip_map_zero_border": MAT_MIP_MAP_ZERO_BORDER,
        "dts_ifl_frame": MAT_IFL_FRAME,
        "dts_detail_map_only": MAT_DETAIL_MAP_ONLY,
        "dts_bump_map_only": MAT_BUMP_MAP_ONLY,
    }

    _reset()
    src = read_shape_file(FIXTURES / "v24_shrub.dts")
    for bit in bits.values():
        src.materials[0].flags |= bit
    want = src.materials[0].flags
    seeded = _tmp(".dts")
    write_shape_file(src, seeded, version=24)

    assert bpy.ops.io_scene_dts.import_dts(filepath=seeded) == {"FINISHED"}
    bmat = next(m for m in bpy.data.materials if m.get("dts_name") == src.materials[0].name)
    for prop in bits:
        assert bmat.get(prop), f"{prop} lost on import"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    got = read_shape_file(out).materials[0].flags
    for prop, bit in bits.items():
        assert got & bit, f"{prop} lost on export"
    assert got == want, (hex(want), hex(got))


def test_dsq_sequences_use_the_same_tables_as_dts_ones():
    """The DSQ path wrote its ground frames and triggers as JSON while the DTS
    path used collections.  Nothing read the JSON any more, so a sequence
    imported from a .dsq lost both on a DTS export -- and once export started
    refusing legacy keys, it could not be exported at all."""
    _reset()
    arm = _import_dts("v24_w_sqknest.dts")
    before = {a.name for a in bpy.data.actions}
    res = bpy.ops.io_scene_dts.import_dsq(filepath=str(FIXTURES / "v24_player_root.dsq"))
    assert res == {"FINISHED"}, res
    fresh = [a for a in bpy.data.actions if a.name not in before]
    assert fresh, "no action imported from the dsq"

    for action in fresh:
        for key in ("dts_ground", "dts_triggers"):
            assert key not in action.keys(), f"{action.name} still writes {key}"

    src = read_dsq(Path(FIXTURES / "v24_player_root.dsq").read_bytes())
    total_ground = sum(len(a.dts_sequence_props.ground) for a in fresh)
    assert total_ground == sum(s.num_ground_frames for s in src.sequences)

    # and it still exports, which the legacy-key guard would otherwise refuse
    out = _tmp(".dsq")
    assert bpy.ops.io_scene_dts.export_dsq(filepath=out) == {"FINISHED"}
    dst = read_dsq(Path(out).read_bytes())
    assert sum(s.num_ground_frames for s in dst.sequences) >= total_ground


def test_export_refuses_a_scene_that_has_not_been_converted():
    """The load_post handler only fires when a file is opened with the add-on
    already enabled.  Enable it afterwards and the legacy keys are still there,
    unread -- exporting then would drop the name table and details silently."""
    _reset()
    arm = _import_dts("v24_ammo.dts")
    arm["dts_names_order"] = json.dumps(["stale"])

    out = _tmp(".dts")
    try:
        res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
        assert res == {"CANCELLED"}, res
    except RuntimeError as exc:
        assert "older version of the add-on" in str(exc), str(exc)

    from io_scene_dts.props import migrate

    migrate.migrate_all()
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}


def _mat_by_index(index):
    return next(m for m in bpy.data.materials if m.get("dts_material_index") == index)


def test_additive_flag_lives_in_the_shader():
    """MAT_ADDITIVE imports as the EEVEE additive graph and exports from it.

    Transparent BSDF + Emission -> Add Shader is both the preview and the
    storage: no dts_additive prop is consulted on export.  The Principled node
    is gone, so nothing downstream tries to fade a surface that is no longer
    connected to the output.
    """
    from io_scene_dts.dtslib.types import MAT_ADDITIVE, MAT_TRANSLUCENT

    _reset()
    _import_dts("v22_energy_explosion.dts")
    bmat = _mat_by_index(0)
    types = {n.type for n in bmat.node_tree.nodes}
    assert "ADD_SHADER" in types and "EMISSION" in types and "BSDF_TRANSPARENT" in types, types
    assert "BSDF_PRINCIPLED" not in types, "Principled left dangling behind the Add Shader"
    assert bmat.surface_render_method == "BLENDED"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    flags = read_shape_file(out).materials[0].flags
    assert flags & MAT_ADDITIVE, "additive lost"
    assert flags & MAT_TRANSLUCENT, "additive material must still blend"


def test_subtractive_round_trips_through_the_invert_node():
    """No corpus shape is subtractive, so the fixture is synthesised.

    The encoding -- the additive graph with the emission colour inverted -- is
    this add-on's own convention; EEVEE cannot render subtractive blending.
    """
    from io_scene_dts.dtslib.types import MAT_SUBTRACTIVE
    from io_scene_dts.dtslib.writer import write_shape_file

    _reset()
    src = read_shape_file(FIXTURES / "v24_shrub.dts")
    src.materials[0].flags |= MAT_SUBTRACTIVE
    seeded = _tmp(".dts")
    write_shape_file(src, seeded, version=24)
    assert bpy.ops.io_scene_dts.import_dts(filepath=seeded) == {"FINISHED"}

    bmat = _mat_by_index(0)
    emission = next(n for n in bmat.node_tree.nodes if n.type == "EMISSION")
    color = emission.inputs["Color"]
    assert color.is_linked and color.links[0].from_node.type == "INVERT", "no invert on emission"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    assert read_shape_file(out).materials[0].flags & MAT_SUBTRACTIVE, "subtractive lost"


def test_shader_edit_reaches_the_exported_blend_flag():
    """The shader is where translucency lives -- this used to be frozen."""
    from io_scene_dts.dtslib.types import MAT_TRANSLUCENT
    from io_scene_dts.mapping.materials import blend_flags_from_material

    _reset()
    _import_dts("v24_shrub.dts")
    bmat = _mat_by_index(0)
    assert blend_flags_from_material(bmat) & MAT_TRANSLUCENT, "fixture starts translucent"
    # and it is not *also* recorded beside the shader, which is the bug the
    # three blend props were: a stored copy that export threw away
    for prop in ("dts_translucent", "dts_additive", "dts_subtractive"):
        assert prop not in bmat.keys(), f"{prop} is stored as well as derived"

    bmat.surface_render_method = "DITHERED"  # the edit that used to do nothing
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    assert not read_shape_file(out).materials[0].flags & MAT_TRANSLUCENT, "shader edit ignored"

    bmat.surface_render_method = "BLENDED"
    out2 = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out2, version="24") == {"FINISHED"}
    assert read_shape_file(out2).materials[0].flags & MAT_TRANSLUCENT, "not translucent again"


def test_migration_drops_the_blend_props_saved_beside_the_shader():
    """A scene from an older version carries dts_translucent and friends.

    They were already ignored on export -- masked off and recomputed from the
    graph -- so deleting them changes no exported file and removes the second
    source of truth.
    """
    from io_scene_dts.props import migrate

    _reset()
    _import_dts("v24_shrub.dts")
    bmat = _mat_by_index(0)
    for prop in migrate.DERIVED_MATERIAL_KEYS:
        bmat[prop] = True

    migrate.migrate_all()
    for prop in migrate.DERIVED_MATERIAL_KEYS:
        assert prop not in bmat.keys(), f"{prop} survived migration"
    # idempotent, and the flags it never owned are untouched
    migrate.migrate_all()
    assert bmat["dts_s_wrap"] is not None


def test_migration_converts_the_old_combine_checkbox():
    """``combine_reflectance`` (bool) becomes ``reflectance_packing`` (enum).

    The bool is not a registered property any more, so it is written and read
    as the raw IDProperty a .blend saved by the older version still holds.

    False was "give it its own texture", which a ticked export box must not
    overrule, so it becomes SEPARATE.  True was only the old *default* -- it
    says nobody objected, not that this material insists -- so it becomes
    DEFAULT, and the box has something to act on.  Both export the bytes they
    did before, because Combine defaults on.
    """
    from io_scene_dts.props import migrate

    _reset()
    _import_dts("v24_shrub.dts")
    # the imported one and a fresh one, because the conversion is not gated on
    # dts_name: the old bool was authorable in a scene with no import in it
    split = _mat_by_index(0)
    kept = bpy.data.materials.new("kept")
    untouched = bpy.data.materials.new("untouched")
    kept.dts_material["combine_reflectance"] = True
    split.dts_material["combine_reflectance"] = False

    migrate.migrate_all()
    assert kept.dts_material.reflectance_packing == "DEFAULT"
    assert split.dts_material.reflectance_packing == "SEPARATE"
    assert untouched.dts_material.reflectance_packing == "DEFAULT"
    for bmat in (kept, split):
        assert "combine_reflectance" not in bmat.dts_material.keys(), (
            "the old key has to go, or it is a second source of truth"
        )

    # idempotent, and it does not undo an override set after the conversion
    split.dts_material.reflectance_packing = "COMBINE"
    migrate.migrate_all()
    assert split.dts_material.reflectance_packing == "COMBINE"


def test_a_translucent_sorted_mesh_records_no_mode():
    """Export infers BSP from the material, so importing it would store a
    second copy of something already decided elsewhere."""
    _reset()
    _import_dts("v21_xorg21.dts")
    src = read_shape_file(FIXTURES / "v21_xorg21.dts")
    assert any(m is not None and m.mesh_type == 3 for m in src.meshes), "fixture has no sorted mesh"

    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert objs and all(o.dts_mesh.sorted_mode == "NONE" for o in objs), (
        [o.dts_mesh.sorted_mode for o in objs]
    )
    # ...and it still comes back sorted, which is why storing it was pointless
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = [m for m in read_shape_file(out).meshes if m is not None]
    assert all(m.mesh_type == 3 for m in dst), [m.mesh_type for m in dst]


def test_an_opaque_sorted_mesh_still_records_its_mode():
    """The 15 corpus sorted meshes that are not translucent have no other way
    to say so, so the choice is written down for them."""
    from io_scene_dts.dtslib import write_shape_file
    from io_scene_dts.dtslib.types import MAT_TRANSLUCENT

    shape = read_shape_file(FIXTURES / "v21_xorg21.dts")
    for mat in shape.materials:
        mat.flags &= ~MAT_TRANSLUCENT
    opaque = _tmp(".dts")
    write_shape_file(shape, opaque, 23)

    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=opaque, import_details=True) == {"FINISHED"}
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert any(o.dts_mesh.sorted_mode == "BSP" for o in objs), (
        [o.dts_mesh.sorted_mode for o in objs]
    )

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = [m for m in read_shape_file(out).meshes if m is not None]
    assert sum(1 for m in dst if m.mesh_type == 3) == 3, [m.mesh_type for m in dst]


def test_fading_an_opaque_material_does_not_make_it_translucent():
    """A visibility fade forces BLENDED; export must not read that as the flag.

    'skins\\ShieldPackAmbient' is opaque in the file and fades, so the render
    method alone would flip it to MAT_TRANSLUCENT -- dts_blend_before_fade is
    what keeps the exported flags honest.
    """
    from io_scene_dts.dtslib.types import MAT_TRANSLUCENT

    _reset()
    _import_dts("v23_pack_upgrade_shield.dts")
    src = read_shape_file(FIXTURES / "v23_pack_upgrade_shield.dts")
    faded = [
        m
        for m in bpy.data.materials
        if "dts_material_index" in m and any(n.type == "OBJECT_INFO" for n in m.node_tree.nodes)
    ]
    opaque_faded = [m for m in faded if not src.materials[int(m["dts_material_index"])].flags & MAT_TRANSLUCENT]
    assert opaque_faded, "fixture no longer fades an opaque material; test is vacuous"
    for m in opaque_faded:
        assert m.surface_render_method == "BLENDED", "fade should have forced blending"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    for m in opaque_faded:
        i = int(m["dts_material_index"])
        assert not dst.materials[i].flags & MAT_TRANSLUCENT, (
            f"{m['dts_name']!r} went translucent because it fades"
        )


def _decal_triangles(shape, decal, slot, mesh):
    """The decal's covered triangles as vertex *positions*.

    Index equality is the wrong bar: a decal is rebuilt by matching its faces
    back onto the target's vertices, and a mesh splits vertices at UV seams,
    so a position can name several.  Either names the same triangle.
    """
    from io_scene_dts.mapping.shape_to_blender import decode_primitives

    target = shape.meshes[shape.objects[decal.raw[3]].start_mesh_index + slot]
    verts = target.verts or target.initial_verts
    return {
        tuple(sorted(tuple(round(c, 4) for c in verts[i]) for i in tri[:3]))
        for tri in decode_primitives(mesh.decal_data)
    }


def test_a_shipped_shapes_decals_can_export_as_meshes():
    """The other checkbox, on a real shape: 24 decals become 24 objects.

    A fresh scene proves the feature is authorable; this proves it survives the
    thing users actually do, which is open a shipped shape and re-export it.
    The object count is the assertion that matters -- a decal that silently
    baked nothing would still produce a valid file.
    """
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / "v23_bioderm_light.dts"), import_details=True
    ) == {"FINISHED"}

    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    out = Path(tempfile.mkdtemp()) / "baked.dts"
    assert bpy.ops.io_scene_dts.export_dts(
        filepath=str(out), version="24", decals_as_meshes=True, export_textures=False
    ) == {"FINISHED"}

    dst = read_shape_file(out)
    assert dst.decals == [], "baked decals must leave no decal table behind"
    plain = Path(tempfile.mkdtemp()) / "plain.dts"
    assert bpy.ops.io_scene_dts.export_dts(
        filepath=str(plain), version="24", export_textures=False
    ) == {"FINISHED"}
    projected = read_shape_file(plain)
    assert len(projected.decals) == len(src.decals) == 24

    # one object per decal that covered something, on top of the ordinary ones
    extra = len(dst.objects) - len(projected.objects)
    assert extra == 24, (extra, len(dst.objects), len(projected.objects))
    # the default states run in step with the objects, and the sequence tracks
    # start after them -- a baked object appended without its state would shift
    # every track that follows
    assert len(dst.object_states) >= len(dst.objects)
    assert dst.sequences[0].base_object_state == len(dst.objects), (
        dst.sequences[0].base_object_state, len(dst.objects)
    )
    # and the baked geometry is real, not empty slots
    baked = dst.objects[len(projected.objects):]
    live = [
        dst.meshes[o.start_mesh_index + j]
        for o in baked for j in range(o.num_meshes)
        if dst.meshes[o.start_mesh_index + j] is not None
    ]
    assert live, "every baked object is empty"
    assert all(m.mesh_type == 0 and m.indices and m.tverts for m in live)


def test_decals_can_import_as_meshes():
    """The checkbox, on: the faces the *file* names, which a projector cannot.

    Coverage is re-derived from the projector volume on export at recall 0.44,
    so the shipped index list exists in Blender only in this form.  It is a way
    to look at a file, not a way to author one -- there are no projectors, and
    a decal is exported from a projector.
    """
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / "v23_bioderm_light.dts"),
        decals_as_meshes=True,
        import_details=True,
    )
    assert res == {"FINISHED"}, res
    from io_scene_dts.mapping.decals import decal_objects
    from io_scene_dts.mapping.shape_to_blender import decode_primitives

    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    assert decal_objects() == [], "meshes and projectors are alternatives, not both"
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and "dts_decal_name" in o]
    assert len(meshes) == 144, len(meshes)
    assert len({int(o["dts_decal_index"]) for o in meshes}) == len(src.decals) == 24

    # each mesh is the file's own triangles, not a re-derivation
    by_key = {(int(o["dts_decal_index"]), int(o["dts_decal_slot"])): o for o in meshes}
    checked = 0
    for di, decal in enumerate(src.decals):
        _name, num, start, _oi, _sib = decal.raw
        for j in range(num):
            mesh = src.meshes[start + j]
            if mesh is None or mesh.decal_data is None:
                continue
            bobj = by_key.get((di, j))
            assert bobj is not None, (di, j)
            want = {tuple(sorted(tri[:3])) for tri in decode_primitives(mesh.decal_data)}
            assert len(bobj.data.polygons) == len(want), (di, j)
            checked += 1
    assert checked == 144, checked

    # the decal material, and UVs that are the file's own texgen planes
    # evaluated per vertex.  Not a range check: 36% of this shape's decal
    # vertices project outside the 0..1 square, because the original exporter
    # kept faces that merely clip it, so [0,1] would be the wrong bar
    for (di, j), bobj in by_key.items():
        assert bobj.data.materials and bobj.data.materials[0] is not None, bobj.name
        dd = src.meshes[src.decals[di].raw[2] + j].decal_data
        s, t = dd.texgen_s[0], dd.texgen_t[0]
        me = bobj.data
        uvs = me.uv_layers["UVMap"].data
        for loop in me.loops:
            v = me.vertices[loop.vertex_index].co
            u = v[0] * s[0] + v[1] * s[1] + v[2] * s[2] + s[3]
            w = 1.0 - (v[0] * t[0] + v[1] * t[1] + v[2] * t[2] + t[3])
            got = uvs[loop.index].uv
            assert abs(got[0] - u) < 1e-4 and abs(got[1] - w) < 1e-4, (
                bobj.name, tuple(got), (u, w)
            )

    # and they are not exported -- neither as decals nor as phantom objects
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    assert len(dst.decals) == 0, f"{len(dst.decals)} decals from meshes nothing reads"
    assert len(dst.objects) == len(src.objects), (
        f"{len(dst.objects)} objects against {len(src.objects)}: decal meshes "
        f"came back as phantom objects"
    )


def test_import_can_leave_the_lods_out():
    """The other checkbox, in its default state.

    Every level stands at the same origin, so importing all ten is ten
    overlapping copies.  Off, only the size-145 level is built.
    """
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / "v23_bioderm_light.dts")
    )
    assert res == {"FINISHED"}, res

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    sizes = {int(o["dts_detail_size"]) for o in meshes}
    assert sizes == {145}, sorted(sizes)

    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    full = [o for o in src.objects]
    assert len(meshes) == sum(
        1 for o in full
        if src.meshes[o.start_mesh_index] is not None
    ), len(meshes)

    # decals still import, onto the one level that is there
    from io_scene_dts.mapping.decals import decal_objects

    assert len(decal_objects()) == 24
    assert all(d.dts_decal.target["dts_detail_size"] == 145 for d in decal_objects())

    # the detail *table* survives -- it is stored on the armature, so the
    # exported shape still declares all ten levels, with geometry at one
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    assert len(dst.details) == len(src.details)
    assert len(dst.objects) == len(src.objects)


def test_collision_details_survive_leaving_the_lods_out():
    """A detail sized -1 is a collision hull, not a level of detail.

    Dropping it would turn a re-exported shape into one the engine cannot
    collide with, which is a far larger loss than the option is asking for.
    """
    _reset()
    res = bpy.ops.io_scene_dts.import_dts(
        filepath=str(FIXTURES / "v22_station_teleport.dts")
    )
    assert res == {"FINISHED"}, res
    src = read_shape_file(FIXTURES / "v22_station_teleport.dts")
    negative = sorted(int(d.size) for d in src.details if d.size < 0)
    assert negative == [-2, -1], negative

    sizes = sorted({int(o["dts_detail_size"]) for o in bpy.data.objects if o.type == "MESH"})
    # Collision-2 is declared with no geometry behind it, so only -1 arrives --
    # what matters is that a collision level is never treated as an LOD
    assert -1 in sizes, sizes
    assert [s for s in sizes if s >= 0] == [max(int(d.size) for d in src.details)], sizes


def test_decal_meshes_are_not_exported_as_objects():
    """A decal hangs off the armature like an ordinary mesh, but belongs to the
    decal table alone.  Exporting it as both gave the shape a phantom object
    per decal, with its own geometry and detail levels."""
    _reset()
    _import_dts("v23_bioderm_light.dts")
    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    assert len(dst.objects) == len(src.objects) == 19
    assert len(dst.details) == len(src.details) == 10
    src_names = sorted(src.name(o.name_index) for o in src.objects)
    dst_names = sorted(dst.name(o.name_index) for o in dst.objects)
    assert dst_names == src_names


def test_decals_roundtrip_through_their_projectors():
    """Everything about a decal round-trips except which faces it covers.

    A decal is a projector empty now, so the planes come back off its matrix
    exactly.  The index list does not: the file's is authored and an empty
    cannot hold one, so export recomputes coverage.  This asserts the identity
    and the projection survive, and pins coverage to a floor -- see
    test_decal_coverage_recall_has_a_floor for why it is a floor and not
    equality.
    """
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
    # the owning object of each decal, in order
    assert [d.raw[3] for d in dst.decals] == [d.raw[3] for d in src.decals]

    # The material list is unchanged even though import now gives every decal
    # target its own *copy* of its material (decals.private_material_for), so
    # a shape's meshes hold many more Blender materials than the file has
    # entries.  They collapse on dts_name; without that this shape would write
    # one entry per decal target instead of one per texture.
    assert [m.name for m in dst.materials] == [m.name for m in src.materials], (
        len(dst.materials), len(src.materials)
    )

    compared = 0
    for d_src, d_dst in zip(src.decals, dst.decals):
        assert d_dst.raw[1] == d_src.raw[1]  # same number of detail slots
        for j in range(d_src.raw[1]):
            m_src = src.meshes[d_src.raw[2] + j]
            m_dst = dst.meshes[d_dst.raw[2] + j]
            if m_src is None or m_src.decal_data is None:
                continue
            if m_dst is None or m_dst.decal_data is None:
                continue  # coverage is recomputed; a slot may come back empty
            compared += 1
            # the projection itself is exact: it is the empty's matrix
            a, b = m_src.decal_data, m_dst.decal_data
            for pa, pb in zip(a.texgen_s + a.texgen_t, b.texgen_s + b.texgen_t):
                for x, y in zip(pa, pb):
                    assert abs(x - y) < 1e-5, (x, y)
            assert (b.material_index & 0x0FFFFFFF) == (a.material_index & 0x0FFFFFFF)
            assert b.indices, "a decal that covers nothing should not be written"
    assert compared >= 120, compared
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


def test_decal_coverage_recall_has_a_floor():
    """Coverage is recomputed, so it drifts -- but it must not silently rot.

    Measured on this fixture the round trip recalls 0.444 of the covered
    triangles at precision 0.589, with rule, depth and angle fitted per decal
    at import (fit_coverage).  The precision floor also guards the facing gate:
    without it the same fit scores 0.312, so dropping it fails here.
    """
    _reset()
    _import_dts("v23_bioderm_light.dts")
    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)

    recall = precision = 0.0
    n = 0
    for d_src, d_dst in zip(src.decals, dst.decals):
        for j in range(d_src.raw[1]):
            m_src = src.meshes[d_src.raw[2] + j]
            m_dst = dst.meshes[d_dst.raw[2] + j]
            if m_src is None or m_src.decal_data is None:
                continue
            if m_dst is None or m_dst.decal_data is None:
                continue
            want = _decal_triangles(src, d_src, j, m_src)
            got = _decal_triangles(dst, d_dst, j, m_dst)
            if not want or not got:
                continue
            hit = len(want & got)
            recall += hit / len(want)
            precision += hit / len(got)
            n += 1
    assert n >= 120, n
    assert recall / n > 0.35, recall / n
    assert precision / n > 0.45, precision / n


def test_decals_import_as_projector_empties():
    """A decal imports as one empty and no mesh at all.

    The empty carries the whole decal, and the target's shader carries the
    preview: a Texture Coordinate reading the empty's object space, masked by
    the coverage attribute so Blender shows the decal only where the exported
    file will name it.
    """
    _reset()
    _import_dts("v23_bioderm_light.dts")
    from io_scene_dts.mapping.decals import coverage_attribute, decal_objects

    empties = decal_objects()
    assert len(empties) == 24, len(empties)
    # the representation this replaced: no decal is a mesh object any more
    assert not [o for o in bpy.data.objects if o.type == "MESH" and "dts_decal_name" in o]
    assert not [
        o for o in bpy.data.objects
        if any(m.type == "UV_PROJECT" for m in o.modifiers)
    ]

    assert len({d.dts_decal.index for d in empties}) == 24
    for d in empties:
        props = d.dts_decal
        assert props.target is not None and props.target.type == "MESH"
        # the coverage cache the shader masks by
        attr = props.target.data.attributes.get(coverage_attribute(props.index))
        assert attr is not None, (d.name, props.index)
        assert attr.domain == "FACE"
        assert sum(1 for v in attr.data if v.value > 0.5) > 0, d.name

        # and the branch that draws it, in the *target's* material
        mat = props.target.active_material
        label = f"DTS Decal {props.index:03d}"
        nodes = {n.type for n in mat.node_tree.nodes if n.label == label}
        assert {"TEX_COORD", "MAPPING", "SEPXYZ", "MIX_SHADER", "OBJECT_INFO"} <= nodes, nodes
        coord = next(
            n for n in mat.node_tree.nodes
            if n.label == label and n.type == "TEX_COORD"
        )
        assert coord.object is d, (coord.object, d)

        # the object gate: this material is shared, so the projector volume on
        # its own would draw the decal on every other object standing in it
        same = next(
            n for n in mat.node_tree.nodes
            if n.label == label and n.type == "MATH" and n.operation == "COMPARE"
        )
        assert abs(same.inputs[1].default_value - props.target.pass_index) < 1e-6, (
            d.name, same.inputs[1].default_value, props.target.pass_index
        )

    # every target answers to its own number, or the gate lets the wrong mesh in
    hosts = [d.dts_decal.target for d in empties]
    ids = {t.pass_index for t in hosts}
    assert 0 not in ids
    assert len(ids) == len({t.name for t in hosts}), sorted(ids)

    # No Attribute node anywhere: EEVEE caps how many attributes one material
    # may use, and every decal on a shape can land on a single material --
    # light_male puts all 58 on the body -- at which point the material fails
    # to compile and the whole mesh renders as broken-material magenta.  The
    # depth mask is computed from the projector coordinates for this reason,
    # so a stray Attribute node is a rendering regression, not a style choice.
    for mat in bpy.data.materials:
        if mat.node_tree is None:
            continue
        attrs = [n for n in mat.node_tree.nodes if n.type == "ATTRIBUTE"]
        assert not attrs, (mat.name, [n.attribute_name for n in attrs])


def test_decals_start_at_the_states_the_file_stores():
    """A decal's rest state is per decal, not a constant.

    Most Tribes 2 decals rest at -1 (off) and a Damage sequence switches them
    on, but 357 of the corpus's 2194 rest at 0 — a wreck is already damaged.
    station_teleport carries 13 of each.
    """
    from io_scene_dts.mapping.decals import decal_prop

    _reset()
    arm = _import_dts("v22_station_teleport.dts")
    src = read_shape_file(FIXTURES / "v22_station_teleport.dts")
    states = src.decal_states[: len(src.decals)]
    assert {s < 0 for s in states} == {True, False}, "fixture must carry both"

    for i, decal in enumerate(src.decals):
        name = src.name(decal.raw[0])
        assert decal_prop(i, name) in arm.keys(), (i, name)
        assert arm[decal_prop(i, name)] == float(states[i]), (i, name)

    # the ones resting on must actually be drawn, and the ones off must not.
    # With no decal object the state gates the branch's Value node instead of
    # object alpha, so that is what is read back.
    from io_scene_dts.mapping.decals import decal_objects

    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    checked = 0
    for d in decal_objects():
        props = d.dts_decal
        mat = props.target.active_material
        label = f"DTS Decal {props.index:03d}"
        value = next(
            (n for n in mat.node_tree.nodes if n.label == label and n.type == "VALUE"),
            None,
        )
        assert value is not None, (d.name, label)
        want = 1.0 if states[props.index] >= 0 else 0.0
        assert abs(value.outputs[0].default_value - want) < 1e-6, (d.name, want)
        checked += 1
    assert checked == len(src.decals), checked


def test_decal_identity_is_the_index_not_the_name():
    """Decal names repeat within a shape — station_teleport's 26 decals share
    13 names, and turret_tank_base gives all fourteen of its decals one name.
    Keying on the name would collapse them on export."""
    from io_scene_dts.mapping.decals import decal_prop

    _reset()
    arm = _import_dts("v22_station_teleport.dts")
    src = read_shape_file(FIXTURES / "v22_station_teleport.dts")
    names = [src.name(d.raw[0]) for d in src.decals]
    assert len(set(names)) < len(names), "fixture must have duplicate names"

    from io_scene_dts.mapping.decals import coverage_attribute, decal_objects

    # one property and one projector per decal, not per name
    assert len([k for k in arm.keys() if k.startswith("dts_decal_")]) == len(src.decals)
    empties = decal_objects()
    assert len(empties) == len(src.decals), len(empties)
    assert len({d.dts_decal.index for d in empties}) == len(src.decals)

    # each decal's coverage cache is keyed by its own index, so two decals
    # sharing a name and a target still mask different faces
    for d in empties:
        props = d.dts_decal
        attr = coverage_attribute(props.index)
        assert props.target.data.attributes.get(attr) is not None, (d.name, attr)

    # and they all survive a round trip, distinctly
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    assert len(dst.decals) == len(src.decals)
    assert [dst.name(d.raw[0]) for d in dst.decals] == names
    assert dst.decal_states[: len(dst.decals)] == src.decal_states[: len(src.decals)]
    # distinct decals keep distinct owners
    assert [d.raw[3] for d in dst.decals] == [d.raw[3] for d in src.decals]


def test_decals_follow_their_state_through_a_sequence():
    """Each decal branch reads the state through a driver, so one strip drives
    pose and damage together."""
    from io_scene_dts.mapping.decals import decal_objects, decal_prop
    from io_scene_dts.mapping.visibility import _do_refresh

    _reset()
    arm = _import_dts("v23_bioderm_light.dts")
    src = read_shape_file(FIXTURES / "v23_bioderm_light.dts")
    names = [src.name(d.raw[0]) for d in src.decals]

    # every decal's Value node is driven, and by that decal's own property
    driven = 0
    for d in decal_objects():
        props = d.dts_decal
        mat = props.target.active_material
        want = decal_prop(props.index, props.decal_name)
        for drv in mat.node_tree.animation_data.drivers:
            var = drv.driver.variables[0]
            if var.targets[0].data_path.strip('[]"') == want:
                driven += 1
                break
        else:
            raise AssertionError(f"{d.name}: no driver for {want}")
    assert driven == len(src.decals), driven

    # the timer that rebuilds driver relations does not fire in background mode
    _do_refresh()

    damage = next((a for a in bpy.data.actions if a.name == "Damage"), None)
    assert damage is not None
    for t in arm.animation_data.nla_tracks:
        t.mute = t.name != "Damage"

    n = _keyframes_of(damage)
    counts = []
    for f in range(1, n + 1):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        counts.append(sum(1 for i, nm in enumerate(names) if arm[decal_prop(i, nm)] >= 0))
    assert counts[0] == 0, counts
    assert counts[-1] > counts[0], counts


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


def _uv_range(shape):
    """(min, max) texture u across every non-decal mesh of a shape."""
    us = [t[0] for m in shape.meshes if m is not None and m.mesh_type != 2 for t in m.tverts]
    return min(us), max(us)


def test_uv_edit_reaches_the_exported_file():
    """A mesh still carrying a source payload must not ignore a UV edit.

    The payload digest used to cover vertex positions and polygons only, so
    editing a UV left it matching and the exporter re-emitted the stale
    payload -- the edit was silently discarded.  Narrow the digest back down
    and this test fails, which is what makes it worth having.
    """
    _reset()
    _import_dts("v24_ammo.dts")
    shifted = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.data.uv_layers.active is None:
            continue
        for datum in obj.data.uv_layers.active.data:
            datum.uv[0] += 0.25
        shifted += 1
    assert shifted, "fixture has no UV layers to edit"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    src_lo, src_hi = _uv_range(read_shape_file(FIXTURES / "v24_ammo.dts"))
    dst_lo, dst_hi = _uv_range(read_shape_file(out))
    assert abs(dst_lo - (src_lo + 0.25)) < 1e-4, (src_lo, dst_lo)
    assert abs(dst_hi - (src_hi + 0.25)) < 1e-4, (src_hi, dst_hi)


def _matframe_blocks(mesh):
    n = len(mesh.verts)
    return [
        [(round(u, 4), round(v, 4)) for u, v in mesh.tverts[f * n : (f + 1) * n]]
        for f in range(mesh.num_mat_frames)
    ]


def test_matframes_survive_an_edit():
    """Extra material frames live in mesh attributes, so editing is allowed.

    They used to sit in the pickled payload behind dts_strict_freeze, which
    refused the export outright rather than lose them.
    """
    _reset()
    _import_dts("v21_weapon_energy.dts")
    src = read_shape_file(FIXTURES / "v21_weapon_energy.dts")

    from io_scene_dts.mapping import matframes

    carriers = [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and matframes.frame_count(o.data) > 1
    ]
    assert carriers, "no matframe attributes created"
    assert all(matframes.frame_count(o.data) == 17 for o in carriers), [
        matframes.frame_count(o.data) for o in carriers
    ]
    assert not any(o.get("dts_strict_freeze") for o in carriers), "matframes still frozen"

    # nudge geometry so the payload is invalidated and the mesh is re-derived
    for obj in carriers:
        obj.data.vertices[0].co.x += 0.01

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)

    src_mf = [m for m in src.meshes if m is not None and m.num_mat_frames > 1]
    dst_mf = [m for m in dst.meshes if m is not None and m.num_mat_frames > 1]
    assert len(dst_mf) == len(src_mf) == 3, (len(dst_mf), len(src_mf))
    for m_src, m_dst in zip(src_mf, dst_mf):
        assert m_dst.num_mat_frames == 17
        assert len(m_dst.tverts) == 17 * len(m_dst.verts)
        src_blocks = _matframe_blocks(m_src)
        dst_blocks = _matframe_blocks(m_dst)
        # the dedup can split a vertex across a UV seam, so compare the set of
        # coordinates a frame holds rather than their order
        for f in range(17):
            assert set(src_blocks[f]) == set(dst_blocks[f]), f"material frame {f}"
        # and the frames stay distinct from one another -- 9 distinct blocks
        # among the 17 is what the fixture ships
        assert len({tuple(b) for b in dst_blocks}) == len({tuple(b) for b in src_blocks})


def test_merge_indices_survive_an_edit():
    """The legacy LOD-morph table rides an int array on the object.

    It does not survive whole: a strip-packed source mesh carries vertices
    that only appear in a degenerate stitch triangle, and re-deriving as
    triangle lists drops them, so a merge entry naming one has nothing to
    point at.  Export warns and keeps the rest.
    """
    _reset()
    _import_dts("v23_weapon_energy_vehicle.dts")

    carriers = [o for o in bpy.context.scene.objects if o.get("dts_merge_indices")]
    assert carriers, "no merge indices stored"

    expected = []
    for obj in carriers:
        me = obj.data
        me.calc_loop_triangles()
        referenced = {
            me.loops[li].vertex_index for tri in me.loop_triangles for li in tri.loops
        }
        expected.append(sum(1 for i in obj["dts_merge_indices"] if i in referenced))
        me.vertices[0].co.x += 0.01

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    dst_counts = sorted(
        len(m.merge_indices) for m in dst.meshes if m is not None and m.merge_indices
    )
    assert dst_counts == sorted(expected), (sorted(expected), dst_counts)
    # whatever survived has to index the vertex array it was written against
    for m in dst.meshes:
        if m is not None and m.merge_indices:
            assert max(m.merge_indices) < len(m.verts)


def test_mesh_flags_survive_an_edit():
    """Every defined bit of the flags word has a named property.

    The billboard bits had one already; the mesh-type echo in the low three
    bits did not, and export dropped it.
    """
    _reset()
    _import_dts("v21_xorg21.dts")
    billboard = [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and o.dts_mesh.billboard
    ]
    assert billboard, "fixture's billboard mesh did not import as one"
    for obj in billboard:
        obj.data.vertices[0].co.x += 0.01

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    assert any(m.flags & (1 << 31) for m in dst.meshes if m is not None), "billboard bit lost"


def test_a_billboard_can_be_authored_from_a_plain_mesh():
    """Turning a mesh *into* a billboard, which the ID-prop form could not do.

    The flags were written only when the bit was set, so a mesh that was not
    already a billboard had no property for a checkbox to bind to: the flag
    could be cleared and never set.  Z-axis is the sharper case — no Tribes 2
    shape sets it, so there was nowhere in any scene to tick it.
    """
    from io_scene_dts.dtslib.types import MESH_BILLBOARD, MESH_BILLBOARD_Z_AXIS

    _reset()
    _import_dts("v24_ammo.dts")
    plain = next(
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and "dts_object_name" in o and not o.dts_mesh.billboard
    )
    plain.dts_mesh.billboard = True
    plain.dts_mesh.billboard_z = True

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    flagged = [
        m for m in dst.meshes
        if m is not None and m.flags & MESH_BILLBOARD and m.flags & MESH_BILLBOARD_Z_AXIS
    ]
    assert flagged, "a billboard authored in Blender did not reach the file"

    # and it comes back as one, so the round trip is closed in both directions
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=out) == {"FINISHED"}
    back = [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and o.dts_mesh.billboard and o.dts_mesh.billboard_z
    ]
    assert back, "the Z-axis billboard did not survive re-import"


def test_every_mesh_flag_is_settable_not_just_clearable():
    """Each flag exists on every DTS mesh with a default, so a panel can draw
    it.  Absence used to mean False, which is not something you can tick."""
    _reset()
    _import_dts("v24_ammo.dts")
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or "dts_object_name" not in obj:
            continue
        props = obj.dts_mesh
        assert props.is_dts
        for field in ("billboard", "billboard_z", "has_detail_texture",
                      "use_encoded_normals", "echo_type_bits", "always_write_depth"):
            assert isinstance(getattr(props, field), bool), field
        assert props.sorted_mode in ("NONE", "FLAT", "BSP")
        # and the old conditionally-present keys are gone
        for key in ("dts_billboard", "dts_billboard_z", "dts_sorted_mode"):
            assert key not in obj.keys(), key


def test_mesh_type_echo_bits_survive_an_edit():
    _reset()
    _import_dts("v24_w_sqknest.dts")
    echoing = [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and o.dts_mesh.echo_type_bits
    ]
    assert echoing, "fixture's skins did not record the type echo"
    for obj in echoing:
        obj.data.vertices[0].co.x += 0.001

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    skins = [m for m in dst.meshes if m is not None and m.mesh_type == SKIN_MESH]
    assert skins, "no skins exported"
    assert any(m.flags & 0x7 == SKIN_MESH for m in skins), "mesh type echo lost"


def _sharing(shape):
    """(child mesh index, parent mesh index) for every shared mesh."""
    return [
        (i, m.parent_mesh)
        for i, m in enumerate(shape.meshes)
        if m is not None and m.parent_mesh >= 0
    ]


def test_lod_vertex_sharing_is_rederived():
    """parent_mesh sharing is rebuilt, not replayed.

    Each detail level of an object is interned into one pool lowest detail
    first, so every smaller level lands on a prefix of the larger one and can
    name it instead of storing vertices of its own.  Losing this was worth
    x1.85 in file size.
    """
    _reset()
    _import_dts("v22_turret_belly_barrell.dts")
    src = read_shape_file(FIXTURES / "v22_turret_belly_barrell.dts")
    assert _sharing(src), "fixture does not use parent_mesh"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)

    shared = _sharing(dst)
    assert shared, "no vertex sharing was re-derived"
    for child_index, parent_index in shared:
        child, parent = dst.meshes[child_index], dst.meshes[parent_index]
        # the reader slices the parent's arrays with the child's own counts, so
        # the parent must precede it and be at least as long
        assert parent_index < child_index, (parent_index, child_index)
        assert parent is not None
        assert len(parent.verts) >= len(child.verts)
        assert parent.verts[: len(child.verts)] == child.verts
        assert parent.tverts[: len(child.tverts)] == child.tverts
        assert parent.norms[: len(child.norms)] == child.norms


def test_lod_sharing_keeps_the_file_from_growing():
    """The point of the pool, stated as a number: re-deriving a multi-detail
    shape must not cost more than the file it was read from."""
    import os

    _reset()
    _import_dts("v22_turret_belly_barrell.dts")
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    before = os.path.getsize(FIXTURES / "v22_turret_belly_barrell.dts")
    after = os.path.getsize(out)
    assert after <= before * 1.05, (before, after)


def test_lod_sharing_degrades_when_a_level_stops_nesting():
    """A detail level replaced by unrelated geometry still exports correctly.

    Nesting holds by construction -- the pool only grows -- so the failure mode
    is a larger pool, never an invalid prefix.
    """
    _reset()
    _import_dts("v22_turret_belly_barrell.dts")
    # the smallest detail level of some object, rebuilt as a cube nowhere near
    # the original geometry
    victim = min(
        (o for o in bpy.context.scene.objects if o.type == "MESH" and "dts_detail_size" in o),
        key=lambda o: int(o["dts_detail_size"]),
    )
    me = victim.data
    me.clear_geometry()
    me.from_pydata(
        [(9.0, 9.0, 9.0), (10.0, 9.0, 9.0), (10.0, 10.0, 9.0), (9.0, 10.0, 9.0)],
        [],
        [(0, 1, 2), (0, 2, 3)],
    )
    me.update()

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    for child_index, parent_index in _sharing(dst):
        child, parent = dst.meshes[child_index], dst.meshes[parent_index]
        assert parent.verts[: len(child.verts)] == child.verts, (parent_index, child_index)


def test_shape_tables_are_editable_collections():
    """Four shape tables were JSON strings on the armature — present in the UI
    and unusable from it."""
    _reset()
    arm = _import_dts("v22_energy_explosion.dts")
    src = read_shape_file(FIXTURES / "v22_energy_explosion.dts")
    props = arm.dts_shape

    assert props.is_shape
    assert [n.name for n in props.names] == list(src.names)
    assert len(props.details) == len(src.details)
    assert [d.name for d in props.details] == [src.name(d.name_index) for d in src.details]
    # the IFL table is not a collection any more -- it is derived from the
    # materials that flip, so what has to survive is the checkbox on them
    flipping = [m for m in bpy.data.materials if m.dts_material.is_ifl]
    assert len(flipping) == len(src.ifl_materials) == 1
    assert len(props.material_order) == len(src.materials)
    # pointers, not names: a name is not unique in a real shape
    assert all(ref.material is not None for ref in props.material_order)

    for key in ("dts_names_order", "dts_details", "dts_materials_order",
                "dts_ifl_materials", "dts_node_transforms"):
        assert key not in arm.keys(), key


def test_editing_a_detail_size_reaches_the_file():
    _reset()
    arm = _import_dts("v24_ammo.dts")
    details = arm.dts_shape.details
    target = next(d for d in details if d.size > 0)
    target.size = 77.0

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    assert 77.0 in [d.size for d in dst.details], [d.size for d in dst.details]


def test_sequence_tables_are_editable_collections():
    _reset()
    _import_dts("v23_pack_upgrade_shield.dts")
    src = read_shape_file(FIXTURES / "v23_pack_upgrade_shield.dts")

    checked = 0
    for seq in src.sequences:
        action = bpy.data.actions.get(src.name(seq.name_index))
        if action is None:
            continue
        props = action.dts_sequence_props
        assert len(props.ground) == min(
            seq.num_ground_frames, max(0, len(src.ground_translations) - seq.first_ground_frame)
        )
        assert len(props.triggers) == seq.num_triggers
        checked += 1
        for key in ("dts_ground", "dts_triggers", "dts_ifl_matters"):
            assert key not in action.keys(), key
    assert checked


def test_a_trigger_can_be_authored():
    """Exactly one sequence in the 630-shape corpus has a trigger, so adding
    one is the case that matters.  The packed U32 comes apart into a state
    number and two flags, so the numbers in the UI are the format's own."""
    _reset()
    _import_dts("v24_woodDoor01.dts")
    action = next(a for a in bpy.data.actions if a.get("dts_sequence"))
    assert len(action.dts_sequence_props.triggers) == 0

    trigger = action.dts_sequence_props.triggers.add()
    trigger.state = 7
    trigger.on = True
    trigger.invert_on_reverse = True
    trigger.pos = 0.25

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    assert dst.triggers
    state = dst.triggers[0].state
    assert state & 0x3FFFFFFF == 1 << 6, hex(state)  # state 7 is bit 6
    assert state & (1 << 31), "on bit lost"
    assert state & (1 << 30), "invert-on-reverse bit lost"
    assert abs(dst.triggers[0].pos - 0.25) < 1e-6


def test_legacy_blend_migrates():
    """A scene saved by an older version keeps working: the blobs convert and
    the pickled payload is discarded rather than unpickled."""
    from io_scene_dts.props import migrate

    _reset()
    bpy.ops.object.armature_add()
    arm = bpy.context.object
    arm["dts_names_order"] = json.dumps(["base", "detail2"])
    arm["dts_details"] = json.dumps([["detail2", 0, 0, 2.0, -1.0, -1.0, 8]])
    arm["dts_materials_order"] = json.dumps([])
    arm["dts_ifl_materials"] = json.dumps(
        [{"name": "flame.ifl", "raw": [0, 1, 2, 0, 5]}]
    )
    bone = arm.data.bones[0]
    bone["dts_name"] = "Bone"
    arm["dts_node_transforms"] = json.dumps({"Bone": [[1, 2, 3, 32767], [0.0, 1.0, 0.0]]})

    action = bpy.data.actions.new("Legacy")
    action["dts_sequence"] = True
    action["dts_triggers"] = json.dumps([[(1 << 3) | (1 << 31), 0.5]])
    action["dts_ground"] = json.dumps([[[1.0, 0.0, 0.0], [0, 0, 0, 32767]]])
    action["dts_keyframes"] = 4

    bpy.ops.mesh.primitive_cube_add()
    mesh_obj = bpy.context.object
    mesh_obj["dts_source_payload"] = "not-actually-a-pickle"
    mesh_obj["dts_strict_freeze"] = True

    report = migrate.migrate_all()
    assert report

    props = arm.dts_shape
    assert props.is_shape
    assert [n.name for n in props.names] == ["base", "detail2"]
    assert len(props.details) == 1 and props.details[0].poly_count == 8
    # The legacy entry named material slot 1, and this scene has no materials
    # at all -- the table was the only record of it, and the table is gone.
    # Migration says so rather than dropping it silently.
    assert any("IFL" in line for line in report), report
    assert bone.dts_node.use_stored
    assert tuple(bone.dts_node.stored_rotation) == (1, 2, 3, 32767)

    seq_props = action.dts_sequence_props
    assert len(seq_props.triggers) == 1
    assert seq_props.triggers[0].state == 4  # bit 3
    assert seq_props.triggers[0].on
    assert len(seq_props.ground) == 1

    # every legacy key is consumed, so the two forms cannot disagree
    assert not migrate.legacy_keys_present(), migrate.legacy_keys_present()
    # and the payload went without being read
    assert "dts_source_payload" not in mesh_obj.keys()
    assert any("discarded rather than unpickled" in line for line in report)

    # idempotent
    assert migrate.migrate_all() == []


def test_legacy_decal_meshes_migrate_to_their_empty():
    """A .blend from before decals became empties must not export phantoms.

    The old form kept the covered faces as a mesh object parented to the
    armature exactly like its target, so the exporter cannot tell it from a
    real mesh: left in place, every decal comes back as an extra shape object
    with its own geometry and detail levels.  Migration removes them and moves
    what only they held -- the target and the material -- onto the empty.
    """
    sys.path.insert(0, str(REPO / "tests" / "blender"))
    import authoring as A

    from io_scene_dts.mapping.decals import decal_objects
    from io_scene_dts.props import migrate

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
    # blended, because a shape that carries decals has to have something
    # translucent for the engine to draw them against
    decal_mat = A.blended_material("scorch")

    legacy = A.mesh_object(
        "scorch2", arm, bone="root", verts=verts, faces=faces, material=decal_mat
    )
    legacy["dts_decal_name"] = "scorch"
    legacy["dts_decal_index"] = 0
    legacy["dts_decal_object"] = "wall"
    legacy["dts_decal_slot"] = 0
    legacy["dts_decal_target"] = target.name

    empty = bpy.data.objects.new("decal_scorch", None)
    bpy.context.scene.collection.objects.link(empty)
    empty["dts_decal_name"] = "scorch"
    empty["dts_decal_index"] = 0
    empty["dts_decal_object"] = "wall"
    empty.matrix_world = target.matrix_world

    assert migrate.migrate_all()

    # the mesh is gone and the empty carries the decal
    assert "scorch2" not in bpy.data.objects
    decals = decal_objects()
    assert len(decals) == 1
    props = decals[0].dts_decal
    assert props.decal_name == "scorch"
    assert props.target is target
    assert props.material is decal_mat
    assert "dts_decal_name" not in empty.keys()

    # and the export has no phantom object for it
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    shape = read_shape_file(out)
    assert [shape.name(o.name_index) for o in shape.objects] == ["wall"]
    assert len(shape.decals) == 1


def _dts_panels():
    from io_scene_dts.ui import panels

    return panels.CLASSES


def test_every_panel_polls_and_draws():
    """A panel that raises in draw() is worse than no panel: Blender swallows
    the error into the console and leaves a blank region.  Nothing here checks
    the layout is *good*, only that it runs against the states it will meet."""

    class _Layout:
        """Records calls instead of drawing, so draw() can run headless."""

        def __getattr__(self, name):
            def call(*args, **kwargs):
                return _Layout()

            return call

        def __setattr__(self, name, value):
            pass

    class _Context:
        def __init__(self, obj=None, bone=None, material=None):
            self.object = obj
            self.bone = bone
            self.material = material
            self.active_nla_strip = None

    _reset()
    empty = _Context()
    for panel in _dts_panels():
        assert panel.poll(empty) in (True, False)

    arm = _import_dts("v23_pack_upgrade_shield.dts")
    bone = arm.data.bones[0]
    mesh = next(o for o in bpy.context.scene.objects if "dts_object_name" in o)
    material = next(m for m in bpy.data.materials if "dts_name" in m)
    contexts = [_Context(arm, bone), _Context(mesh), _Context(mesh, material=material)]

    class _Self:
        """A Panel subclass cannot be instantiated outside Blender's own
        registration, but draw() is a plain function; it only wants a `layout`."""

        layout = _Layout()

    drawn = 0
    for ctx in contexts:
        for panel in _dts_panels():
            if not panel.poll(ctx):
                continue
            panel.draw(_Self(), ctx)
            drawn += 1
    assert drawn >= 8, drawn


def test_the_list_operators_reach_their_collections():
    _reset()
    arm = _import_dts("v24_ammo.dts")
    bpy.context.view_layer.objects.active = arm
    before = len(arm.dts_shape.details)

    assert bpy.ops.io_scene_dts.list_add(path="object.dts_shape.details") == {"FINISHED"}
    assert len(arm.dts_shape.details) == before + 1
    assert bpy.ops.io_scene_dts.list_remove(path="object.dts_shape.details") == {"FINISHED"}
    assert len(arm.dts_shape.details) == before

    # order is load-bearing in the name table, so moving has to work
    arm.dts_shape.names_index = 1
    first = [n.name for n in arm.dts_shape.names]
    assert bpy.ops.io_scene_dts.list_move(
        path="object.dts_shape.names", direction="UP"
    ) == {"FINISHED"}
    after = [n.name for n in arm.dts_shape.names]
    assert after[0] == first[1] and after[1] == first[0], (first[:3], after[:3])

    # a path that names nothing is refused rather than raising
    assert bpy.ops.io_scene_dts.list_remove(path="object.dts_shape.nope") == {"CANCELLED"}


def _ifl_fixture():
    """switch.dts in a real mod tree: shapes/ beside textures/skins/.

    The two .ifl files and every frame texture they name are copied in, which
    no fixture directory in this repo has -- the corpus keeps shapes and
    textures in trees that are not siblings, so nothing here resolves a
    flipbook by the add-on's own rules without building the layout first.
    """
    import shutil

    # the pristine source tree, not tribes2-exported -- that one is an output
    # directory whose contents change every time anybody exports into it
    src = Path("/home/henrik/Documents/Repositories/agentic-torque/mygame"
               "/animation-test/data/shapes/tribes2")
    frames = Path("/home/henrik/Documents/Repositories/hasell-engine/files")
    if not (src / "switch.dts").is_file():
        return None
    root = Path(tempfile.mkdtemp())
    (root / "shapes").mkdir()
    skins = root / "textures" / "skins"
    skins.mkdir(parents=True)
    shutil.copy(src / "switch.dts", root / "shapes")
    for ifl in list(src.glob("*.ifl")) + list((src / "skins").glob("*.ifl")):
        if ifl.stem in ("jetflare00", "screenstatic1") and not (skins / ifl.name).exists():
            shutil.copy(ifl, skins)
    # first writer wins: the game files are read-only, so a copy keeps that
    # mode and a second copy over the same name cannot open it
    for png in list(src.glob("*.png")) + [
        p for pattern in ("tribes/textures/skins/jetflare0*.png",
                          "shapes/textures/skins/screenstatic*.png")
        for p in frames.glob(pattern)
    ]:
        if not (skins / png.name).exists():
            shutil.copy(png, skins)
    return root / "shapes" / "switch.dts"


def test_an_ifl_imports_its_frames_and_previews_them():
    """The .ifl is the animation, and until now nothing read one."""
    path = _ifl_fixture()
    if path is None:
        return
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(path), import_details=True) == {"FINISHED"}

    flipping = {m["dts_name"]: m for m in bpy.data.materials if m.dts_material.is_ifl}
    assert set(flipping) == {"skins\\jetflare00", "skins\\screenstatic1"}, list(flipping)
    jet = flipping["skins\\jetflare00"]
    frames = list(jet.dts_material.ifl_frames)
    # 210 lines over 6 textures, ordered 00,03,01,04,02,05 -- a sequence, not a set
    assert len(frames) == 210
    assert len({f.image.name for f in frames if f.image}) == 6
    assert all(f.image is not None for f in frames), "a frame texture did not resolve"

    # the preview: one node per distinct image, and a constant-interpolated
    # index over the schedule the durations describe
    from io_scene_dts.mapping.sequences import _iter_fcurves

    action = jet.node_tree.animation_data.action
    curve = next(f for f in _iter_fcurves(action) if "dts_ifl_frame" in f.data_path)
    assert len(curve.keyframe_points) == 210
    assert curve.keyframe_points[0].interpolation == "CONSTANT"
    assert {round(k.co[1]) for k in curve.keyframe_points} == set(range(6))


def test_an_ifl_round_trips_through_its_material():
    """The table is derived, so what has to survive is the material and the
    .ifl -- and the sequence that advances it."""
    path = _ifl_fixture()
    if path is None:
        return
    _reset()
    assert bpy.ops.io_scene_dts.import_dts(filepath=str(path), import_details=True) == {"FINISHED"}
    src = read_shape_file(path)
    out = Path(tempfile.mkdtemp()) / "switch.dts"
    assert bpy.ops.io_scene_dts.export_dts(filepath=str(out), version="24") == {"FINISHED"}
    dst = read_shape_file(out)

    # the slot is preserved; the name is deliberately *not* -- it is written
    # bare so the engine opens the .ifl beside the textures it lists, rather
    # than in a skins/ subdirectory MaterialList would never look in
    assert [e.raw[1] for e in dst.ifl_materials] == [e.raw[1] for e in src.ifl_materials]
    assert [dst.name(e.raw[0]) for e in dst.ifl_materials] == [
        "jetflare00.ifl", "screenstatic1.ifl"
    ]
    assert [src.name(e.raw[0]) for e in src.ifl_materials] == [
        "skins\\jetflare00.ifl", "skins\\screenstatic1.ifl"
    ]
    # the source's num_frames is uninitialised memory; ours is the real count
    assert [e.raw[4] for e in dst.ifl_materials] == [210, 120]
    assert all(e.raw[2] == e.raw[3] == 0 for e in dst.ifl_materials)
    assert [sorted(q.ifl_matters.indices()) for q in dst.sequences] == [
        sorted(q.ifl_matters.indices()) for q in src.sequences
    ]

    written = sorted(p.name for p in out.parent.glob("*.ifl"))
    assert written == ["jetflare00.ifl", "screenstatic1.ifl"], written
    lines = (out.parent / "jetflare00.ifl").read_text().splitlines()
    assert len(lines) == 210 and lines[:3] == [
        "jetflare00.png 1", "jetflare03.png 1", "jetflare01.png 1"
    ], lines[:3]
    # the frame textures came off disk, and go with the .ifl that lists them:
    # a flipbook whose frames are somewhere else is a flipbook of nothing
    beside = {p.name for p in out.parent.glob("*.png")}
    assert {line.split()[0] for line in lines} <= beside, sorted(beside)


def test_ifl_preserved():
    """The IFL table is derived now, so what must survive is what it is derived
    from: the material flips, and its entry names it."""
    _reset()
    _import_dts("v22_energy_explosion.dts")
    src = read_shape_file(FIXTURES / "v22_energy_explosion.dts")
    out = _tmp(".dts")
    res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="23")
    assert res == {"FINISHED"}, res
    dst = read_shape_file(out)
    assert len(dst.ifl_materials) == len(src.ifl_materials) == 1
    # the name is written bare, not with the source's skins\ prefix: the engine
    # opens a .ifl at shapePath/<name> but strips the prefix off a *material*
    # name, so only a bare name puts the .ifl beside the textures it lists
    from io_scene_dts.mapping.ifl import material_name_for

    assert material_name_for(dst.name(dst.ifl_materials[0].raw[0])) == \
        material_name_for(src.name(src.ifl_materials[0].raw[0])).rpartition("\\")[2]
    assert "\\" not in dst.name(dst.ifl_materials[0].raw[0])
    assert dst.ifl_materials[0].raw[1] == src.ifl_materials[0].raw[1], "material slot moved"
    # ...and the three the engine fills from the .ifl are written as zeros
    # rather than the uninitialised memory the shipped files carry
    assert dst.ifl_materials[0].raw[2] == dst.ifl_materials[0].raw[3] == 0
    assert [m.name for m in dst.materials] == [m.name for m in src.materials]
    from io_scene_dts.dtslib.types import MAT_IFL_MATERIAL

    slot = dst.ifl_materials[0].raw[1]
    assert dst.materials[slot].flags & MAT_IFL_MATERIAL


def test_sorted_meshes_survive_an_edit():
    """Editing a sorted mesh used to be refused outright: its BSP cluster
    tables lived only in the pickled payload and could not be re-derived.
    dtslib/sorted_build.py generates them, so it is ordinary geometry now."""
    _reset()
    _import_dts("v21_xorg21.dts")
    src = read_shape_file(FIXTURES / "v21_xorg21.dts")
    assert any(m is not None and m.mesh_type == 3 for m in src.meshes), "fixture has no sorted mesh"

    src_sorted = [m for m in src.meshes if m is not None and m.mesh_type == 3]
    # not selected by dts_sorted_mode: every sorted mesh in this fixture is on
    # a translucent material, so export infers the mode and the importer
    # deliberately records nothing
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert objs and not any(o.get("dts_strict_freeze") for o in objs), "still frozen"

    for obj in objs:
        obj.data.vertices[0].co.x += 0.05

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    dst_sorted = [m for m in dst.meshes if m is not None and m.mesh_type == 3]
    assert len(dst_sorted) >= len(src_sorted), (len(dst_sorted), len(src_sorted))
    for mesh in dst_sorted:
        assert mesh.sorted_data is not None
        assert mesh.sorted_data.clusters, "no cluster tree was built"
        assert mesh.sorted_data.num_verts == [len(mesh.verts)]


def test_an_imported_translucent_mesh_is_promoted_on_re_export():
    """The promotion is unconditional -- it fires on a re-export too.

    v24_shrub's one mesh arrives as a STANDARD_MESH on a translucent material
    and leaves as a sorted one, so a plain round trip changes the mesh type.
    That is the deliberate cost of inferring sorting from the material rather
    than only honouring an explicit setting.
    """
    _reset()
    _import_dts("v24_shrub.dts")
    src = read_shape_file(FIXTURES / "v24_shrub.dts")
    live = [m for m in src.meshes if m is not None]
    assert all(m.mesh_type == 0 for m in live), "fixture should start standard"

    obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    assert obj.dts_mesh.sorted_mode == "NONE", "nothing asked for sorting"

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = [m for m in read_shape_file(out).meshes if m is not None]
    assert all(m.mesh_type == 3 for m in dst), [m.mesh_type for m in dst]
    assert all(m.sorted_data is not None and m.sorted_data.clusters for m in dst)


def test_sorted_cluster_tree_walks_like_the_engine_reads_it():
    """The generated tree is checked with the same walk simulation that
    measured the shipped art, so both are held to one reading of the format."""
    import sys as _sys

    _sys.path.insert(0, str(REPO / "tests"))
    from sorted_walk import camera_positions, triangles_of, walk

    _reset()
    _import_dts("v21_xorg21.dts")
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)

    checked = 0
    for mesh in dst.meshes:
        if mesh is None or mesh.mesh_type != 3 or not mesh.sorted_data:
            continue
        checked += 1
        every = {
            tuple(sorted(t))
            for t in triangles_of(mesh.primitives, mesh.indices, range(len(mesh.primitives)))
        }
        for camera in camera_positions(mesh.verts, 32):
            drawn = walk(mesh.sorted_data, camera)  # raises if it never terminates
            assert len(set(drawn)) == len(drawn), "a primitive was drawn twice"
            got = {
                tuple(sorted(t))
                for t in triangles_of(mesh.primitives, mesh.indices, drawn)
            }
            # better than the shipped art, which drops triangles from some
            # angles on 54 of its 119 sorted meshes
            assert got == every
    assert checked, "no sorted meshes were exported"


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
        n = _keyframes_of(action)
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
    n = _keyframes_of(bpy.data.actions["ambient"])
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


def _file_default_vis(shape):
    """{object name: resting vis} — the first len(objects) states are defaults."""
    return {
        shape.name(obj.name_index): shape.object_states[i].vis
        for i, obj in enumerate(shape.objects)
        if i < len(shape.object_states)
    }


def _file_vis_tracks(shape):
    """{sequence name: {object name: [vis per keyframe]}} as the file stores it."""
    out = {}
    for seq in shape.sequences:
        n = seq.num_keyframes
        tracks = {}
        for oi in seq.vis_matters.indices():
            if oi >= len(shape.objects):
                continue
            base = seq.base_object_state + seq.vis_matters.ordinal_of(oi) * n
            tracks[shape.name(shape.objects[oi].name_index)] = [
                shape.object_states[base + kf].vis for kf in range(n)
            ]
        if tracks:
            out[shape.name(seq.name_index).lower()] = tracks
    return out


def _vis_keyframes(action, dts_object_name):
    from io_scene_dts.mapping.visibility import vis_path

    path = vis_path(dts_object_name)
    fc = next((f for f in _fcurves(action) if f.data_path == path), None)
    return None if fc is None else [kp.co[1] for kp in fc.keyframe_points]


def test_visibility_survives_export_and_reimport():
    """Resting object states and animated vis tracks must round-trip.

    The importer only previews what the file already said; the file is what
    has to come back.  v22_disc hides four of its five objects at rest and
    fades them in, and the cloaking pack fades four objects over 40 keyframes.
    """
    from io_scene_dts.mapping.visibility import vis_prop

    for fixture in ("v22_disc.dts", "v23_pack_upgrade_cloaking.dts"):
        _reset()
        _import_dts(fixture)
        out = _tmp(".dts")
        res = bpy.ops.io_scene_dts.export_dts(filepath=out, version="24")
        assert res == {"FINISHED"}, (fixture, res)

        src = read_shape_file(FIXTURES / fixture)
        dst = read_shape_file(out)

        # resting state: defaulting these to 1.0 would draw every hidden mesh
        src_def, dst_def = _file_default_vis(src), _file_default_vis(dst)
        assert set(dst_def) == set(src_def), fixture
        for name, vis in src_def.items():
            assert abs(dst_def[name] - vis) < 1e-6, (fixture, name, dst_def[name], vis)
        assert any(v == 0.0 for v in src_def.values()), f"{fixture} tests nothing"

        # animated tracks, keyframe for keyframe
        src_tracks, dst_tracks = _file_vis_tracks(src), _file_vis_tracks(dst)
        assert set(dst_tracks) == set(src_tracks), fixture
        for seq_name, objects in src_tracks.items():
            assert set(dst_tracks[seq_name]) == set(objects), (fixture, seq_name)
            for obj_name, track in objects.items():
                got = dst_tracks[seq_name][obj_name]
                assert len(got) == len(track), (fixture, seq_name, obj_name)
                for kf, (a, b) in enumerate(zip(track, got)):
                    assert abs(a - b) < 1e-6, (fixture, seq_name, obj_name, kf, a, b)
        assert src_tracks, f"{fixture} has no vis tracks"

        # and the exported file must import back into a working preview
        _reset()
        assert bpy.ops.io_scene_dts.import_dts(filepath=out) == {"FINISHED"}
        arm = _armature()
        # the scene was reset, so no action needed a dedup suffix
        actions = {a.name.lower(): a for a in bpy.data.actions if a.get("dts_sequence")}
        for seq_name, objects in src_tracks.items():
            action = actions.get(seq_name)
            assert action is not None, (fixture, seq_name, sorted(actions))
            for obj_name, track in objects.items():
                keys = _vis_keyframes(action, obj_name)
                assert keys is not None, (fixture, seq_name, obj_name)
                assert len(keys) == len(track)
                for kf, (a, b) in enumerate(zip(track, keys)):
                    assert abs(a - b) < 1e-5, (fixture, seq_name, obj_name, kf, a, b)
        for name, vis in src_def.items():
            if name in {o for objs in src_tracks.values() for o in objs}:
                assert abs(arm[vis_prop(name)] - vis) < 1e-6, (fixture, name)


def _vis_fcurve(action, dts_object_name):
    from io_scene_dts.mapping.objectstate import path_for
    from io_scene_dts.mapping.sequences import _iter_fcurves

    want = path_for("vis", dts_object_name)
    return next((fc for fc in _iter_fcurves(action) if fc.data_path == want), None)


def test_keyframed_visibility_reaches_the_exported_file():
    """Editing a visibility key used to change the preview and nothing else.

    Export rebuilt the object-state tracks from a dts_object_anim JSON blob and
    never looked at the curves the drivers read, so the blob was the authored
    form and the curves were decoration.  There is no blob now.
    """
    _reset()
    _import_dts("v23_bioderm_light.dts")
    arm = _armature()

    from io_scene_dts.mapping.objectstate import parse_path
    from io_scene_dts.mapping.sequences import _iter_fcurves

    edited = None
    for action in bpy.data.actions:
        for fcurve in _iter_fcurves(action):
            parsed = parse_path(fcurve.data_path)
            if parsed and parsed[0] == "vis" and len(fcurve.keyframe_points) > 1:
                edited = (action, parsed[1], fcurve)
                break
        if edited:
            break
    assert edited, "fixture has no visibility curve to edit"
    action, name, fcurve = edited

    # drive every key to a value the file cannot already contain
    for point in fcurve.keyframe_points:
        point.co = (point.co[0], 0.375)
    fcurve.update()

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)

    seq = next(s for s in dst.sequences if dst.name(s.name_index) == action.name)
    oi = next(
        i for i, o in enumerate(dst.objects) if dst.name(o.name_index) == name
    )
    assert seq.vis_matters.test(oi), f"{name} lost its vis track"
    ordinal = seq.vis_matters.ordinal_of(oi)
    states = [
        dst.object_states[seq.base_object_state + ordinal * seq.num_keyframes + kf].vis
        for kf in range(seq.num_keyframes)
    ]
    assert states, "no object states written"
    assert all(abs(v - 0.375) < 1e-4 for v in states), states


def test_removing_a_keyframe_shortens_the_sequence():
    """Sequence length used to come from a stored dts_keyframes, which an
    imported sequence always had — so adding or removing keys in Blender
    changed nothing about the exported file."""
    from io_scene_dts.mapping.sequences import _iter_fcurves

    _reset()
    _import_dts("v24_woodDoor01.dts")
    action = next(a for a in bpy.data.actions if a.get("dts_sequence"))
    before = _keyframes_of(action)
    assert before > 2, before

    # drop the last key from every curve in the action
    for fcurve in _iter_fcurves(action):
        while fcurve.keyframe_points and fcurve.keyframe_points[-1].co[0] >= before:
            fcurve.keyframe_points.remove(fcurve.keyframe_points[-1])
        fcurve.update()
    assert _keyframes_of(action) == before - 1

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)
    seq = next(s for s in dst.sequences if dst.name(s.name_index) == action.name)
    assert seq.num_keyframes == before - 1, (seq.num_keyframes, before)


def test_matters_sets_are_inferred_from_the_channels_that_exist():
    """The rotation/translation sets used to be stored by node name.  Inferring
    them reproduces the file exactly, and means a bone channel added in Blender
    marks its node instead of being ignored."""
    _reset()
    _import_dts("v24_woodDoor01.dts")
    src = read_shape_file(FIXTURES / "v24_woodDoor01.dts")
    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="24") == {"FINISHED"}
    dst = read_shape_file(out)

    for a, b in zip(src.sequences, dst.sequences):
        assert sorted(a.rotation_matters.indices()) == sorted(b.rotation_matters.indices()), (
            src.name(a.name_index)
        )
        assert sorted(a.translation_matters.indices()) == sorted(
            b.translation_matters.indices()
        ), src.name(a.name_index)


def test_scale_animation_rides_the_bone_scale_channels():
    """Node scale used to be a dts_scale_anim blob, so scaling a pose bone in
    Blender produced nothing and the blob was the only truth."""
    from io_scene_dts.mapping.sequences import _iter_fcurves

    _reset()
    _import_dts("v22_disc.dts")
    src = read_shape_file(FIXTURES / "v22_disc.dts")
    src_seq = next(s for s in src.sequences if s.flags & 0x7)
    assert src_seq.animates_aligned_scale()

    action = next(a for a in bpy.data.actions if a.name == src.name(src_seq.name_index))
    assert action.dts_sequence_props.scale_mode == "ALIGNED"
    scale_curves = [fc for fc in _iter_fcurves(action) if fc.data_path.endswith(".scale")]
    assert len(scale_curves) == 3, len(scale_curves)
    assert "dts_scale_anim" not in action.keys()

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    dst_seq = next(s for s in dst.sequences if s.flags & 0x7)
    assert dst_seq.animates_aligned_scale()
    assert sorted(dst_seq.scale_matters.indices()) == sorted(src_seq.scale_matters.indices())

    n = src_seq.num_keyframes
    count = src_seq.scale_matters.count()
    src_vals = src.node_aligned_scales[src_seq.base_scale : src_seq.base_scale + count * n]
    dst_vals = dst.node_aligned_scales[dst_seq.base_scale : dst_seq.base_scale + count * n]
    assert len(dst_vals) == len(src_vals) == count * n
    for a, b in zip(src_vals, dst_vals):
        assert all(abs(x - y) < 1e-4 for x, y in zip(a, b)), (a, b)


def test_editing_a_scale_key_reaches_the_file():
    from io_scene_dts.mapping.sequences import _iter_fcurves

    _reset()
    _import_dts("v22_disc.dts")
    action = next(
        a for a in bpy.data.actions if a.dts_sequence_props.scale_mode != "NONE"
    )
    for fcurve in _iter_fcurves(action):
        if fcurve.data_path.endswith(".scale") and fcurve.array_index == 0:
            for point in fcurve.keyframe_points:
                point.co = (point.co[0], 2.75)
            fcurve.update()
            break

    out = _tmp(".dts")
    assert bpy.ops.io_scene_dts.export_dts(filepath=out, version="23") == {"FINISHED"}
    dst = read_shape_file(out)
    seq = next(s for s in dst.sequences if s.flags & 0x7)
    n, count = seq.num_keyframes, seq.scale_matters.count()
    vals = dst.node_aligned_scales[seq.base_scale : seq.base_scale + count * n]
    assert vals and all(abs(v[0] - 2.75) < 1e-4 for v in vals), vals[:3]


def test_object_state_blobs_are_gone():
    """The JSON that used to shadow the curves must not come back."""
    _reset()
    _import_dts("v23_bioderm_light.dts")
    for action in bpy.data.actions:
        for key in ("dts_object_anim", "dts_decal_anim"):
            assert key not in action.keys(), f"{action.name} still carries {key}"


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
