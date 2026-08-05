"""One vertex array shared by the detail levels of a single DTS object.

A DTS mesh can set ``parent_mesh`` to another mesh in the same object and then
declare only a vertex *count*: the engine reads a prefix of the parent's
``verts``/``tverts``/``norms`` arrays instead of storing its own
(``dtslib/mesh_io.py:76-104``).  39% of the meshes in the Tribes 2 corpus do
this, and it is worth x1.85 in file size -- losing it is by far the largest
cost of dropping the pickled payload that used to replay it verbatim.

It cannot be replayed once a mesh is re-derived from Blender, because each LOD
is a separate mesh datablock whose vertices are deduplicated independently.  So
it is rebuilt instead, by interning every detail level of one object into a
single pool **lowest detail first**.  Each LOD then occupies a prefix of the
pool by construction -- there is no nesting test to fail, because the pool only
ever grows and a sealed length never moves.

The interning key is geometric (quantized position, uv and normal in DTS
space).  It has to be: the per-mesh dedup in ``blender_to_shape`` keys on the
Blender vertex index, which means nothing across two datablocks.  Positions
match bit-exactly between an object's LODs because they share one ``to_dts``
matrix, applied to the same float32 values the importer wrote.

Sharing is never a size loss.  The pool is the union of the per-LOD vertex
sets, so it can never be larger than their sum, which is what the LODs would
cost stored separately.  A pool carrying vertices the largest LOD does not use
costs runtime memory, not bytes, so ``unused_in_slot`` exists to report that
rather than to trigger a fallback.  ``scripts/analyze_lod_share.py`` measures
both against the corpus: 89.3% of objects waste nothing, and the pooled arrays
come to x0.71 of the shipped bytes.
"""

from __future__ import annotations

# Quantization of the interning key.  Position at 5 places is far finer than
# any DTS shape's modelling precision.
#
# Normals are the delicate one.  The importer writes one normal per vertex with
# normals_split_custom_set_from_vertices, but Blender stores split normals
# compressed and hands them back varying from corner to corner of the same
# vertex -- in the *fourth* decimal within one mesh, and rather more than that
# between two meshes with different topology, which is what decides whether a
# detail level can share its neighbour's vertices.
#
# Measured on the fixtures, stored vertices against what the source file holds:
#
#     places   ammo   shield   gman   sqknest   bioderm      (source: 89 / 518
#          4     ...      ...    ...       ...     9384       / 1135 / 301 / 1344)
#          3      88      524   1231       328     2845
#          2      83      497   1145       305     2692
#
# Two places lands the skinned shapes on source parity.  It merges normals
# within about 0.6 degrees of each other: far finer than the 256-entry
# encoded-normal table the format itself ships (dtslib/normals.py), whose
# entries average some 13 degrees apart.  A hard edge is not at risk -- those
# differ by tens of degrees -- and a UV seam still splits, because uv is keyed
# six places down.
#
# Only identity is quantized.  The value stored is the one first seen, at full
# precision.
POS_PLACES = 5
UV_PLACES = 6
NRM_PLACES = 2


class VertexPool:
    """Deduplicating vertex store shared by one object's detail levels."""

    def __init__(self, pos_places=POS_PLACES, uv_places=UV_PLACES, nrm_places=NRM_PLACES):
        self.verts: list[tuple[float, float, float]] = []
        self.tverts: list[tuple[float, float]] = []
        self.norms: list[tuple[float, float, float]] = []
        self._pos_places = pos_places
        self._uv_places = uv_places
        self._nrm_places = nrm_places
        self._index: dict[tuple, int] = {}
        self._sealed = 0
        self._used: set[int] = set()
        # pooled entries the level sealed most recently never referenced;
        # diagnostic only, see the module docstring
        self.unused_in_last_slot = 0

    def __len__(self) -> int:
        return len(self.verts)

    def intern(self, pos, uv, normal, split=None) -> int:
        """Index of this vertex in the pool, adding it if it is new.

        ``split`` forces a vertex to stay distinct from an otherwise identical
        one.  The caller passes the Blender vertex index for a mesh carrying
        shape keys: two coincident vertices can move apart in a later frame, so
        merging them here would silently drop one of the two frame tracks.
        """
        key = (
            tuple(round(c, self._pos_places) for c in pos),
            tuple(round(c, self._uv_places) for c in uv),
            tuple(round(c, self._nrm_places) for c in normal),
            split,
        )
        index = self._index.get(key)
        if index is None:
            index = len(self.verts)
            self._index[key] = index
            self.verts.append(tuple(pos))
            self.tverts.append(tuple(uv))
            self.norms.append(tuple(normal))
        self._used.add(index)
        return index

    def seal(self) -> int:
        """Finish a detail level; returns the prefix length it occupies.

        Everything interned since the last seal belongs to this level.  The
        returned length is final for that level: later levels only append.
        """
        self.unused_in_last_slot = len(self.verts) - len(self._used)
        self._sealed = len(self.verts)
        self._used = set()
        return self._sealed
