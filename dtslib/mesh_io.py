"""Per-mesh-type assemble/disassemble over the three-buffer memory block.

Ports of TSMesh::assemble/disassemble (tsMesh.cc:3041/:3140),
TSSkinMesh::assemble/disassemble (:3206/:3274), TSSortedMesh (tsSortedMesh.cc)
and TSDecalMesh (tsDecal.cc).

Data shared with a parent mesh (parentMesh >= 0) is simply absent from the
file; on read we copy it out of the already-read parent so the in-memory model
is self-contained, and on write we emit it only when parent_mesh < 0.
"""

from __future__ import annotations

from .errors import DtsError
from .normals import encode_normal
from .primitives import bits_to_f32, f32_to_bits
from .stream import ReadAlloc, WriteAlloc
from .types import (
    DECAL_MESH,
    NULL_MESH,
    SKIN_MESH,
    SORTED_MESH,
    STANDARD_MESH,
    DecalMeshData,
    Mesh,
    Primitive,
    SortedData,
)

MESH_TYPE_MASK = 0x7


def _group3(flat):
    return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]


def _group2(flat):
    return [tuple(flat[i : i + 2]) for i in range(0, len(flat), 2)]


def _group4(flat):
    return [tuple(flat[i : i + 4]) for i in range(0, len(flat), 4)]


def read_mesh(alloc: ReadAlloc, version: int, prev_meshes: list) -> Mesh | None:
    """Read one mesh: the type word plus its payload.

    prev_meshes: meshes read so far in the same section (for parentMesh
    sharing).  Returns None for a null mesh.
    """
    type_word = alloc.get32()
    mesh_type = type_word & MESH_TYPE_MASK
    if mesh_type == NULL_MESH:
        return None
    if mesh_type == DECAL_MESH:
        return _read_decal_mesh(alloc, version, type_word)

    mesh = _read_base_mesh(alloc, version, prev_meshes, type_word)
    if mesh_type == SKIN_MESH:
        _read_skin_extension(alloc, version, mesh, prev_meshes)
    elif mesh_type == SORTED_MESH:
        _read_sorted_extension(alloc, mesh)
    return mesh


def _read_base_mesh(alloc: ReadAlloc, version: int, prev_meshes, type_word) -> Mesh:
    alloc.guard()

    mesh = Mesh(mesh_type=type_word & MESH_TYPE_MASK)
    mesh.num_frames = alloc.get32()
    mesh.num_mat_frames = alloc.get32()
    mesh.parent_mesh = alloc.get32()
    mesh.bounds = alloc.get32fn(6)
    mesh.center = alloc.get32fn(3)
    mesh.radius_int = alloc.get32()  # stored as (S32)mRadius

    parent = None
    if mesh.parent_mesh >= 0:
        if mesh.parent_mesh >= len(prev_meshes) or prev_meshes[mesh.parent_mesh] is None:
            raise DtsError(f"mesh references bad parent {mesh.parent_mesh}")
        parent = prev_meshes[mesh.parent_mesh]

    # NOTE: a child mesh declares its *own* counts and shares a prefix of the
    # parent's arrays (the engine sets the parent's data pointer with the
    # child's count) — slice, don't alias the whole array.
    num_verts = alloc.get32()
    if parent is None:
        mesh.verts = _group3(alloc.get32fn(3 * num_verts))
    else:
        mesh.verts = parent.verts[:num_verts]

    num_tverts = alloc.get32()
    if parent is None:
        mesh.tverts = _group2(alloc.get32fn(2 * num_tverts))
    else:
        mesh.tverts = parent.tverts[:num_tverts]

    if parent is None:
        mesh.norms = _group3(alloc.get32fn(3 * num_verts))
        if version > 21:
            mesh.encoded_norms = alloc.get8n(num_verts)
    else:
        mesh.norms = parent.norms[:num_verts]
        mesh.encoded_norms = parent.encoded_norms[:num_verts]

    num_prims = alloc.get32()
    prim16 = alloc.get16n(2 * num_prims)
    prim32 = alloc.get32n(num_prims)
    mesh.primitives = [
        Primitive(prim16[2 * i], prim16[2 * i + 1], prim32[i]) for i in range(num_prims)
    ]

    num_indices = alloc.get32()
    mesh.indices = list(alloc.getu16n(num_indices))

    num_merge = alloc.get32()
    mesh.merge_indices = list(alloc.getu16n(num_merge))

    mesh.verts_per_frame = alloc.get32()
    mesh.flags = alloc.getu32()

    alloc.guard()
    return mesh


