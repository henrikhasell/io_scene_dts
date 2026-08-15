"""Decals as projected UVs.

A ``TSDecalMesh`` has no geometry of its own.  It stores a subset of its
*target* mesh's indices, one material, and two ``Point4F`` planes, and the
engine computes every UV as a dot product against those planes
(``tsDecal.cc``)::

    tv.x = v->x*s.x + v->y*s.y + v->z*s.z + s.w;
    tv.y = v->x*t.x + v->y*t.y + v->z*t.z + t.w;

That is an affine planar projection, so a decal *is* an empty: it imports as
one, exports by reading the planes back out of its matrix, and moving it moves
the decal in the exported file.  Nothing is frozen and there is no decal mesh
object — a shape with 24 decals across 6 detail levels used to land 144 mesh
objects in the outliner, none of which was geometry anybody should edit.

The cost is the index list.  A TSDecalMesh names the target triangles it
covers, and an empty does not, so export recomputes them from the projector
volume (:func:`covered_faces`).  That does not reproduce what the shipped files
store.  The original exporter decided coverage with a conjunction this does
not reproduce — a unit-square overlap test in *texture* space, a per-vertex
projection test against the decal mesh's own faces, and a filter bitmap that
only ever existed in the Max scene (``ShapeMimic.cc:5762-5764``).  What is
reproduced is the facing gate, which is the one part that needs nothing but
the shape.  Import narrows the gap by fitting rule, depth and angle per decal
against the list the file does have (:func:`fit_coverage`); on bioderm_light
that returns the covered triangles at recall 0.444 and precision 0.589, and
0.4% of corpus slots come back identical.  Re-deriving coverage is still a
deliberate, documented loss; see UNSUPPORTED.md and DECALS.md.

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
    STANDARD_MESH,
    Decal,
    DecalMeshData,
    Mesh,
    Object,
    ObjectState,
    Primitive,
    Shape,
)
from ..dtslib.types import MAT_ADDITIVE, MAT_SUBTRACTIVE
from ..props.decal import SCHEMA_VERSION as DECAL_SCHEMA_VERSION
from .materials import diffuse_image_node

PROJECTOR_PREFIX = "decal_"
# A decal is off when its state is negative (tsShapeInstance.cc: `if (decalMesh
# && frame>=0)`).  Most Tribes 2 decals rest at -1 and a Damage sequence
# switches them on, but 357 of the corpus's 2194 rest at 0 — a wreck shows its
# damage from the start, and 15 shapes carry both kinds at once.  The state
# rides on the armature in the bones' own slot, exactly like object visibility,
# so one NLA strip drives pose and damage together.
DECAL_PREFIX = "dts_decal_"
# The face-domain attribute recording which faces a decal covers, per target
# mesh.  A derived cache, written by write_coverage() and never authored:
# export calls covered_faces() itself rather than reading it back.  It makes
# the exported face set inspectable; it is not what the shader masks by (see
# write_coverage for why not).
COVERAGE_ATTRIBUTE = "dts_decal_%03d"
# The pass index the preview's object gate compares against, remembered on the
# target so the assignment is idempotent and inspectable.  See decal_host_id.
DECAL_HOST_PROP = "dts_decal_host"


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


def decal_objects():
    """Every projector empty in the scene, in index order.

    A decal is an empty and nothing else, so this is the whole population --
    import, export, preview and the UI all start here.
    """
    found = [
        obj
        for obj in bpy.data.objects
        if obj.type == "EMPTY" and getattr(obj, "dts_decal", None) is not None
        and obj.dts_decal.is_dts
    ]
    found.sort(key=lambda o: o.dts_decal.index)
    return found


# ----------------------------------------------------------------------
# coverage: the one predicate export and preview both go through
# ----------------------------------------------------------------------


def covered_faces(
    target_obj, projector_matrix: Matrix, *, depth=4.0, rule="CENTRE", max_angle=90.0
):
    """Face indices of ``target_obj`` the projector covers.

    This *is* the decal's index list.  The file stores one and an empty cannot,
    so it is recomputed here and both consumers -- the exporter and the shader
    preview mask -- call this same function, which is the only reason the two
    can be relied on to agree.

    ``depth`` is in multiples of the projector's half-width, so it scales with
    the projector rather than with the model.

    ``max_angle`` is the facing gate, in degrees: a face whose normal turns
    further than this from the projector's axis is not covered.  It is the
    original exporter's rule -- ``mDot(normal, n) < minCos`` where ``minCos =
    cos(DECAL::MAX_ANGLE)``, defaulting to 90 degrees, i.e. reject anything
    pointing away (``ShapeMimic.cc:5546-5548``, ``:6043``).  Measured over the
    corpus it raises precision from 0.476 to 0.516 for CENTRE at depth 4, and
    costs 0.22 recall doing it -- see ``props/decal.py`` for why that trade is
    still worth making.  Its plainest justification is not statistical: without
    it a decal on a chest also lands on the back.

    See ``props/decal.py`` for what the rules recall; none of them is exact.
    """
    to_projector = projector_matrix.inverted() @ target_obj.matrix_world
    mesh = target_obj.data
    # the projector looks along its own +Z, and normals need the inverse
    # transpose rather than the matrix itself once an object is scaled
    axis = (projector_matrix.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    normal_matrix = target_obj.matrix_world.to_3x3().inverted_safe().transposed()
    # The tolerance leans toward rejecting, and that direction is the whole
    # point: cos(pi/2) is 6.1e-17 rather than 0, so a face exactly edge-on to
    # the projector -- the sides of any axis-aligned box, which is most of this
    # art -- would be kept or dropped on rounding alone.  Leaning the other way
    # decals the sides of a crate from a projector aimed at its lid.
    # 180 means no gate at all, and must not reject a face pointing exactly
    # away, so it is taken out of the comparison entirely.
    min_cos = (
        -2.0 if max_angle >= 180.0 else math.cos(math.radians(max_angle)) + 1e-6
    )
    hits = []

    def in_square(p):
        return abs(p.x) <= 0.5 and abs(p.y) <= 0.5

    for polygon in mesh.polygons:
        centre = to_projector @ polygon.center
        # depth is measured at the centre whichever rule is in force: which
        # corners fall in the square and how far the face has turned are
        # separate questions, and max_angle is the one that answers the second
        if abs(centre.z) > depth:
            continue
        if (normal_matrix @ polygon.normal).normalized().dot(axis) < min_cos:
            continue
        if rule == "CENTRE":
            inside = in_square(centre)
        else:
            flags = [
                in_square(to_projector @ mesh.vertices[v].co) for v in polygon.vertices
            ]
            inside = any(flags) if rule == "ANY" else all(flags)
        if inside:
            hits.append(polygon.index)
    return hits


#: what :func:`fit_coverage` searches.  Depths are multiples of the projector's
#: half-width; the rules are the three :data:`props.decal.COVERAGE_RULES`.
#: The angles mirror DECAL::MAX_ANGLE being a per-decal property in the
#: original exporter rather than a constant -- 90 was only its default.
FIT_RULES = ("CENTRE", "ANY", "ALL")
FIT_DEPTHS = (0.25, 0.5, 1.0, 2.0, 4.0)
FIT_ANGLES = (90.0, 120.0, 180.0)


def fit_coverage(target_obj, projector_matrix: Matrix, wanted) -> tuple:
    """Pick the (rule, depth, max_angle) that best reproduces a known face set.

    Import has something authoring never does: the list of faces the file
    actually covers.  Coverage is re-derived on export, so rather than impose
    one rule on every imported decal, this fits the rule to each decal --
    which is worth about 0.42 recall against 0.23 for a fixed default.

    Scored by Jaccard so a rule cannot win by covering everything.
    """
    wanted = set(wanted)
    if not wanted:
        return ("CENTRE", 4.0, 90.0)
    best, best_score = ("CENTRE", 4.0, 90.0), -1.0
    for rule in FIT_RULES:
        for depth in FIT_DEPTHS:
            for angle in FIT_ANGLES:
                got = set(covered_faces(
                    target_obj, projector_matrix,
                    depth=depth, rule=rule, max_angle=angle,
                ))
                union = len(got | wanted)
                score = len(got & wanted) / union if union else 0.0
                if score > best_score:
                    best, best_score = (rule, depth, angle), score
    return best


def coverage_attribute(index: int) -> str:
    return COVERAGE_ATTRIBUTE % index


def write_coverage(target_obj, index: int, faces) -> str:
    """Record a coverage set as a face-domain attribute.

    Derived, not authored: export recomputes coverage rather than reading this
    back.  It is what makes the exported face set *inspectable* in Blender --
    the spreadsheet, geometry nodes, a selection operator all read it.

    It is deliberately not what the shader masks by.  That needs one Attribute
    node per decal, and EEVEE caps how many attributes a material may use;
    light_male puts all 58 of its decals on the single body material, which
    took it past the limit and rendered the whole body as broken-material
    magenta.  The preview masks by the projector volume instead, which is the
    same test at pixel rather than face granularity.
    """
    name = coverage_attribute(index)
    mesh = target_obj.data
    attr = mesh.attributes.get(name)
    if attr is not None and (attr.domain != "FACE" or attr.data_type != "FLOAT"):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name=name, type="FLOAT", domain="FACE")
    wanted = set(faces)
    for polygon in mesh.polygons:
        attr.data[polygon.index].value = 1.0 if polygon.index in wanted else 0.0
    return name


def decal_host_id(target_obj) -> int:
    """A pass index unique to this object, for the preview's object gate.

    The preview draws in the *target's material*, and a DTS material is one
    Blender datablock shared by every mesh that uses it -- 5999 of the corpus's
    6053 decals sit on a material another DTS object also uses.  The projector
    volume alone therefore does not say which object is being shaded, and a burn
    on a hull drew on the turret too.  Object Info's Object Index does say, and
    it is the only per-object number a shader can read without an Attribute
    node (see write_coverage for why that matters), so the add-on claims
    ``pass_index`` on any mesh a decal targets.

    Idempotent: several decals on one target must land on the same id, and
    rewiring must not renumber.  The ID property records what was assigned, so
    a pass_index the user has since edited by hand is noticed rather than
    silently honoured.  Numbering starts at 1, which keeps the default 0 --
    every object this has never touched -- outside the space entirely.
    """
    existing = int(target_obj.get(DECAL_HOST_PROP, 0))
    if existing > 0 and target_obj.pass_index == existing:
        return existing
    # by name, not identity: bpy hands back a fresh wrapper per read, so ``is``
    # would ask about Python objects rather than about the scene
    used = {o.pass_index for o in bpy.data.objects if o.name != target_obj.name}
    n = 1
    while n in used:
        n += 1
    target_obj.pass_index = n
    target_obj[DECAL_HOST_PROP] = n
    return n


def _host_gate_nodes(index: int):
    """Every COMPARE node carrying a decal's object gate, across all materials."""
    label = _branch_label(index)
    for mat in bpy.data.materials:
        nt = getattr(mat, "node_tree", None)
        if nt is None:
            continue
        for node in nt.nodes:
            if (
                node.label == label
                and node.type == "MATH"
                and node.operation == "COMPARE"
            ):
                yield node


