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
| More than 192 nodes or objects | `TSIntegerSet` is 6 dwords wide, so a shape cannot name a 193rd node in a matters set. | `mapping/blender_to_shape.py:76,276`, `dtslib/primitives.py:14` |
| More than 65535 unique vertices in one mesh | 16-bit index buffer. | `mapping/blender_to_shape.py:563` |
| Exporting without an armature | `select an armature (the DTS shape root)` — the armature *is* the shape. | `mapping/blender_to_shape.py:62` |
| Arbitrary node scale | Per-axis factors *plus* an orientation quaternion for the axes to be measured along, which a bone's scale cannot express.  Refused rather than half-written.  No sequence in the 630-shape corpus uses it. | `mapping/sequences.py:502` |

---

## 2. Opaque — round-trips, but you cannot see or edit it

**This tier is empty.**  Nothing is pickled and nothing rides as a JSON string
any more.  The tables that used to live here — the name table, the detail
table, the material ordering, IFL entries, ground frames, triggers and node
rest transforms — are typed collections with UILists on them; see §3 for which
of them you still cannot *preview*, which is a different complaint.


### What used to be here: the mesh payload

No mesh data is on this list any more.  Every mesh is re-derived from the
Blender geometry on every export — there is no pickled payload and nothing is
replayed — so an edit always reaches the file.  `tests/test_no_pickle.py` keeps
it that way.  What the payload used to buy is paid back in the exporter
instead:

- **`parent_mesh` vertex sharing is re-derived, not carried.**  Each detail
  level of an object is interned into one pool lowest-detail-first, so every
  smaller level occupies a prefix of the larger one and names it as its parent
  (`mapping/vertex_pool.py`).  Sharing can never cost bytes — the pool is the
  union of the per-level vertex sets, so it cannot exceed their sum.
- **Strip and fan packing is dropped deliberately.**  Everything exports as
  indexed triangle lists.  Measured across the corpus this is worth **×1.00**:
  312,733 strip primitives become triangles for no size change at all, because
  the u16 index buffer is dwarfed by the float vertex arrays.
- **Vertex order and encoded normals are not preserved.**  Both are recomputed;
  the encoded normals from the 256-entry table in `dtslib/normals.py`.

Where that lands, fixture by fixture: `turret_belly_barrell` ×0.98, `ammo`
×0.97, `pack_upgrade_shield` ×0.97, `weapon_chaingun_ammocasing` ×1.01,
`gman` ×1.23, `bioderm_light` ×1.32.  The two that grow are dominated by
sequence data rather than geometry — see §4.

The remaining geometry cost is normal quantization.  Blender hands split
normals back compressed, varying between two meshes of different topology, so
the pool keys them two decimals down (~0.6°, far finer than the format's own
encoded-normal table) or detail levels stop matching each other.
`mapping/vertex_pool.py`

---

## 3. Blind — correct in the file, invisible in the viewport

The data survives a round trip, but Blender shows you nothing, so there is no
way to check your work short of re-reading the exported file.

- **`frame` (vertex animation) tracks.**  Multi-frame meshes import as shape
  keys `frame_001…frame_NNN`, and the sequence's `frame` track is keyframed on
  the armature as `dts_frame_<object>` — but nothing drives the shape keys from
  it.  The frames are there and the animation is there; they are not connected.
  `mapping/shape_to_blender.py:490`
- **`matframe` (material/UV animation) tracks.**  Keyframed as
  `dts_mat_frame_<object>` on the armature, no preview at all.
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
  for what an edit costs. `mapping/shape_to_blender.py:435`
- **Sorted meshes.**  A `SORTED_MESH` is the engine's answer to translucency:
  its triangles are partitioned into a small tree of clusters, and at draw time
  the engine walks the tree from the camera to get a back-to-front order without
  sorting per frame.  Roughly 120 meshes across 33 corpus shapes — trees,
  vehicle canopies — all of them on translucent materials.

  The tree is regenerated on export from the geometry itself, so the mesh is
  ordinary editable geometry; it used to be frozen behind the payload and
  refused edits.  `dts_sorted_mode` (`NONE`/`FLAT`/`BSP`), `dts_sorted_depth`
  and `dts_always_write_depth` say how.  Nothing in Blender previews the draw
  order, and the generated tree does not match the original.

  It is not inferred from material translucency: that would silently convert
  every translucent mesh in a scene into a sorted one and change how the engine
  draws it.  Triangles are never split, since that would change the vertex count
  and break the detail-level sharing above, so a large face crossing a splitting
  plane can still draw out of order — as it can in the shipped art, which drops
  triangles entirely from some angles on 54 of its 119 sorted meshes.
  `dtslib/sorted_build.py`
