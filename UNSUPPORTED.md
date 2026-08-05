# Unsupported and partially supported DTS features

What this add-on does *not* do, as of v1.2.0.  Everything here was read out of
the code rather than remembered; file:line references point at the deciding
line so a claim can be checked or corrected.

"Supported" is not binary.  A DTS feature sits in one of five tiers:

| Tier | Meaning |
| --- | --- |
| **Refused** | The operation stops with an error rather than write something wrong. |
| **Opaque** | Round-trips byte-for-byte through a custom-prop payload, but is invisible and uneditable in Blender. |
| **Blind** | Imports and exports correctly, but nothing in the viewport shows it, so you are editing numbers you cannot see. |
| **Dropped** | Read but not written, or written but not read — data is silently or noisily lost. |
| **Frozen** | Present and correct, but a Blender-side edit does not reach the file. |

The tiers below are ordered by how likely they are to surprise you.

---

## 1. Refused outright

These stop with a clear error.  None of them corrupt a file.

| Feature | Behaviour | Where |
| --- | --- | --- |
| DTS versions 15–16 | `unsupported DTS version 15 (supported: 17-24)`.  The keyframe-table era stores animation in a layout nothing else in the reader shares. | `dtslib/old_reader.py:40` |
| DTS version 25+ | Same error.  Torque 3D–era shapes are not read. | `dtslib/reader.py:71` |
| Writing any version but 23/24 | `only 24 (Torque) and 23 (Tribes 2) are supported — older versions keep skins in a separate section`.  You cannot import a v19 shape and write a v19 shape. | `dtslib/writer.py:22` |
| Ground frames in a v23 export | Refused unless **Strip Ground Frames** is checked, which discards them (movement animations lose their ground speed). v23 has nowhere to store them. | `dtslib/writer.py:27`, `ops/export_dts.py:37` |
| Editing a sorted or multi-matframe mesh | `is a {sorted,multi-matframe} mesh that only round-trips verbatim, but its geometry has been edited — revert the edits or delete the object` | `mapping/blender_to_shape.py:530` |
| More than 192 nodes or objects | `TSIntegerSet` is 6 dwords wide, so a shape cannot name a 193rd node in a matters set. | `mapping/blender_to_shape.py:77,287`, `dtslib/primitives.py:14` |
| More than 65535 unique vertices in one mesh | 16-bit index buffer. | `mapping/blender_to_shape.py:588` |
| Exporting without an armature | `select an armature (the DTS shape root)` — the armature *is* the shape. | `mapping/blender_to_shape.py:62` |

---

## 2. Opaque — round-trips, but you cannot see or edit it

Preserved as a pickled/JSON custom property and re-emitted verbatim.  Safe to
import and re-export; impossible to author or modify.

- **Sorted meshes** (`SORTED_MESH`) — the engine's BSP-ordered geometry.  The
  cluster/BSP tables cannot be re-derived from a Blender mesh at all, so these
  carry `dts_strict_freeze` and refuse edits. `mapping/shape_to_blender.py:401`
- **Multi-matframe meshes** — same treatment, same reason.
- **IFL material entries** (animated texture flipbooks).  Stored as
  `dts_ifl_materials` JSON on the armature; per-sequence membership as
  `dts_ifl_matters`.  You cannot add, remove or preview one.
  `mapping/shape_to_blender.py:192`, `mapping/sequences.py:407`
- **Node scale animation** (uniform, aligned and arbitrary).  Preserved as a
  `dts_scale_anim` JSON blob on the action.  Scaling a pose bone in Blender
  does **not** produce scale animation. `mapping/sequences.py:435`
- **Mesh strip packing, vertex order, `parent_mesh` vertex sharing,
  `merge_indices` and encoded normals.**  Preserved via `dts_source_payload`
  and re-emitted verbatim *only while the geometry is untouched* — a digest
  detects edits.  Edit the mesh and all of it is regenerated from scratch,
  which roughly triples the file. `mapping/shape_to_blender.py:395`

---

## 3. Blind — correct in the file, invisible in the viewport

The data survives a round trip, but Blender shows you nothing, so there is no
way to check your work short of re-reading the exported file.

- **`frame` (vertex animation) tracks.**  Multi-frame meshes import as shape
  keys `frame_001…frame_NNN`, and the sequence's `frame` track is preserved in
  `dts_object_anim` — but nothing drives the shape keys from the track.  The
  frames are there and the animation is there; they are not connected.
  `mapping/shape_to_blender.py:413`
- **`matframe` (material/UV animation) tracks.**  Preserved in
  `dts_object_anim`, no preview at all.
- **Triggers** (footstep sounds, effect hooks).  `dts_triggers` JSON on the
  action.  No timeline markers, no UI.
- **Ground frames** (root-motion speed).  `dts_ground` JSON.  Not shown as
  motion.