def remove_decal_branch(nt, label: str) -> bool:
    """Unpick one decal branch, closing the surface chain behind it.

    The branches are chained -- each Mix Shader takes the previous surface as
    its first input -- so a branch cannot merely be deleted: whatever its output
    fed has to be reconnected to what fed it, or the chain ends at a dangling
    socket and the material draws black.
    """
    nodes = [n for n in nt.nodes if n.label == label]
    if not nodes:
        return False
    mix = next((n for n in nodes if n.type == "MIX_SHADER"), None)
    if mix is not None:
        upstream = mix.inputs[1].links[0].from_socket if mix.inputs[1].is_linked else None
        downstream = [l.to_socket for l in mix.outputs[0].links]
        for link in list(mix.outputs[0].links):
            nt.links.remove(link)
        if upstream is not None:
            for socket in downstream:
                nt.links.new(upstream, socket)
    for node in nodes:
        nt.nodes.remove(node)
    return True


def _shape_armature_for(decal_obj):
    """The armature whose property a re-wired branch's state driver reads."""
    parent = decal_obj.parent
    if parent is not None and parent.type == "ARMATURE":
        return parent
    return next(
        (o for o in bpy.data.objects
         if o.type == "ARMATURE" and getattr(o.dts_shape, "is_shape", False)),
        None,
    )