- **Mesh flag bits.**  All four defined bits (`MESH_BILLBOARD`,
  `MESH_BILLBOARD_Z_AXIS`, `MESH_HAS_DETAIL_TEXTURE`,
  `MESH_USE_ENCODED_NORMALS`) plus the mesh-type echo in the low three bits get
  a named boolean, so nothing hides in a packed word.  Only `dts_billboard`
  changes anything you can see.  Undocumented bits are dropped with a warning;
  no corpus mesh has one. `mapping/shape_to_blender.py:392`
- **Triggers** (footstep sounds, effect hooks).  A collection on the action,
  edited in the DTS tab of the Dope Sheet or NLA sidebar: a state number 1..30
  and two flags rather than the packed U32 the file holds.  Pose markers show
  where they fire, but nothing plays a sound.  `props/sequence.py`
- **Ground frames** (root-motion speed).  A collection on the action, in the
  same panel.  Editable as numbers; nothing shows them as motion.
- **IFL material entries** (animated texture flipbooks).  A collection on the
  armature with a UIList, and per-sequence membership in the sequence panel.
  You can add and remove them; nothing previews one.
  `props/shape.py`, `mapping/shape_to_blender.py:219`
- **The name table, detail table and material ordering.**  Collections on the
  armature under Object Properties → DTS Shape.  The name table's *order* is
  load-bearing — every name index in the file is an offset into it — which the
  panel says and the reorder buttons respect.  Material slots are real
  pointers rather than names, which also fixes the aliasing in §4: two
  materials sharing a name can no longer be confused for each other.
- **A node's stored rest transform.**  On the bone, under Bone Properties →
  DTS Node, as the raw `Quat16` int16s the file holds.  It was a JSON dict on
  the armature keyed by DTS node name, so a bone's own rest data lived
  somewhere else under a name that is not always the bone's.  Clear
  **Keep Imported Rest Transform** and the bone's matrix wins.  `props/node.py`
- **Blend sequences.**  Import stores raw blend offsets in the pose, which is
  correct for export but does not look like the additive result the engine
  produces.  They are also the one case where both a rotation and a location
  curve are written for every animated node: a blend pose is an absolute
  offset, so a missing channel is not the same as an identity one.
- **Node scale animation** rides the pose bones' own `scale` channels, and
  `dts_scale_mode` on the action says which of the DTS forms to write.
  Scaling a pose bone now produces scale animation; it used to be a
  `dts_scale_anim` blob that the bone had no connection to.  Nothing shows you
  that a scale channel means *node* scale rather than object scale.
  `mapping/sequences.py:176`
- **Material maps beyond the diffuse texture** — bump, detail and
  reflectance/environment maps, plus `detail_scale` and `reflection_amount`.
  Round-trip as `dts_*` custom props on the material; the Principled BSDF the
  importer builds ignores all of them.  A map referencing a material that was
  not exported is dropped with a warning. `mapping/materials.py:351,437`
  Two further ways a map slot can be lost or silently retargeted are in §4.
- **Material flags.**  All fourteen bits have a checkbox, in Material
  Properties → DTS Material.  Four of them —`MAT_MIP_MAP_ZERO_BORDER` (8),
  `MAT_IFL_FRAME` (28), `MAT_DETAIL_MAP_ONLY` (29) and `MAT_BUMP_MAP_ONLY`
  (30) — used to survive only inside a packed `dts_flags` word, so setting one
  meant computing an integer by hand.  None of the four occurs anywhere in the
  630-file corpus.  Nothing previews what any of them does.
  `mapping/materials.py:47`
