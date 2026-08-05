"""Decals as projected UVs.

A ``TSDecalMesh`` has no geometry of its own.  It stores a subset of its
*target* mesh's indices, one material, and two ``Point4F`` planes, and the
engine computes every UV as a dot product against those planes
(``tsDecal.cc``)::

    tv.x = v->x*s.x + v->y*s.y + v->z*s.z + s.w;
    tv.y = v->x*t.x + v->y*t.y + v->z*t.z + t.w;

That is an affine planar projection — exactly what Blender's UV Project
modifier computes from an empty.  So a decal imports as the faces it covers
plus a projector empty, and exports by reading the planes back out of that
empty.  Nothing is frozen: the empty is the authored form, and moving it moves
the decal in the exported file.

The correspondence is exact.  Blender projects with

    uv = 0.5 * (P @ p).xy + 0.5,   P = projector⁻¹ @ object_matrix

and DTS's V axis runs opposite Blender's (as it does for ordinary tverts), so

    P row 0 =  2*S.xyz, 2*S.w - 1
    P row 1 = -2*T.xyz, 1 - 2*T.w

Row 2 is the projection depth, which no UV depends on; it is set to the
normalised cross product of the other two so the matrix inverts cleanly.
Measured round-trip error on a Tribes 2 texgen is ~6e-8, i.e. float32 noise.
"""

import math

import bpy
from mathutils import Matrix, Vector

from ..dtslib import (
    PRIM_INDEXED,
    PRIM_MATERIAL_MASK,
    PRIM_TRIANGLES,
    Decal,
    DecalMeshData,
    Mesh,
    Primitive,
    Shape,
)
from .materials import emission_of_add_shader, fade_emission, remember_blend_state

PROJECTOR_PREFIX = "decal_"
# A decal is off when its state is negative (tsShapeInstance.cc: `if (decalMesh
# && frame>=0)`).  Most Tribes 2 decals rest at -1 and a Damage sequence
# switches them on, but 357 of the corpus's 2194 rest at 0 — a wreck shows its
# damage from the start, and 15 shapes carry both kinds at once.  The state
# rides on the armature in the bones' own slot, exactly like object visibility,
# so one NLA strip drives pose and damage together.
DECAL_PREFIX = "dts_decal_"
# the engine draws decals with a polygon offset; Blender has no per-object
# depth bias, so a Displace modifier lifts the preview off the target instead.
# It is a modifier, not an edit, so the mesh data export reads is untouched.
DECAL_LIFT = 0.002


def _depth_row(r0: Vector, r1: Vector) -> Vector:
    """A third row that keeps the projection matrix well conditioned.

    Any row independent of the first two encodes the same projection, but the
    export path inverts this matrix after a float32 round trip through the
    empty, so matching its magnitude to the others buys about half a digit.
    """
    n = r0.cross(r1)
    if n.length < 1e-12:  # S and T parallel: a degenerate texgen
        n = Vector((0.0, 0.0, 1.0))
    return n.normalized() * math.sqrt(r0.length * r1.length)