def sync_host_gate(decal_obj) -> int:
    """Point a decal's object gate at whatever its target is now.

    Retargeting is a pointer assignment in the panel, and the branch it has to
    agree with lives in a material, so nothing would otherwise tell the shader
    the number changed.  Returns the host id, or 0 if there is no target.
    """
    props = decal_obj.dts_decal
    target = props.target
    if target is None or target.type != "MESH":
        return 0
    host = decal_host_id(target)
    label = _branch_label(props.index)

    # Since private_material_for gives every decal target its own copy of the
    # material, retargeting has to *move* the branch too: left where it was it
    # would go on being compiled into a material the new target does not use,
    # and the decal would simply vanish.
    wanted = _host_material_for(target)
    if wanted is not None and getattr(wanted, "node_tree", None) is not None:
        moved = False
        for mat in bpy.data.materials:
            nt = getattr(mat, "node_tree", None)
            if nt is not None and mat is not wanted:
                moved |= remove_decal_branch(nt, label)
        # Only ever *move* a branch, never create one.  This runs from the
        # target pointer's update callback, and import assigns that pointer
        # before it assigns the decal's own material -- building a branch here
        # would build it with no image, and the target would render as a
        # missing texture while the real wiring silently skipped the label it
        # found already taken.
        if moved and not any(n.label == label for n in wanted.node_tree.nodes):
            wire_decal_branch(wanted, decal_obj, _shape_armature_for(decal_obj))

    for node in _host_gate_nodes(props.index):
        node.inputs[1].default_value = float(host)
    return host


def rebuild_branch(decal_obj) -> bool:
    """Throw one decal's shader branch away and build it again.

    ``wire_decal_branch`` refuses to touch a label it already finds, which is
    what stops a re-import stacking branches -- and which also means a branch
    built by an older version of this add-on stays exactly as it was built.  A
    scene saved before decals were previewed *lit* keeps its unlit Emission
    forever, and so does one whose decal material has been changed since.

    Removing the branch first is what makes the rebuild possible; the removal
    closes the surface chain behind it, so nothing is left dangling if the
    rewire then declines (a decal with no target, say).
    """
    props = decal_obj.dts_decal
    label = _branch_label(props.index)
    rebuilt = False
    for mat in bpy.data.materials:
        nt = getattr(mat, "node_tree", None)
        if nt is not None and any(n.label == label for n in nt.nodes):
            remove_decal_branch(nt, label)
            rebuilt = True
    target_mat = _host_material_for(props.target)
    if target_mat is None:
        return rebuilt
    return wire_decal_branch(target_mat, decal_obj, _shape_armature_for(decal_obj)) or rebuilt


def refresh_coverage(decal_obj) -> int:
    """Recompute one decal's coverage cache from its empty.  Returns the count."""
    props = decal_obj.dts_decal
    target = props.target
    if target is None or target.type != "MESH":
        return 0
    faces = covered_faces(
        target, decal_obj.matrix_world,
        depth=props.depth, rule=props.rule, max_angle=props.max_angle,
    )
    write_coverage(target, props.index, faces)
    # the gate is the other half of the preview, and retargeting moves it
    sync_host_gate(decal_obj)
    return len(faces)


def _target_verts(mesh):
    """The vertex array the engine's texgen reads, in the same truncation the
    mesh importer uses so decal indices line up with the target object."""
    verts = mesh.verts or mesh.initial_verts
    if mesh.num_frames > 1 and mesh.verts_per_frame > 0:
        verts = verts[: mesh.verts_per_frame]
    return verts


def _image_of(mat):
    """The image a decal material projects, if it has one.

    The node feeding Base Color, not merely the first image node: a material
    with a reflectance map has two, and which one comes first in the node list
    is not something to project a decal from.
    """
    node = diffuse_image_node(mat)
    if node is not None:
        return node.image
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return None
    for node in nt.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
    return None


def _decal_shader(nt, mat, colour_out, label, location):
    """The surface a decal branch mixes in: lit, unless the engine says not.

    A decal is ordinary geometry to the engine.  ``TSDecalMesh::render`` hands
    it ``glNormalPointer`` from the *target mesh's* normals and
    ``initDecalMaterials`` sets ``GL_MODULATE`` without ever touching
    ``GL_LIGHTING`` (``engine/ts/tsDecal.cc``), so a decal is shaded exactly
    like the surface it sits on -- with that surface's normals, which is why a
    Principled here and not the host's own shader re-used: same lights, same
    normals, the decal's colour.

    Lighting comes off only where ``TSMesh::setMaterial`` turns it off:
    ``MAT_SELF_ILLUMINATING``, or a material with no lighting to modulate at
    all.  Additive and subtractive are on that list because their graph has no
    Principled to read a base colour from in the first place.

    This used to be an Emission unconditionally, which made every decal preview
    as though it were self-illuminating -- neon on a surface the engine would
    have shaded down into shadow with the rest of the mesh.
    """
    if _decal_is_unlit(mat):
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.label = label
        emit.location = location
        nt.links.new(colour_out, emit.inputs["Color"])
        return emit.outputs["Emission"]

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.label = label
    bsdf.location = location
    # the same roughness material_to_blender gives an imported material: the
    # engine's fixed-function lighting has no gloss term, so anything less
    # would add a highlight the game never draws
    bsdf.inputs["Roughness"].default_value = 1.0
    nt.links.new(colour_out, bsdf.inputs["Base Color"])
    return bsdf.outputs["BSDF"]


def _decal_is_unlit(mat) -> bool:
    """Whether the engine would draw this decal's material with lighting off."""
    if mat is None:
        return False
    if mat.get("dts_self_illuminating"):
        return True
    from .materials import blend_flags_from_material

    return bool(blend_flags_from_material(mat) & (MAT_ADDITIVE | MAT_SUBTRACTIVE))


def _base_colour_of(mat):
    """A decal material's flat colour, for when its texture is missing.

    Half the corpus fixtures ship without their .png files, and a decal whose
    preview silently does not exist is worse than a flat one -- the branch is
    what makes coverage visible at all.
    """
    nt = getattr(mat, "node_tree", None)
    if nt is not None:
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            return tuple(bsdf.inputs["Base Color"].default_value)
    return (0.8, 0.1, 0.1, 1.0)


def _branch_label(index: int) -> str:
    return f"DTS Decal {index:03d}"


