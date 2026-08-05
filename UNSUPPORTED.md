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
| Editing a sorted mesh | `is a sorted mesh that only round-trips verbatim, but its geometry has been edited — revert the edits or delete the object`.  The BSP cluster tables cannot yet be re-derived.  Multi-matframe meshes used to be refused too and no longer are. | `mapping/blender_to_shape.py:483` |
| More than 192 nodes or objects | `TSIntegerSet` is 6 dwords wide, so a shape cannot name a 193rd node in a matters set. | `mapping/blender_to_shape.py:77,287`, `dtslib/primitives.py:14` |
| More than 65535 unique vertices in one mesh | 16-bit index buffer. | `mapping/blender_to_shape.py:541` |
| Exporting without an armature | `select an armature (the DTS shape root)` — the armature *is* the shape. | `mapping/blender_to_shape.py:62` |

---

## 2. Opaque — round-trips, but you cannot see or edit it

Preserved as a pickled/JSON custom property and re-emitted verbatim.  Safe to
import and re-export; impossible to author or modify.

- **Sorted meshes** (`SORTED_MESH`) — the engine's BSP-ordered geometry, used
  for translucent surfaces that need a back-to-front draw order.  The cluster
  tables cannot yet be re-derived from a Blender mesh, so these carry
  `dts_strict_freeze` and refuse edits. `mapping/shape_to_blender.py:541`
- **IFL material entries** (animated texture flipbooks).  Stored as
  `dts_ifl_materials` JSON on the armature; per-sequence membership as
  `dts_ifl_matters`.  You cannot add, remove or preview one.
  `mapping/shape_to_blender.py:203`, `mapping/sequences.py:208`
- **Node scale animation** (uniform, aligned and arbitrary).  Preserved as a
  `dts_scale_anim` JSON blob on the action.  Scaling a pose bone in Blender
  does **not** produce scale animation. `mapping/sequences.py:255`
- **Mesh strip packing, vertex order, `parent_mesh` vertex sharing and encoded
  normals.**  Preserved via `dts_source_payload` — a pickled `dtslib.Mesh` —
  and re-emitted verbatim *only while the mesh is untouched*.  Edit it and all
  of that is regenerated from scratch. `mapping/shape_to_blender.py:558`
  What that costs is now measured rather than guessed
  (`scripts/analyze_lod_share.py`): strip packing is worth **×1.00**, because
  the u16 index buffer is dwarfed by the float vertex arrays, while losing
  `parent_mesh` sharing costs **×1.85 on average and ×3.5 at worst**.
  The digest covers UVs, split normals, material assignment, shape keys, skin
  weights and material-frame attributes as well as geometry
  (`mapping/shape_to_blender.py:277`); until it did, editing any of those left
  the digest matching and the edit was silently discarded.

---

## 3. Blind — correct in the file, invisible in the viewport

The data survives a round trip, but Blender shows you nothing, so there is no
way to check your work short of re-reading the exported file.

- **`frame` (vertex animation) tracks.**  Multi-frame meshes import as shape
  keys `frame_001…frame_NNN`, and the sequence's `frame` track is preserved in
  `dts_object_anim` — but nothing drives the shape keys from the track.  The
  frames are there and the animation is there; they are not connected.
  `mapping/shape_to_blender.py:563`
- **`matframe` (material/UV animation) tracks.**  Preserved in
  `dts_object_anim`, no preview at all.
- **The material frames themselves.**  A mesh with `num_mat_frames > 1` is a
  texture flipbook: one vertex array and several blocks of texture
  coordinates.  Frame 0 is the active UV map; frames 1..n-1 are `FLOAT2`
  POINT-domain attributes named `dts_matframe_001…`, listed in Object Data
  Properties → Attributes and editable in the Spreadsheet editor.  Nothing
  previews a frame other than 0.  Attributes rather than UV layers because
  `Mesh.uv_layers` caps at 8 while real shapes reach 62 — and it caps
  *silently*, `uv_layers.new()` returning without adding. `mapping/matframes.py`