- **Blend sequences.**  Import stores raw blend offsets in the pose, which is
  correct for export but does not look like the additive result the engine
  produces.
- **Material maps beyond the diffuse texture** — bump, detail and
  reflectance/environment maps, plus `detail_scale` and `reflection_amount`.
  Round-trip as `dts_*` custom props on the material; the Principled BSDF the
  importer builds ignores all of them.  A map referencing a material that was
  not exported is dropped with a warning. `mapping/materials.py:199,260`

Object **visibility** and **decals** used to be on this list.  Both are now
previewed through per-object drivers into alpha, and round-trip through DTS —
see the README.  Both are still blind in one direction: see §4.

Two caveats specific to decals, neither of which occurs in the Tribes 2 corpus
or changes what renders:

- **Multi-frame decals import only their first frame**, and the rest are lost
  on export (warned).  A decal frame is a whole alternative projection *and*
  face subset, so previewing more than one would mean a mesh per frame.  All
  10,584 decal meshes across the 240-shape corpus are single-frame.
- **Decal indices may alias to a coincident vertex.**  Export re-derives them
  by matching faces onto the target's vertices, and a mesh splits vertices at
  UV seams, so a position can name several.  Round-tripping bioderm_light's
  144 decal meshes, the covered triangles are identical *by position* but 2
  of 23 name a different duplicate index.  The engine reads position alone,
  so the render is unchanged.

---

## 4. Dropped — data is lost

- **Everything object-related in a DSQ.**  `DsqFile` has no `object_states`,
  `decal_states` or IFL tables at all (`dtslib/types.py:345`), so exporting an
  action to `.dsq` silently discards its visibility, frame, matframe and decal
  tracks.  Visibility and decal states round-trip through **DTS only**.  There
  is currently no warning when this happens — worth adding.
- **Decal faces that do not sit on the target mesh.**  A decal can only cover
  its target's own geometry, since the engine indexes the target's vertex
  array; a face moved off it is dropped with a warning.
  `mapping/decals.py` (`_decal_mesh_from_blender`)
- **Scale animation in a DSQ, both directions.**  `dtslib` reads and writes
  the DSQ scale tables, but `mapping/dsq.py` never touches them, so importing
  a scale-animated DSQ loses the scale and exporting one never writes it.
  (The DTS path preserves it as a blob — §2.)
- **`frame_*` shape keys on a skinned mesh.**  `'X': frame_* shape keys on a
  skin are not supported; ignored` — DTS cannot combine vertex animation with
  skinning. `mapping/blender_to_shape.py:624`
- **DSQ channels for nodes the armature lacks.**  `DSQ node 'X' not found in
  armature; its channels are dropped` — expected when applying a sequence to a
  different skeleton. `mapping/dsq.py:59`
- **Bone channels with no DTS node.**  A bone you add in Blender animates
  nothing on export. `mapping/sequences.py:301`, `mapping/dsq.py:180`
- **Duplicate detail sizes for one object.**  `duplicate detail 'X' for object
  'Y'; 'Z' skipped`. `mapping/blender_to_shape.py:156`

---

## 5. Frozen — the file wins over your edit

Subtle, because nothing errors and nothing warns.

- **Sequence length.**  Export uses the stored `dts_keyframes`, falling back to
  the real key count only when the property is absent:
  `n = int(action.get("dts_keyframes", 0)) or _keyframe_count(action)`.  An
  imported sequence always has the property, so **adding or removing keyframes
  in Blender does not change the exported length** — keys are sampled at frames
  `1..n` regardless.  Changing a key's *value* inside that range does export.
  `mapping/sequences.py:274`, `mapping/dsq.py:155`
- **Sequence timing.**  `dts_duration` is the single source of truth.  NLA
  strip scale is display-only by design, so retiming a strip cannot change the
  file — you must edit `dts_duration` on the action.
- **Detail sizes.**  Taken from the object-name suffix (`shape2`, `shape32`,
  `collision-1`), with `dts_*` custom props overriding.  Renaming a collection
  does nothing.
- **Material flags.**  The `dts_*` boolean props are authoritative, not the
  Blender shader.  Making a material transparent in the node editor does not
  set `MAT_TRANSLUCENT`.

---

## 6. Not a DTS feature, but missing

- No warning when a sequence carrying `dts_object_anim` is exported to `.dsq`
  and loses it (§4).
- No UI for any of the `dts_*` custom properties — they are edited through the
  N-panel's Custom Properties, or Python.
- No authoring path for a shape built from scratch: sub-shapes, detail
  hierarchy and object states can only be *preserved*, not created, without
  hand-setting custom properties.

---

## Coverage

`tests/blender/test_operators.py` (41 tests) covers the round-trip of every
"opaque" item above, plus visibility and decal export/reimport.  The remaining
"blind" items are tested at the file level only — no test asserts a preview
exists, because none does.  The "dropped" items have no tests: they are known
losses, not regressions to guard.
