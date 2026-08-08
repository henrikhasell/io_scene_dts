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
    hull["dts_reflection_amount"] = 0.75
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
        combine=False,
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
        combine=True,
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
        combine=True,
    )
    # ...and the checkbox does not get to contradict the map either
    ticked = A.image_material(
        "ticked",
        diffuse=A.generated_image("ticked_diffuse"),
        reflectance=A.generated_image("ticked_refl", ramp=True),
        combine=True,
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
        combine=False,
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
    target.data.materials.append(A.principled_material("scorch"))
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


def test_an_operator_decal_projects_inside_its_texture():
    """The texgen planes have to land the covered faces inside the 0..1 square,
    or the decal samples outside its own texture."""
    A.reset()
    arm = A.armature("Wall")
    verts, faces = A.quad_geometry()
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
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
    target = A.mesh_object("hull2", arm, bone="shell", verts=verts, faces=faces)

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
    target = A.mesh_object("plate2", arm, bone="shell", verts=verts, faces=faces)

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
    high = A.mesh_object("hull32", arm, bone="root", verts=verts, faces=faces)
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
    target = A.mesh_object("wall2", arm, bone="root", verts=verts, faces=faces)
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
    58 on the body material, and vehicle_land_mpbase 163 of them.  The gate is
    an Object Info comparison, chosen over an Attribute node because EEVEE caps
    how many attributes a material may use and a shape can put 58 decals on one.
    """
    from io_scene_dts.mapping.decals import (
        DECAL_HOST_PROP,
        PROJECTOR_PREFIX,
        _branch_label,
    )

    A.reset()
    arm = A.armature("Hull")
    verts, faces = A.quad_geometry()
    shared = A.principled_material("hull_skin")
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
    branch = [n for n in shared.node_tree.nodes if n.label == label]
    info = next(n for n in branch if n.type == "OBJECT_INFO")
    same = next(n for n in branch if n.type == "MATH" and n.operation == "COMPARE")
    assert same.inputs[0].links[0].from_node.name == info.name
    assert same.inputs[0].links[0].from_socket.name == "Object Index"
    assert abs(same.inputs[1].default_value - target.pass_index) < 1e-6, (
        same.inputs[1].default_value, target.pass_index
    )
    # integers either side, so the window must not reach the next one
    assert same.inputs[2].default_value < 1.0

    mix = next(n for n in branch if n.type == "MIX_SHADER")
    assert _feeds(mix.inputs["Fac"], same), "the gate does not reach the mix factor"

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

    # and retargeting moves it, or the decal keeps drawing on the mesh it left
    props.target = decoy
    assert decoy.pass_index > 0 and decoy.pass_index != target.pass_index
    assert abs(same.inputs[1].default_value - decoy.pass_index) < 1e-6, (
        same.inputs[1].default_value, decoy.pass_index
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
    props.material = A.principled_material("scorch")
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
    target.data.materials.append(A.principled_material("scorch"))
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
    target.data.materials.append(A.principled_material("scorch"))
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


def test_v23_refuses_ground_frames():
    """v23 has nowhere to store them, so it errors rather than dropping the
    speed off every movement animation silently."""
    A.reset()
    arm = A.armature("T2", bones=(("root", None), ("j", "root")))
    A.mesh_object("body2", arm, bone="j")
    action = A.action_for(arm, "run", frames=4)
    action["dts_sequence"] = True
    item = action.dts_sequence_props.ground.add()
    item.translation = (0.0, 1.0, 0.0)
    item.rotation = (0, 0, 0, 32767)

    path = A.tmp(".dts")
    try:
        result = bpy.ops.io_scene_dts.export_dts(filepath=path, version="23")
        assert result == {"CANCELLED"}, result
    except RuntimeError as exc:
        assert "ground frame" in str(exc), str(exc)

    # ...and exports when told to drop them
    result = bpy.ops.io_scene_dts.export_dts(
        filepath=path, version="23", drop_ground_frames=True
    )
    assert result == {"FINISHED"}, result
    assert A.read(path).sequences[0].num_ground_frames == 0


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