def texgen_to_projector(s, t, obj_matrix: Matrix) -> Matrix:
    """DTS texgen planes -> the world matrix of a UV Project projector."""
    r0 = Vector((2.0 * s[0], 2.0 * s[1], 2.0 * s[2]))
    r1 = Vector((-2.0 * t[0], -2.0 * t[1], -2.0 * t[2]))
    n = _depth_row(r0, r1)
    P = Matrix((
        (r0.x, r0.y, r0.z, 2.0 * s[3] - 1.0),
        (r1.x, r1.y, r1.z, 1.0 - 2.0 * t[3]),
        (n.x, n.y, n.z, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    return obj_matrix @ P.inverted()


def projector_to_texgen(projector_matrix: Matrix, obj_matrix: Matrix):
    """The inverse of :func:`texgen_to_projector`; returns ``(S, T)``."""
    P = projector_matrix.inverted() @ obj_matrix
    s = (P[0][0] / 2.0, P[0][1] / 2.0, P[0][2] / 2.0, (P[0][3] + 1.0) / 2.0)
    t = (-P[1][0] / 2.0, -P[1][1] / 2.0, -P[1][2] / 2.0, (1.0 - P[1][3]) / 2.0)
    return s, t


def decal_prop(index: int, decal_name: str) -> str:
    """Keyed by index, not name.

    A decal's name is not unique within a shape: 18 of the 54 Tribes 2 shapes
    with decals reuse one, and turret_tank_base gives all fourteen of its
    decals the same name.  The index is the file's own identity for a decal —
    decal_states[i] and the sequences' decal_matters bits both use it — so it
    is what the property, the projector and the export grouping key on.  The
    name rides along so the property is still readable.
    """
    return f"{DECAL_PREFIX}{index:03d}_{decal_name}"


def decal_path(index: int, decal_name: str) -> str:
    return f'["{decal_prop(index, decal_name)}"]'


def decal_names_by_index(shape: Shape) -> dict:
    return {i: shape.name(d.raw[0]) for i, d in enumerate(shape.decals)}


def apply_default_states(arm_obj, shape: Shape) -> None:
    """Seed each decal's state from the shape's own defaults.

    Stored as a float even though a state is an integer frame index, to match
    the visibility properties; export rounds back.
    """
    for i, name in decal_names_by_index(shape).items():
        state = shape.decal_states[i] if i < len(shape.decal_states) else -1
        arm_obj[decal_prop(i, name)] = float(state)


def decal_path_of(index: int, name: str) -> str:
    return decal_path(index, name)


def parse_decal_path(data_path: str) -> int | None:
    """The decal index an fcurve drives, or None if it drives something else."""
    if not (data_path.startswith(f'["{DECAL_PREFIX}') and data_path.endswith('"]')):
        return None
    rest = data_path[2 + len(DECAL_PREFIX) : -2]
    number, _, _name = rest.partition("_")
    return int(number) if number.isdigit() else None


def read_decal_tracks(action, n: int) -> dict[int, list]:
    """Sample the decal-state fcurves at frames 1..n.

    These curves are the authored form; nothing else stores the states.
    """
    from .sequences import _iter_fcurves

    tracks = {}
    for fcurve in _iter_fcurves(action):
        index = parse_decal_path(fcurve.data_path)
        if index is not None:
            tracks[index] = [fcurve.evaluate(kf + 1) for kf in range(n)]
    return tracks


def write_decal_fcurves(bag, action, arm_obj, tracks: dict, names_by_index: dict) -> set:
    """Keyframe the sequence's decal-state tracks onto the armature.

    Torque switches a decal on and off; there is nothing between frames, so
    the keys are CONSTANT — linear interpolation would fade a decal in through
    fractional states that mean nothing.
    """
    written = set()
    for key, track in tracks.items():
        index = int(key)
        name = names_by_index.get(index)
        if name is None or not track:
            continue
        if decal_prop(index, name) not in arm_obj.keys():
            arm_obj[decal_prop(index, name)] = float(track[0])
        fc = bag.fcurves.new(data_path=decal_path(index, name), index=0)
        fc.keyframe_points.add(len(track))
        for i, v in enumerate(track):
            kp = fc.keyframe_points[i]
            kp.co = (i + 1, float(v))
            kp.interpolation = "CONSTANT"
        fc.update()
        written.add(name)
    return written


def wire_decal_drivers(arm_obj, warnings=None) -> int:
    """Fade a decal out when its state goes negative.

    This drives object alpha rather than ``hide_viewport``.  The hide flags
    restructure the dependency graph, so Blender only applies them when the
    graph is rebuilt — setting the property by hand works, but an NLA strip
    animating it does not, which is exactly how Damage plays.  Alpha is a plain
    evaluated value and tracks the strip frame by frame.
    """
    from .visibility import refresh_driver_relations

    wired, touched = 0, []
    for obj in bpy.data.objects:
        name = obj.get("dts_decal_name")
        if obj.type != "MESH" or name is None:
            continue
        touched.append(obj)
        index = int(obj.get("dts_decal_index", -1))
        if decal_prop(index, name) not in arm_obj.keys():
            continue
        existing = obj.animation_data.drivers if obj.animation_data else []
        if any(d.data_path == "color" and d.array_index == 3 for d in existing):
            continue  # already wired; re-import must not stack drivers
        drv = obj.driver_add("color", 3).driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = "state"
        var.type = "SINGLE_PROP"
        var.targets[0].id = arm_obj
        var.targets[0].data_path = decal_path(index, name)
        # a bare comparison, not "1.0 if state >= 0 else 0.0": Blender evaluates
        # simple expressions natively and falls back to full Python for anything
        # else, which silently yields 0.0 unless the user has enabled auto-run
        drv.expression = "state >= 0"
        wired += 1
    refresh_driver_relations(touched)
    return wired


def _target_verts(mesh):
    """The vertex array the engine's texgen reads, in the same truncation the
    mesh importer uses so decal indices line up with the target object."""
    verts = mesh.verts or mesh.initial_verts
    if mesh.num_frames > 1 and mesh.verts_per_frame > 0:
        verts = verts[: mesh.verts_per_frame]
    return verts


def _build_decal_mesh(name, tris, verts_src, bmat):
    """The covered faces, as their own object.

    A decal shares its target's vertices, so this is a copy of the subset the
    indices name — which is also what makes it authorable: deleting a face
    here removes it from the exported decal.
    """
    used = sorted({i for tri in tris for i in tri[:3]})
    remap = {old: new for new, old in enumerate(used)}
    bm = bpy.data.meshes.new(name)
    bm.from_pydata(
        [Vector(verts_src[i]) for i in used],
        [],
        [(remap[a], remap[b], remap[c]) for a, b, c, _ in tris],
    )
    bm.uv_layers.new(name="UVMap")
    if bmat is not None:
        bm.materials.append(bmat)
    bm.validate()
    bm.update()
    return bpy.data.objects.new(name, bm)


def wire_decal_material(mat) -> bool:
    """Make the decal material blend, and let it read the object's alpha.

    The engine always draws decals with SRC_ALPHA/ONE_MINUS_SRC_ALPHA whatever
    the material's own flags say (``initDecalMaterials``), so the texture alpha
    has to reach the shader.  Object alpha is multiplied in on top, which is
    how a decal switched off by its state disappears — see
    :func:`wire_decal_drivers`.
    """
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return False
    # the blend forced below must not read back as MAT_TRANSLUCENT on export
    remember_blend_state(mat)
    emission = emission_of_add_shader(nt)
    if emission is not None:
        # additive/subtractive decal: fade the glow, there is no Principled
        if not any(n.type == "OBJECT_INFO" for n in nt.nodes):
            fade_emission(nt, emission)
        _set_decal_blend(mat)
        return True
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False
    alpha_in = bsdf.inputs["Alpha"]

    if not alpha_in.is_linked:
        base = bsdf.inputs["Base Color"]
        tex = base.links[0].from_node if base.is_linked else None
        if tex is not None and tex.type == "TEX_IMAGE" and "Alpha" in tex.outputs:
            nt.links.new(tex.outputs["Alpha"], alpha_in)

    if not any(n.type == "OBJECT_INFO" for n in nt.nodes):
        info = nt.nodes.new("ShaderNodeObjectInfo")
        info.location = (bsdf.location.x - 600, bsdf.location.y - 300)
        if "Alpha" in info.outputs:
            if alpha_in.is_linked:
                source = alpha_in.links[0].from_socket
                mul = nt.nodes.new("ShaderNodeMath")
                mul.operation = "MULTIPLY"
                mul.location = (bsdf.location.x - 300, bsdf.location.y - 300)
                nt.links.new(source, mul.inputs[0])
                nt.links.new(info.outputs["Alpha"], mul.inputs[1])
                nt.links.new(mul.outputs[0], alpha_in)
            else:
                nt.links.new(info.outputs["Alpha"], alpha_in)
        else:
            nt.nodes.remove(info)

    _set_decal_blend(mat)
    return True


def _set_decal_blend(mat) -> None:
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    mat.use_backface_culling = True  # the engine draws decals one-sided


def _vertex_lookup(verts, places: int = 5) -> dict:
    """Position -> index into the target mesh's vertex array.

    A decal borrows its target's vertices, so the copies made on import still
    sit exactly where they came from.  Meshes split vertices at UV seams, so a
    position can name several — any of them renders the same triangle, and the
    texgen reads position alone, so the collision does not matter.

    Fallback only.  Prefer the Blender-space table the mesh exporter hands over
    (see ``blender_lookup`` below): matching against re-derived DTS positions
    compares the target's recomputed coordinates against the decal's untouched
    copies of the originals, and the two drift apart in the fifth decimal.
    """
    table = {}
    for i, v in enumerate(verts):
        table.setdefault((round(v[0], places), round(v[1], places), round(v[2], places)), i)
    return table


def _decal_mesh_from_blender(
    bobj, target_mesh, s, t, material_index, warnings, blender_lookup=None
) -> Mesh | None:
    """Rebuild one TSDecalMesh: the covered triangles, as indices into the
    target, plus the projection planes read back off the empty.

    ``blender_lookup`` maps the *target's* Blender-local vertex positions to the
    DTS indices they were exported as.  A decal mesh holds bit-identical copies
    of those coordinates, so matching there is exact; matching against the
    exported DTS positions is not, because those went through the object's
    transform on the way out and came back a few ULPs different.
    """
    me = bobj.data
    me.calc_loop_triangles()
    lookup = blender_lookup or _vertex_lookup(target_mesh.verts or target_mesh.initial_verts)

    indices, missed = [], 0
    for tri in me.loop_triangles:
        mapped = []
        for li in reversed(tri.loops):  # DTS winding is the reverse of Blender's
            co = me.vertices[me.loops[li].vertex_index].co
            key = (round(co.x, 5), round(co.y, 5), round(co.z, 5))
            idx = lookup.get(key)
            if idx is None:
                break
            mapped.append(idx)
        if len(mapped) == 3:
            indices.extend(mapped)
        else:
            missed += 1

    if missed:
        warnings.append(
            f"decal {bobj.name!r}: {missed} face(s) do not sit on the target mesh "
            f"and were dropped — a decal can only cover its target's own geometry"
        )
    if not indices:
        return None

    dd = DecalMeshData()
    dd.indices = indices
    dd.primitives = [
        Primitive(0, len(indices), PRIM_TRIANGLES | PRIM_INDEXED | (material_index & PRIM_MATERIAL_MASK))
    ]
    dd.start_primitive = [0]
    dd.texgen_s = [tuple(s)]
    dd.texgen_t = [tuple(t)]
    dd.material_index = PRIM_INDEXED | (material_index & PRIM_MATERIAL_MASK)

    mesh = Mesh(mesh_type=2)  # DECAL_MESH
    mesh.decal_data = dd
    return mesh


def blender_lookup_of(bobj, dts_index_of_bvert, places: int = 5) -> dict:
    """Target's Blender-local vertex positions -> the DTS indices they became.

    Built while the target mesh is exported, because that is the only place
    both halves of the mapping exist at once.  Keyed by the target's Blender
    object name, which is what ``dts_decal_target`` records.
    """
    table = {}
    for bvert, dts_index in dts_index_of_bvert.items():
        co = bobj.data.vertices[bvert].co
        table.setdefault(
            (round(co.x, places), round(co.y, places), round(co.z, places)), dts_index
        )
    return table


def build_decals(
    shape: Shape, arm_obj, object_index_by_name, material_index_of, warnings,
    target_lookups=None,
) -> dict:
    """Recompute the decal table from the projector empties and their meshes.

    Nothing is replayed from a stored payload: the texgen planes come back out
    of each empty's matrix and the indices are re-derived from the faces, so
    moving a projector or deleting a face changes the exported file.
    Returns the old decal index -> new index map the sequence exporter needs.
    """
    # grouped by index, never by name — turret_tank_base names all fourteen of
    # its decals the same, and grouping by name would export one
    by_index = {}
    projectors = {}
    for obj in bpy.data.objects:
        name = obj.get("dts_decal_name")
        if name is None:
            continue
        index = int(obj.get("dts_decal_index", -1))
        if obj.type == "EMPTY":
            projectors[index] = obj
        elif obj.type == "MESH":
            by_index.setdefault(index, []).append(obj)
    if not by_index:
        return {}

    def owner_subshape(obj_index: int) -> int:
        for s in range(len(shape.sub_shape_first_object)):
            first = shape.sub_shape_first_object[s]
            if first <= obj_index < first + shape.sub_shape_num_objects[s]:
                return s
        return 0

    placed = []
    for index, meshes in by_index.items():
        name = str(meshes[0].get("dts_decal_name", ""))
        owner = meshes[0].get("dts_decal_object", "")
        obj_index = object_index_by_name.get(str(owner))
        if obj_index is None:
            warnings.append(
                f"decal {name!r}: owner object {owner!r} was not exported; decal dropped"
            )
            continue
        if index not in projectors:
            warnings.append(
                f"decal {name!r} (#{index}): no projector empty; decal dropped"
            )
            continue
        placed.append((owner_subshape(obj_index), index, name, obj_index, meshes))
    placed.sort(key=lambda p: (p[0], p[1]))

    # subShapeFirstDecal ranges have to stay contiguous per subshape
    shape.sub_shape_first_decal = [0] * len(shape.sub_shape_first_object)
    shape.sub_shape_num_decals = [0] * len(shape.sub_shape_first_object)
    next_first = 0
    for s in range(len(shape.sub_shape_first_object)):
        shape.sub_shape_first_decal[s] = next_first
        n = sum(1 for p in placed if p[0] == s)
        shape.sub_shape_num_decals[s] = n
        next_first += n

    decal_index_map = {}
    for _sub, index, name, obj_index, meshes in placed:
        owner_obj = shape.objects[obj_index]
        by_slot = {int(m.get("dts_decal_slot", 0)): m for m in meshes}
        projector = projectors[index]

        start = len(shape.meshes)
        for j in range(owner_obj.num_meshes):
            bobj = by_slot.get(j)
            target = (
                shape.meshes[owner_obj.start_mesh_index + j]
                if owner_obj.start_mesh_index + j < len(shape.meshes)
                else None
            )
            if bobj is None or target is None:
                shape.meshes.append(None)
                continue
            s, t = projector_to_texgen(projector.matrix_world, bobj.matrix_world)
            bmat = bobj.material_slots[0].material if bobj.material_slots else None
            mat_index = material_index_of(bmat) if bmat is not None else 0
            shape.meshes.append(
                _decal_mesh_from_blender(
                    bobj, target, s, t, max(mat_index, 0), warnings,
                    blender_lookup=(target_lookups or {}).get(
                        str(bobj.get("dts_decal_target", ""))
                    ),
                )
            )

        decal_index_map[index] = len(shape.decals)
        shape.decals.append(
            Decal((shape.add_name(str(name)), owner_obj.num_meshes, start, obj_index, -1))
        )
        state = arm_obj.get(decal_prop(index, name), -1.0)
        shape.decal_states.append(int(round(float(state))))
    return decal_index_map


def import_decals(
    shape: Shape,
    arm_obj,
    bmats,
    targets: dict,
    collection_of,
    parent_like,
    warnings,
) -> tuple[int, int]:
    """Build a projector empty per decal and a mesh per (decal, detail level).

    *targets* maps ``(object_index, mesh_slot) -> (blender object, dtslib
    mesh)``; *collection_of* returns the detail collection a slot belongs in;
    *parent_like* re-parents a new object the way the target was parented.
    Returns ``(decals imported, meshes built)``.
    """
    from .shape_to_blender import decode_primitives

    if not shape.decals:
        return 0, 0

    coll = bpy.data.collections.new(f"{arm_obj.name}.decals")
    bpy.context.scene.collection.children.link(coll)

    wired_materials = set()
    n_decals = n_meshes = 0

    for decal_index, decal in enumerate(shape.decals):
        name_index, num_meshes, start, obj_index, _sibling = decal.raw
        decal_name = shape.name(name_index)
        owner = (
            shape.name(shape.objects[obj_index].name_index)
            if 0 <= obj_index < len(shape.objects)
            else ""
        )

        projector = None
        built_any = False
        for j in range(num_meshes):
            src = shape.meshes[start + j] if start + j < len(shape.meshes) else None
            if src is None or src.decal_data is None:
                continue
            dd = src.decal_data
            entry = targets.get((obj_index, j))
            if entry is None:
                continue
            target_obj, target_mesh = entry
            if not dd.texgen_s or not dd.texgen_t or not dd.indices:
                continue
            if len(dd.start_primitive) > 1:
                warnings.append(
                    f"decal {decal_name!r}: {len(dd.start_primitive)} frames; "
                    f"only the first is imported and the rest are lost on export"
                )

            verts_src = _target_verts(target_mesh)
            tris = [
                tri
                for tri in decode_primitives(dd)
                if max(tri[:3]) < len(verts_src)
            ]
            if not tris:
                continue

            mat_index = dd.material_index & PRIM_MATERIAL_MASK
            bmat = bmats[mat_index] if 0 <= mat_index < len(bmats) else None
            if bmat is not None and bmat.name not in wired_materials:
                wired_materials.add(bmat.name)
                wire_decal_material(bmat)

            size = target_obj.get("dts_detail_size", j)
            bobj = _build_decal_mesh(f"{decal_name}{size}", tris, verts_src, bmat)
            coll.objects.link(bobj)
            parent_like(bobj, target_obj)

            bobj["dts_decal_name"] = decal_name
            bobj["dts_decal_index"] = decal_index
            bobj["dts_decal_object"] = owner
            bobj["dts_decal_slot"] = j
            # the target by Blender identity, not by (object name, detail slot):
            # export rebuilds the detail table and can number the slots
            # differently, and a decal that matches its faces against the wrong
            # LOD's vertices covers nothing at all
            bobj["dts_decal_target"] = target_obj.name
            bobj["dts_detail_size"] = size
            bobj["dts_subshape"] = target_obj.get("dts_subshape", 0)

            # one projector per decal: the texgen is shared across detail
            # levels, and every LOD of one object hangs off the same node, so
            # a single empty is correct for all of them
            if projector is None:
                projector = bpy.data.objects.new(f"{PROJECTOR_PREFIX}{decal_name}", None)
                projector.empty_display_type = "IMAGE"
                projector.empty_display_size = 0.15
                projector.matrix_world = texgen_to_projector(
                    dd.texgen_s[0], dd.texgen_t[0], bobj.matrix_world
                )
                coll.objects.link(projector)
                parent_like(projector, target_obj, keep_transform=True)
                projector["dts_decal_name"] = decal_name
                projector["dts_decal_object"] = owner
                projector["dts_decal_index"] = decal_index
                if decal_index < len(shape.decal_states):
                    projector["dts_decal_state"] = shape.decal_states[decal_index]

            mod = bobj.modifiers.new("Decal Projection", "UV_PROJECT")
            mod.uv_layer = "UVMap"
            mod.projector_count = 1
            mod.projectors[0].object = projector
            mod.aspect_x = mod.aspect_y = mod.scale_x = mod.scale_y = 1.0

            lift = bobj.modifiers.new("Decal Lift", "DISPLACE")
            lift.strength = DECAL_LIFT
            lift.mid_level = 0.0

            target_coll = collection_of(target_obj)
            if target_coll is not None:
                target_coll.objects.link(bobj)
                coll.objects.unlink(bobj)

            n_meshes += 1
            built_any = True

        if built_any:
            n_decals += 1

    if not n_meshes:
        bpy.data.collections.remove(coll)
    return n_decals, n_meshes


# ----------------------------------------------------------------------
# authoring: build a decal the way the importer would have
# ----------------------------------------------------------------------


def next_decal_index() -> int:
    """A free decal index for the scene.

    The index, not the name, is a decal's identity -- decal_states[i] and the
    sequences' decal_matters bits both use it, and turret_tank_base gives all
    fourteen of its decals the same name.
    """
    used = {
        int(obj.get("dts_decal_index", -1))
        for obj in bpy.data.objects
        if "dts_decal_index" in obj
    }
    return max(used, default=-1) + 1


def selected_face_triangles(bobj, faces=None):
    """(a, b, c, 0) triangles for the chosen faces, indexing the mesh's verts.

    A decal is a subset of its target's faces, so this is what a user picks in
    edit mode.  ``faces`` overrides the selection, for projecting the same
    decal onto another detail level.
    """
    mesh = bobj.data
    mesh.calc_loop_triangles()
    wanted = (
        {p.index for p in mesh.polygons if p.select} if faces is None else set(faces)
    )
    return [
        (tri.vertices[0], tri.vertices[1], tri.vertices[2], 0)
        for tri in mesh.loop_triangles
        if tri.polygon_index in wanted
    ]


def projector_for(bobj, tris) -> Matrix:
    """A world matrix that projects square-on onto the given triangles.

    Blender's UV Project maps local (+-0.5, +-0.5) to UV 0..1, so the
    projector is scaled to twice the footprint's half-extent and aimed down
    the faces' average normal.
    """
    mesh = bobj.data
    used = sorted({i for tri in tris for i in tri[:3]})
    points = [bobj.matrix_world @ mesh.vertices[i].co for i in used]
    centre = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)

    normal = Vector((0.0, 0.0, 0.0))
    by_index = {p.index: p for p in mesh.polygons}
    seen = set()
    for tri in tris:
        key = tuple(sorted(tri[:3]))
        if key in seen:
            continue
        seen.add(key)
        a, b, c = (bobj.matrix_world @ mesh.vertices[i].co for i in tri[:3])
        normal += (b - a).cross(c - a)
    del by_index
    if normal.length < 1e-9:
        normal = Vector((0.0, 0.0, 1.0))
    normal.normalize()

    # any two axes orthogonal to the normal will do; pick the more stable one
    helper = Vector((0.0, 0.0, 1.0)) if abs(normal.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    x_axis = helper.cross(normal)
    x_axis.normalize()
    y_axis = normal.cross(x_axis)

    half = max(
        (max(abs((p - centre).dot(axis)) for p in points) for axis in (x_axis, y_axis)),
        default=0.5,
    ) or 0.5
    scale = 2.0 * half

    matrix = Matrix.Identity(4)
    for column, axis in enumerate((x_axis, y_axis, normal)):
        for row in range(3):
            matrix[row][column] = axis[row] * scale
    matrix.translation = centre + normal * half
    return matrix


def faces_under_projector(bobj, projector_matrix: Matrix, depth: float = 4.0):
    """Face indices of ``bobj`` that fall inside the projector's square.

    How a decal reaches the other detail levels of its object: the same
    projection, applied to whatever geometry that level happens to have.
    """
    to_projector = projector_matrix.inverted() @ bobj.matrix_world
    hits = []
    for polygon in bobj.data.polygons:
        local = to_projector @ polygon.center
        if abs(local.x) <= 0.5 and abs(local.y) <= 0.5 and abs(local.z) <= depth:
            hits.append(polygon.index)
    return hits


def create_decal(arm_obj, target_obj, *, name, material=None, index=None,
                 all_details=True, collection_of=None, parent_like=None):
    """Build a decal over the selected faces of ``target_obj``.

    The inverse of import_decals, and deliberately the same shape of data: a
    copy of the covered faces plus one projector empty, wired with the
    properties the exporter reads.  Returns (index, [decal objects]).
    """
    from .naming import dts_object_and_size

    if parent_like is None:
        from .shape_to_blender import _parent_like as parent_like
    if collection_of is None:
        def collection_of(obj):
            return obj.users_collection[0] if obj.users_collection else None

    index = next_decal_index() if index is None else index
    owner, _size = dts_object_and_size(target_obj)

    tris = selected_face_triangles(target_obj)
    if not tris:
        raise ValueError(f"{target_obj.name!r} has no selected faces to cover")

    projector_matrix = projector_for(target_obj, tris)

    # every detail level of the same DTS object, so the decal does not vanish
    # as the engine drops LOD -- which is what every shipped decal does
    targets = [(target_obj, tris)]
    if all_details:
        for other in bpy.data.objects:
            if other is target_obj or other.type != "MESH":
                continue
            if dts_object_and_size(other)[0] != owner:
                continue
            faces = faces_under_projector(other, projector_matrix)
            if faces:
                targets.append((other, selected_face_triangles(other, faces)))

    if material is not None:
        wire_decal_material(material)

    made = []
    projector = None
    for slot, (host, host_tris) in enumerate(targets):
        _owner, size = dts_object_and_size(host)
        verts = [tuple(v.co) for v in host.data.vertices]
        bobj = _build_decal_mesh(f"{name}{size}", host_tris, verts, material)
        coll = collection_of(host)
        if coll is not None:
            coll.objects.link(bobj)
        parent_like(bobj, host)

        bobj["dts_decal_name"] = name
        bobj["dts_decal_index"] = index
        bobj["dts_decal_object"] = owner
        bobj["dts_decal_slot"] = slot
        bobj["dts_decal_target"] = host.name
        bobj["dts_detail_size"] = size
        bobj["dts_subshape"] = host.get("dts_subshape", 0)

        if projector is None:
            projector = bpy.data.objects.new(f"{PROJECTOR_PREFIX}{name}", None)
            projector.empty_display_type = "IMAGE"
            projector.empty_display_size = 0.15
            projector.matrix_world = projector_matrix
            if coll is not None:
                coll.objects.link(projector)
            parent_like(projector, host, keep_transform=True)
            projector["dts_decal_name"] = name
            projector["dts_decal_object"] = owner
            projector["dts_decal_index"] = index

        mod = bobj.modifiers.new("Decal Projection", "UV_PROJECT")
        mod.uv_layer = "UVMap"
        mod.projector_count = 1
        mod.projectors[0].object = projector
        mod.aspect_x = mod.aspect_y = mod.scale_x = mod.scale_y = 1.0

        lift = bobj.modifiers.new("Decal Lift", "DISPLACE")
        lift.strength = DECAL_LIFT
        lift.mid_level = 0.0
        made.append(bobj)

    # the state the decal rests at.  0 is on; -1 would be off, which is what a
    # damage decal wants, but a decal you just made should be visible.
    arm_obj[decal_prop(index, name)] = 0.0
    return index, made