def private_material_for(target_obj, mat):
    """Give *target_obj* its own copy of *mat* before a decal is wired into it.

    A decal branch is ~14 nodes and a shader cannot skip one.  The host gate in
    ``wire_decal_branch`` multiplies the wrong object's contribution by zero,
    but the GPU still runs the projection, the texture fetch and the mix for
    every pixel of every mesh the material is on -- the gate decides what you
    *see*, never what is *computed*.  Shared, that scales the wrong way:
    light_male puts one material on 25 meshes and carries 58 decals across 17
    targets, so every mesh paid for all 58 branches to show at most 6.
    Measured on that shape, 12 frames of playback in a MATERIAL viewport:

        branches active   58     12      6      3      0
        fps              3.9   24.0   33.4   39.5   58.4

    Splitting caps a material at the decals that actually target it -- 6 in the
    worst case here, so 3.9 to 33.4 fps against a 58.4 ceiling with no decals.

    The copy keeps the source's ``dts_name``, and the export material list is
    keyed on that name (``blender_to_shape._material_slot_index``), so a split
    material is still one entry in the .dts.  Idempotent: a second decal onto
    the same target finds the copy already made and wires into that.
    """
    if mat is None or target_obj is None:
        return mat
    if mat.get("dts_decal_host") == target_obj.name:
        return mat                      # already this object's private copy
    copy = mat.copy()
    # the name the .dts gets, which is what export dedupes on.  Set explicitly
    # rather than trusting mat.copy() to carry it, so a material authored in
    # Blender -- no dts_name, and a ".001" suffix on the copy -- collapses too.
    copy["dts_name"] = str(mat.get("dts_name") or mat.name)
    copy["dts_decal_host"] = target_obj.name
    for slot in target_obj.material_slots:
        if slot.material is mat:
            slot.material = copy
    return copy


def _host_material_for(target_obj):
    """The target's own material, split off from any it shares.

    Never the *decal's* material: that one is the scorch texture and its own
    entry in the file's material list, and confusing the two both draws the
    wrong image and renumbers the decal on export.
    """
    if target_obj is None:
        return None
    mat = target_obj.active_material
    if mat is None:
        mat = next((s.material for s in target_obj.material_slots if s.material), None)
    return private_material_for(target_obj, mat)


