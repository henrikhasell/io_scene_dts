"""Every DTS feature, built from nothing and exported.

CLAUDE.md: a feature is fully implemented when it can be imported, edited,
*created in a fresh scene*, and exported.  The third is the one an
import-edit-export test cannot check, because the exporter may be leaning on
something only the importer would have written.

So nothing here imports a `.dts`.  Each test constructs Blender data the way a
user would, exports, and reads the feature back out of the file.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO.parent))
sys.path.insert(0, str(REPO / "tests" / "blender"))

import authoring as A  # noqa: E402
from io_scene_dts.dtslib.types import (  # noqa: E402
    MAT_ADDITIVE,
    MAT_IFL_MATERIAL,
    MAT_NEVER_ENV_MAP,
    MAT_NO_MIP_MAP,
    MAT_REFLECTANCE_MAP_ONLY,
    MAT_S_WRAP,
    MAT_SELF_ILLUMINATING,
    MAT_SUBTRACTIVE,
    MAT_T_WRAP,
    MAT_TRANSLUCENT,
    MESH_BILLBOARD,
    MESH_BILLBOARD_Z_AXIS,
    SKIN_MESH,
    SORTED_MESH,
    STANDARD_MESH,
)


# ----------------------------------------------------------------------
# geometry and structure
# ----------------------------------------------------------------------


def test_a_bare_shape_exports():
    """The floor: an armature and one mesh named for its detail level."""
    A.reset()
    arm = A.armature("Widget")
    A.mesh_object("body2", arm, bone="root")

    shape = A.read(A.export_dts())
    assert len(shape.nodes) == 1
    assert A.object_names(shape) == ["body"]
    assert len(shape.details) == 1
    assert shape.details[0].size == 2.0
    mesh = A.live_meshes(shape)[0]
    assert len(mesh.indices) // 3 == 12
    # A DTS vertex is a position *with* its uv and normal, so a cube's eight
    # corners split into more than eight: each is shared by three faces
    # pointing different ways, and the UV unwrap splits some further.  The
    # distinct *positions* are still the eight.
    corners = {tuple(round(c, 4) for c in v) for v in mesh.verts}
    assert len(corners) == 8, sorted(corners)
    assert 24 <= len(mesh.verts) <= 36, len(mesh.verts)


def test_exported_normals_point_outward():
    """DTS is single-sided and back-face culled, with no flag to change it
    (tsMesh.cc:625), so winding is not cosmetic: a mesh wound inward exports
    clean and is invisible in the engine."""
    A.reset()
    arm = A.armature("Solid")
    A.mesh_object("body2", arm, bone="root")

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    outward = 0
    for position, normal in zip(mesh.verts, mesh.norms):
        # every point of a cube centred on the origin faces away from it
        if sum(p * n for p, n in zip(position, normal)) > 0:
            outward += 1
    assert outward == len(mesh.verts), f"{len(mesh.verts) - outward} normals point inward"


def test_triangle_winding_is_clockwise_front():
    """DTS is clockwise-front (tsMesh.cc:626 sets glFrontFace(GL_CW)) while
    Blender is counter-clockwise, so the exporter reverses every triangle.

    Winding is not cosmetic here: with back-face culling hardcoded and no flag
    to disable it, a shape wound the wrong way is invisible from outside and
    solid from within.  Normals are a separate array and stay correct either
    way, so a normals check does not catch this.
    """
    A.reset()
    arm = A.armature("Solid")
    A.mesh_object("body2", arm, bone="root")

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    front = 0
    total = 0
    for i in range(0, len(mesh.indices) - 2, 3):
        a, b, c = (Vector(mesh.verts[mesh.indices[i + k]]) for k in range(3))
        # the counter-clockwise normal of a clockwise-front triangle points
        # away from its visible side, so on an outward cube it aims inward
        ccw = (b - a).cross(c - a)
        centroid = (a + b + c) / 3.0
        total += 1
        if ccw.dot(centroid) < 0:
            front += 1
    assert total == 12, total
    assert front == total, f"{total - front} of {total} triangles are wound inside-out"


def test_a_node_hierarchy():
    A.reset()
    arm = A.armature(
        "Rig",
        bones=(("root", None), ("arm", "root"), ("hand", "arm")),
    )
    A.mesh_object("body2", arm, bone="hand")

    shape = A.read(A.export_dts())
    names = [shape.node_name(i) for i in range(len(shape.nodes))]
    assert set(names) == {"root", "arm", "hand"}
    by_name = {shape.node_name(i): shape.nodes[i] for i in range(len(shape.nodes))}
    assert by_name["arm"].parent_index == names.index("root")
    assert by_name["hand"].parent_index == names.index("arm")
    assert by_name["root"].parent_index == -1


def test_detail_levels():
    """Several LODs of one object, said with the name suffix alone."""
    A.reset()
    arm = A.armature("Tree")
    for size, scale in ((64, 0.5), (32, 0.4), (8, 0.3), (2, 0.2)):
        verts, faces = A.cube_geometry(scale)
        A.mesh_object(f"trunk{size}", arm, bone="root", verts=verts, faces=faces)

    shape = A.read(A.export_dts())
    assert A.object_names(shape) == ["trunk"]
    sizes = sorted(d.size for d in shape.details)
    assert sizes == [2.0, 8.0, 32.0, 64.0]
    obj = shape.objects[0]
    assert obj.num_meshes == 4
    # highest detail first, which is the order the engine walks
    ordered = [d.size for d in shape.details]
    assert ordered == sorted(ordered, reverse=True), ordered
    for slot in range(4):
        assert shape.meshes[obj.start_mesh_index + slot] is not None


def test_a_collision_mesh_is_a_negative_detail():
    """Collision and line-of-sight levels are details with a negative size, so
    the engine never draws them."""
    A.reset()
    arm = A.armature("Crate")
    A.mesh_object("body2", arm, bone="root")
    A.mesh_object("Collision-1", arm, bone="root")

    shape = A.read(A.export_dts())
    sizes = sorted(d.size for d in shape.details)
    assert sizes[0] < 0, sizes
    assert shape.smallest_visible_size >= 0


def test_lod_meshes_share_a_vertex_array():
    """parent_mesh sharing is derived, so it has to appear for a shape that
    was never imported."""
    A.reset()
    arm = A.armature("Post")
    verts, faces = A.cube_geometry(0.5)
    for size in (64, 32, 8):
        A.mesh_object(f"post{size}", arm, bone="root", verts=verts, faces=faces)

    shape = A.read(A.export_dts())
    shared = [m for m in A.live_meshes(shape) if m.parent_mesh >= 0]
    assert shared, "identical detail levels did not share a vertex array"
    for mesh in shared:
        parent = shape.meshes[mesh.parent_mesh]
        assert parent.verts[: len(mesh.verts)] == mesh.verts


# ----------------------------------------------------------------------
# materials
# ----------------------------------------------------------------------


def test_a_material_reaches_the_file():
    A.reset()
    arm = A.armature("Painted")
    mat = A.principled_material("hull")
    A.mesh_object("body2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts())
    assert [m.name for m in shape.materials] == ["hull"]
    # a material created in Blender gets engine-safe wrapping
    assert shape.materials[0].flags & MAT_S_WRAP
    assert shape.materials[0].flags & MAT_T_WRAP


def test_material_flags_are_authorable():
    A.reset()
    arm = A.armature("Flagged")
    mat = A.principled_material("panel")
    mat["dts_self_illuminating"] = True
    mat["dts_never_env_map"] = True
    mat["dts_no_mip_map"] = True
    A.mesh_object("body2", arm, bone="root", material=mat)

    flags = A.read(A.export_dts()).materials[0].flags
    assert flags & MAT_SELF_ILLUMINATING
    assert flags & MAT_NEVER_ENV_MAP
    assert flags & MAT_NO_MIP_MAP


def _blended(mat):
    mat.surface_render_method = "BLENDED"
    return mat


def test_translucent_is_authored_in_the_shader():
    """MAT_TRANSLUCENT is read off the material's render method, not a prop."""
    A.reset()
    arm = A.armature("Glass")
    mat = _blended(A.principled_material("glass"))
    A.mesh_object("body2", arm, bone="root", material=mat)

    flags = A.read(A.export_dts()).materials[0].flags
    assert flags & MAT_TRANSLUCENT, hex(flags)


def _additive_graph(mat, *, subtractive=False):
    """Transparent BSDF + Emission -> Add Shader, which is what the importer
    builds for MAT_ADDITIVE and what the exporter reads back."""
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    add = tree.nodes.new("ShaderNodeAddShader")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    emission = tree.nodes.new("ShaderNodeEmission")
    colour = (0.2, 0.4, 0.9, 1.0)
    if subtractive:
        # the add-on's own convention: EEVEE has no subtractive blend, so the
        # flag is stored as the additive graph with the emission inverted
        invert = tree.nodes.new("ShaderNodeInvert")
        invert.inputs["Color"].default_value = colour
        tree.links.new(invert.outputs["Color"], emission.inputs["Color"])
    else:
        emission.inputs["Color"].default_value = colour
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], out.inputs["Surface"])
    mat.surface_render_method = "BLENDED"
    return mat


def test_additive_is_authored_in_the_shader():
    A.reset()
    arm = A.armature("Glow")
    mat = _additive_graph(A.principled_material("glow"))
    A.mesh_object("body2", arm, bone="root", material=mat)

    flags = A.read(A.export_dts()).materials[0].flags
    assert flags & MAT_ADDITIVE, hex(flags)


def test_subtractive_is_authored_in_the_shader():
    A.reset()
    arm = A.armature("Shadow")
    mat = _additive_graph(A.principled_material("shadow"), subtractive=True)
    A.mesh_object("body2", arm, bone="root", material=mat)

    flags = A.read(A.export_dts()).materials[0].flags
    assert flags & MAT_SUBTRACTIVE, hex(flags)


def test_map_slots_are_authorable():
    """Reflectance (the engine's specular/environment map), bump and detail.

    All three are named by material, and the exporter decides a material
    carries maps at all by testing for the reflectance slot.
    """
    A.reset()
    arm = A.armature("Mapped")
    hull = A.principled_material("hull")
    spec = A.principled_material("hull.spec")
    bump = A.principled_material("hull.bump")
    detail = A.principled_material("hull.detail")
    hull["dts_reflectance_map"] = "hull.spec"
    hull["dts_bump_map"] = "hull.bump"
    hull["dts_detail_map"] = "hull.detail"
    hull["dts_detail_scale"] = 4.0
    hull.dts_material.reflection_amount = 0.75
    for index, mat in enumerate((hull, spec, bump, detail)):
        A.mesh_object(f"part{index}_2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts())
    by_name = {m.name: m for m in shape.materials}
    hull_mat = by_name["hull"]
    names = [m.name for m in shape.materials]
    assert names[hull_mat.reflectance_map] == "hull.spec", hull_mat.reflectance_map
    assert names[hull_mat.bump_map] == "hull.bump"
    assert names[hull_mat.detail_map] == "hull.detail"
    assert abs(hull_mat.detail_scale - 4.0) < 1e-5
    assert abs(hull_mat.reflection_amount - 0.75) < 1e-5


def test_a_material_without_maps_gets_engine_safe_defaults():
    """A self-index reflectance map, never 0xFFFFFFFF, which crashes it."""
    A.reset()
    arm = A.armature("Plain")
    A.mesh_object("body2", arm, bone="root", material=A.principled_material("plain"))

    mat = A.read(A.export_dts()).materials[0]
    assert mat.reflectance_map != 0xFFFFFFFF
    assert mat.reflectance_map == 0


def test_a_reflectance_map_is_authorable():
    """A separate reflectance texture, from nothing.

    The file has no way to name a texture that no mesh uses except as another
    material-list entry, so export has to invent one and flag it
    MAT_REFLECTANCE_MAP_ONLY -- which is what that bit is for.
    """
    A.reset()
    arm = A.armature("Shiny")
    mat = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse"),
        reflectance=A.generated_image("hull_refl", ramp=True),
        packing="SEPARATE",
    )
    A.mesh_object("body2", arm, bone="root", material=mat)

    path = A.export_dts()
    shape = A.read(path)
    assert len(shape.materials) == 2, [m.name for m in shape.materials]
    hull, refl = shape.materials
    assert hull.name == "hull"
    assert hull.reflectance_map == 1, hull.reflectance_map
    assert not hull.flags & MAT_NEVER_ENV_MAP, "a reflectance map needs env-mapping on"
    assert refl.flags & MAT_REFLECTANCE_MAP_ONLY, "the invented entry must say what it is"
    assert refl.reflectance_map == 1, "self-index, never 0xFFFFFFFF"

    beside = Path(path).parent
    assert (beside / "hull.png").is_file()
    assert (beside / f"{refl.name}.png").is_file(), sorted(p.name for p in beside.iterdir())


def test_a_combined_reflectance_is_authorable():
    """The other packing: the mask goes into the diffuse's alpha channel.

    One material, one texture, reflectance pointing at itself -- which is what
    every material in Tribes 2's own shapes looks like.
    """
    A.reset()
    arm = A.armature("Shiny")
    mat = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse", colour=(0.25, 0.5, 0.75)),
        reflectance=A.generated_image("hull_refl", ramp=True),
        packing="COMBINE",
    )
    A.mesh_object("body2", arm, bone="root", material=mat)

    path = A.export_dts()
    shape = A.read(path)
    assert len(shape.materials) == 1, [m.name for m in shape.materials]
    assert shape.materials[0].reflectance_map == 0
    assert not shape.materials[0].flags & MAT_NEVER_ENV_MAP

    written = Path(path).parent / "hull.png"
    assert written.is_file()
    # the mask has to actually be in the alpha channel, not merely promised
    combined = bpy.data.images.load(str(written))
    alpha = list(combined.pixels)[3::4]
    assert min(alpha) < 0.05 and max(alpha) > 0.95, sorted(set(alpha))