- **`merge_indices`**, the legacy LOD-morph table.  An int array on the mesh
  object (`dts_merge_indices`), editable only as raw numbers in the N-panel.
  Order matters and entries repeat, so a vertex group cannot hold it.  See §4
  for what an edit costs. `mapping/shape_to_blender.py:468`
- **Mesh flag bits.**  All four defined bits (`MESH_BILLBOARD`,
  `MESH_BILLBOARD_Z_AXIS`, `MESH_HAS_DETAIL_TEXTURE`,
  `MESH_USE_ENCODED_NORMALS`) plus the mesh-type echo in the low three bits get
  a named boolean, so nothing hides in a packed word.  Only `dts_billboard`
  changes anything you can see.  Undocumented bits are dropped with a warning;
  no corpus mesh has one. `mapping/shape_to_blender.py:425`
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
  not exported is dropped with a warning. `mapping/materials.py:351,437`
  Two further ways a map slot can be lost or silently retargeted are in §4.
- **Four material flag bits have no named property.**  `_FLAG_PROPS` gives a
  boolean checkbox to ten of the fourteen flags; `MAT_MIP_MAP_ZERO_BORDER` (8),
  `MAT_IFL_FRAME` (28), `MAT_DETAIL_MAP_ONLY` (29) and `MAT_BUMP_MAP_ONLY` (30)
  survive only inside the packed `dts_flags` word.  They import and export
  correctly, but setting one means computing an integer by hand.  None of the
  four occurs anywhere in the 630-file corpus. `mapping/materials.py:35`
- **Subtractive materials preview only approximately.**  EEVEE has no
  subtractive blend mode.  `MAT_SUBTRACTIVE` is encoded as the additive graph
  with the emission colour inverted — this add-on's own convention, chosen so
  the flag has a place on the material to live.  It round-trips exactly; it
  does not render the way the engine draws it.  No shape in the 630-file corpus
  is subtractive, so the encoding has never been checked against real art.
  `mapping/materials.py:226`
- **`dts_flags` holds the low 31 bits only.**  Blender's integer
  ID-properties are a C `int`, so a word past `INT_MAX` cannot be assigned at
  all.  `MAT_REFLECTANCE_MAP_ONLY` (1 << 31) is the one material flag above
  that line: it is masked out of the stored word and carried by its
  `dts_reflectance_map_only` checkbox, which `_flags_from_blender` ORs back in.
  Read `dts_flags` as the full word and you will be wrong about that one bit.
  `mapping/materials.py:53,348`

Object **visibility** and **decals** used to be on this list.  Both are now
previewed through per-object drivers into alpha, and round-trip through DTS —
see the README.  Both are still blind in one direction: see §4.

**Translucent and additive** materials are no longer blind either.  Both are
carried by the shader the importer builds — `surface_render_method` for
translucency, `Transparent BSDF + Emission -> Add Shader` for additive — and
export reads the flags back out of it, so editing the material in the node
editor changes the file. `mapping/materials.py:309`

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
- **`merge_indices` naming a vertex no face uses.**  A strip-packed source mesh
  carries vertices that only ever appear in a degenerate stitch triangle.  Once
  the mesh is edited and re-derived as triangle lists those vertices are gone,
  so a merge entry pointing at one has nothing left to name and is dropped with
  a warning — 15 of 61 entries on `weapon_energy_vehicle`'s first mesh.  The
  entries that survive are remapped exactly.  An unedited mesh still round-trips
  the whole table through the payload. `mapping/blender_to_shape.py:588`
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
  skinning. `mapping/blender_to_shape.py:601`
- **DSQ channels for nodes the armature lacks.**  `DSQ node 'X' not found in
  armature; its channels are dropped` — expected when applying a sequence to a
  different skeleton. `mapping/dsq.py:59`
- **Bone channels with no DTS node.**  A bone you add in Blender animates
  nothing on export. `mapping/sequences.py:303`, `mapping/dsq.py:180`
- **Duplicate detail sizes for one object.**  `duplicate detail 'X' for object
  'Y'; 'Z' skipped`. `mapping/blender_to_shape.py:156`