def wire_decal_branch(target_mat, decal_obj, arm_obj=None) -> bool:
    """Project a decal into its *target's* material.

    With no decal mesh there is no second surface to draw over the target, so
    the decal has to composite in the target's own shader.  The coordinates
    come straight off the empty -- Texture Coordinate's Object output is the
    position in that object's local space, which is exactly what the UV Project
    modifier used to compute -- and Mapping shifts local +-0.5 onto UV 0..1 to
    match ``projector_for``'s ``scale = 2.0 * half``.

    The decal is masked to the projector volume: the image is CLIPped so its
    alpha is 0 outside the 0..1 square, and a depth test on the projector's own
    Z closes the box.  That is the per-pixel form of what
    :func:`covered_faces` decides per face, so the preview is close to the
    exported coverage without being identical to it -- see the note in
    :func:`write_coverage`.

    A volume is not a mesh, though, and the branch lives in a material rather
    than on an object, so the box alone drew the decal on every *other* object
    sharing the material as well.  The gate that stops it is an Object Info
    comparison against :func:`decal_host_id` -- chosen because it costs no
    attribute slots, which is the constraint the depth mask is shaped by too.
    """
    nt = getattr(target_mat, "node_tree", None)
    if nt is None:
        return False
    props = decal_obj.dts_decal
    label = _branch_label(props.index)
    if any(n.label == label for n in nt.nodes):
        return True  # already wired; re-import must not stack branches

    output = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None or not output.inputs["Surface"].is_linked:
        return False
    surface = output.inputs["Surface"].links[0].from_socket

    x, y = output.location.x, output.location.y - 400 - 260 * props.index

    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.label = label
    coord.location = (x - 1200, y)
    coord.object = decal_obj

    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.label = label
    mapping.location = (x - 1000, y)
    mapping.inputs["Location"].default_value = (0.5, 0.5, 0.5)
    nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    image = _image_of(props.material)
    if image is not None:
        source = nt.nodes.new("ShaderNodeTexImage")
        source.image = image
        # outside the 0..1 square there is no decal, so it must not tile
        source.extension = "CLIP"
        nt.links.new(mapping.outputs["Vector"], source.inputs["Vector"])
        colour_out, alpha_out = source.outputs["Color"], source.outputs["Alpha"]
    else:
        # no texture on disk: fall back to the material's flat colour, covering
        # the whole square.  Coverage still limits it to the decal's own faces,
        # so this shows where the decal is even with the art missing.
        source = nt.nodes.new("ShaderNodeRGB")
        source.outputs[0].default_value = _base_colour_of(props.material)
        colour_out, alpha_out = source.outputs[0], None
    source.label = label
    source.location = (x - 800, y)

    # Depth mask, computed from the projector coordinates rather than read
    # from the coverage attribute.
    #
    # The attribute would be exact -- it is the very face set export writes --
    # but it costs one Attribute node per decal, and EEVEE caps how many
    # attributes a material may use.  Every one of light_male's 58 decals lands
    # on the single body material, so the attribute form failed to compile and
    # the whole body rendered as the broken-material magenta.  This is the
    # per-pixel approximation of the same test: inside the 0..1 square (already
    # handled by CLIP on the image, which makes alpha 0 outside it) and within
    # the depth window.  It uses no attributes at all, so it does not care how
    # many decals share a material.
    axes = nt.nodes.new("ShaderNodeSeparateXYZ")
    axes.label = label
    axes.location = (x - 1000, y - 300)
    nt.links.new(coord.outputs["Object"], axes.inputs["Vector"])

    depth_abs = nt.nodes.new("ShaderNodeMath")
    depth_abs.label = label
    depth_abs.operation = "ABSOLUTE"
    depth_abs.location = (x - 800, y - 300)
    nt.links.new(axes.outputs["Z"], depth_abs.inputs[0])

    mask = nt.nodes.new("ShaderNodeMath")
    mask.label = label
    mask.operation = "LESS_THAN"
    mask.location = (x - 620, y - 300)
    nt.links.new(depth_abs.outputs[0], mask.inputs[0])
    mask.inputs[1].default_value = props.depth

    fac = nt.nodes.new("ShaderNodeMath")
    fac.label = label
    fac.operation = "MULTIPLY"
    fac.location = (x - 600, y - 150)
    if alpha_out is not None:
        nt.links.new(alpha_out, fac.inputs[0])
    else:
        fac.inputs[0].default_value = 1.0
    nt.links.new(mask.outputs[0], fac.inputs[1])

    # the decal's state: negative means the engine does not draw it at all
    state = nt.nodes.new("ShaderNodeValue")
    state.label = label
    state.location = (x - 800, y - 450)
    state.outputs[0].default_value = 1.0
    gate = nt.nodes.new("ShaderNodeMath")
    gate.label = label
    gate.operation = "MULTIPLY"
    gate.location = (x - 400, y - 250)
    nt.links.new(fac.outputs[0], gate.inputs[0])
    nt.links.new(state.outputs[0], gate.inputs[1])
    if arm_obj is not None:
        _drive_state(state, arm_obj, props.index, props.decal_name)

    # The object gate.  Everything above is computed from the projector, and
    # the projector does not know which object the shader is running on; this
    # is the only part that does.  Object Index is Blender's per-object integer
    # and reaches EEVEE as a uniform, so unlike the coverage attribute it costs
    # nothing per decal -- light_male's 58 branches on one material each carry
    # their own copy of these two nodes and the material still compiles.
    factor = gate
    host = decal_host_id(props.target) if props.target is not None else 0
    if host:
        info = nt.nodes.new("ShaderNodeObjectInfo")
        info.label = label
        info.location = (x - 800, y - 600)

        same = nt.nodes.new("ShaderNodeMath")
        same.label = label
        same.operation = "COMPARE"
        same.location = (x - 600, y - 600)
        nt.links.new(info.outputs["Object Index"], same.inputs[0])
        same.inputs[1].default_value = float(host)
        # pass indices are integers, so half a unit is the whole window
        same.inputs[2].default_value = 0.5

        mine = nt.nodes.new("ShaderNodeMath")
        mine.label = label
        mine.operation = "MULTIPLY"
        mine.location = (x - 300, y - 400)
        nt.links.new(gate.outputs[0], mine.inputs[0])
        nt.links.new(same.outputs[0], mine.inputs[1])
        factor = mine

    shader = _decal_shader(nt, props.material, colour_out, label, (x - 400, y + 150))

    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.label = label
    mix.location = (x - 200, y)
    nt.links.new(factor.outputs[0], mix.inputs["Fac"])
    nt.links.new(surface, mix.inputs[1])
    nt.links.new(shader, mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # deliberately *not* forcing the target material to BLEND.  Both sides of
    # the mix are opaque and the factor selects between them, so nothing here
    # needs alpha blending -- and the old decal path had to call
    # remember_blend_state() precisely because forcing it read back as
    # MAT_TRANSLUCENT and changed the target's exported material flags.
    return True


def _drive_state(value_node, arm_obj, index: int, decal_name: str) -> None:
    """Switch a decal branch off when its state goes negative.

    The state used to drive the decal object's alpha; with no object it drives
    the branch's own Value node instead.  Same expression, and the same reason
    for it: Blender evaluates a bare comparison natively and falls back to full
    Python for anything richer, which silently yields 0.0 unless the user has
    turned auto-run on.
    """
    drv = value_node.outputs[0].driver_add("default_value").driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "state"
    var.type = "SINGLE_PROP"
    var.targets[0].id = arm_obj
    var.targets[0].data_path = decal_path(index, decal_name)
    drv.expression = "state >= 0"


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


def _decal_mesh_from_faces(
    target_obj, faces, target_mesh, s, t, material_index, warnings, blender_lookup=None
) -> Mesh | None:
    """One TSDecalMesh: the covered faces, as indices into the target.

    ``faces`` comes from :func:`covered_faces` -- the decal has no stored index
    list to replay, so this is where the projector becomes geometry again.

    ``blender_lookup`` maps the *target's* Blender-local vertex positions to the
    DTS indices they were exported as.  The faces are the target's own, so the
    positions are bit-identical and the match is exact; matching against the
    exported DTS positions is not, because those went through the object's
    transform on the way out and came back a few ULPs different.
    """
    me = target_obj.data
    me.calc_loop_triangles()
    lookup = blender_lookup or _vertex_lookup(target_mesh.verts or target_mesh.initial_verts)
    wanted = set(faces)

    indices, missed = [], 0
    for tri in me.loop_triangles:
        if tri.polygon_index not in wanted:
            continue
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
            f"decal on {target_obj.name!r}: {missed} face(s) could not be matched "
            f"to the exported vertices and were dropped"
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
    object name, which is how ``build_decals`` looks one up.
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
    target_lookups=None, slot_objects=None,
) -> dict:
    """Recompute the decal table from the projector empties.

    The empty is the whole decal: the texgen planes come back out of its matrix
    and the covered faces are recomputed from its volume, so moving or scaling
    it changes the exported file.  Nothing is replayed from a stored payload --
    which also means the index list a file was imported from is not preserved.
    Returns the old decal index -> new index map the sequence exporter needs.
    """
    # keyed by index, never by name — turret_tank_base names all fourteen of
    # its decals the same, and grouping by name would export one
    projectors = {d.dts_decal.index: d for d in decal_objects()}
    if not projectors:
        return {}

    def owner_subshape(obj_index: int) -> int:
        for s in range(len(shape.sub_shape_first_object)):
            first = shape.sub_shape_first_object[s]
            if first <= obj_index < first + shape.sub_shape_num_objects[s]:
                return s
        return 0

    placed = []
    for index, projector in projectors.items():
        props = projector.dts_decal
        name = str(props.decal_name)
        owner = str(props.object_name)
        obj_index = object_index_by_name.get(owner)
        if obj_index is None:
            warnings.append(
                f"decal {name!r}: owner object {owner!r} was not exported; decal dropped"
            )
            continue
        if props.target is None:
            warnings.append(
                f"decal {name!r} (#{index}): no target mesh; decal dropped"
            )
            continue
        placed.append((owner_subshape(obj_index), index, name, obj_index, projector))
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
    for _sub, index, name, obj_index, projector in placed:
        owner_obj = shape.objects[obj_index]
        props = projector.dts_decal
        by_slot = (slot_objects or {}).get(str(props.object_name), {})
        mat_index = max(material_index_of(props.material), 0) if props.material else 0

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
            # every detail level gets its own coverage: the projector is the
            # same, but a coarser level has different faces under it
            faces = covered_faces(
                bobj, projector.matrix_world,
                depth=props.depth, rule=props.rule, max_angle=props.max_angle,
            )
            if not faces:
                shape.meshes.append(None)
                continue
            s, t = projector_to_texgen(projector.matrix_world, bobj.matrix_world)
            shape.meshes.append(
                _decal_mesh_from_faces(
                    bobj, faces, target, s, t, mat_index, warnings,
                    blender_lookup=(target_lookups or {}).get(bobj.name),
                )
            )

        decal_index_map[index] = len(shape.decals)
        shape.decals.append(
            Decal((shape.add_name(str(name)), owner_obj.num_meshes, start, obj_index, -1))
        )
        state = arm_obj.get(decal_prop(index, name), -1.0)
        shape.decal_states.append(int(round(float(state))))
    return decal_index_map


