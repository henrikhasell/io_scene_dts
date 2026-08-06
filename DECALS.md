# Decal projection, and how far it reverses

How a DTS decal is projected onto its target, what the original exporter did
that this add-on does not, and which parts of the operation can be run
backwards.  Written to explain the one documented loss in `mapping/decals.py`
— the recomputed coverage set — and to record where the answer came from.

Everything below is read out of source, not remembered.  See
[Provenance](#provenance) for what was read and what was inferred.

---

## 1. What the file stores

A `TSDecalMesh` has no geometry.  It stores a subset of its *target* mesh's
indices, one material, and two `Point4F` planes.  The engine computes every UV
as a dot product (`tsDecal.cc`, quoted in `mapping/decals.py:6`):

    tv.x = v->x*s.x + v->y*s.y + v->z*s.z + s.w;
    tv.y = v->x*t.x + v->y*t.y + v->z*t.z + t.w;

So the projection is an affine map ℝ³ → ℝ², eight floats wide:

    u = S.xyz · v + S.w
    w = T.xyz · v + T.w

Two facts follow immediately, and most of this document is their consequences:

- **The map is rank ≤ 2.**  Its kernel is at least one-dimensional — a whole
  line of displacement in object space produces no UV change.
- **The eight floats *are* the projection.**  Nothing about it is stored
  anywhere else, so "recovering the projection" from a file is not a
  reconstruction problem.  It is already there.

---

## 2. How the original exporter built the planes

The pre-"Plus" `max2dtsExporter` — the only DTS exporter known to have written
decals — did **not** author them from a projector.  In Max a decal was a real
mesh with real UVs, tagged by naming its node `Decal::*`
(`SceneEnum.cc:104-106`), and the planes were *solved* from its geometry.

`ShapeMimic::findDecalTexGen` (`ShapeMimic.cc:5846`) takes the decal mesh's
**first face** — three verts and their three UVs — and solves one 4×4 per
plane:

    v0 · S.xyz + S.w = u0
    v1 · S.xyz + S.w = u1
    v2 · S.xyz + S.w = u2
     n · S.xyz       = 0        <- row (n, 0)

and the same with `(w0, w1, w2)` for T.  In the code the origin is first
shifted to the face centroid so the matrix is guaranteed invertible, then `ws`
and `wt` are corrected back for the shift (`ShapeMimic.cc:5871-5912`).

The row count matters.  Three vertices with UVs give **6 equations for 8
unknowns** — under-determined by exactly 2, which is the kernel from §1
appearing again.  The fourth row is what closes it, and it is a *gauge fix
rather than information*: `n · S = 0` and `n · T = 0` say that S and T have no
component along the decal's own normal, i.e. the projection is aimed down that
normal.  This add-on's `projector_for` makes the same choice with the averaged
face normal, which is why the two parameterisations agree.

---

## 3. Is it reversible?

Three separate questions live inside that one.  They have different answers.

| Question | Reversible? |
| --- | --- |
| The projection map, from `(S, T)` | Yes, trivially — it is stored |
| The projector *transform* (a 4×4) | Only up to a one-parameter gauge |
| The planes, from a baked mesh + UVs | Yes, from one triangle, given the gauge |
| The covered face set | **No** — needs art that is not in the file |

### 3.1 The projector transform: a retraction, not an inverse

A 4×4 projector has a third row that no UV depends on, so `texgen_to_projector`
has to invent one.  `_depth_row` (`mapping/decals.py:73`) does, and the module
docstring is right that any row independent of the first two encodes the same
projection.  The consequence is asymmetric:

- `planes -> projector -> planes` is the **identity** (measured ~6e-8, float32
  noise).
- `projector -> planes -> projector` is a **retraction**: it replaces the depth
  row with the canonical normalised cross product.  Two projectors differing
  only in depth-axis direction or scale collapse onto one representative.

It is idempotent, so nothing degrades on repeated round trips.  But a user who
scales a projector empty along its local Z will find that scale silently
normalised on the next export→import cycle, because no UV ever depended on it.
That is worth a line in `UNSUPPORTED.md` under *Frozen*.

Degenerate case: `S × T ≈ 0` (the planes parallel) admits no invertible
projector at all.  `_depth_row` already guards it with the `(0,0,1)` fallback.

### 3.2 Baked mesh + UVs -> planes: yes, from a single triangle

§2 *is* the reverse operation.  Given any one triangle of a decal that still
carries its UVs, the planes come back uniquely under the `n · S = 0`
convention.  You never need the whole decal and you never need a projector.

**The caveat is planarity.**  `getDecalTVert` (`ShapeMimic.cc:5920`) loops over
*every* decal face, finds which one a target vertex projects into, and
interpolates that face's UVs — a piecewise-affine map.  But the file stores two
planes, and `findDecalTexGen` fits them from `faces[0]` alone.  For a
multi-face decal sheet the export collapses piecewise-affine to affine and face
0 wins.  This is very likely why `PlaneDecal` exists as a distinct `decalType`
with its own `getPlaneDecalTVSpecial` fallback (`ShapeMimic.cc:6053`).
*Inferred from those two functions; see Provenance.*

### 3.3 Coverage: not reversible, and not for a geometric reason

The index list is a thresholded function of the planes, and thresholds destroy
information — many projector placements yield an identical face set.  Coverage
→ planes is therefore hopeless, but it also does not matter, because the planes
are in the file.

The direction that matters, planes → coverage, is forward-deterministic and so
in principle reproducible.  The problem is that the original predicate has two
stages and this add-on implements neither of them.

---

## 4. The original coverage rule

### 4.1 `checkDecalFace` — a 2D test in texture space

`ShapeMimic.cc:6268`.  A face is covered iff, **after projection into decal UV
space**, any of:

1. any of its three verts lies in the unit square [0,1]²;
2. (0,0) lies inside the face — i.e. the square is inside the triangle;
3. any face edge intersects the unit square.

(The code tests only `tv0` for case 1, not all three verts as its comment
says; the edge tests in case 3 cover the difference.)  It is also short-
circuited when the decal material wraps — `onBmp` is true outright for a
wrapping material (`:5762`) — but **0 of the corpus's 27,243 decal mesh slots
use a wrapping material**, so it did run for every shipped decal.

This is box-versus-triangle overlap in *texture* space.  `covered_faces`
(`mapping/decals.py:215`) instead tests 3D projector-local coordinates against
`|x|, |y| <= 0.5` with a depth cutoff, under ANY / ALL / CENTRE.  Different
space, and **cases 2 and 3 have no equivalent**: a large triangle that swallows
the decal, or one that merely clips a corner, is covered by the original rule
and missed by all three of ours.  That is a plausible large share of the
measured 0.61 recall.

### 4.2 `checkDecalFilter` — a second gate, driven by art

`ShapeMimic.cc:6304`.  Coverage is *additionally* masked by a **filter
bitmap**: the triangle is scanline-rasterised over a filter image and kept only
if it hits a non-zero texel.  Dynamix masked decal coverage by the texture's
own content.

That bitmap lived in the Max scene.  It is not in the DTS file and cannot be
derived from one.  Note it is *optional*, though: `checkDecalFilter` returns
true when `gDecalInfo.filter` is NULL, so this gate is the identity for any
decal that had no filter assigned, and nothing in the file says which.

### 4.2b `used[]` — a third gate, and the only one we can reproduce

The predicate is not two stages but a three-way conjunction (`:5764`):

    if (!onBmp || !used[idx0] || !used[idx1] || !used[idx2] || !checkDecalFilter(...))

`used[]` is the return of `getDecalTVert` (`:5920`), which requires the target
vertex to project *inside the decal mesh's own faces* — and `getDecalProjPoint`
(`:5982`) rejects on facing:

    if (mDot(normal,n[decalFaceIdx]) < gDecalInfo.minCos) return false;

with `minCos = cos(DECAL::MAX_ANGLE)`, defaulting to 90 degrees (`:5546-5548`).
That is the one part of the rule needing nothing but the shape, and it is now
implemented as `Max Angle` on the projector empty.

### 4.3 What this means for the 0.9% figure

`mapping/decals.py:20-23` records that the best rule recalls 0.61 of the
covered faces at precision 0.21 and reproduces the exact set 0.9% of the time,
and concludes "shipped coverage was picked by hand."

That conclusion is probably wrong, and in a useful direction.  Coverage was not
hand-picked — it was computed by a two-stage predicate, of which we implement a
different-shaped approximation to stage one and nothing of stage two.  It is
texture-driven, which is indistinguishable from arbitrary if you only test
geometric predicates.

---

## 5. Actionable

1. **Done: the facing gate** (§4.2b) is implemented as `Max Angle`, with the
   exporter's own 90-degree default.  As a *fixed* rule it is a poor trade —
   it costs 0.22 recall to buy 0.04 precision and is worse on F1 — so it earns
   its place another way: `fit_coverage` chooses it per decal, with 180 degrees
   in the search so it can be switched off, and without it a decal on the front
   of a shape also lands on the back.
2. **`checkDecalFace` was considered and dropped.**  Porting cases 2 and 3
   would raise recall, but the measurement above shows recall is not where the
   error is concentrated, and the rule would still be gated by a filter bitmap
   nobody has.  A more complicated geometric predicate chasing an art-driven
   residual is the thing item 3 warns against.
3. **Do not chase the last of it.**  Whatever `checkDecalFace` does not
   explain is `checkDecalFilter`, and that needs art nobody has.  Recording
   that as the floor is more honest than continuing to tune a geometric rule
   against it.
4. **Note the depth-axis retraction** (§3.1) in `UNSUPPORTED.md`.  Still open.

---

## Provenance

### Read directly, and quoted above

The pre-"Plus" Torque SDK Max exporter, found locally at
`~/.wine/drive_c/Torque/SDK/tools/max2dtsExporter/`.  This is *not* the Tribes 2
plugin and not the `max2dtsExporterPlus` rewrite — it is the intermediate
GarageGames exporter that still implements decals: 361 decal lines and 17 decal
functions in `ShapeMimic.cc` (8,819 lines).

| What | Where |
| --- | --- |
| Plane solve from one face | `ShapeMimic.cc:5846` `findDecalTexGen` |
| Per-vertex UV, piecewise over decal faces | `ShapeMimic.cc:5920` `getDecalTVert` |
| Projection point, per `decalType` | `ShapeMimic.cc:5982` `getDecalProjPoint` |
| Planar-decal fallback | `ShapeMimic.cc:6053` `getPlaneDecalTVSpecial` |
| Coverage rule, stage 1 | `ShapeMimic.cc:6268` `checkDecalFace` |
| Coverage rule, stage 2 (filter bitmap) | `ShapeMimic.cc:6304` `checkDecalFilter` |
| Decal node naming convention `Decal::*` | `SceneEnum.cc:104-106` |
| Mimic structs | `ShapeMimic.h:152,160` |
| Decal animation sequence params | `Sequence.h:55-57` |

This repo, for the comparison: `mapping/decals.py` module docstring and
`texgen_to_projector` / `projector_to_texgen` / `_depth_row` / `covered_faces`.

### Read, and establishes that no other exporter does this

- **`dtsSDKPlus/ShapeMimic.cpp:198`** (bundled in `nzchris/ms2dtsExporterPlus`)
  — `// compute subShape numDecals -- don't do decals...so this should be easy`,
  then zeroes the counts.  The Plus rewrite dropped the feature.
- **`dtsSDK/DTSMesh.h:66`** — `T_Decal = 2, //!< DEPRECATED`; `DTSShape.h:41,133`
  marks `struct Decal` and `struct DecalState` the same way.
- **`qoh/io_scene_dts`** — reads and writes the decal *table* so files
  round-trip structurally, but `self.decals = []` on construction and
  `grep texgen` returns **0 hits** across the repo.  No `DecalMeshData`, no
  planes read or written.
- **Blender 2.49 Torque Exporter** (Greenawalt, RELEASE0964) — bones, meshes,
  skin weights, sequences, ground frames, triggers, visibility animation,
  animated collision meshes.  No decals.
- **`dae2dts` / `TSShapeConstructor`** — COLLADA has no decal concept and the
  constructor API has no decal methods, so nothing can arrive that way.
- **`~/.wine/drive_c/Torque/SDK/tools/blender/ExportDTS.py`** — a 2005 v0.1 stub.
  Decal fields declared, never used; no writer at all.
- The **DTS format spec** marks decals deprecated throughout, which is why all
  of the above is true.

### Inferred, not verified

- **§3.2, the piecewise-affine collapse.**  Read off `getDecalTVert` and
  `findDecalTexGen` together: the former is piecewise over all decal faces, the
  latter fits from `faces[0]`.  `prepareDecal` (`ShapeMimic.cc:5533`),
  `generateDecalFrame` (`:5669`) and the `decalType` enum were **not** read.  If
  `prepareDecal` rejects non-planar decal meshes then the collapse never
  happens and this caveat is void.
- **§4.1, the share of the recall gap attributable to cases 2 and 3.**  A
  hypothesis, and the point of item 1 in §5.  Not measured.
- **The Tribes 2 3ds Max 2.5 plugin** was never examined — its documentation is
  not online and the binary was not unpacked.  Something wrote the 10,584 decal
  meshes in the corpus; whether it was that plugin, this exporter's ancestor, or
  an unreleased internal tool is unestablished.
