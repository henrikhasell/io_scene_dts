"""Which of a shape's meshes draw translucent, and what that means for decals.

Two rules the engine needs and the file does not enforce, kept here because
both are answered from the finished :class:`Shape` alone -- no bpy, so the fast
test loop can ask them and so the corpus can be measured against them.

**Translucent meshes draw last.**  A shape's objects are drawn in the order the
object list gives, and a translucent surface only composites correctly over
what is already in the frame buffer.  An opaque mesh drawn *after* one blends
against the wrong background, and one drawn after a decal erases it.

**A shape with decals has something translucent.**  Every one of the corpus's
153 decal-bearing shapes does: 94 on a regular object mesh and the other 59 on
the decal's own mesh material, which is why both count here.  Nothing in the
format says so -- a `.dts` with decals and no translucency writes and reads
fine, it just does not draw the decals the way the author meant.
"""

from __future__ import annotations

from .types import MAT_TRANSLUCENT, PRIM_MATERIAL_MASK, Shape


def _mesh_material_indices(mesh) -> set[int]:
    """Every material-list index one mesh draws with.

    A decal mesh keeps its material on ``decal_data`` rather than in a
    primitive of its own, so both places are read -- the 59 shapes whose only
    translucency is a decal's are invisible to a scan that checks primitives
    alone.
    """
    used = {p.mat_index & PRIM_MATERIAL_MASK for p in mesh.primitives or []}
    if mesh.decal_data is not None:
        used.add(mesh.decal_data.material_index & PRIM_MATERIAL_MASK)
    return used


def translucent_material_indices(shape: Shape) -> set[int]:
    return {i for i, m in enumerate(shape.materials) if m.flags & MAT_TRANSLUCENT}


def mesh_is_translucent(shape: Shape, mesh) -> bool:
    if mesh is None:
        return False
    return bool(_mesh_material_indices(mesh) & translucent_material_indices(shape))


def translucent_object_indices(shape: Shape) -> set[int]:
    """Objects with at least one translucent mesh, by index in ``shape.objects``.

    Any mesh, not all of them: an object is one draw call per detail level and
    one blended level is enough to put it in the wrong place in the order.
    """
    translucent = translucent_material_indices(shape)
    if not translucent:
        return set()
    out = set()
    for i, obj in enumerate(shape.objects):
        for m in shape.meshes[obj.start_mesh_index : obj.start_mesh_index + obj.num_meshes]:
            if m is not None and _mesh_material_indices(m) & translucent:
                out.add(i)
                break
    return out


def objects_out_of_order(shape: Shape) -> list[int]:
    """Object indices that are opaque but sit after a translucent object.

    Per sub-shape, since the object list is sliced by ``sub_shape_first_object``
    and only ever drawn a slice at a time -- an opaque object in sub-shape 1 is
    not behind a translucent one in sub-shape 0.
    """
    translucent = translucent_object_indices(shape)
    if not translucent:
        return []
    bad = []
    for first, count in zip(shape.sub_shape_first_object, shape.sub_shape_num_objects):
        seen_translucent = False
        for i in range(first, first + count):
            if i in translucent:
                seen_translucent = True
            elif seen_translucent:
                bad.append(i)
    return bad


def shape_has_translucent_mesh(shape: Shape) -> bool:
    """Whether anything the shape draws -- object mesh or decal -- blends."""
    translucent = translucent_material_indices(shape)
    if not translucent:
        return False
    return any(
        m is not None and _mesh_material_indices(m) & translucent for m in shape.meshes
    )