# The engine draws decals with a polygon offset, and two places here have to
# stand in for one: the imported mesh form, where a Displace modifier lifts the
# copy off its target (a modifier and not an edit, so the copied vertices stay
# where the file put them), and a baked decal, whose lift is in the exported
# vertices because the file has nowhere else to put it.
#
# Not an export option.  It is in shape units, so the number that is right for
# one shape is right for every shape built at the same scale, and every shape
# this format is aimed at is: 0.002 is well under the thickness of any surface
# in the corpus and well over the depth precision of the era's hardware.
DECAL_LIFT = 0.002


def _baked_decal_mesh(bobj, faces, s, t, to_dts, normal_mat, mat_index):
    """The covered faces as a ``STANDARD_MESH``, with the texgen baked into UVs.

    The other half of ``Export Decals as Meshes``.  A ``TSDecalMesh`` stores
    indices into its target and two planes the engine turns into UVs at draw
    time; this evaluates those planes per vertex instead and writes ordinary
    geometry, so a reader that skips the decal section still draws the decal.

    ``s`` and ``t`` are in ``bobj``'s *local* space, which is what
    :func:`projector_to_texgen` returns for ``bobj.matrix_world`` -- so the UV
    is computed before ``to_dts`` and the position after it, and neither has to
    care which space the other is in.

    ``lift`` moves each vertex along its own normal.  Baked geometry is
    coplanar with the surface it was copied from and the polygon offset that
    used to keep it in front is gone with the decal, so without this the two
    z-fight.  Along the *vertex* normal rather than the projector's axis: a
    decal wrapping a curved surface has no single direction to be lifted in.
    """
    from .vertex_pool import VertexPool

    me = bobj.data
    me.calc_loop_triangles()
    wanted = set(faces)

    store = VertexPool()
    corner_index = {}
    for tri in me.loop_triangles:
        if tri.polygon_index not in wanted:
            continue
        for loop_index in tri.loops:
            loop = me.loops[loop_index]
            vi = loop.vertex_index
            co = me.vertices[vi].co
            normal = Vector(loop.normal if hasattr(loop, "normal") else me.vertices[vi].normal)
            uv = (
                co[0] * s[0] + co[1] * s[1] + co[2] * s[2] + s[3],
                co[0] * t[0] + co[1] * t[1] + co[2] * t[2] + t[3],
            )
            n = normal_mat @ normal
            n.normalize()
            # lift in DTS space, so the offset is the distance it looks like
            # rather than whatever the object's scale makes of it
            corner_index[loop_index] = store.intern(
                tuple(to_dts @ co + n * DECAL_LIFT),
                uv,  # already DTS convention: the texgen is what the engine reads
                tuple(n),
            )

    if not corner_index:
        return None

    length = store.seal()
    indices = []
    for tri in me.loop_triangles:
        if tri.polygon_index not in wanted:
            continue
        # DTS winding is the reverse of Blender's, the same as an ordinary mesh
        indices.extend(corner_index[li] for li in reversed(tri.loops))

    word = PRIM_TRIANGLES | PRIM_INDEXED | (mat_index & PRIM_MATERIAL_MASK)
    mesh = Mesh(mesh_type=STANDARD_MESH)
    mesh.verts = store.verts[:length]
    mesh.tverts = store.tverts[:length]
    mesh.norms = store.norms[:length]
    mesh.indices = indices
    mesh.primitives = [Primitive(0, len(indices), word)]
    mesh.verts_per_frame = len(mesh.verts)
    return mesh


def bake_decals_as_objects(
    shape: Shape, arm_obj, subshape: int, object_index_by_name, material_index_of,
    placement_of, warnings, slot_objects=None,
) -> dict:
    """Write this subshape's decals as ordinary objects.

    Called from inside ``blender_to_shape``'s per-subshape loop rather than
    after it, because ``sub_shape_first_object`` and ``sub_shape_num_objects``
    are contiguous ranges -- an object appended later would fall outside the
    range of the subshape it belongs to.  ``build_decals`` has no such problem
    and runs afterwards, which is why the two are separate functions rather
    than two branches of one.

    ``shape.decals`` is left empty on purpose: an engine that understands
    decals would otherwise draw the same art twice, once as geometry and once
    projected.

    Returns ``{decal index: (object index, default visibility)}``.  The caller
    owns ``shape.object_states`` -- it fills one per object after every
    subshape is placed -- so the rest state comes back rather than being
    appended here.
    """
    projectors = {d.dts_decal.index: d for d in decal_objects()}
    if not projectors:
        return {}

    first = shape.sub_shape_first_object[subshape]
    mine = []
    for index, projector in sorted(projectors.items()):
        props = projector.dts_decal
        name = str(props.decal_name)
        owner = str(props.object_name)
        obj_index = object_index_by_name.get(owner)
        if obj_index is None:
            warnings.append(
                f"decal {name!r}: owner object {owner!r} was not exported; decal dropped"
            )
            continue
        if obj_index < first or obj_index >= len(shape.objects):
            continue  # another subshape's, and its own pass will take it
        if props.target is None:
            warnings.append(f"decal {name!r} (#{index}): no target mesh; decal dropped")
            continue
        mine.append((index, name, obj_index, projector))

    baked = {}
    for index, name, obj_index, projector in mine:
        props = projector.dts_decal
        owner_obj = shape.objects[obj_index]
        by_slot = (slot_objects or {}).get(str(props.object_name), {})
        mat_index = max(material_index_of(props.material), 0) if props.material else 0

        start = len(shape.meshes)
        node_index, any_geometry = -1, False
        for j in range(owner_obj.num_meshes):
            bobj = by_slot.get(j)
            if bobj is None:
                shape.meshes.append(None)
                continue
            faces = covered_faces(
                bobj, projector.matrix_world,
                depth=props.depth, rule=props.rule, max_angle=props.max_angle,
            )
            if not faces:
                shape.meshes.append(None)
                continue
            _skin, node, to_dts, normal_mat = placement_of(bobj)
            s, t = projector_to_texgen(projector.matrix_world, bobj.matrix_world)
            mesh = _baked_decal_mesh(
                bobj, faces, s, t, to_dts, normal_mat, mat_index
            )
            shape.meshes.append(mesh)
            if mesh is not None:
                any_geometry = True
                if node_index < 0:
                    node_index = node

        if not any_geometry:
            # the meshes are already appended and other objects' start indices
            # depend on where they are, so the slots stay and the object does
            # not.  A decal covering nothing is the coverage rule's answer, and
            # it is the same answer build_decals gives.
            warnings.append(
                f"decal {name!r} (#{index}): covers no faces at any detail level; "
                f"nothing was baked"
            )
            continue

        baked[index] = (len(shape.objects), 1.0 if _rest_state(arm_obj, index, name) else 0.0)
        shape.objects.append(
            Object(
                name_index=shape.add_name(name),
                num_meshes=owner_obj.num_meshes,
                start_mesh_index=start,
                node_index=node_index if node_index >= 0 else owner_obj.node_index,
            )
        )
    return baked