def _read_skin_extension(alloc: ReadAlloc, version: int, mesh: Mesh, prev_meshes) -> None:
    parent = prev_meshes[mesh.parent_mesh] if mesh.parent_mesh >= 0 else None

    num_verts = alloc.get32()
    if parent is None:
        mesh.initial_verts = _group3(alloc.get32fn(3 * num_verts))
        mesh.initial_norms = _group3(alloc.get32fn(3 * num_verts))
        if version > 21:
            mesh.initial_encoded_norms = alloc.get8n(num_verts)
    else:
        mesh.initial_verts = parent.initial_verts[:num_verts]
        mesh.initial_norms = parent.initial_norms[:num_verts]
        mesh.initial_encoded_norms = parent.initial_encoded_norms[:num_verts]

    num_transforms = alloc.get32()
    if parent is None:
        flat = alloc.get32fn(16 * num_transforms)
        mesh.initial_transforms = [tuple(flat[i : i + 16]) for i in range(0, len(flat), 16)]
    else:
        mesh.initial_transforms = parent.initial_transforms[:num_transforms]

    num_vi = alloc.get32()
    if parent is None:
        mesh.vertex_index = list(alloc.get32n(num_vi))
        mesh.bone_index = list(alloc.get32n(num_vi))
        mesh.weight = list(alloc.get32fn(num_vi))
    else:
        mesh.vertex_index = parent.vertex_index[:num_vi]
        mesh.bone_index = parent.bone_index[:num_vi]
        mesh.weight = parent.weight[:num_vi]

    num_ni = alloc.get32()
    if parent is None:
        mesh.node_index = list(alloc.get32n(num_ni))
    else:
        mesh.node_index = parent.node_index[:num_ni]

    alloc.guard()


def _read_sorted_extension(alloc: ReadAlloc, mesh: Mesh) -> None:
    sd = SortedData()
    num_clusters = alloc.get32()
    flat = alloc.get32n(8 * num_clusters)
    sd.clusters = [tuple(flat[i : i + 8]) for i in range(0, len(flat), 8)]
    sd.start_cluster = list(alloc.get32n(alloc.get32()))
    sd.first_verts = list(alloc.get32n(alloc.get32()))
    sd.num_verts = list(alloc.get32n(alloc.get32()))
    sd.first_tverts = list(alloc.get32n(alloc.get32()))
    sd.always_write_depth = alloc.get32()
    mesh.sorted_data = sd
    alloc.guard()


def _read_decal_mesh(alloc: ReadAlloc, version: int, type_word: int) -> Mesh:
    mesh = Mesh(mesh_type=DECAL_MESH)
    dd = DecalMeshData()

    if version < 20:
        # decals used to be derived from meshes: an empty-mesh header
        alloc.guard()
        alloc.get32n(15)

    num_prims = alloc.get32()
    prims = []
    for _ in range(num_prims):
        s, n = alloc.get16n(2)
        (mat,) = alloc.get32n(1)
        prims.append(Primitive(s, n, mat))
    dd.primitives = prims

    num_indices = alloc.get32()
    dd.indices = list(alloc.getu16n(num_indices))

    if version < 20:
        alloc.get32n(3)
        alloc.guard()

    num_start = alloc.get32()
    dd.start_primitive = list(alloc.get32n(num_start))

    if version >= 19:
        dd.texgen_s = _group4(alloc.get32fn(4 * num_start))
        dd.texgen_t = _group4(alloc.get32fn(4 * num_start))

    dd.material_index = alloc.get32()
    mesh.decal_data = dd
    alloc.guard()
    return mesh


# ----------------------------------------------------------------------
# write side (the three-buffer versions, 19-24)
# ----------------------------------------------------------------------


def write_mesh(alloc: WriteAlloc, mesh: Mesh | None, version: int = 24) -> None:
    """Write one mesh: type word + payload (TSShape::disassembleShape loop)."""
    if mesh is None:
        alloc.set32(NULL_MESH)
        return
    alloc.set32(mesh.mesh_type & MESH_TYPE_MASK)
    if mesh.mesh_type == DECAL_MESH:
        _write_decal_mesh(alloc, mesh, version)
        return
    _write_base_mesh(alloc, mesh, version)
    if mesh.mesh_type == SKIN_MESH:
        _write_skin_extension(alloc, mesh, version)
    elif mesh.mesh_type == SORTED_MESH:
        _write_sorted_extension(alloc, mesh)


def _write_base_mesh(alloc: WriteAlloc, mesh: Mesh, version: int = 24) -> None:
    alloc.guard()

    alloc.set32(mesh.num_frames)
    alloc.set32(mesh.num_mat_frames)
    alloc.set32(mesh.parent_mesh)
    alloc.set32fn(mesh.bounds)
    alloc.set32fn(mesh.center)
    alloc.set32(mesh.radius_int)

    alloc.set32(len(mesh.verts))
    if mesh.parent_mesh < 0:
        alloc.set32fn([c for v in mesh.verts for c in v])

    alloc.set32(len(mesh.tverts))
    if mesh.parent_mesh < 0:
        alloc.set32fn([c for v in mesh.tverts for c in v])

    if mesh.parent_mesh < 0:
        alloc.set32fn([c for v in mesh.norms for c in v])
        # encoded normals: v22 introduced them; preserve read bytes, else
        # compute (TSMesh::disassemble, tsMesh.cc:3168)
        if version > 21:
            if mesh.encoded_norms:
                alloc.set8bytes(bytes(mesh.encoded_norms))
            else:
                alloc.set8bytes(bytes(encode_normal(n) for n in mesh.norms))

    alloc.set32(len(mesh.primitives))
    for p in mesh.primitives:
        alloc.set16n((p.start, p.num_elements))
        alloc.set32(p.mat_index)

    alloc.set32(len(mesh.indices))
    alloc.setu16n(mesh.indices)

    alloc.set32(len(mesh.merge_indices))
    alloc.setu16n(mesh.merge_indices)

    alloc.set32(mesh.verts_per_frame)
    alloc.setu32(mesh.flags)

    alloc.guard()