- **Subtractive materials preview only approximately.**  EEVEE has no
  subtractive blend mode.  `MAT_SUBTRACTIVE` is encoded as the additive graph
  with the emission colour inverted — this add-on's own convention, chosen so
  the flag has a place on the material to live.  It round-trips exactly; it
  does not render the way the engine draws it.  No shape in the 630-file corpus
  is subtractive, so the encoding has never been checked against real art.
  `mapping/materials.py:226`


All three object-state channels — `vis`, `frame` and `matframe` — plus decal
states are keyframed on the armature and **read back off those curves on
export**, so editing a key changes the file.  Until they were, export rebuilt
the tracks from `dts_object_anim` / `dts_decal_anim` JSON and never looked at
the curves the drivers read: the blob was the authored form and the keys were
decoration.  `mapping/objectstate.py`

Object **visibility** and **decals** used to be on this list.  Both are now
previewed through per-object drivers into alpha, and round-trip through DTS —
see the README.  Both are still blind in one direction: see §4.

**Translucent and additive** materials are no longer blind either.  Both are
carried by the shader the importer builds — `surface_render_method` for
translucency, `Transparent BSDF + Emission -> Add Shader` for additive — and
export reads the flags back out of it, so editing the material in the node
editor changes the file. `mapping/materials.py:318`

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
- **Skin and multi-frame meshes do not share vertices across detail levels.**
  A shared skin would need `initial_verts`, `vertex_index`, `bone_index`,
  `weight` and `node_index` to be prefixes too (`dtslib/mesh_io.py:107-140`),
  and a multi-frame mesh's array runs past the shared prefix into its frame
  blocks.  A multi-frame mesh can still be a *parent*.  14 meshes in the whole
  corpus share a skin, so this costs almost nothing.
  `mapping/blender_to_shape.py:466`
- **Sequences re-export larger than they were read.**  `gman`'s node rotations
  go from 13,556 to 18,588 entries, which is most of that shape's growth.  This
  predates the geometry work and is not caused by it: the exporter writes a key
  for every node in the stored matters sets rather than for the channels that
  actually exist.
- **`merge_indices` naming a vertex no face uses.**  A strip-packed source mesh
  carries vertices that only ever appear in a degenerate stitch triangle.  Once
  the mesh is edited and re-derived as triangle lists those vertices are gone,
  so a merge entry pointing at one has nothing left to name and is dropped with
  a warning — 15 of 61 entries on `weapon_energy_vehicle`'s first mesh.  The
  entries that survive are remapped exactly.  An unedited mesh still round-trips
  the whole table through the payload. `mapping/blender_to_shape.py:649`
- **Decal faces that do not sit on the target mesh.**  A decal can only cover
  its target's own geometry, since the engine indexes the target's vertex
  array; a face moved off it is dropped with a warning.
  `mapping/decals.py` (`_decal_mesh_from_blender`)
- **Scale animation in a DSQ, both directions.**  `dtslib` reads and writes
  the DSQ scale tables, but `mapping/dsq.py` never touches them, so importing
  a scale-animated DSQ loses the scale and exporting one never writes it.  The
  DTS path keyframes it onto the bones — see §3 — so this is now a gap in the
  DSQ path alone.
- **`frame_*` shape keys on a skinned mesh.**  `'X': frame_* shape keys on a
  skin are not supported; ignored` — DTS cannot combine vertex animation with
  skinning. `mapping/blender_to_shape.py:650`
- **DSQ channels for nodes the armature lacks.**  `DSQ node 'X' not found in
  armature; its channels are dropped` — expected when applying a sequence to a
  different skeleton. `mapping/dsq.py:60`
- **Bone channels with no DTS node.**  A bone you add in Blender animates
  nothing on export. `mapping/sequences.py:297`, `mapping/dsq.py:179`
- **Duplicate detail sizes for one object.**  `duplicate detail 'X' for object
  'Y'; 'Z' skipped`. `mapping/blender_to_shape.py:155`