def _rest_state(arm_obj, index: int, name: str) -> bool:
    """Is this decal on before any sequence runs?

    A decal state is -1 for off and a frame index for on, and a baked decal is
    an object, whose equivalent is visibility.  Most decals rest at -1 and a
    Damage sequence switches them on; a wreck's rest at 0.
    """
    return float(arm_obj.get(decal_prop(index, name), -1.0)) >= 0.0


def _decal_uvs(mesh, verts, s, t) -> None:
    """Bake the file's own texgen planes into a UV layer.

    The engine computes every decal UV as two dot products against the stored
    planes, so this is not an approximation of the projection -- it *is* the
    projection, evaluated per vertex.  DTS's V axis runs opposite Blender's, as
    it does for ordinary tverts.
    """
    layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        v = verts[loop.vertex_index]
        layer.data[loop.index].uv = (
            v[0] * s[0] + v[1] * s[1] + v[2] * s[2] + s[3],
            1.0 - (v[0] * t[0] + v[1] * t[1] + v[2] * t[2] + t[3]),
        )


def _build_decal_mesh(name, tris, verts_src, bmat, s, t):
    """The covered faces, as their own object.

    A decal borrows its target's vertices, so this is a copy of the subset the
    file's index list names -- the one thing the projector form cannot
    reproduce, since a projector has to re-derive coverage from a volume.
    """
    used = sorted({i for tri in tris for i in tri[:3]})
    remap = {old: new for new, old in enumerate(used)}
    verts = [verts_src[i] for i in used]
    bm = bpy.data.meshes.new(name)
    bm.from_pydata(
        [Vector(v) for v in verts],
        [],
        [(remap[a], remap[b], remap[c]) for a, b, c, *_ in tris],
    )
    _decal_uvs(bm, verts, s, t)
    if bmat is not None:
        bm.materials.append(bmat)
    bm.validate()
    bm.update()
    return bpy.data.objects.new(name, bm)


def import_decal_meshes(
    shape: Shape,
    arm_obj,
    bmats,
    targets: dict,
    collection_of,
    parent_like,
    warnings,
) -> tuple[int, int]:
    """Build one mesh per (decal, detail level), and no projectors.

    The other half of the ``Import Decals as Meshes`` checkbox.  What it buys
    is the one thing the projector form gives up: a ``TSDecalMesh`` names the
    triangles it covers, an empty cannot hold a list, and export therefore
    re-derives coverage from the projector volume at recall 0.44 -- so the
    faces a shipped file actually names are visible *here* and nowhere else.

    What it costs is the export path.  A decal is exported from a projector
    empty and from nothing else, so these meshes reach no file; the exporter
    skips them rather than emitting each one again as a phantom object
    (``blender_to_shape._gather_mesh_objects``).  This is a way to look at what
    a file says, not a way to author one.  The caller warns.

    Same arguments as :func:`import_decals`.  Returns ``(decals, meshes)``.
    """
    from .shape_to_blender import decode_primitives

    coll = bpy.data.collections.new(f"{arm_obj.name}.decals")
    bpy.context.scene.collection.children.link(coll)
    n_decals = n_meshes = 0

    for decal_index, decal in enumerate(shape.decals):
        name_index, num_meshes, start, obj_index, _sibling = decal.raw
        decal_name = shape.name(name_index)
        owner = (
            shape.name(shape.objects[obj_index].name_index)
            if 0 <= obj_index < len(shape.objects)
            else ""
        )
        built = False
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
                    f"only the first is imported and the rest are lost"
                )

            verts_src = _target_verts(target_mesh)
            tris = [
                tri for tri in decode_primitives(dd) if max(tri[:3]) < len(verts_src)
            ]
            if not tris:
                continue

            mat_index = dd.material_index & PRIM_MATERIAL_MASK
            bmat = bmats[mat_index] if 0 <= mat_index < len(bmats) else None
            size = target_obj.get("dts_detail_size", j)
            bobj = _build_decal_mesh(
                f"{decal_name}{size}", tris, verts_src, bmat,
                dd.texgen_s[0], dd.texgen_t[0],
            )
            coll.objects.link(bobj)
            parent_like(bobj, target_obj)

            # the legacy property names, deliberately: the exporter already
            # keys its "do not export this as an object" guard on
            # dts_decal_name, and props/migrate.py already knows how to turn a
            # scene full of these into projectors
            bobj["dts_decal_name"] = decal_name
            bobj["dts_decal_index"] = decal_index
            bobj["dts_decal_object"] = owner
            bobj["dts_decal_slot"] = j
            bobj["dts_decal_target"] = target_obj.name
            bobj["dts_detail_size"] = size
            bobj["dts_subshape"] = target_obj.get("dts_subshape", 0)

            lift = bobj.modifiers.new("Decal Lift", "DISPLACE")
            lift.strength = DECAL_LIFT
            lift.mid_level = 0.0

            target_coll = collection_of(target_obj)
            if target_coll is not None:
                target_coll.objects.link(bobj)
                coll.objects.unlink(bobj)

            n_meshes += 1
            built = True
        if built:
            n_decals += 1

    if not n_meshes:
        bpy.data.collections.remove(coll)
    return n_decals, n_meshes