- **`dts_bump_map` and `dts_detail_map` on a material created in Blender.**
  The export path decides whether a material carries map references by testing
  for `dts_reflectance_map` *alone* — `has_refs = _MAP_PROPS[0] in bmat` — and
  when it is absent it writes engine-safe defaults instead of reading the other
  two props.  So a new material given only `dts_bump_map` and `dts_detail_map`
  exports as `bump=NO_MAP, detail=NO_MAP`, with no warning.  Adding
  `dts_reflectance_map` as well makes all three take effect; imported materials
  always carry all three, so they are unaffected.
  `mapping/materials.py:445`
- **The identity of a map target whose name is not unique.**  Map slots are
  stored as material *names* so they survive reordering, and resolved back
  through `index_by_name = {m.name.lower(): i ...}`, where a later entry
  overwrites an earlier one.  Material names are not unique in real shapes —
  the importer says so itself, and keys material identity on
  `dts_material_index` for that reason (`mapping/materials.py:345`) — so a slot
  pointing at the *first* of two materials named `glass` comes back pointing at
  the last, silently.  104 of the 630 corpus shapes have duplicate material
  names, but none of them has a map slot targeting a duplicated name, so no
  real file is currently mistranslated. `mapping/materials.py:336,426`

---

## 5. Frozen — the file wins over your edit

Subtle, because nothing errors and nothing warns.

- **Sequence length.**  Export uses the stored `dts_keyframes`, falling back to
  the real key count only when the property is absent:
  `n = int(action.get("dts_keyframes", 0)) or _keyframe_count(action)`.  An
  imported sequence always has the property, so **adding or removing keyframes
  in Blender does not change the exported length** — keys are sampled at frames
  `1..n` regardless.  Changing a key's *value* inside that range does export.
  `mapping/sequences.py:277`, `mapping/dsq.py:155`
- **Sequence timing.**  `dts_duration` is the single source of truth.  NLA
  strip scale is display-only by design, so retiming a strip cannot change the
  file — you must edit `dts_duration` on the action.
- **Detail sizes.**  Taken from the object-name suffix (`shape2`, `shape32`,
  `collision-1`), with `dts_*` custom props overriding.  Renaming a collection
  does nothing.
- **Material flags, except the three blend bits.**  The `dts_*` boolean props
  are authoritative for wrapping, self-illumination, env-mapping, mip-mapping
  and the IFL bits; the Blender shader has no bearing on them.
  `MAT_TRANSLUCENT`, `MAT_ADDITIVE` and `MAT_SUBTRACTIVE` are no longer on this
  list — they are read off the material, and their props are rewritten to match
  on export. `mapping/materials.py:393`
- **The blend state of a material that fades.**  The visibility and decal
  wiring force `surface_render_method = BLENDED` so a fade renders at all,
  which on an opaque material would otherwise read back as `MAT_TRANSLUCENT`.
  The state from before the fade is recorded once in `dts_blend_before_fade`,
  and *that* is what export reads — so on a faded material, switching the
  render method by hand does not reach the file.  Delete the property to make
  the current setting count.  `mapping/materials.py:286`,
  `mapping/visibility.py:185`, `mapping/decals.py:242`

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

`tests/blender/test_operators.py` (51 tests) covers the round-trip of every
"opaque" item above, plus visibility and decal export/reimport.  The remaining
"blind" items are tested at the file level only — no test asserts a preview
exists, because none does.  Most "dropped" items have no tests: they are known
losses, not regressions to guard; the exception is the partial `merge_indices`
loss, which is asserted exactly so it cannot quietly get worse.

Five of those tests assert that an *edit* reaches the file — UVs, material
frames, merge indices and both halves of the flags word.  Each is paired with a
mutation in `scripts/mutate.py` that disables the capability and checks the
test notices, because import and export share property names and a round-trip
test can otherwise pass without either end touching the file.  Run them with
`scripts/mutate.py`; `--list` shows what each one breaks.