- **`dts_bump_map` and `dts_detail_map` on a material created in Blender.**
  The export path decides whether a material carries map references by testing
  for `dts_reflectance_map` *alone* — `has_refs = _MAP_PROPS[0] in bmat` — and
  when it is absent it writes engine-safe defaults instead of reading the other
  two props.  So a new material given only `dts_bump_map` and `dts_detail_map`
  exports as `bump=NO_MAP, detail=NO_MAP`, with no warning.  Adding
  `dts_reflectance_map` as well makes all three take effect; imported materials
  always carry all three, so they are unaffected.
  `mapping/materials.py:450`
- **The identity of a map target whose name is not unique.**  Map slots are
  stored as material *names* so they survive reordering, and resolved back
  through `index_by_name = {m.name.lower(): i ...}`, where a later entry
  overwrites an earlier one.  Material names are not unique in real shapes —
  the importer says so itself, and keys material identity on
  `dts_material_index` for that reason (`mapping/materials.py:356`) — so a slot
  pointing at the *first* of two materials named `glass` comes back pointing at
  the last, silently.  104 of the 630 corpus shapes have duplicate material
  names, but none of them has a map slot targeting a duplicated name, so no
  real file is currently mistranslated. `mapping/materials.py:336,426`

---

## 5. Frozen — the file wins over your edit

Subtle, because nothing errors and nothing warns.  Two entries left this tier:
**sequence length** now comes from the keys the action actually has, and the
**rotation/translation matters sets** are inferred from the channels that
exist, so adding a bone channel marks its node instead of being ignored.

- **Sequence timing.**  `dts_duration` is the single source of truth.  NLA
  strip scale is display-only by design, so retiming a strip cannot change the
  file — you must edit `dts_duration` on the action.
- **Detail sizes.**  Taken from the object-name suffix (`shape2`, `shape32`,
  `collision-1`), with `dts_*` custom props overriding.  Renaming a collection
  does nothing.
- **Material flags, except the three blend bits.**  The checkboxes are
  authoritative for wrapping, self-illumination, env-mapping, mip-mapping and
  the IFL bits; the Blender shader has no bearing on them, so a material that
  looks unlit still exports as self-illuminating if the box is ticked.
  `MAT_TRANSLUCENT`, `MAT_ADDITIVE` and `MAT_SUBTRACTIVE` are not on this list
  — they are read off the shader, and the boxes are rewritten to match on
  export, which is why the panel shows those three greyed out.
  `mapping/materials.py:389`
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

- No warning when a sequence's object-state tracks are exported to `.dsq` and
  lost (§4).
- **Some scalar `dts_*` properties still have no panel.**  The ones with a
  decision behind them are laid out — mesh flags and sorted mode in Object
  Properties → DTS Mesh, the material flags and map slots in Material
  Properties → DTS Material, sequence timing in the Dope Sheet's DTS tab.  The
  rest (subshape indices, object detail numbers, default object states) are
  still raw entries in the N-panel's Custom Properties: a real place to edit
  them, but not a designed one.
- No authoring path for a shape built from scratch: sub-shapes, detail
  hierarchy and object states can only be *preserved*, not created, without
  hand-setting custom properties.
- A scene saved by v1.2 or earlier converts on load (`props/migrate.py`), but
  its **mesh payloads are discarded rather than unpickled** — deliberately, since
  reading one would put `pickle.loads` back on a path fed by an arbitrary
  `.blend`.  Strip packing, merge indices, material frames and cluster tables
  are lost with it; re-import the `.dts` to recover them.  A note on the
  armature says so, and export refuses while a legacy key is still present.

---

## Coverage

`tests/blender/test_operators.py` (72 tests) covers the round-trip of every
"opaque" item above, plus visibility and decal export/reimport.  The remaining
"blind" items are tested at the file level only — no test asserts a preview
exists, because none does.  Most "dropped" items have no tests: they are known
losses, not regressions to guard; the exception is the partial `merge_indices`
loss, which is asserted exactly so it cannot quietly get worse.

Several of those tests assert that an *edit* reaches the file — UVs, material
frames, merge indices, both halves of the flags word, and the re-derived
detail-level vertex sharing.  Each is paired with a
mutation in `scripts/mutate.py` that disables the capability and checks the
test notices, because import and export share property names and a round-trip
test can otherwise pass without either end touching the file.  Run them with
`scripts/mutate.py`; `--list` shows what each one breaks.