def import_decals(
    shape: Shape,
    arm_obj,
    bmats,
    targets: dict,
    collection_of,
    parent_like,
    warnings,
) -> tuple[int, int]:
    """Build one projector empty per decal.  No meshes.

    *targets* maps ``(object_index, mesh_slot) -> (blender object, dtslib
    mesh)``; *collection_of* returns the detail collection a slot belongs in;
    *parent_like* re-parents a new object the way the target was parented.
    Returns ``(decals imported, targets covered)``.

    The file's index list is *not* kept.  It is used here to seed the coverage
    cache so the preview starts out matching the file, but export recomputes
    coverage from the empty, so the two part company as soon as the projector
    moves -- and, per the measurements in the module docstring, usually before
    that.
    """
    from .shape_to_blender import decode_primitives

    if not shape.decals:
        return 0, 0

    coll = bpy.data.collections.new(f"{arm_obj.name}.decals")
    bpy.context.scene.collection.children.link(coll)

    n_decals = n_targets = 0

    for decal_index, decal in enumerate(shape.decals):
        name_index, num_meshes, start, obj_index, _sibling = decal.raw
        decal_name = shape.name(name_index)
        owner = (
            shape.name(shape.objects[obj_index].name_index)
            if 0 <= obj_index < len(shape.objects)
            else ""
        )

        projector = None
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
                tri for tri in decode_primitives(dd) if max(tri[:3]) < len(verts_src)
            ]
            if not tris:
                continue

            # one projector per decal: the texgen is shared across detail
            # levels, and every LOD of one object hangs off the same node, so
            # a single empty is correct for all of them
            if projector is None:
                mat_index = dd.material_index & PRIM_MATERIAL_MASK
                bmat = bmats[mat_index] if 0 <= mat_index < len(bmats) else None
                projector = bpy.data.objects.new(f"{PROJECTOR_PREFIX}{decal_name}", None)
                projector.empty_display_type = "IMAGE"
                projector.empty_display_size = 0.15
                projector.matrix_world = texgen_to_projector(
                    dd.texgen_s[0], dd.texgen_t[0], target_obj.matrix_world
                )
                coll.objects.link(projector)
                parent_like(projector, target_obj, keep_transform=True)

                props = projector.dts_decal
                props.is_dts = True
                props.schema_version = DECAL_SCHEMA_VERSION
                props.decal_name = decal_name
                props.index = decal_index
                props.object_name = owner
                props.target = target_obj
                props.material = bmat
                props.subshape = int(target_obj.get("dts_subshape", 0))
                if bmat is not None:
                    # Split before wiring, so the shared material never
                    # receives a branch and the meshes with no decals keep it.
                    # props.material is deliberately left alone: that is the
                    # *decal's* own material (its scorch texture, and its own
                    # entry in the file's material list), not the surface the
                    # branch composites into.
                    wire_decal_branch(
                        _host_material_for(target_obj), projector, arm_obj
                    )
                n_decals += 1

            # seed the preview from the file's own index list rather than from
            # covered_faces(), so a freshly imported shape looks like the file
            # even where the rule would not have picked those faces
            file_faces = _faces_of_indices(target_obj, tris)
            write_coverage(target_obj, decal_index, file_faces)
            if target_obj is projector.dts_decal.target:
                # fit the derivation to this decal's own coverage while the
                # file's answer is still in hand; export has only the projector
                rule, depth, angle = fit_coverage(
                    target_obj, projector.matrix_world, file_faces
                )
                projector.dts_decal.rule = rule
                projector.dts_decal.depth = depth
                projector.dts_decal.max_angle = angle
            n_targets += 1

    if not n_decals:
        bpy.data.collections.remove(coll)
    return n_decals, n_targets


def _faces_of_indices(target_obj, tris) -> list:
    """Which of the target's faces the file's triangle list names.

    The importer feeds the DTS vertex array straight into ``from_pydata``
    (shape_to_blender.py:472), so a Blender vertex index *is* the DTS index and
    the triangles compare directly.  Only used to seed the preview cache;
    nothing exported depends on it.
    """
    mesh = target_obj.data
    mesh.calc_loop_triangles()
    wanted = {tuple(sorted(tri[:3])) for tri in tris}
    return sorted(
        {
            tri.polygon_index
            for tri in mesh.loop_triangles
            if tuple(sorted(tri.vertices)) in wanted
        }
    )


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


def create_decal(arm_obj, target_obj, *, name, material=None, index=None,
                 all_details=True, collection_of=None, parent_like=None):
    """Build a decal over the selected faces of ``target_obj``.

    The inverse of import_decals, and the same shape of data: one projector
    empty and nothing else.  The face selection is what the projector is *fitted
    to*, not what the decal stores -- coverage is recomputed from the empty on
    export, so moving the empty afterwards moves the decal.

    Returns (index, projector empty).
    """
    from .naming import dts_object_and_size

    if parent_like is None:
        from .shape_to_blender import _parent_like as parent_like
    if collection_of is None:
        def collection_of(obj):
            return obj.users_collection[0] if obj.users_collection else None

    # Fit to where the target actually is.  A freshly built or freshly
    # re-parented object still reports its pre-evaluation matrix -- bone
    # parenting in particular only lands once the depsgraph has run -- and the
    # projector is fitted against ``target_obj.matrix_world``.  Fitting to a
    # stale one used to cost nothing, because the covered faces were stored at
    # this moment; now they are recomputed from the projector at export, by
    # which time the target has moved and the decal covers the wrong faces.
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()

    index = next_decal_index() if index is None else index
    owner, _size = dts_object_and_size(target_obj)

    tris = selected_face_triangles(target_obj)
    if not tris:
        raise ValueError(f"{target_obj.name!r} has no selected faces to cover")

    projector_matrix = projector_for(target_obj, tris)

    projector = bpy.data.objects.new(f"{PROJECTOR_PREFIX}{name}", None)
    projector.empty_display_type = "IMAGE"
    projector.empty_display_size = 0.15
    projector.matrix_world = projector_matrix
    coll = collection_of(target_obj)
    if coll is not None:
        coll.objects.link(projector)
    parent_like(projector, target_obj, keep_transform=True)

    props = projector.dts_decal
    props.is_dts = True
    props.schema_version = DECAL_SCHEMA_VERSION
    props.decal_name = name
    props.index = index
    props.object_name = owner
    props.target = target_obj
    props.material = material
    props.subshape = int(target_obj.get("dts_subshape", 0))

    # seed the preview cache on every detail level of the same DTS object, so
    # the decal does not vanish as the engine drops LOD -- which is what every
    # shipped decal does.  Export walks the levels itself; this is preview only.
    hosts = [target_obj]
    if all_details:
        hosts += [
            other
            for other in bpy.data.objects
            if other is not target_obj
            and other.type == "MESH"
            and dts_object_and_size(other)[0] == owner
        ]
    covered = 0
    for host in hosts:
        faces = covered_faces(
            host, projector_matrix,
            depth=props.depth, rule=props.rule, max_angle=props.max_angle,
        )
        if faces:
            write_coverage(host, index, faces)
            covered += 1

    if material is not None:
        wire_decal_branch(_host_material_for(target_obj), projector, arm_obj)

    # the state the decal rests at.  0 is on; -1 would be off, which is what a
    # damage decal wants, but a decal you just made should be visible.
    arm_obj[decal_prop(index, name)] = 0.0
    return index, projector