def test_reflection_amount_is_authorable():
    """The scalar the engine multiplies the whole reflection by.

    It was an ID property the importer wrote, which meant a material made in a
    fresh scene had no such key and no way to grow one from the UI -- the
    condition CLAUDE.md calls (3), failed quietly.  It is a slider on
    `dts_material` now, so this test sets it the way the panel does.
    """
    A.reset()
    arm = A.armature("Shiny")
    mat = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse"),
        reflectance=A.generated_image("hull_refl", ramp=True),
        packing="SEPARATE",
    )
    mat.dts_material.reflection_amount = 0.25
    A.mesh_object("body2", arm, bone="root", material=mat)

    hull = A.read(A.export_dts()).materials[0]
    assert abs(hull.reflection_amount - 0.25) < 1e-5, hull.reflection_amount
    assert not hull.flags & MAT_NEVER_ENV_MAP


def test_a_reflectance_map_is_authorable_by_operator():
    """The button, not the node editor: (3) means the UI can do it too.

    ``io_scene_dts.add_reflectance`` is the only thing standing between "a
    reflectance map is a node" and "a material that has never had one shows no
    way to get one" -- the same shape of gap billboards had.
    """
    A.reset()
    arm = A.armature("Shiny")
    mat = A.principled_material("hull")
    A.mesh_object("body2", arm, bone="root", material=mat)

    from io_scene_dts.mapping import envmap

    with A.material_context(mat):
        assert bpy.ops.io_scene_dts.add_reflectance() == {"FINISHED"}
    node = envmap.mask_socket(mat).node
    assert node.type == "TEX_IMAGE"
    node.image = A.generated_image("hull_refl", ramp=True)
    mat.dts_material.reflectance_packing = "SEPARATE"

    shape = A.read(A.export_dts())
    assert len(shape.materials) == 2, [m.name for m in shape.materials]
    hull, refl = shape.materials
    assert hull.reflectance_map == 1
    assert not hull.flags & MAT_NEVER_ENV_MAP, "asking for a map asks for env-mapping"
    assert refl.flags & MAT_REFLECTANCE_MAP_ONLY

    with A.material_context(mat):
        assert bpy.ops.io_scene_dts.remove_reflectance() == {"FINISHED"}
    assert envmap.group_node(mat) is None
    assert A.read(A.export_dts()).materials[0].flags & MAT_NEVER_ENV_MAP, (
        "with the map gone the material must go back to never env-mapping"
    )


def test_the_reflection_previews_over_the_principled():
    """The graph has to be the engine's equation, not merely present.

    ``env*k + lit*(1-k)`` is a Mix Shader whose second input is an unlit
    Emission -- the reflection displaces the diffuse and no light touches it.
    A material wired any other way would export the same bytes and show the
    wrong thing, which is the failure this whole change is about.
    """
    A.reset()
    mat = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse"),
        reflectance=A.generated_image("hull_refl", ramp=True),
    )

    nt = mat.node_tree
    output = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    surface = output.inputs["Surface"].links[0].from_node
    assert surface.type == "MIX_SHADER", surface.type
    assert surface.inputs[1].links[0].from_node.type == "BSDF_PRINCIPLED"
    assert surface.inputs[2].links[0].from_node.type == "EMISSION"
    assert surface.inputs["Fac"].links[0].from_socket.name == "Factor"
    # and Metallic is left alone: two paths to the same fact is the bug
    assert not next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED").inputs[
        "Metallic"
    ].is_linked