def _write_skin_extension(alloc: WriteAlloc, mesh: Mesh, version: int = 24) -> None:
    alloc.set32(len(mesh.initial_verts))
    if mesh.parent_mesh < 0:
        alloc.set32fn([c for v in mesh.initial_verts for c in v])
        alloc.set32fn([c for v in mesh.initial_norms for c in v])
        if version > 21:
            if mesh.initial_encoded_norms:
                alloc.set8bytes(bytes(mesh.initial_encoded_norms))
            else:
                alloc.set8bytes(bytes(encode_normal(n) for n in mesh.initial_norms))

    alloc.set32(len(mesh.initial_transforms))
    if mesh.parent_mesh < 0:
        alloc.set32fn([c for t in mesh.initial_transforms for c in t])

    alloc.set32(len(mesh.vertex_index))
    if mesh.parent_mesh < 0:
        alloc.set32n(mesh.vertex_index)
        alloc.set32n(mesh.bone_index)
        alloc.set32fn(mesh.weight)

    alloc.set32(len(mesh.node_index))
    if mesh.parent_mesh < 0:
        alloc.set32n(mesh.node_index)

    alloc.guard()


def _write_sorted_extension(alloc: WriteAlloc, mesh: Mesh) -> None:
    sd = mesh.sorted_data or SortedData()
    alloc.set32(len(sd.clusters))
    alloc.set32n([w for c in sd.clusters for w in c])
    alloc.set32(len(sd.start_cluster))
    alloc.set32n(sd.start_cluster)
    alloc.set32(len(sd.first_verts))
    alloc.set32n(sd.first_verts)
    alloc.set32(len(sd.num_verts))
    alloc.set32n(sd.num_verts)
    alloc.set32(len(sd.first_tverts))
    alloc.set32n(sd.first_tverts)
    alloc.set32(sd.always_write_depth)
    alloc.guard()


def _write_decal_mesh(alloc: WriteAlloc, mesh: Mesh, version: int = 24) -> None:
    dd = mesh.decal_data or DecalMeshData()

    if version < 20:
        # decals used to be derived from meshes, so a decal starts with an
        # empty mesh header the engine reads past and discards
        # (TSDecalMesh::assemble, tsDecal.cc:169).  Its primitives and indices
        # then land in the base mesh's slots, which is where they already are
        alloc.guard()
        alloc.set32(1)  # numFrames
        alloc.set32(1)  # numMatFrames
        alloc.set32(-1)  # parentMesh
        alloc.set32n([0] * 10)  # bounds, center, radius
        alloc.set32(0)  # numVerts
        alloc.set32(0)  # numTVerts

    alloc.set32(len(dd.primitives))
    for p in dd.primitives:
        alloc.set16n((p.start, p.num_elements))
        alloc.set32(p.mat_index)
    alloc.set32(len(dd.indices))
    alloc.setu16n(dd.indices)

    if version < 20:
        alloc.set32(0)  # mergeIndices
        alloc.set32(0)  # vertsPerFrame
        alloc.set32(0)  # flags
        alloc.guard()

    alloc.set32(len(dd.start_primitive))
    alloc.set32n(dd.start_primitive)
    if version >= 19:
        # one plane pair per start primitive, always: the reader sizes these off
        # the count above, so a decal that came from a v18 file (which stores no
        # planes at all) gets null ones rather than a short buffer
        zero = (0.0, 0.0, 0.0, 0.0)
        n = len(dd.start_primitive)
        for planes in (dd.texgen_s, dd.texgen_t):
            padded = list(planes[:n]) + [zero] * (n - len(planes[:n]))
            alloc.set32fn([c for v in padded for c in v])
    alloc.set32(dd.material_index)
    alloc.guard()


def compute_mesh_bounds(mesh: Mesh) -> None:
    """Port of TSMesh::computeBounds (tsMesh.cc:2966).

    The bounds, centre and radius a pre-v19 file leaves out: the loader works
    them out from the vertices, so anything writing that far back has to do the
    same to know what the file will read back as.
    """
    verts = mesh.verts or mesh.initial_verts
    if not verts:
        return
    xs, ys, zs = zip(*verts)
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    mesh.bounds = mn + mx
    # keep the center float32-representable so a rewrite round-trips exactly
    center = tuple(bits_to_f32(f32_to_bits((a + b) / 2.0)) for a, b in zip(mn, mx))
    mesh.center = center
    radius = max(
        ((v[0] - center[0]) ** 2 + (v[1] - center[1]) ** 2 + (v[2] - center[2]) ** 2) ** 0.5
        for v in verts
    )
    mesh.radius_int = int(radius)