def test_a_reflectance_left_on_metallic_still_exports():
    """A .blend from before the preview existed keeps working.

    Nothing writes Metallic any more, and migration deliberately does not
    re-wire node trees on load, so the export path has to keep reading it.
    """
    A.reset()
    arm = A.armature("Legacy")
    mat = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse"),
        reflectance=A.generated_image("hull_refl", ramp=True),
        packing="SEPARATE",
        on_metallic=True,
    )
    A.mesh_object("body2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts())
    assert len(shape.materials) == 2, [m.name for m in shape.materials]
    assert shape.materials[0].reflectance_map == 1
    assert not shape.materials[0].flags & MAT_NEVER_ENV_MAP


def _shiny_scene(*, packing=None, count=1):
    """A fresh shape whose materials each show a diffuse and a reflectance."""
    A.reset()
    arm = A.armature("Shiny")
    for index in range(count):
        name = f"hull{index}"
        mat = A.image_material(
            name,
            diffuse=A.generated_image(f"{name}_diffuse", colour=(0.25, 0.5, 0.75)),
            reflectance=A.generated_image(f"{name}_refl", ramp=True),
            packing=packing,
        )
        A.mesh_object(f"part{index}_2", arm, bone="root", material=mat)
    return arm


def test_the_export_box_combines_materials_that_do_not_object():
    """Ticked -- the default -- and a material left alone writes one texture.

    This is the fresh-scene case the export checkbox exists for: nothing was
    imported, no material carries a packing of its own, and the box decides.
    """
    _shiny_scene(count=2)

    path = A.export_dts(A.tmp_in_own_dir(), combine_reflectance=True)
    shape = A.read(path)
    assert [m.name for m in shape.materials] == ["hull0", "hull1"], (
        "combined, so no material list entry is invented for the mask"
    )
    assert [m.reflectance_map for m in shape.materials] == [0, 1], "each points at itself"

    beside = Path(path).parent
    assert sorted(p.name for p in beside.glob("*.png")) == ["hull0.png", "hull1.png"]
    for name in ("hull0.png", "hull1.png"):
        alpha = list(bpy.data.images.load(str(beside / name)).pixels)[3::4]
        assert min(alpha) < 0.05 and max(alpha) > 0.95, (name, sorted(set(alpha)))


def test_unticking_the_export_box_writes_two_textures():
    """Unticked, the same scene writes the maps as separate files.

    The mask has nowhere to live in the .dts except as a material-list entry of
    its own, so one is invented per image and flagged for what it is.
    """
    _shiny_scene(count=2)

    path = A.export_dts(A.tmp_in_own_dir(), combine_reflectance=False)
    shape = A.read(path)
    names = [m.name for m in shape.materials]
    assert names[:2] == ["hull0", "hull1"], names
    assert len(names) == 4, names
    for i in (0, 1):
        target = shape.materials[shape.materials[i].reflectance_map]
        assert target.name != names[i], f"{names[i]} must not point at itself"
        assert target.flags & MAT_REFLECTANCE_MAP_ONLY, target.name

    beside = Path(path).parent
    written = sorted(p.stem for p in beside.glob("*.png"))
    assert written == sorted(names), written
    # and the diffuse the engine draws must not have picked up a mask on the way
    for name in ("hull0", "hull1"):
        alpha = list(bpy.data.images.load(str(beside / f"{name}.png")).pixels)[3::4]
        assert set(alpha) == {1.0}, (name, sorted(set(alpha)))


def test_a_material_overrules_the_export_box_in_either_direction():
    """The per-material setting is an exception to the shape-wide one.

    Both directions, in one shape, so neither can pass by accident of the box:
    the box is off and the COMBINE material still writes one texture, while the
    SEPARATE material would have been combined and is not.
    """
    A.reset()
    arm = A.armature("Mixed")
    for name, packing in (("stubborn", "COMBINE"), ("plain", None)):
        mat = A.image_material(
            name,
            diffuse=A.generated_image(f"{name}_diffuse"),
            reflectance=A.generated_image(f"{name}_refl", ramp=True),
            packing=packing,
        )
        A.mesh_object(f"{name}2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts(combine_reflectance=False))
    by_name = {m.name: m for m in shape.materials}
    assert by_name["stubborn"].reflectance_map == list(by_name).index("stubborn"), (
        "COMBINE outranks an unticked box"
    )
    assert shape.materials[by_name["plain"].reflectance_map].name != "plain"

    # ...and now the same trick the other way round
    A.reset()
    arm = A.armature("Mixed")
    for name, packing in (("stubborn", "SEPARATE"), ("plain", None)):
        mat = A.image_material(
            name,
            diffuse=A.generated_image(f"{name}_diffuse"),
            reflectance=A.generated_image(f"{name}_refl", ramp=True),
            packing=packing,
        )
        A.mesh_object(f"{name}2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts(combine_reflectance=True))
    by_name = {m.name: m for m in shape.materials}
    assert shape.materials[by_name["stubborn"].reflectance_map].name != "stubborn", (
        "SEPARATE outranks a ticked box"
    )
    assert by_name["plain"].reflectance_map == list(by_name).index("plain")


def test_a_reflectance_material_is_env_mapped():
    """Showing a reflectance map is how you ask for env-mapping.

    A fresh material gets MAT_NEVER_ENV_MAP so it cannot crash the engine on a
    null reflectance map.  One that *has* a reflectance map must not, or the
    thing the user just authored is dead in the game.
    """
    A.reset()
    arm = A.armature("Pair")
    plain = A.principled_material("plain")
    shiny = A.image_material(
        "shiny",
        diffuse=A.generated_image("shiny_diffuse"),
        reflectance=A.generated_image("shiny_refl", ramp=True),
        packing="COMBINE",
    )
    # ...and the checkbox does not get to contradict the map either
    ticked = A.image_material(
        "ticked",
        diffuse=A.generated_image("ticked_diffuse"),
        reflectance=A.generated_image("ticked_refl", ramp=True),
        packing="COMBINE",
    )
    ticked["dts_never_env_map"] = True
    for index, mat in enumerate((plain, shiny, ticked)):
        A.mesh_object(f"part{index}_2", arm, bone="root", material=mat)

    by_name = {m.name: m for m in A.read(A.export_dts()).materials}
    assert by_name["plain"].flags & MAT_NEVER_ENV_MAP, "no map: env-mapping stays off"
    assert not by_name["shiny"].flags & MAT_NEVER_ENV_MAP, "a map: env-mapping goes on"
    assert not by_name["ticked"].flags & MAT_NEVER_ENV_MAP, (
        "a reflectance map the engine is told never to read is not a feature"
    )


def test_a_reflectance_map_does_not_shift_primitive_material_indices():
    """The invented entry goes on the end, where nothing points.

    Every mat_index in the shape was decided before the material list existed,
    so appending is safe -- but only if it really is appending.
    """
    A.reset()
    arm = A.armature("Three")
    first = A.principled_material("first")
    middle = A.image_material(
        "middle",
        diffuse=A.generated_image("middle_diffuse"),
        reflectance=A.generated_image("middle_refl", ramp=True),
        packing="SEPARATE",
    )
    last = A.principled_material("last")
    for index, mat in enumerate((first, middle, last)):
        A.mesh_object(f"part{index}_2", arm, bone="root", material=mat)

    shape = A.read(A.export_dts())
    names = [m.name for m in shape.materials]
    assert names[:3] == ["first", "middle", "last"], names
    assert len(names) == 4 and names[3].startswith("middle"), names

    # each object's one mesh has one primitive, naming the material it was given
    used = []
    for obj in shape.objects:
        mesh = shape.meshes[obj.start_mesh_index]
        for prim in mesh.primitives:
            used.append(names[prim.mat_index & 0x0FFFFFFF])
    assert sorted(used) == ["first", "last", "middle"], used


# ----------------------------------------------------------------------
# mesh kinds
# ----------------------------------------------------------------------


def test_a_billboard_is_authorable():
    A.reset()
    arm = A.armature("Flare")
    obj = A.mesh_object("flare2", arm, bone="root")
    obj.dts_mesh.billboard = True

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.flags & MESH_BILLBOARD, hex(mesh.flags)


def test_a_z_axis_billboard_is_authorable():
    """No Tribes 2 shape sets this bit, so nothing can be copied from one."""
    A.reset()
    arm = A.armature("Trunk")
    obj = A.mesh_object("trunk2", arm, bone="root")
    obj.dts_mesh.billboard = True
    obj.dts_mesh.billboard_z = True

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.flags & MESH_BILLBOARD
    assert mesh.flags & MESH_BILLBOARD_Z_AXIS, hex(mesh.flags)


def test_an_upright_billboard_card_exports_flat_along_y():
    """The flag alone is not the feature: the card also has to be modelled right.

    Billboarding throws the mesh's node rotation away and keeps only position
    and scale, so the engine draws the mesh's *own* vertices with an identity
    rotation -- local x is screen-right and local z screen-up.  A card built
    lying down therefore exports with perfect flags and renders in-game as a
    smear on the ground, which is exactly what the 02_billboards example did
    until this was measured against stock art.

    Every billboard mesh in the shipped Tribes 2 shapes is flat along local y
    (all four in grenade_flare.dts span x and z at y == 0).  This asserts that
    ``upright_quad_geometry`` still comes out the same way, since the axis
    mapping between a Blender mesh and its DTS node is what could drift.
    """
    A.reset()
    arm = A.armature("Card")
    verts, faces = A.upright_quad_geometry()
    obj = A.mesh_object("card2", arm, bone="root", verts=verts, faces=faces)
    obj.dts_mesh.billboard = True

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    spans = [
        max(v[axis] for v in mesh.verts) - min(v[axis] for v in mesh.verts)
        for axis in range(3)
    ]
    assert spans[1] < 1e-4, f"card is not flat along local y: {spans}"
    assert spans[0] > 0.5 and spans[2] > 0.5, f"card has no width or height: {spans}"


def test_a_sorted_mesh_is_authorable():
    A.reset()
    arm = A.armature("Foliage")
    # more than leaf_size triangles, or the builder correctly stops at one leaf
    verts, faces = A.cards_geometry(16)
    obj = A.mesh_object("leaves2", arm, bone="root", verts=verts, faces=faces)
    obj.dts_mesh.sorted_mode = "BSP"

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SORTED_MESH
    assert mesh.sorted_data is not None
    assert len(mesh.sorted_data.clusters) > 1, "no tree was built"
    assert mesh.sorted_data.num_verts == [len(mesh.verts)]


def _translucent_material(name="glass"):
    """Blended in the shader, which is where export reads translucency from."""
    mat = A.principled_material(name)
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    return mat


def test_a_translucent_mesh_is_promoted_to_a_sorted_one():
    """Translucency is what sorted meshes are for, so it asks for one.

    Nothing DTS-specific is set here: a material is made blended in the node
    editor, and the mesh comes out of the file with a cluster tree.
    """
    A.reset()
    arm = A.armature("Foliage")
    verts, faces = A.cards_geometry(16)
    A.mesh_object(
        "leaves2", arm, bone="root", verts=verts, faces=faces,
        material=_translucent_material(),
    )

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SORTED_MESH, mesh.mesh_type
    assert mesh.sorted_data is not None
    assert len(mesh.sorted_data.clusters) > 1, "promoted, but no tree was built"


def test_an_opaque_mesh_is_left_standard():
    """The other half of the rule, and the reason it is a rule at all."""
    A.reset()
    arm = A.armature("Foliage")
    verts, faces = A.cards_geometry(16)
    A.mesh_object(
        "leaves2", arm, bone="root", verts=verts, faces=faces,
        material=A.principled_material("solid"),
    )

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == STANDARD_MESH, mesh.mesh_type
    assert mesh.sorted_data is None


def test_an_explicit_sorted_mode_outranks_the_promotion():
    """FLAT means "sorted, but claim no ordering".  A promotion to BSP would
    partition geometry the user said not to partition."""
    A.reset()
    arm = A.armature("Foliage")
    verts, faces = A.cards_geometry(16)
    obj = A.mesh_object(
        "leaves2", arm, bone="root", verts=verts, faces=faces,
        material=_translucent_material(),
    )
    obj.dts_mesh.sorted_mode = "FLAT"

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SORTED_MESH
    assert len(mesh.sorted_data.clusters) == 1, "FLAT is one cluster, not a tree"


def test_a_translucent_skin_stays_a_skin():
    """mesh_type is one field, so the promotion has to lose -- silently, since
    nobody asked for it."""
    A.reset()
    arm = A.armature("Creature", bones=(("root", None), ("upper", "root")))
    obj = A.mesh_object("skin2", arm, material=_translucent_material())
    obj.parent_type = "OBJECT"
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    lower = obj.vertex_groups.new(name="root")
    upper = obj.vertex_groups.new(name="upper")
    for vertex in obj.data.vertices:
        (upper if vertex.co.z > 0 else lower).add([vertex.index], 1.0, "REPLACE")

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SKIN_MESH, mesh.mesh_type


def test_a_sorted_mesh_walks_correctly():
    """Built from scratch, the tree still has to satisfy the engine's walk."""
    sys.path.insert(0, str(REPO / "tests"))
    from sorted_walk import camera_positions, triangles_of, walk

    A.reset()
    arm = A.armature("Foliage")
    verts, faces = A.cards_geometry(16)
    obj = A.mesh_object("leaves2", arm, bone="root", verts=verts, faces=faces)
    obj.dts_mesh.sorted_mode = "BSP"

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    every = {
        tuple(sorted(t))
        for t in triangles_of(mesh.primitives, mesh.indices, range(len(mesh.primitives)))
    }
    for camera in camera_positions(mesh.verts, 32):
        drawn = walk(mesh.sorted_data, camera)
        assert len(set(drawn)) == len(drawn)
        got = {tuple(sorted(t)) for t in triangles_of(mesh.primitives, mesh.indices, drawn)}
        assert got == every


def test_flat_sorted_mode_is_authorable():
    A.reset()
    arm = A.armature("Pane")
    verts, faces = A.quad_geometry()
    obj = A.mesh_object("pane2", arm, bone="root", verts=verts, faces=faces)
    obj.dts_mesh.sorted_mode = "FLAT"

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SORTED_MESH
    assert len(mesh.sorted_data.clusters) == 1
    start, end, *_, front, back = mesh.sorted_data.clusters[0]
    assert (start, end) == (0, len(mesh.primitives))
    assert front == back == -1


def test_a_skinned_mesh_is_authorable():
    """Vertex groups named after bones, plus an armature modifier -- which is
    how anyone rigs in Blender, with nothing DTS-specific about it."""
    A.reset()
    arm = A.armature("Creature", bones=(("root", None), ("upper", "root")))
    obj = A.mesh_object("skin2", arm)
    obj.parent_type = "OBJECT"
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    lower = obj.vertex_groups.new(name="root")
    upper = obj.vertex_groups.new(name="upper")
    for vertex in obj.data.vertices:
        (upper if vertex.co.z > 0 else lower).add([vertex.index], 1.0, "REPLACE")

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.mesh_type == SKIN_MESH
    assert mesh.vertex_index and mesh.bone_index and mesh.weight
    assert len(mesh.node_index) == 2, mesh.node_index
    assert len(mesh.initial_transforms) == 2
    assert all(0.0 < w <= 1.0 for w in mesh.weight)


def test_vertex_animation_is_authorable():
    """frame_NNN shape keys become the DTS frame blocks."""
    A.reset()
    arm = A.armature("Disc")
    obj = A.mesh_object("disc2", arm, bone="root")
    obj.shape_key_add(name="Basis")
    for frame in range(1, 4):
        key = obj.shape_key_add(name=f"frame_{frame:03d}", from_mix=False)
        for point in key.data:
            point.co = Vector((point.co.x, point.co.y, point.co.z + frame * 0.25))

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.num_frames == 4, mesh.num_frames
    assert len(mesh.verts) == mesh.num_frames * mesh.verts_per_frame


def test_vertex_animation_previews_in_a_fresh_scene():
    """The frame track drives the frame_NNN keys of a shape nobody imported,
    so scrubbing shows the animation rather than one pose for every keyframe."""
    from io_scene_dts.mapping.framepreview import wire_frame_drivers
    from io_scene_dts.mapping.objectstate import ensure_props, path_for

    A.reset()
    arm = A.armature("Waver", bones=(("root", None), ("mount", "root")))
    obj = A.mesh_object("cloth2", arm, bone="mount")
    obj.shape_key_add(name="Basis")
    for frame in range(1, 4):
        key = obj.shape_key_add(name=f"frame_{frame:03d}", from_mix=False)
        key.value = 0.0
        for point in key.data:
            point.co = Vector((point.co.x, point.co.y, point.co.z + frame))

    action = A.action_for(arm, "wave", frames=4)
    action["dts_sequence"] = True
    bag = A.channelbag(action, arm)
    ensure_props(arm, "frame", ["cloth"])
    curve = bag.fcurves.new(data_path=path_for("frame", "cloth"), index=0)
    curve.keyframe_points.add(4)
    for index, value in enumerate((0, 1, 2, 3)):
        point = curve.keyframe_points[index]
        point.co = (index + 1, float(value))
        point.interpolation = "CONSTANT"
    curve.update()

    assert wire_frame_drivers(arm, {"cloth"}) == 1

    rest = [v.co.z for v in obj.data.vertices]
    for scene_frame, expected in ((1, 0), (2, 1), (3, 2), (4, 3)):
        bpy.context.scene.frame_set(scene_frame)
        deps = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(deps)
        shown = evaluated.to_mesh()
        try:
            lift = [v.co.z - z for v, z in zip(shown.vertices, rest)]
        finally:
            evaluated.to_mesh_clear()
        assert all(abs(z - expected) < 1e-5 for z in lift), (
            f"scene frame {scene_frame}: expected frame {expected} "
            f"(lift {expected}), got {lift[:4]}"
        )


def test_material_frames_are_authorable():
    """Extra UV blocks as FLOAT2 point attributes."""
    from io_scene_dts.mapping import matframes

    A.reset()
    arm = A.armature("Switch")
    obj = A.mesh_object("panel2", arm, bone="root")
    for frame in range(1, 4):
        attr = obj.data.attributes.new(
            name=matframes.attr_name(frame), type="FLOAT2", domain="POINT"
        )
        for index, datum in enumerate(attr.data):
            datum.vector = (frame * 0.1, index * 0.05)

    mesh = A.live_meshes(A.read(A.export_dts()))[0]
    assert mesh.num_mat_frames == 4, mesh.num_mat_frames
    assert len(mesh.tverts) == mesh.num_mat_frames * len(mesh.verts)


# ----------------------------------------------------------------------
# animation
# ----------------------------------------------------------------------


def test_a_sequence_is_authorable():
    A.reset()
    arm = A.armature("Door", bones=(("root", None), ("panel", "root")))
    A.mesh_object("body2", arm, bone="panel")
    action = A.action_for(arm, "open", frames=6)
    action["dts_sequence"] = True
    action["dts_duration"] = 1.5
    action["dts_priority"] = 3
    action["dts_cyclic"] = True

    shape = A.read(A.export_dts())
    assert len(shape.sequences) == 1
    seq = shape.sequences[0]
    assert shape.name(seq.name_index) == "open"
    assert seq.num_keyframes == 6, seq.num_keyframes
    assert abs(seq.duration - 1.5) < 1e-5
    assert seq.priority == 3
    assert seq.flags & 0x10  # SEQ_CYCLIC
    assert seq.rotation_matters.count() >= 1
    assert len(shape.node_rotations) >= 6


def test_a_translation_only_channel_marks_only_translation():
    """The matters sets are inferred from the channels that exist, so a
    location-only bone must not claim rotation."""
    A.reset()
    arm = A.armature("Slider", bones=(("root", None), ("lift", "root")))
    A.mesh_object("body2", arm, bone="lift")
    action = bpy.data.actions.new("slide")
    action.use_fake_user = True
    action["dts_sequence"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["lift"]
    for frame in range(1, 5):
        bone.location = (0.0, 0.0, frame * 0.1)
        bone.keyframe_insert("location", frame=frame)

    seq = A.read(A.export_dts()).sequences[0]
    assert seq.translation_matters.count() == 1
    assert seq.rotation_matters.count() == 0, "a location channel claimed rotation"


def test_triggers_are_authorable():
    A.reset()
    arm = A.armature("Walker", bones=(("root", None), ("leg", "root")))
    A.mesh_object("body2", arm, bone="leg")
    action = A.action_for(arm, "run", frames=8)
    action["dts_sequence"] = True
    for state, pos, on in ((1, 0.25, True), (2, 0.75, False)):
        item = action.dts_sequence_props.triggers.add()
        item.state, item.pos, item.on = state, pos, on

    shape = A.read(A.export_dts())
    assert len(shape.triggers) == 2
    assert shape.triggers[0].state & 0x3FFFFFFF == 1 << 0
    assert shape.triggers[0].state & (1 << 31), "on bit lost"
    assert not shape.triggers[1].state & (1 << 31)
    assert abs(shape.triggers[1].pos - 0.75) < 1e-5


def test_ground_frames_are_authorable():
    A.reset()
    arm = A.armature("Runner", bones=(("root", None), ("hip", "root")))
    A.mesh_object("body2", arm, bone="hip")
    action = A.action_for(arm, "sprint", frames=4)
    action["dts_sequence"] = True
    for step in range(3):
        item = action.dts_sequence_props.ground.add()
        item.translation = (0.0, step * 0.5, 0.0)
        item.rotation = (0, 0, 0, 32767)

    shape = A.read(A.export_dts())
    seq = shape.sequences[0]
    assert seq.num_ground_frames == 3
    assert len(shape.ground_translations) == 3
    assert abs(shape.ground_translations[1][1] - 0.5) < 1e-5


def test_object_visibility_is_authorable():
    """A vis track is a keyframed property on the armature, which is also
    where a user would put it -- one animated ID, one strip."""
    from io_scene_dts.mapping.objectstate import ensure_props, path_for

    A.reset()
    arm = A.armature("Blinker", bones=(("root", None), ("mount", "root")))
    A.mesh_object("lamp2", arm, bone="mount")
    action = A.action_for(arm, "blink", frames=4)
    action["dts_sequence"] = True

    ensure_props(arm, "vis", ["lamp"])
    bag = A.channelbag(action, arm)
    curve = bag.fcurves.new(data_path=path_for("vis", "lamp"), index=0)
    curve.keyframe_points.add(4)
    for index, value in enumerate((1.0, 0.5, 0.0, 1.0)):
        curve.keyframe_points[index].co = (index + 1, value)
    curve.update()

    shape = A.read(A.export_dts())
    seq = shape.sequences[0]
    assert seq.vis_matters.count() == 1, "the vis track did not reach the file"
    ordinal = seq.vis_matters.ordinal_of(0)
    states = [
        shape.object_states[seq.base_object_state + ordinal * seq.num_keyframes + kf].vis
        for kf in range(seq.num_keyframes)
    ]
    assert [round(v, 3) for v in states] == [1.0, 0.5, 0.0, 1.0], states


def test_node_scale_animation_is_authorable():
    A.reset()
    arm = A.armature("Pulse", bones=(("root", None), ("core", "root")))
    A.mesh_object("body2", arm, bone="core")
    action = A.action_for(arm, "pulse", frames=4)
    action["dts_sequence"] = True
    action.dts_sequence_props.scale_mode = "ALIGNED"
    bone = arm.pose.bones["core"]
    for frame in range(1, 5):
        bone.scale = (1.0 + frame * 0.1, 1.0, 1.0 - frame * 0.05)
        bone.keyframe_insert("scale", frame=frame)

    shape = A.read(A.export_dts())
    seq = shape.sequences[0]
    assert seq.animates_aligned_scale(), hex(seq.flags)
    assert seq.scale_matters.count() == 1
    assert len(shape.node_aligned_scales) == seq.num_keyframes
    assert abs(shape.node_aligned_scales[0][0] - 1.1) < 1e-4


def test_a_dsq_is_authorable():
    """A .dsq carries sequences alone, for a skeleton the shape already has."""
    A.reset()
    arm = A.armature("Player", bones=(("root", None), ("spine", "root")))
    A.mesh_object("body2", arm, bone="spine")
    action = A.action_for(arm, "wave", frames=5)
    action["dts_sequence"] = True
    action["dts_duration"] = 0.8

    dsq = A.read_dsq_file(A.export_dsq())
    assert dsq.sequence_names == ["wave"], dsq.sequence_names
    assert len(dsq.sequences) == 1
    assert dsq.sequences[0].num_keyframes == 5
    assert abs(dsq.sequences[0].duration - 0.8) < 1e-5
    assert dsq.node_rotations, "no rotation keys were written"


def test_a_dsq_carries_triggers_and_ground():
    A.reset()
    arm = A.armature("Player", bones=(("root", None), ("spine", "root")))
    A.mesh_object("body2", arm, bone="spine")
    action = A.action_for(arm, "stride", frames=4)
    action["dts_sequence"] = True
    trigger = action.dts_sequence_props.triggers.add()
    trigger.state, trigger.pos, trigger.on = 5, 0.5, True
    ground = action.dts_sequence_props.ground.add()
    ground.translation = (0.0, 1.0, 0.0)
    ground.rotation = (0, 0, 0, 32767)

    dsq = A.read_dsq_file(A.export_dsq())
    assert len(dsq.triggers) == 1
    assert dsq.triggers[0].state & 0x3FFFFFFF == 1 << 4
    assert len(dsq.ground_translations) == 1


# ----------------------------------------------------------------------
# shape-level tables
# ----------------------------------------------------------------------


def test_an_ifl_material_is_authorable():
    """A flipbook from nothing: images, holds, and the .ifl beside the .dts.

    Nothing DTS-specific is typed here beyond ticking the box -- the entry in
    the shape's IFL table is derived from the material, because that is the
    only thing that can say a material flips.
    """
    A.reset()
    arm = A.armature("Flame")
    mat = A.principled_material("flame")
    mat.dts_material.is_ifl = True
    for index, hold in enumerate((3, 1, 1, 12)):
        frame = mat.dts_material.ifl_frames.add()
        frame.image = A.generated_image(f"flame{index}")
        frame.duration = hold
    A.mesh_object("body2", arm, bone="root", material=mat)

    import tempfile

    path = A.export_dts(str(Path(tempfile.mkdtemp()) / "flame.dts"))
    shape = A.read(path)
    assert len(shape.ifl_materials) == 1
    raw = shape.ifl_materials[0].raw
    assert shape.name(raw[0]) == "flame.ifl"
    assert raw[1] == 0, "the entry must name the material's own slot"
    assert raw[4] == 4, "num_frames is the length of the list"
    # engine scratch, not data: written as zeros rather than invented
    assert raw[2] == raw[3] == 0
    assert shape.materials[0].flags & MAT_IFL_MATERIAL, "the flag is derived from the box"

    beside = Path(path).parent
    written = (beside / "flame.ifl").read_text()
    assert written.splitlines() == [
        "flame0.png 3", "flame1.png 1", "flame2.png 1", "flame3.png 12"
    ], written
    assert (beside / "flame0.png").is_file(), sorted(p.name for p in beside.iterdir())


def test_export_textures_gates_images_but_not_the_ifl():
    """The checkbox is about art.  A .ifl is the shape's own animation data and
    the .dts names it by filename, so suppressing it would leave the material
    pointing at a flipbook that does not exist."""
    import tempfile

    A.reset()
    arm = A.armature("Flame")
    mat = A.principled_material("flame")
    mat.dts_material.is_ifl = True
    for index in range(3):
        frame = mat.dts_material.ifl_frames.add()
        frame.image = A.generated_image(f"flame{index}")
    A.mesh_object("body2", arm, bone="root", material=mat)

    on = Path(A.export_dts(str(Path(tempfile.mkdtemp()) / "flame.dts"))).parent
    assert sorted(p.suffix for p in on.iterdir()) == [".dts", ".ifl", ".png", ".png", ".png"]

    off = Path(tempfile.mkdtemp()) / "flame.dts"
    A.export_dts(str(off), export_textures=False)
    assert sorted(p.suffix for p in off.parent.iterdir()) == [".dts", ".ifl"]
    # ...and the entry still names it, so the file is not left inconsistent
    shape = A.read(off)
    assert shape.name(shape.ifl_materials[0].raw[0]) == "flame.ifl"


def test_textures_are_scaled_to_a_power_of_two_on_export():
    """Torque's texture loader assumes power-of-two dimensions.

    A 100x60 PNG looks right in Blender and renders white or garbled in-game,
    and nothing in the .dts records a size, so there is no later point at which
    this could be caught.  Nearest in log space: 100 -> 128 and 60 -> 64 (both
    up), while 80 would go down to 64.

    The scaling happens on the way out only.  ``Image.scale`` resamples in
    place, so doing it to the scene's image would resize the texture the user
    paints on, and a second export would resample the resample.
    """
    import tempfile

    A.reset()
    arm = A.armature("Odd")
    image = bpy.data.images.new("odd", width=100, height=60, alpha=True)
    image.pixels = [0.5] * (100 * 60 * 4)
    image.update()
    image.pack()
    A.mesh_object("body2", arm, bone="root",
                  material=A.image_material("odd", diffuse=image))

    out = Path(tempfile.mkdtemp()) / "odd.dts"
    A.export_dts(str(out))

    # the file on disk is power of two...
    written = bpy.data.images.load(str(out.parent / "odd.png"))
    assert tuple(written.size) == (128, 64), tuple(written.size)
    # ...the scene's own image is exactly as the user left it...
    assert tuple(image.size) == (100, 60), "export resampled the scene's image"
    # ...and the scratch copy did not stay behind in the .blend
    assert sorted(i.name for i in bpy.data.images if i.name.startswith("odd")) == [
        "odd", "odd.png",
    ]

    # unticked, the file keeps the authored size
    plain = Path(tempfile.mkdtemp()) / "odd.dts"
    A.export_dts(str(plain), scale_textures_pot=False)
    kept = bpy.data.images.load(str(plain.parent / "odd.png"))
    assert tuple(kept.size) == (100, 60), tuple(kept.size)


def test_the_export_size_rule():
    """The two checkboxes as one table, since between them they decide every
    dimension that reaches disk."""
    from io_scene_dts.mapping.texture_io import export_size

    # power of two only: rounding in log space, no cap
    assert export_size((100, 60), True, None) == (128, 64)
    assert export_size((1024, 256), True, None) == (1024, 256)

    # the cap divides both sides, so the aspect ratio survives -- clamping
    # only the side that is too big would turn 1024x256 into 512x256
    assert export_size((1024, 256), True, 512) == (512, 128)
    assert export_size((2048, 2048), True, 512) == (512, 512)

    # the cap without the rounding: fit exactly, whatever shape that leaves
    assert export_size((600, 600), False, 512) == (512, 512)
    assert export_size((2000, 1000), False, 512) == (512, 256)
    assert export_size((100, 60), False, 512) == (100, 60)

    # rounding after the fit cannot climb back over the cap: 1000 rounds to
    # 1024, half of that is 512, and 512 is already a power of two.  The
    # awkward case is a side that fits to just under a boundary -- 700x400
    # rounds to 512x512 and stays there rather than becoming 512x1024
    assert export_size((1000, 600), True, 512) == (512, 256)
    assert export_size((700, 400), True, 512) == (512, 512)
    for size in [(w, h) for w in (300, 700, 1000, 1900, 4096) for h in (7, 400, 1000)]:
        assert max(export_size(size, True, 512)) <= 512, size
        assert max(export_size(size, False, 512)) <= 512, size

    # nothing to do is nothing to do, both boxes ticked
    assert export_size((256, 128), True, 512) == (256, 128)


def test_a_texture_larger_than_512_is_scaled_down_on_export():
    """Default on: an oversized texture still renders, but the engine uploads
    and mipmaps all of it and the driver resamples what the card cannot hold,
    so the size is paid for twice and then thrown away.

    Already a power of two, so the cap is the only thing acting -- and it acts
    on both sides, keeping 4:1 as 4:1.
    """
    import tempfile

    A.reset()
    arm = A.armature("Huge")
    image = bpy.data.images.new("huge", width=1024, height=256, alpha=True)
    image.pixels.foreach_set([0.5] * (1024 * 256 * 4))
    image.update()
    image.pack()
    A.mesh_object("body2", arm, bone="root",
                  material=A.image_material("huge", diffuse=image))

    out = Path(tempfile.mkdtemp()) / "huge.dts"
    A.export_dts(str(out))
    written = bpy.data.images.load(str(out.parent / "huge.png"))
    assert tuple(written.size) == (512, 128), tuple(written.size)
    # the scene's own image is exactly as the user left it
    assert tuple(image.size) == (1024, 256), "export resampled the scene's image"

    # unticked, the file keeps the authored size
    big = Path(tempfile.mkdtemp()) / "huge.dts"
    A.export_dts(str(big), limit_texture_size=False)
    kept = bpy.data.images.load(str(big.parent / "huge.png"))
    assert tuple(kept.size) == (1024, 256), tuple(kept.size)


def test_a_power_of_two_texture_is_left_alone():
    """No copy, no resample and no warning for art that is already correct."""
    import tempfile

    from io_scene_dts.mapping.texture_io import nearest_power_of_two

    assert [nearest_power_of_two(n) for n in (1, 60, 80, 100, 128, 129)] == [
        1, 64, 64, 128, 128, 128,
    ]

    A.reset()
    arm = A.armature("Even")
    image = bpy.data.images.new("even", width=64, height=32, alpha=True)
    image.pixels = [0.5] * (64 * 32 * 4)
    image.update()
    image.pack()
    A.mesh_object("body2", arm, bone="root",
                  material=A.image_material("even", diffuse=image))

    out = Path(tempfile.mkdtemp()) / "even.dts"
    A.export_dts(str(out))
    written = bpy.data.images.load(str(out.parent / "even.png"))
    assert tuple(written.size) == (64, 32)
    assert sorted(i.name for i in bpy.data.images if i.name.startswith("even")) == [
        "even", "even.png",
    ]


def test_a_texture_loaded_from_disk_is_copied_beside_the_dts():
    """Open a .png, put it on a material, export: the .png comes along.

    Authorable in the sense CLAUDE.md means -- no import anywhere, just a user
    who has a texture somewhere on disk and points a material at it.  The .dts
    names it by bare filename and the engine looks beside the shape, so an
    export that referenced it where it lay would name a file that is not there.
    """
    import tempfile

    A.reset()
    # a .png somewhere else on disk, which is all a source texture ever is
    source_dir = Path(tempfile.mkdtemp())
    scratch = A.generated_image("hull", ramp=True)
    scratch.file_format = "PNG"
    scratch.save(filepath=str(source_dir / "hull.png"))
    loaded = bpy.data.images.load(str(source_dir / "hull.png"))
    assert loaded.filepath, "the fixture has to be file-backed or it proves nothing"

    arm = A.armature("Hull")
    mat = A.image_material("hull", diffuse=loaded)
    A.mesh_object("body2", arm, bone="root", material=mat)

    beside = Path(tempfile.mkdtemp())
    path = A.export_dts(str(beside / "hull.dts"))
    assert (beside / "hull.png").is_file(), sorted(p.name for p in beside.iterdir())
    assert A.read(path).materials[0].name == "hull"
    # and the datablock still points where it came from: export copies, it does
    # not re-home the scene's images
    assert Path(bpy.path.abspath(loaded.filepath)).parent == source_dir


def test_an_ifl_material_without_frames_still_gets_its_entry():
    """A shape whose .ifl could not be found still flips in the engine; losing
    its table entry because the sidecar was missing would be the worse answer."""
    A.reset()
    arm = A.armature("Flame")
    mat = A.principled_material("flame")
    mat.dts_material.is_ifl = True
    A.mesh_object("body2", arm, bone="root", material=mat)

    import tempfile

    path = A.export_dts(str(Path(tempfile.mkdtemp()) / "flame.dts"))
    shape = A.read(path)
    assert len(shape.ifl_materials) == 1
    assert shape.ifl_materials[0].raw[4] == 0
    assert not (Path(path).parent / "flame.ifl").exists(), "no frames, no file"


def test_detail_metrics_are_authorable():
    """The LOD selection metrics the add-on cannot recompute."""
    A.reset()
    arm = A.armature("Metered")
    A.mesh_object("body2", arm, bone="root")
    detail = arm.dts_shape.details.add()
    detail.name = "detail2"
    detail.size = 2.0
    detail.average_error = 0.25
    detail.max_error = 1.5
    detail.poly_count = 12

    shape = A.read(A.export_dts())
    match = next(d for d in shape.details if abs(d.size - 2.0) < 1e-6)
    assert abs(match.average_error - 0.25) < 1e-5
    assert abs(match.max_error - 1.5) < 1e-5
    assert match.poly_count == 12


def test_a_decal_is_made_by_an_operator():
    """The way a user actually makes one: select faces, press the button.

    Everything below this used to require nine exactly-named properties across
    three datablocks.  A feature that can only be built by typing magic strings
    is not creatable in the sense CLAUDE.md means.
    """
    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
    target.data.materials.append(A.blended_material("scorch"))
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target

    result = bpy.ops.io_scene_dts.add_decal(name="scorch")
    assert result == {"FINISHED"}, result

    shape = A.read(A.export_dts())
    assert len(shape.decals) == 1, "the operator's decal did not reach the file"
    assert shape.name(shape.decals[0].raw[0]) == "scorch"
    decal = shape.decals[0]
    covered = [
        shape.meshes[decal.raw[2] + i]
        for i in range(decal.raw[1])
        if shape.meshes[decal.raw[2] + i] is not None
    ]
    assert covered and covered[0].decal_data.indices
    # and it is not also exported as a shape object
    assert A.object_names(shape) == ["wall"], A.object_names(shape)


def _decal_scene(*, blended=True):
    """A wall with a decal on all of its faces, built from nothing."""
    from io_scene_dts.mapping.decals import create_decal

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    skin = A.blended_material("wall_skin") if blended else A.principled_material("wall_skin")
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces, material=skin)
    scorch = A.image_material("scorch", diffuse=A.generated_image("scorch", ramp=True))
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    index, projector = create_decal(arm, target, name="scorch", material=scorch)
    return arm, target, index, projector


def test_a_decal_is_authorable_as_a_baked_mesh():
    """Ticked, a decal leaves the file as geometry instead of a TSDecalMesh.

    Fresh scene: the projector is the only decal in it, and what comes back is
    an ordinary object any reader draws -- with the projection evaluated into
    its UVs, which is the whole substitute for the texgen planes that are now
    not in the file.
    """
    _decal_scene()

    shape = A.read(A.export_dts(decals_as_meshes=True))
    assert shape.decals == [], "the decal table must be empty, or it draws twice"
    assert A.object_names(shape) == ["wall", "scorch"], A.object_names(shape)

    baked = shape.objects[1]
    mesh = shape.meshes[baked.start_mesh_index]
    assert mesh is not None and mesh.mesh_type == 0, "a baked decal is a STANDARD_MESH"
    assert mesh.decal_data is None
    assert len(mesh.indices) == 6, mesh.indices  # the quad, as two triangles
    assert len(mesh.primitives) == 1

    # the projection has to land inside the texture, or it samples its edge
    us = [uv[0] for uv in mesh.tverts]
    vs = [uv[1] for uv in mesh.tverts]
    assert min(us) >= -1e-4 and max(us) <= 1 + 1e-4, sorted(us)
    assert min(vs) >= -1e-4 and max(vs) <= 1 + 1e-4, sorted(vs)
    # a degenerate projection also lands inside the square, so the span is the
    # assertion that matters -- the operator's projector leaves a margin, so
    # this quad covers the middle half of its texture
    assert max(us) - min(us) > 0.4, sorted(us)
    assert max(vs) - min(vs) > 0.4, sorted(vs)


def test_a_baked_decal_covers_what_the_decal_form_covers():
    """The checkbox changes the representation and nothing else.

    Both paths call the same ``covered_faces``, and if they ever stopped
    agreeing the box would quietly be changing which faces get a decal on them
    as well as how they are stored.
    """
    _decal_scene()
    projected = A.read(A.export_dts(decals_as_meshes=False))
    _decal_scene()
    baked = A.read(A.export_dts(decals_as_meshes=True))

    decal = projected.decals[0]
    dd = projected.meshes[decal.raw[2]].decal_data
    assert len(dd.indices) == len(baked.meshes[baked.objects[1].start_mesh_index].indices)


def test_a_baked_decal_is_lifted_off_its_target():
    """Coplanar geometry z-fights, and the polygon offset went with the decal.

    Checked against the target's own exported vertices rather than a constant,
    so it stays true if the fixture moves.
    """
    _decal_scene()
    from io_scene_dts.mapping.decals import DECAL_LIFT

    shape = A.read(A.export_dts(decals_as_meshes=True))

    wall = shape.meshes[shape.objects[0].start_mesh_index]
    scorch = shape.meshes[shape.objects[1].start_mesh_index]
    # the quad is flat, so every baked vertex sits one lift off the wall plane
    # along the shared normal
    normal = scorch.norms[0]
    plane = sum(w[i] * normal[i] for i in range(3) for w in [wall.verts[0]])
    for v in scorch.verts:
        assert abs(sum(v[i] * normal[i] for i in range(3)) - plane - DECAL_LIFT) < 1e-5, v


def test_baking_decals_needs_nothing_translucent():
    """The refusal is about the decal table, and a baked shape has none.

    A shape whose decals are geometry does not need a blended mesh for the
    engine to draw them against -- that requirement is what a TSDecalMesh
    brings, so lifting it here is not a loosened check but a different file.
    """
    _decal_scene(blended=False)
    shape = A.read(A.export_dts(decals_as_meshes=True))
    assert A.object_names(shape) == ["wall", "scorch"]

    # ...and it is still refused the other way, so the check did not just go
    _decal_scene(blended=False)
    try:
        A.export_dts(decals_as_meshes=False)
    except Exception:
        pass
    else:
        raise AssertionError("a decal with nothing translucent must still be refused")


def test_a_decal_branch_projects_the_decals_own_image():
    """The branch must sample the *decal's* texture, not fall back to a colour.

    A branch built at the wrong moment is fully wired and completely wrong: the
    target pointer has an update callback, import assigns that pointer before it
    assigns the decal's material, and a branch built from the callback gets
    ``None`` for the material.  ``_image_of(None)`` is no image and
    ``_base_colour_of(None)`` is a hardcoded red, so every surface carrying a
    decal renders as a flat pink patch -- which is what all 58 of light_male's
    decals did while every structural assertion about the branch still passed.
    """
    from io_scene_dts.mapping.decals import _branch_label, create_decal

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object(
        "wall2", arm, bone="root", verts=verts, faces=faces,
        material=A.blended_material("wall_skin"),
    )
    scorch = A.image_material("scorch", diffuse=A.generated_image("scorch", ramp=True))
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    index, _projector = create_decal(arm, target, name="scorch", material=scorch)

    host_mat = target.active_material
    branch = [n for n in host_mat.node_tree.nodes if n.label == _branch_label(index)]
    assert branch, "no branch was wired"
    images = [n for n in branch if n.type == "TEX_IMAGE"]
    assert images, "the branch fell back to a flat colour instead of the image"
    assert any(n.image is not None and n.image.name.startswith("scorch")
               for n in images), [getattr(n.image, "name", None) for n in images]


def _decal_branch_shader(host_mat, index=0):
    """The surface an *unlit* decal branch mixes over the host.

    Only unlit decals have one.  A lit decal is a colour now, shaded by the
    host's own Principled -- see :func:`_decal_colour_reaches_the_host_shader`.
    """
    from io_scene_dts.mapping.decals import _branch_label

    mix = next(
        n for n in host_mat.node_tree.nodes
        if n.label == _branch_label(index) and n.type == "MIX_SHADER"
    )
    return mix.inputs[2].links[0].from_node


def _decal_colour_reaches_the_host_shader(host_mat, index=0):
    """Does the branch's colour arrive at the host Principled's Base Color?"""
    from io_scene_dts.mapping.decals import _branch_label

    label = _branch_label(index)
    bsdf = next(n for n in host_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    socket, seen = bsdf.inputs["Base Color"], set()
    while socket is not None and socket.is_linked:
        node = socket.links[0].from_node
        if node.label == label:
            return True
        if node.name in seen:
            return False
        seen.add(node.name)
        socket = next((i for i in node.inputs if i.is_linked), None)
    return False


def _wall_with_decal(*, self_illuminating=False):
    """A wall carrying one decal; returns the host material the branch is in.

    Everything is built after the reset -- a datablock made before it is gone
    by the time the scene exists, which is the whole point of a fresh scene.
    """
    from io_scene_dts.mapping.decals import create_decal

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces,
                           material=A.blended_material("wall_skin"))
    decal_mat = A.blended_material("mark_skin")
    if self_illuminating:
        decal_mat["dts_self_illuminating"] = True

    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    # create_decal rather than the operator: the operator takes the target's
    # own material as the decal's, and the branch is wired during creation, so
    # a distinct decal material has to be named up front
    create_decal(arm, target, name="mark", material=decal_mat)
    # wire_decal_branch gives the target its own copy of the host material, so
    # the branch is in that copy and not in the one built above
    return target.material_slots[0].material


def test_a_decal_previews_lit_the_way_the_engine_lights_it():
    """A decal is ordinary geometry to the engine, shaded like its host.

    ``TSDecalMesh::render`` supplies the *target mesh's* normals and
    ``initDecalMaterials`` sets GL_MODULATE without disabling GL_LIGHTING
    (engine/ts/tsDecal.cc), so a decal falls into shadow with the surface it
    sits on.  This previewed as an unlit Emission for a long time, which made
    every decal look self-illuminating -- the flags round-tripped perfectly and
    the viewport lied about all of them.

    It is shaded by the *host's* Principled, not one of its own.  That is both
    the closest reading of the engine -- the decal is drawn with the target
    mesh's normals and no material state of its own beyond the texture -- and
    the difference between a material costing one BSDF per pixel and N+1.  A
    Mix Shader evaluates both sides whatever the factor, so the old form billed
    for every decal on the material whether or not any of them were on screen.
    """
    from io_scene_dts.mapping.decals import _branch_label

    host_mat = _wall_with_decal()
    nt = host_mat.node_tree
    branch = [n for n in nt.nodes if n.label == _branch_label(0)]
    assert branch, "no branch was wired"

    assert not [n for n in branch if n.type == "EMISSION"], "a lit decal must not emit"
    assert not [n for n in branch if n.type == "BSDF_PRINCIPLED"], (
        "the decal must borrow the host's shader, not carry its own"
    )
    assert len([n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"]) == 1, (
        "one decal must not double what the material costs to shade"
    )
    assert _decal_colour_reaches_the_host_shader(host_mat), (
        "the decal's colour never reaches the shader that lights it"
    )


def test_a_decal_does_not_rename_its_hosts_texture():
    """Compositing into Base Color puts a node between the host and its image.

    Export finds a material's texture by walking back from Base Color, and that
    walk used to take the first linked input it saw.  The colour mix has its
    *factor* linked too, and the factor is wired to the decal's own image, so
    the walk went down the factor and came back with the decal's texture --
    exporting the host material pointing at the wrong file.  Both images are
    real here, and distinct, so a confusion has somewhere to show.
    """
    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    host = A.image_material("wall_skin", diffuse=A.generated_image("wall_diffuse"))
    host.surface_render_method = "BLENDED"  # a shape with decals needs one
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces,
                           material=host)
    decal_mat = A.image_material("mark_skin", diffuse=A.generated_image("mark_diffuse"))

    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    from io_scene_dts.mapping.decals import create_decal

    create_decal(arm, target, name="mark", material=decal_mat)

    host_mat = target.material_slots[0].material
    from io_scene_dts.mapping.materials import diffuse_image_node

    node = diffuse_image_node(host_mat)
    assert node is not None, "the host material lost its texture behind the mix"
    assert node.image.name == "wall_diffuse", node.image.name

    # and at the file level, which is what actually ships
    path = A.export_dts()
    beside = Path(path).parent
    assert (beside / "wall_skin.png").is_file(), sorted(p.name for p in beside.iterdir())


def test_a_decal_takes_the_reflection_off_what_it_covers():
    """A decal is geometry over the host, so it hides the host's reflection.

    The Mix Shader form got that for nothing: it replaced the whole shaded
    surface, environment map included.  Compositing into Base Color puts the
    decal *under* the reflection instead, so the mask the environment map is
    gated by has to lose the decal's coverage or scorch marks come back shiny.
    """
    from io_scene_dts.mapping import envmap
    from io_scene_dts.mapping.decals import _branch_label, create_decal
    from io_scene_dts.mapping.materials import reflectance_image_node

    A.reset()
    arm = A.armature("Shiny")
    verts, faces = A.quad_geometry()
    host = A.image_material(
        "hull",
        diffuse=A.generated_image("hull_diffuse"),
        reflectance=A.generated_image("hull_refl", ramp=True),
        packing="SEPARATE",
    )
    host.surface_render_method = "BLENDED"
    target = A.mesh_object("hull2", arm, bone="root", verts=verts, faces=faces,
                           material=host)
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    create_decal(arm, target, name="scorch",
                 material=A.blended_material("scorch_skin"))

    host_mat = target.material_slots[0].material
    group = envmap.group_node(host_mat)
    assert group is not None, "the reflectance never reached the environment group"

    label = _branch_label(0)
    damp = group.inputs["Mask"].links[0].from_node
    assert damp.label == label, (
        f"the mask is fed by {damp.label!r}, not by the decal's damping"
    )
    assert damp.operation == "MULTIPLY"

    # and the mask still reads back as the reflectance map, or export invents a
    # second material entry for a texture the shape already has
    node = reflectance_image_node(host_mat)
    assert node is not None and node.image.name == "hull_refl", node


def test_a_self_illuminating_decal_previews_unlit():
    """...and the one case where the engine really does drop lighting.

    ``TSMesh::setMaterial`` disables GL_LIGHTING for MAT_SELF_ILLUMINATING, so
    the Emission is right here and only here.
    """
    host_mat = _wall_with_decal(self_illuminating=True)
    assert _decal_branch_shader(host_mat).type == "EMISSION"


def test_rebuilding_a_decal_preview_relights_an_old_branch():
    """A scene saved before decals previewed lit has to be fixable.

    ``wire_decal_branch`` refuses a label it already finds -- that is what stops
    a re-import stacking branches -- so a branch built by an older version stays
    unlit forever unless something rebuilds it.  Without this operator the fix
    reaches new imports and no existing .blend, which is most of them.
    """
    from io_scene_dts.mapping.decals import _branch_label

    host_mat = _wall_with_decal()
    nt = host_mat.node_tree
    label = _branch_label(0)
    assert _decal_colour_reaches_the_host_shader(host_mat)

    # Put the branch back the way an older add-on built it: the decal's colour
    # through an Emission of its own, mixed over the host surface.  This is the
    # graph in every .blend saved before either change, and it is what the
    # rebuild has to be able to recognise and replace.
    blend = next(n for n in nt.nodes if n.label == label and n.type == "MIX")
    sockets = [i for i in blend.inputs if i.enabled]  # Factor, A, B
    factor = sockets[0].links[0].from_socket
    base = sockets[1].links[0].from_socket if sockets[1].is_linked else None
    colour = sockets[2].links[0].from_socket
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    nt.nodes.remove(blend)
    if base is not None:
        nt.links.new(base, bsdf.inputs["Base Color"])

    output = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    surface = output.inputs["Surface"].links[0].from_socket
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.label = label
    nt.links.new(colour, emit.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.label = label
    nt.links.new(factor, mix.inputs["Fac"])
    nt.links.new(surface, mix.inputs[1])
    nt.links.new(emit.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs[0], output.inputs["Surface"])
    assert _decal_branch_shader(host_mat).type == "EMISSION"

    projector = next(o for o in bpy.data.objects if o.dts_decal.is_dts)
    with bpy.context.temp_override(object=projector):
        assert bpy.ops.io_scene_dts.rebuild_decal_preview(all_decals=False) == {"FINISHED"}

    host_mat = projector.dts_decal.target.material_slots[0].material
    nt = host_mat.node_tree
    assert _decal_colour_reaches_the_host_shader(host_mat), (
        "the rebuild has to relight a branch an older version left emissive"
    )
    # and it must not stack, nor leave the legacy nodes behind
    labelled = [n for n in nt.nodes if n.label == _branch_label(0)]
    assert not [n for n in labelled if n.type in {"MIX_SHADER", "EMISSION"}], (
        [n.type for n in labelled]
    )
    assert sum(1 for n in labelled if n.type == "MIX") == 1, [n.type for n in labelled]
    output = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    assert output.inputs["Surface"].is_linked


def test_an_operator_decal_projects_inside_its_texture():
    """The texgen planes have to land the covered faces inside the 0..1 square,
    or the decal samples outside its own texture."""
    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object(
        "wall2", arm, bone="root", verts=verts, faces=faces,
        material=A.blended_material("wall_skin"),
    )
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.io_scene_dts.add_decal(name="mark") == {"FINISHED"}

    shape = A.read(A.export_dts())
    decal = shape.decals[0]
    mesh = next(
        shape.meshes[decal.raw[2] + i]
        for i in range(decal.raw[1])
        if shape.meshes[decal.raw[2] + i] is not None
    )
    target_mesh = shape.meshes[shape.objects[decal.raw[3]].start_mesh_index]
    s_plane = mesh.decal_data.texgen_s[0]
    t_plane = mesh.decal_data.texgen_t[0]
    for index in set(mesh.decal_data.indices):
        v = target_mesh.verts[index]
        u = v[0] * s_plane[0] + v[1] * s_plane[1] + v[2] * s_plane[2] + s_plane[3]
        w = v[0] * t_plane[0] + v[1] * t_plane[1] + v[2] * t_plane[2] + t_plane[3]
        assert -0.01 <= u <= 1.01, u
        assert -0.01 <= w <= 1.01, w


def test_a_damage_ramp_of_decals_is_authorable():
    """The shipped pattern: each decal switches on further into a Damage
    sequence, so scorch marks accumulate as an object takes hits.  47 of the
    49 decal-bearing Tribes 2 shapes do exactly this."""
    from io_scene_dts.mapping.decals import decal_path, decal_prop

    A.reset()
    arm = A.armature("Hull", bones=(("root", None), ("shell", "root")))
    verts, faces = [], []
    n = 4
    for row in range(n + 1):
        for col in range(n + 1):
            verts.append(((col / n - 0.5) * 2.0, (row / n - 0.5) * 2.0, 0.0))
    for row in range(n):
        for col in range(n):
            a = row * (n + 1) + col
            faces += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    target = A.mesh_object(
        "hull2", arm, bone="shell", verts=verts, faces=faces,
        material=A.blended_material("hull_skin"),
    )

    from io_scene_dts.mapping.decals import create_decal

    for index, patch in enumerate(([0, 1], [10, 11])):
        for polygon in target.data.polygons:
            polygon.select = polygon.index in patch
        create_decal(arm, target, name=f"burn{index}", index=index, all_details=False)

    action = A.action_for(arm, "Damage", frames=11)
    action["dts_sequence"] = True
    bag = A.channelbag(action, arm)
    for index, first_on in ((0, 3), (1, 7)):
        name = f"burn{index}"
        arm[decal_prop(index, name)] = -1.0
        curve = bag.fcurves.new(data_path=decal_path(index, name), index=0)
        curve.keyframe_points.add(11)
        for kf in range(11):
            point = curve.keyframe_points[kf]
            point.co = (kf + 1, 0.0 if kf >= first_on else -1.0)
            point.interpolation = "CONSTANT"
        curve.update()

    shape = A.read(A.export_dts())
    seq = shape.sequences[0]
    assert seq.decal_matters.count() == 2, seq.decal_matters.count()
    n_keys = seq.num_keyframes
    tracks = []
    for di in sorted(seq.decal_matters.indices()):
        ordinal = seq.decal_matters.ordinal_of(di)
        tracks.append([
            shape.decal_states[seq.base_decal_state + ordinal * n_keys + kf]
            for kf in range(n_keys)
        ])
    assert tracks[0] == [-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0], tracks[0]
    assert tracks[1] == [-1] * 7 + [0] * 4, tracks[1]


def test_a_baked_decals_state_track_becomes_object_visibility():
    """Baked, a decal has no entry to hold a state, so the track has to move.

    Without this the Damage sequence above exports a shape whose scorch marks
    are all on from frame one -- the geometry is there, nothing switches it,
    and the loss is invisible in the file.
    """
    from io_scene_dts.mapping.decals import create_decal, decal_path, decal_prop

    A.reset()
    arm = A.armature("Hull", bones=(("root", None), ("shell", "root")))
    verts, faces = A.quad_geometry()
    target = A.mesh_object(
        "hull2", arm, bone="shell", verts=verts, faces=faces,
        material=A.blended_material("hull_skin"),
    )
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    create_decal(arm, target, name="burn", index=0, all_details=False)

    action = A.action_for(arm, "Damage", frames=4)
    action["dts_sequence"] = True
    bag = A.channelbag(action, arm)
    arm[decal_prop(0, "burn")] = -1.0
    curve = bag.fcurves.new(data_path=decal_path(0, "burn"), index=0)
    curve.keyframe_points.add(4)
    for kf in range(4):
        point = curve.keyframe_points[kf]
        point.co = (kf + 1, 0.0 if kf >= 2 else -1.0)
        point.interpolation = "CONSTANT"
    curve.update()

    shape = A.read(A.export_dts(decals_as_meshes=True))
    names = A.object_names(shape)
    assert names == ["hull", "burn"], names
    # off at rest: a decal that a sequence switches on must not start visible
    assert shape.object_states[1].vis == 0.0, shape.object_states[1]

    seq = shape.sequences[0]
    assert seq.decal_matters.count() == 0, "there is no decal table to point into"
    assert sorted(seq.vis_matters.indices()) == [1], sorted(seq.vis_matters.indices())
    n_keys = seq.num_keyframes
    ordinal = seq.vis_matters.ordinal_of(1)
    track = [
        shape.object_states[seq.base_object_state + ordinal * n_keys + kf].vis
        for kf in range(n_keys)
    ]
    assert track == [0.0, 0.0, 1.0, 1.0], track


def test_a_decal_projects_inside_the_zero_to_one_square():
    """The texgen planes have to land the covered faces inside the texture.

    Outside it, the decal samples its own transparent border and renders
    nothing -- which is indistinguishable from a decal that was never written,
    and is what a hand-placed projector produced before create_decal sized it.
    """
    A.reset()
    arm = A.armature("Plate", bones=(("root", None), ("shell", "root")))
    verts, faces = [], []
    n = 4
    for row in range(n + 1):
        for col in range(n + 1):
            verts.append(((col / n - 0.5) * 2.0, (row / n - 0.5) * 2.0, 0.0))
    for row in range(n):
        for col in range(n):
            a = row * (n + 1) + col
            faces += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    target = A.mesh_object(
        "plate2", arm, bone="shell", verts=verts, faces=faces,
        material=A.blended_material("plate_skin"),
    )

    from io_scene_dts.mapping.decals import create_decal

    for polygon in target.data.polygons:
        polygon.select = polygon.index in (8, 9, 10, 11)
    create_decal(arm, target, name="mark", index=0, all_details=False)

    shape = A.read(A.export_dts())
    decal = shape.decals[0]
    mesh = next(
        shape.meshes[decal.raw[2] + i]
        for i in range(decal.raw[1])
        if shape.meshes[decal.raw[2] + i] is not None
    )
    target_mesh = shape.meshes[shape.objects[decal.raw[3]].start_mesh_index]
    s_plane, t_plane = mesh.decal_data.texgen_s[0], mesh.decal_data.texgen_t[0]
    for index in set(mesh.decal_data.indices):
        v = target_mesh.verts[index]
        u = sum(v[i] * s_plane[i] for i in range(3)) + s_plane[3]
        w = sum(v[i] * t_plane[i] for i in range(3)) + t_plane[3]
        assert -0.01 <= u <= 1.01, f"u={u} outside the texture"
        assert -0.01 <= w <= 1.01, f"v={w} outside the texture"


def test_an_operator_decal_covers_every_detail_level():
    """A decal that only covers the highest LOD vanishes as the engine drops
    detail, which is why every shipped decal spans all of them."""
    A.reset()
    arm = A.armature("Hull")
    verts, faces = A.quad_geometry()
    high = A.mesh_object(
        "hull32", arm, bone="root", verts=verts, faces=faces,
        material=A.blended_material("hull_skin"),
    )
    A.mesh_object("hull2", arm, bone="root", verts=verts, faces=faces)
    for polygon in high.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = high
    assert bpy.ops.io_scene_dts.add_decal(name="stripe") == {"FINISHED"}

    shape = A.read(A.export_dts())
    decal = shape.decals[0]
    covered = [
        shape.meshes[decal.raw[2] + i]
        for i in range(decal.raw[1])
        if shape.meshes[decal.raw[2] + i] is not None
        and shape.meshes[decal.raw[2] + i].decal_data is not None
    ]
    assert len(covered) == 2, f"covered {len(covered)} of 2 detail levels"


def test_moving_the_projector_moves_the_decal():
    """The empty is the authored form: the texgen planes are read back off it,
    so dragging it changes the file."""
    from io_scene_dts.mapping.decals import PROJECTOR_PREFIX

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object(
        "wall2", arm, bone="root", verts=verts, faces=faces,
        material=A.blended_material("wall_skin"),
    )
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.io_scene_dts.add_decal(name="mark") == {"FINISHED"}

    def texgen_of(path):
        shape = A.read(path)
        decal = shape.decals[0]
        mesh = next(
            shape.meshes[decal.raw[2] + i]
            for i in range(decal.raw[1])
            if shape.meshes[decal.raw[2] + i] is not None
        )
        return mesh.decal_data.texgen_s[0]

    before = texgen_of(A.export_dts())
    projector = next(o for o in bpy.data.objects if o.name.startswith(PROJECTOR_PREFIX))
    projector.matrix_world.translation.x += 0.35
    after = texgen_of(A.export_dts())
    assert any(abs(a - b) > 1e-4 for a, b in zip(before, after)), (before, after)


def _feeds(socket, node) -> bool:
    """Whether ``node``'s output reaches ``socket`` through the node tree.

    Compared by name, not identity: ``nodes`` hands back a fresh wrapper every
    time it is read, so ``is`` asks a question about Python objects rather than
    about the node tree.
    """
    seen = set()
    stack = [socket]
    while stack:
        current = stack.pop()
        for link in current.links:
            if link.from_node.name == node.name:
                return True
            if link.from_node.name in seen:
                continue
            seen.add(link.from_node.name)
            stack += [i for i in link.from_node.inputs if i.is_linked]
    return False


def test_a_decal_previews_only_on_its_target():
    """The preview draws in a *material*, and a material is shared.

    A decal has no mesh, so its branch composites in the target's own shader,
    masked by the projector volume -- and a volume says where, not what.  Every
    other object using the same material and standing inside the box drew the
    decal too, which in the corpus is 5999 of 6053 decals: light_male puts all
    58 on the body material, and vehicle_land_mpbase 163 of them.

    Two things stop it now, and the order matters.  The target gets its **own
    copy** of the material, so a branch is only ever compiled into the one mesh
    it belongs to -- a gate can hide a branch but never stop the GPU running it,
    and 58 branches on 25 meshes cost light_male 3.9 fps against 33.4 split.
    The Object Info gate stays as the correctness backstop for the copy being
    shared again later, and is chosen over an Attribute node because EEVEE caps
    how many attributes a material may use.
    """
    from io_scene_dts.mapping.decals import (
        DECAL_HOST_PROP,
        PROJECTOR_PREFIX,
        _branch_label,
    )

    A.reset()
    arm = A.armature("Hull")
    verts, faces = A.quad_geometry()
    shared = A.blended_material("hull_skin")
    # two *different* DTS objects, one material, both inside the projector box
    target = A.mesh_object(
        "hull2", arm, bone="root", verts=verts, faces=faces, material=shared
    )
    decoy_verts, decoy_faces = A.quad_geometry(z=-0.1)
    decoy = A.mesh_object(
        "fin2", arm, bone="root", verts=decoy_verts, faces=decoy_faces,
        material=shared,
    )
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.io_scene_dts.add_decal(name="scorch") == {"FINISHED"}

    projector = next(o for o in bpy.data.objects if o.name.startswith(PROJECTOR_PREFIX))
    props = projector.dts_decal

    # the id the shader compares against: assigned, remembered, and not shared
    assert target.pass_index > 0, "the target was given no host id"
    assert target[DECAL_HOST_PROP] == target.pass_index
    assert decoy.pass_index != target.pass_index, (
        "the decoy answers to the target's id, so the gate cannot tell them apart"
    )

    label = _branch_label(props.index)

    # the branch is in the target's *own* copy, and the decoy is left on the
    # shared material with no branch in it at all -- that is what makes the
    # cost proportional to the decals a mesh actually carries
    host_mat = target.active_material
    assert host_mat is not shared, "the target was not split off the shared material"
    assert str(host_mat.get("dts_name")) == str(shared.get("dts_name") or shared.name), (
        "the copy must keep the source's DTS name or it exports as a second material"
    )
    assert decoy.active_material is shared, "the decoy should not have been copied"
    assert not [n for n in shared.node_tree.nodes if n.label == label], (
        "a branch was wired into the shared material"
    )

    branch = [n for n in host_mat.node_tree.nodes if n.label == label]
    info = next(n for n in branch if n.type == "OBJECT_INFO")
    same = next(n for n in branch if n.type == "MATH" and n.operation == "COMPARE")
    assert same.inputs[0].links[0].from_node.name == info.name
    assert same.inputs[0].links[0].from_socket.name == "Object Index"
    assert abs(same.inputs[1].default_value - target.pass_index) < 1e-6, (
        same.inputs[1].default_value, target.pass_index
    )
    # integers either side, so the window must not reach the next one
    assert same.inputs[2].default_value < 1.0

    blend = next(n for n in branch if n.type == "MIX")
    assert _feeds(blend.inputs[0], same), "the gate does not reach the mix factor"

    # the gate is preview only: the file is what it was without one
    shape = A.read(A.export_dts())
    decal = shape.decals[0]
    covered = [
        shape.meshes[decal.raw[2] + i]
        for i in range(decal.raw[1])
        if shape.meshes[decal.raw[2] + i] is not None
    ]
    assert len(covered) == 1 and covered[0].decal_data.indices
    assert shape.name(shape.objects[decal.raw[3]].name_index) == "hull"
    assert sorted(A.object_names(shape)) == ["fin", "hull"], A.object_names(shape)

    # and retargeting moves it, or the decal keeps drawing on the mesh it left.
    # With the split that means moving the branch between materials, not just
    # changing the gate's number: left in the old copy it would be compiled
    # into a material the new target does not use, and the decal would vanish.
    props.target = decoy
    assert decoy.pass_index > 0 and decoy.pass_index != target.pass_index

    moved_mat = decoy.active_material
    assert moved_mat is not shared, "the new target was not split off"
    moved = [n for n in moved_mat.node_tree.nodes if n.label == label]
    assert moved, "the branch did not follow the decal to its new target"
    assert not [n for n in host_mat.node_tree.nodes if n.label == label], (
        "the branch was left behind in the old target's material"
    )
    same = next(n for n in moved if n.type == "MATH" and n.operation == "COMPARE")
    assert abs(same.inputs[1].default_value - decoy.pass_index) < 1e-6, (
        same.inputs[1].default_value, decoy.pass_index
    )
    blend = next(n for n in moved if n.type == "MIX")
    assert _feeds(blend.inputs[0], same), "the moved gate does not reach the mix"
    assert _decal_colour_reaches_the_host_shader(moved_mat), (
        "the moved branch never reaches the new host's shader"
    )


def test_a_decal_is_authorable_by_hand():
    """The long way round, which the operator now wraps -- kept because it is
    exactly what the exporter reads, and a change to those property names
    should fail here rather than silently at export time.

    The whole decal is one empty: no mesh, no face list, no material slot.
    """
    from io_scene_dts.mapping.decals import PROJECTOR_PREFIX, decal_prop

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)

    projector = bpy.data.objects.new(f"{PROJECTOR_PREFIX}scorch", None)
    bpy.context.scene.collection.objects.link(projector)
    # after the update, not before: the target is bone-parented, so until the
    # depsgraph runs its matrix_world is still the identity and a projector
    # copied from it ends up perpendicular to the face it is meant to project
    # onto -- which the facing gate then correctly refuses to cover
    bpy.context.view_layer.update()
    projector.matrix_world = target.matrix_world

    props = projector.dts_decal
    props.is_dts = True
    props.decal_name = "scorch"
    props.index = 0
    props.object_name = "wall"
    props.target = target
    props.material = A.blended_material("scorch")
    arm[decal_prop(0, "scorch")] = 0.0

    shape = A.read(A.export_dts())
    assert len(shape.decals) == 1, "no decal was written"
    assert shape.name(shape.decals[0].raw[0]) == "scorch"
    meshes = [
        shape.meshes[shape.decals[0].raw[2] + i]
        for i in range(shape.decals[0].raw[1])
        if shape.meshes[shape.decals[0].raw[2] + i] is not None
    ]
    covered = [m for m in meshes if m.decal_data is not None]
    assert covered, "the decal covered no faces"
    assert covered[0].decal_data.indices
    assert covered[0].decal_data.texgen_s and covered[0].decal_data.texgen_t
    # and it is not also a shape object
    assert A.object_names(shape) == ["wall"], A.object_names(shape)


def test_translucent_objects_are_written_last():
    """Objects are drawn in list order and a blended surface only composites
    over what is already in the frame buffer, so everything translucent goes to
    the end -- whatever order the scene built them in."""
    from io_scene_dts.dtslib.translucency import (
        objects_out_of_order,
        translucent_object_indices,
    )

    A.reset()
    arm = A.armature("Bay", bones=(("root", None),))
    # built glass-first on purpose: discovery order is what the sort overrides
    A.mesh_object("canopy2", arm, bone="root", material=A.blended_material("glass"))
    A.mesh_object("hull2", arm, bone="root", material=A.principled_material("hull"))
    A.mesh_object("strut2", arm, bone="root", material=A.principled_material("strut"))

    shape = A.read(A.export_dts())
    assert A.object_names(shape) == ["hull", "strut", "canopy"], A.object_names(shape)
    assert translucent_object_indices(shape) == {2}
    assert objects_out_of_order(shape) == []


def test_an_all_opaque_shape_keeps_its_order():
    """Nothing translucent, nothing to move: the sort is stable, so a shape
    without glass comes out in exactly the order it was built."""
    A.reset()
    arm = A.armature("Crate", bones=(("root", None),))
    for name in ("lid", "body", "base"):
        A.mesh_object(f"{name}2", arm, bone="root", material=A.principled_material(name))

    shape = A.read(A.export_dts())
    assert A.object_names(shape) == ["lid", "body", "base"], A.object_names(shape)


def test_a_decal_needs_something_translucent_to_draw_against():
    """A shape carrying decals with nothing blended in it writes and reads
    perfectly and draws its decals wrong, which nothing downstream can
    diagnose -- so export refuses it rather than shipping it."""
    from io_scene_dts.mapping.decals import create_decal

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    opaque = A.principled_material("wall_skin")
    target = A.mesh_object(
        "wall2", arm, bone="root", verts=verts, faces=faces, material=opaque,
    )
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.update()
    create_decal(arm, target, name="scorch", material=opaque)

    path = A.tmp(".dts")
    try:
        result = bpy.ops.io_scene_dts.export_dts(filepath=path, version="24")
        assert result == {"CANCELLED"}, result
    except RuntimeError as exc:
        assert "translucent" in str(exc), str(exc)

    # blending the material it already has is the whole fix -- read off the
    # object rather than reusing `opaque`, because giving a decal target its
    # own material copy means the slot no longer holds the one built above
    target.active_material.surface_render_method = "BLENDED"
    shape = A.read(A.export_dts())
    assert len(shape.decals) == 1


def test_decal_coverage_is_what_the_export_writes():
    """The preview mask and the exported index list come from one function.

    Coverage is recomputed rather than stored, so the face attribute the shader
    masks by is a cache.  If it could disagree with what export derives, the
    viewport would be showing a decal that is not the one in the file.
    """
    from io_scene_dts.mapping.decals import (
        coverage_attribute,
        covered_faces,
        decal_objects,
    )

    A.reset()
    arm = A.armature("Wall")
    # a grid, not a single quad: with one face every rule agrees trivially
    verts, faces = [], []
    n = 4
    for row in range(n + 1):
        for col in range(n + 1):
            verts.append(((col / n - 0.5) * 2.0, (row / n - 0.5) * 2.0, 0.0))
    for row in range(n):
        for col in range(n):
            a = row * (n + 1) + col
            faces += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
    target.data.materials.append(A.blended_material("scorch"))
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.io_scene_dts.add_decal(name="scorch") == {"FINISHED"}

    decal = decal_objects()[0]
    props = decal.dts_decal
    derived = set(
        covered_faces(target, decal.matrix_world, depth=props.depth, rule=props.rule)
    )
    attr = target.data.attributes[coverage_attribute(props.index)]
    cached = {i for i, v in enumerate(attr.data) if v.value > 0.5}
    assert cached == derived, (sorted(cached), sorted(derived))
    assert derived, "the decal covers nothing"


def test_a_decal_does_not_reach_the_far_side():
    """The facing gate, tested as behaviour rather than as a corpus average.

    A projector has depth but no notion of what is in front of what, so without
    a facing test a decal aimed at the front of a box also covers the back --
    the burn mark appears on both sides.  The original exporter rejected a face
    whose normal turned more than DECAL::MAX_ANGLE from the projection axis
    (ShapeMimic.cc:6043, default 90 degrees); Max Angle is that rule.

    The aggregate recall floor cannot catch this: fit_coverage searches the
    angle too, so removing the gate just makes the fit pick 180 and the numbers
    barely move.  This asserts the behaviour directly.
    """
    from io_scene_dts.mapping.decals import covered_faces

    A.reset()
    arm = A.armature("Box")
    verts, faces = A.cube_geometry(0.5)
    target = A.mesh_object("box2", arm, bone="root", verts=verts, faces=faces)
    bpy.context.view_layer.update()

    # a projector above the box, looking straight down, deep enough to reach
    # right through it
    projector = Matrix.Translation(target.matrix_world.translation + Vector((0, 0, 2)))

    front = target.matrix_world.to_3x3().inverted_safe().transposed()
    covered = covered_faces(target, projector, depth=8.0, rule="CENTRE", max_angle=90.0)
    assert covered, "the facing gate rejected everything"
    for index in covered:
        n = (front @ target.data.polygons[index].normal).normalized()
        assert n.z > 0.0, f"face {index} points away ({n.z:.3f}) and was covered"

    # and with the gate opened all the way the far side comes back, which is
    # what makes the assertion above about the gate and not about the depth
    both = covered_faces(target, projector, depth=8.0, rule="CENTRE", max_angle=180.0)
    assert len(both) > len(covered), (len(both), len(covered))


def test_moving_the_projector_changes_the_covered_faces():
    """Scaling the empty down must shrink the decal in the exported file.

    The old representation stored the faces, so this could only ever change the
    texgen planes.  Coverage is derived now, which is the whole point of the
    empty being the decal: shrink it and it covers fewer faces.
    """
    from io_scene_dts.mapping.decals import decal_objects

    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
    target.data.materials.append(A.blended_material("scorch"))
    for polygon in target.data.polygons:
        polygon.select = True
    bpy.context.view_layer.objects.active = target
    assert bpy.ops.io_scene_dts.add_decal(name="scorch") == {"FINISHED"}

    def covered_count():
        shape = A.read(A.export_dts())
        decal = shape.decals[0]
        return sum(
            len(shape.meshes[decal.raw[2] + i].decal_data.indices) // 3
            for i in range(decal.raw[1])
            if shape.meshes[decal.raw[2] + i] is not None
            and shape.meshes[decal.raw[2] + i].decal_data is not None
        )

    before = covered_count()
    assert before > 0, before

    projector = decal_objects()[0]
    # far too small to reach any face centre or corner of the quad
    projector.matrix_world = projector.matrix_world @ Matrix.Scale(0.01, 4)
    bpy.context.view_layer.update()

    after = covered_count()
    assert after < before, (before, after)


# ----------------------------------------------------------------------
# the shape as a whole
# ----------------------------------------------------------------------


def test_bounds_are_computed():
    A.reset()
    arm = A.armature("Boxed")
    verts, faces = A.cube_geometry(2.0)
    A.mesh_object("body2", arm, bone="root", verts=verts, faces=faces)

    shape = A.read(A.export_dts())
    assert shape.radius > 0
    assert shape.bounds[3] > shape.bounds[0]
    mesh = A.live_meshes(shape)[0]
    assert mesh.radius_int >= 1


def test_a_fresh_shape_reimports():
    """The exported file has to be readable by the add-on's own importer --
    the one place this suite touches it, because a file nothing can open is
    not an export."""
    A.reset()
    arm = A.armature("Round", bones=(("root", None), ("spin", "root")))
    mat = A.principled_material("skin")
    A.mesh_object("body2", arm, bone="spin", material=mat)
    action = A.action_for(arm, "turn", frames=4)
    action["dts_sequence"] = True
    path = A.export_dts()

    A.reset()
    assert bpy.ops.io_scene_dts.import_dts(
        filepath=path, import_details=True
    ) == {"FINISHED"}
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert meshes
    assert any(o.type == "ARMATURE" for o in bpy.context.scene.objects)
    assert any(a.get("dts_sequence") for a in bpy.data.actions)


# ----------------------------------------------------------------------
# the rest of the surface
# ----------------------------------------------------------------------


def test_several_objects_in_one_shape():
    A.reset()
    arm = A.armature("Turret", bones=(("root", None), ("barrel", "root")))
    A.mesh_object("base2", arm, bone="root")
    A.mesh_object("gun2", arm, bone="barrel")

    shape = A.read(A.export_dts())
    assert sorted(A.object_names(shape)) == ["base", "gun"]
    by_name = {shape.name(o.name_index): o for o in shape.objects}
    names = [shape.node_name(i) for i in range(len(shape.nodes))]
    assert names[by_name["gun"].node_index] == "barrel"
    assert names[by_name["base"].node_index] == "root"


def test_a_named_detail_level():
    """Detail names are free-form; the size comes from the suffix."""
    A.reset()
    arm = A.armature("Named")
    obj = A.mesh_object("body2", arm, bone="root")
    obj["dts_detail_name"] = "MyDetail"

    shape = A.read(A.export_dts())
    assert [shape.name(d.name_index) for d in shape.details] == ["MyDetail"]


def test_default_object_states_are_authorable():
    """An object can rest hidden, or on a frame other than the first, without
    any sequence animating it."""
    A.reset()
    arm = A.armature("Resting")
    obj = A.mesh_object("panel2", arm, bone="root")
    obj["dts_default_vis"] = 0.25

    shape = A.read(A.export_dts())
    assert shape.object_states
    assert abs(shape.object_states[0].vis - 0.25) < 1e-5


def test_frame_and_matframe_tracks_are_authorable():
    """The other two object-state channels, keyframed like visibility."""
    from io_scene_dts.mapping.objectstate import ensure_props, path_for

    A.reset()
    arm = A.armature("Flip", bones=(("root", None), ("mount", "root")))
    obj = A.mesh_object("card2", arm, bone="mount")
    obj.shape_key_add(name="Basis")
    for frame in range(1, 3):
        obj.shape_key_add(name=f"frame_{frame:03d}", from_mix=False)

    action = A.action_for(arm, "flip", frames=4)
    action["dts_sequence"] = True
    bag = A.channelbag(action, arm)
    for kind, values in (("frame", (0, 1, 2, 1)), ("matframe", (0, 0, 1, 1))):
        ensure_props(arm, kind, ["card"])
        curve = bag.fcurves.new(data_path=path_for(kind, "card"), index=0)
        curve.keyframe_points.add(4)
        for index, value in enumerate(values):
            point = curve.keyframe_points[index]
            point.co = (index + 1, float(value))
            point.interpolation = "CONSTANT"
        curve.update()

    shape = A.read(A.export_dts())
    seq = shape.sequences[0]
    assert seq.frame_matters.count() == 1, "the frame track did not reach the file"
    assert seq.mat_frame_matters.count() == 1, "the matframe track did not reach the file"
    ordinal = seq.frame_matters.ordinal_of(0)
    frames = [
        shape.object_states[seq.base_object_state + ordinal * seq.num_keyframes + kf].frame_index
        for kf in range(seq.num_keyframes)
    ]
    assert frames == [0, 1, 2, 1], frames


def test_sequence_flags_are_authorable():
    A.reset()
    arm = A.armature("Flagged", bones=(("root", None), ("j", "root")))
    A.mesh_object("body2", arm, bone="j")
    action = A.action_for(arm, "path", frames=4)
    action["dts_sequence"] = True
    action["dts_makepath"] = True
    action["dts_blend"] = True

    from io_scene_dts.dtslib.types import SEQ_BLEND, SEQ_MAKE_PATH

    seq = A.read(A.export_dts()).sequences[0]
    assert seq.flags & SEQ_MAKE_PATH, hex(seq.flags)
    assert seq.flags & SEQ_BLEND, hex(seq.flags)


def test_ifl_membership_is_authorable():
    """Which IFL materials a sequence advances -- a pointer, not an index."""
    A.reset()
    arm = A.armature("Burner", bones=(("root", None), ("j", "root")))
    mat = A.principled_material("flame")
    mat.dts_material.is_ifl = True
    frame = mat.dts_material.ifl_frames.add()
    frame.image = A.generated_image("flame0")
    A.mesh_object("body2", arm, bone="j", material=mat)
    action = A.action_for(arm, "burn", frames=4)
    action["dts_sequence"] = True
    action.dts_sequence_props.ifl_matters.add().material = mat

    seq = A.read(A.export_dts()).sequences[0]
    assert seq.ifl_matters.count() == 1
    assert sorted(seq.ifl_matters.indices()) == [0]


def test_smallest_visible_size_is_authorable():
    A.reset()
    arm = A.armature("Ranged")
    A.mesh_object("body2", arm, bone="root")
    A.mesh_object("body32", arm, bone="root")
    arm["dts_smallest_visible_size"] = 8.0
    arm["dts_smallest_visible_dl"] = 1

    shape = A.read(A.export_dts())
    assert abs(shape.smallest_visible_size - 8.0) < 1e-5
    assert shape.smallest_visible_dl == 1


def test_exporting_v23():
    """Tribes 2's version, which keeps skins inline and has no ground frames."""
    A.reset()
    arm = A.armature("T2", bones=(("root", None), ("j", "root")))
    A.mesh_object("body2", arm, bone="j", material=A.principled_material("hull"))
    action = A.action_for(arm, "idle", frames=4)
    action["dts_sequence"] = True

    shape = A.read(A.export_dts(version="23"))
    assert shape.source_version == 23
    assert len(shape.sequences) == 1
    assert A.live_meshes(shape)


def _shape_with_a_ground_frame(name):
    arm = A.armature(name, bones=(("root", None), ("j", "root")))
    A.mesh_object("body2", arm, bone="j")
    action = A.action_for(arm, "run", frames=4)
    action["dts_sequence"] = True
    item = action.dts_sequence_props.ground.add()
    item.translation = (0.0, 1.0, 0.0)
    item.rotation = (0, 0, 0, 32767)


def test_v23_drops_ground_frames_rather_than_refusing():
    """v23 has nowhere to store them and no edit would change that, so the
    export goes through without them -- warning the user, which
    tests/test_writer.py checks the wording of."""
    A.reset()
    _shape_with_a_ground_frame("T2")

    shape = A.read(A.export_dts(version="23"))
    assert not shape.ground_translations
    assert shape.sequences[0].num_ground_frames == 0


def test_v24_keeps_the_ground_frames_v23_would_drop():
    """The same shape at the version that has the storage."""
    A.reset()
    _shape_with_a_ground_frame("TGE")

    shape = A.read(A.export_dts(version="24"))
    assert shape.sequences[0].num_ground_frames == 1
    assert len(shape.ground_translations) == 1


def test_exporting_from_edit_mode():
    """Exporting mid-edit is what a user does, and it used to raise IndexError.

    Edit Mode holds the geometry in a BMesh and does not write the Mesh
    datablock's parallel arrays until it is flushed.  A UV layer is the trap:
    it exists, so the exporter's `if uv_layer` guard passes, while its `data`
    is empty beside a full `loops` -- so the first corner blew up on a raw
    `bpy_prop_collection[index]` error naming nothing the user could act on.

    The UVs have to be *right*, not merely present: a flush that did not happen
    would read zeros for every corner, which is a shape that exports and is
    textured wrong.
    """
    A.reset()
    arm = A.armature("Panel")
    verts, faces = A.quad_geometry()
    target = A.mesh_object(
        "panel2", arm, bone="root", verts=verts, faces=faces,
        material=A.principled_material("hazard"),
    )
    # a UV that is not the default, so a zeroed read is distinguishable
    uv_layer = target.data.uv_layers[0] if target.data.uv_layers else target.data.uv_layers.new()
    for i, loop_uv in enumerate(uv_layer.data):
        loop_uv.uv = (0.25 + 0.5 * (i % 2), 0.75)

    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="EDIT")
    assert target.mode == "EDIT"
    # the datablock the exporter reads is not caught up yet -- the bug's cause
    assert len(target.data.uv_layers[0].data) == 0

    shape = A.read(A.export_dts())

    # ...and the mode is the user's: export puts it back, having borrowed it
    assert target.mode == "EDIT", "export left the user out of Edit Mode"

    mesh = A.live_meshes(shape)[0]
    assert mesh.tverts, "no UVs reached the file"
    # V flipped on the way out (blender_to_shape.py:665), so 0.75 is 0.25 here
    assert all(abs(v - 0.25) < 1e-5 for _, v in mesh.tverts), mesh.tverts
    assert {round(u, 5) for u, _ in mesh.tverts} == {0.25, 0.75}, mesh.tverts


def test_a_texture_is_found_beside_the_file():
    """The engine looks a material's texture up by name next to the .dts, so
    the material name is the filename: no path is stored."""
    A.reset()
    arm = A.armature("Textured")
    A.mesh_object("body2", arm, bone="root", material=A.principled_material("crate01"))

    shape = A.read(A.export_dts())
    assert [m.name for m in shape.materials] == ["crate01"]


def test_a_material_name_can_differ_from_the_datablock():
    """dts_name is what the file gets, so a Blender name like `hull.001` does
    not become the texture the engine looks for."""
    A.reset()
    arm = A.armature("Renamed")
    mat = A.principled_material("hull.001")
    mat["dts_name"] = "hull"
    A.mesh_object("body2", arm, bone="root", material=mat)

    assert [m.name for m in A.read(A.export_dts()).materials] == ["hull"]


def test_two_materials_on_one_mesh():
    """One primitive per material, which is what the engine's own exporter
    emits and what the material index in the primitive word selects."""
    A.reset()
    arm = A.armature("TwoTone")
    verts, faces = A.cube_geometry()
    obj = A.mesh_object("body2", arm, bone="root", verts=verts, faces=faces)
    obj.data.materials.append(A.principled_material("top"))
    obj.data.materials.append(A.principled_material("bottom"))
    for index, polygon in enumerate(obj.data.polygons):
        polygon.material_index = 0 if index < 6 else 1

    shape = A.read(A.export_dts())
    assert len(shape.materials) == 2
    mesh = A.live_meshes(shape)[0]
    assert len(mesh.primitives) == 2, len(mesh.primitives)
    used = {p.mat_index & 0x0FFFFFFF for p in mesh.primitives}
    assert used == {0, 1}, used


def test_selected_only_export():
    A.reset()
    arm = A.armature("Partial")
    keep = A.mesh_object("keep2", arm, bone="root")
    A.mesh_object("drop2", arm, bone="root")
    bpy.ops.object.select_all(action="DESELECT")
    keep.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm

    shape = A.read(A.export_dts(selected_only=True))
    assert A.object_names(shape) == ["keep"], A.object_names(shape)


def test_exporting_without_sequences():
    A.reset()
    arm = A.armature("Static", bones=(("root", None), ("j", "root")))
    A.mesh_object("body2", arm, bone="j")
    action = A.action_for(arm, "idle", frames=4)
    action["dts_sequence"] = True

    shape = A.read(A.export_dts(export_sequences=False))
    assert shape.sequences == []
    assert A.live_meshes(shape)


# ----------------------------------------------------------------------
# format versions
# ----------------------------------------------------------------------

WRITABLE_VERSIONS = tuple(range(15, 25))


def _versioned_scene():
    """A scene with something to lose in every version: a node hierarchy, two
    detail levels, a material, and a rotating sequence."""
    A.reset()
    arm = A.armature("Rig", bones=(("root", None), ("panel", "root")))
    A.mesh_object("body2", arm, bone="panel", material=A.principled_material("hull"))
    A.mesh_object("body1", arm, bone="panel")
    action = A.action_for(arm, "spin", frames=5)
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    return arm


def test_the_version_menu_offers_exactly_what_the_library_writes():
    """The dropdown and the writer have to agree, or a listed version errors
    out on export and an unlisted one is unreachable."""
    from io_scene_dts.dtslib.fit import MAX_VERSION, MIN_VERSION
    from io_scene_dts.ops.export_dts import ExportDTS

    listed = sorted(int(item[0]) for item in ExportDTS.__annotations__["version"].keywords["items"])
    assert listed == list(range(MIN_VERSION, MAX_VERSION + 1))


def test_every_version_is_writable_from_a_fresh_scene():
    """No import anywhere: the shape is built in Blender and written ten times.

    Each read-back is checked for the things the version does keep, so a
    version whose layout is subtly wrong fails here rather than in the engine.
    """
    for version in WRITABLE_VERSIONS:
        _versioned_scene()
        shape = A.read(A.export_dts(version=str(version)))
        assert shape.source_version == version, version
        assert [shape.name(n.name_index) for n in shape.nodes] == ["root", "panel"]
        assert A.object_names(shape) == ["body"]
        assert sorted(d.size for d in shape.details) == [1.0, 2.0], version
        assert [m.name for m in shape.materials] == ["hull"], version
        assert len(shape.sequences) == 1, version
        seq = shape.sequences[0]
        assert shape.name(seq.name_index) == "spin", version
        assert seq.num_keyframes == 5, (version, seq.num_keyframes)
        assert abs(seq.duration - 1.0) < 1e-5, version
        assert seq.rotation_matters.count() == 1, version
        mesh = A.live_meshes(shape)[0]
        assert mesh.verts and mesh.norms and mesh.indices, version
        assert mesh.mesh_type == STANDARD_MESH, version


def _two_channel_scene():
    """Two bones rotating differently over four keys.

    Two channels and more than one key is the minimum that makes the pre-v17
    transpose observable: with one channel, keyframe-major and channel-major
    orderings are the same list, and a transpose that never happened looks
    correct.
    """
    A.reset()
    arm = A.armature("Arm", bones=(("root", None), ("upper", "root"), ("lower", "upper")))
    A.mesh_object("body2", arm, bone="lower")
    action = bpy.data.actions.new("wave")
    action.use_fake_user = True
    action["dts_sequence"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    for index, name in enumerate(("upper", "lower")):
        bone = arm.pose.bones[name]
        bone.rotation_mode = "QUATERNION"
        for frame in range(1, 5):
            angle = (frame - 1) * (0.3 + 0.4 * index)
            bone.rotation_quaternion = (
                math.cos(angle / 2), math.sin(angle / 2) * (1 - index), 0.0,
                math.sin(angle / 2) * index,
            )
            bone.keyframe_insert("rotation_quaternion", frame=frame)
    return arm


def _rotation_tracks(shape):
    """Each animated node's whole rotation track, per channel."""
    seq = shape.sequences[0]
    nodes = list(seq.rotation_matters.indices())
    return {
        node: shape.node_rotations[
            seq.base_rotation + o * seq.num_keyframes :
            seq.base_rotation + (o + 1) * seq.num_keyframes
        ]
        for o, node in enumerate(nodes)
    }


def test_animation_survives_the_keyframe_major_versions():
    """v15 and v16 store animation per keyframe rather than per channel.  The
    transpose is invisible in the file's shape, so this compares the tracks the
    old versions come back with against the modern one's, key for key."""
    _two_channel_scene()
    modern = _rotation_tracks(A.read(A.export_dts(version="24")))
    assert len(modern) == 2, modern
    assert len(next(iter(modern.values()))) == 4
    # the two channels must actually differ, or the transpose is unobservable
    assert list(modern.values())[0] != list(modern.values())[1]

    for version in WRITABLE_VERSIONS:
        _two_channel_scene()
        old = _rotation_tracks(A.read(A.export_dts(version=str(version))))
        assert old == modern, f"v{version} rotation tracks: {old} != {modern}"


def test_a_skin_is_authorable_in_every_version():
    """Pre-v23 keeps skins in a section with no room for the object's name or
    node.  Written into the mesh list instead, both survive -- and every version
    of the reader accepts them there."""
    for version in WRITABLE_VERSIONS:
        A.reset()
        arm = A.armature("Skinned", bones=(("root", None), ("spine", "root")))
        obj = A.mesh_object("body2", arm)
        obj.parent_type = "OBJECT"
        obj.modifiers.new("Armature", "ARMATURE").object = arm
        lower = obj.vertex_groups.new(name="root")
        upper = obj.vertex_groups.new(name="spine")
        for vertex in obj.data.vertices:
            (upper if vertex.co.z > 0 else lower).add([vertex.index], 1.0, "REPLACE")

        shape = A.read(A.export_dts(version=str(version)))
        assert A.object_names(shape) == ["body"], version
        mesh = A.live_meshes(shape)[0]
        assert mesh.mesh_type == SKIN_MESH, version
        assert mesh.initial_verts and mesh.weight and mesh.node_index, version


def test_pre_v22_pairs_a_translation_only_channel():
    """A bone that only slides has no rotation track to store, and v21 has no
    way to say so: it stores one node state, rotation and translation together.
    The rotation it gains has to be the bone's rest pose, not zero."""
    A.reset()
    arm = A.armature("Slider", bones=(("root", None), ("lift", "root")))
    A.mesh_object("body2", arm, bone="lift")
    action = bpy.data.actions.new("slide")
    action.use_fake_user = True
    action["dts_sequence"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["lift"]
    for frame in range(1, 5):
        bone.location = (0.0, 0.0, frame * 0.1)
        bone.keyframe_insert("location", frame=frame)

    shape = A.read(A.export_dts(version="21"))
    seq = shape.sequences[0]
    assert seq.translation_matters.count() == 1
    # v21 reads one set back into both, so rotation now "matters" too
    assert seq.rotation_matters == seq.translation_matters
    node = next(seq.translation_matters.indices())
    rest = shape.default_rotations[node]
    filled = shape.node_rotations[seq.base_rotation : seq.base_rotation + seq.num_keyframes]
    assert filled == [rest] * seq.num_keyframes, filled
    # and the translation the user authored is still there, still rising
    zs = [
        t[2]
        for t in shape.node_translations[
            seq.base_translation : seq.base_translation + seq.num_keyframes
        ]
    ]
    assert zs == sorted(zs) and zs[-1] > zs[0], zs
