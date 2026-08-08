# Unsupported and partially supported DTS features

What this add-on does *not* do, as of v1.3.0.  Everything here was read out of
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

**A tier is a gap this add-on could close.**  Something the DTS format cannot
express is not an unsupported feature — it is the shape of the problem, and no
amount of work here would change it.  The same goes for what Blender cannot
represent.  Those are real answers to "can I do this", so they are written down,
but in §7 rather than in a tier: keeping them out is what stops the list
overstating what is left to do.

---

## 1. Refused outright

These stop with a clear error.  None of them corrupt a file.  Four more
operations are refused too, but because the format or Blender cannot hold the
result rather than because this add-on will not — they are in §7.

| Feature | Behaviour | Where |
| --- | --- | --- |
| DTS versions 15–16 | `unsupported DTS version 15 (supported: 17-24)`.  The keyframe-table era stores animation in a layout nothing else in the reader shares. | `dtslib/old_reader.py:40` |
| DTS version 25+ | Same error.  Torque 3D–era shapes are not read. | `dtslib/reader.py:71` |
| Writing any version but 23/24 | `only 24 (Torque) and 23 (Tribes 2) are supported — older versions keep skins in a separate section`.  You cannot import a v19 shape and write a v19 shape. | `dtslib/writer.py:22` |
| Exporting without an armature | `select an armature (the DTS shape root)` — the armature *is* the shape. | `mapping/blender_to_shape.py:68` |

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
  `mapping/shape_to_blender.py:581`
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
  for what an edit costs. `mapping/shape_to_blender.py:488`
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

  It *is* inferred from material translucency: a mesh whose material blends and
  whose mode is `NONE` is promoted to `BSP` on export, because sorting is what
  translucency needs and every sorted mesh in the corpus sits on a translucent
  material.  The inference is read off the shader rather than the
  `dts_translucent` prop, additive and subtractive count as translucent, and
  one blended slot out of several is enough.  Setting the mode by hand still
  wins — `NONE` is the value being promoted from, so it is not a way to opt
  out.  Skins and vertex-animation meshes cannot be sorted at all (§7) and keep
  their type, silently, since nobody asked.  What this costs is in §4.
  `mapping/blender_to_shape.py:720`

  Triangles are never split, since that would change the vertex count
  and break the detail-level sharing above, so a large face crossing a splitting
  plane can still draw out of order — as it can in the shipped art, which drops
  triangles entirely from some angles on 54 of its 119 sorted meshes.
  `dtslib/sorted_build.py`
- **Billboards.**  `MESH_BILLBOARD` makes the engine draw a mesh facing the
  camera, by replacing its rotation with the identity and keeping only
  position and scale (`tsMesh.cc:59`); `MESH_BILLBOARD_Z_AXIS` modifies that
  rather than replacing it, keeping the Z axis so the mesh spins but stays
  upright — what a trunk or a flame wants.  87 meshes across 10 corpus shapes
  are billboards; none is Z-axis-locked.

  Both are checkboxes in Object Properties → DTS Mesh, along with
  `MESH_HAS_DETAIL_TEXTURE`, `MESH_USE_ENCODED_NORMALS` and the mesh-type echo
  in the low three bits, so no flag hides in a packed word.  **Nothing in the
  viewport turns a billboard to face you**, which is why they are here: the
  flag is correct in the file and invisible while you work.

  **Authoring one that the engine actually turns is not solved.**  What is
  established, in Tribes 2 as a `StaticShape`:

  - The flags are written correctly — `0x80000000`, and `0xa0000000` with the
    Z-axis bit — read back off the exported file.
  - `grenade_flare.dts` imported and exported again by this add-on **still
    billboards**, side by side with the stock original.  The writer preserves
    a working billboard.
  - The `examples/02_billboards` cards **do not** billboard: they draw lying
    on the ground.  Built flat in Blender's XY, built upright in XZ to match
    every billboard in the corpus, at four sizes, and at object scale 1 and
    1.6 — all draw flat.
  - A probe shape carrying four upright cards *did* billboard, so it is
    reachable from a fresh scene.  The difference between those cards and the
    example's has not been isolated.

  So this is a **blind** item for authoring and a **preserved** one for
  round-tripping.  `MESH_BILLBOARD_Z_AXIS` is worse: no shipped Tribes 2 shape
  sets it (0 of 87 billboard meshes), so there is no reference render to
  compare against at all.
  Undocumented bits are dropped with a warning; no corpus mesh has one.
  `props/mesh.py`, `mapping/shape_to_blender.py:457`
- **Triggers** (footstep sounds, effect hooks).  A collection on the action,
  edited in the DTS tab of the Dope Sheet or NLA sidebar: a state number 1..30
  and two flags rather than the packed U32 the file holds.  Pose markers show
  where they fire, but nothing plays a sound.  `props/sequence.py`
- **Ground frames** (root-motion speed).  A collection on the action, in the
  same panel.  Editable as numbers; nothing shows them as motion.
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
- **Bump and detail maps**, plus `detail_scale`.  Round-trip as `dts_*` custom
  props on the material; the Principled BSDF the importer builds ignores both.
  Neither is used anywhere in the 852-file corpus — 0 materials of 3185 set
  either slot, and `detail_scale` is 1.0 in all of them.  A map referencing a
  material that was not exported is dropped with a warning.
  `mapping/materials.py:1121`
  Two further ways a map slot can be lost or silently retargeted are in §4.
  The reflectance map is no longer on this list; see below.
- **`reflection_amount`**, the scalar the engine multiplies the whole
  reflection by.  Round-trips as a custom prop and previews as nothing at all —
  it is 1.0 in 265 of the 270 env-mapped corpus materials, 0.5 in 3 and 0.1 in
  2. `mapping/materials.py:1122`
- **Material flags.**  Eleven of the fourteen bits have a checkbox, in Material
  Properties → DTS Material.  Four of them —`MAT_MIP_MAP_ZERO_BORDER` (8),
  `MAT_IFL_FRAME` (28), `MAT_DETAIL_MAP_ONLY` (29) and `MAT_BUMP_MAP_ONLY`
  (30) — used to survive only inside a packed `dts_flags` word, so setting one
  meant computing an integer by hand.  None of the four occurs anywhere in the
  630-file corpus.  Nothing previews what any of them does.
  `mapping/materials.py:73`  `MAT_IFL_MATERIAL` (27) is not among them either:
  it is derived from the material's IFL checkbox, the one that also owns its
  frame list.  `MAT_REFLECTANCE_MAP_ONLY` (31) is no longer only
  a checkbox: export sets it on the material-list entries it invents to hold a
  separate reflectance texture.
- **The reflectance map previews as `Metallic`, which is not what the engine
  does.**  A DTS reflectance map is a per-texel mask on a spherical environment
  map, scaled by `reflection_amount`; Blender's Metallic input is a PBR
  parameter that turns the surface into metal.  The mapping was chosen so that
  the shader is the source of truth for *which image is the reflectance* — the
  same reason the blend bits live in the graph rather than in a property — and
  so a bright mask looks reflective in the viewport.  It is a handle, not a
  render. `mapping/materials.py:523`


All three object-state channels — `vis`, `frame` and `matframe` — plus decal
states are keyframed on the armature and **read back off those curves on
export**, so editing a key changes the file.  Until they were, export rebuilt
the tracks from `dts_object_anim` / `dts_decal_anim` JSON and never looked at
the curves the drivers read: the blob was the authored form and the keys were
decoration.  `mapping/objectstate.py`

Object **visibility** and **decals** used to be on this list.  Visibility is
previewed through per-object drivers into alpha.  A decal is previewed by a
branch in its *target's* material — a Texture Coordinate reading the projector
empty's object space, masked to the projector's box and to the one object the
decal targets — with its state driving the branch.  That mask is per *pixel*
where the export decides per *face*, so the preview is close to the exported
coverage rather than identical to it: the exact face set would need one
Attribute node per decal, and EEVEE caps how many attributes a material may
use.  `light_male` puts all 58 of its decals on one body material, which took
it past that cap and rendered the body as broken-material magenta.  The object
half of the mask has the same constraint and the same answer — an Object Info
comparison rather than an attribute, because a material is shared and 5999 of
the corpus's 6053 decals sit on one another DTS object also uses, so the box
alone drew a hull's burn on the turret as well.  `mapping/decals.py:737`  Both
still lose something: see §4.

A gate hides a branch but never stops the GPU running it, so sharing was also
what made this expensive: every mesh on a material paid for every decal on it.
Each target now gets its **own copy** of its material, capping a material at
the decals that actually aim at it — 6 in light_male's worst case rather than
58, measured at 3.9 → 33.4 fps of viewport playback.  The copies collapse back
to one entry on export, because the material list is keyed on `dts_name`, so a
shape whose meshes hold 20 Blender materials still writes the 15 the file had.

**Translucent and additive** materials are no longer blind either.  Both are
carried by the shader the importer builds — `surface_render_method` for
translucency, `Transparent BSDF + Emission -> Add Shader` for additive — and
export reads the flags back out of it, so editing the material in the node
editor changes the file. `mapping/materials.py:374`

One caveat specific to decals, which does not occur in the Tribes 2 corpus:

- **Multi-frame decals import only their first frame**, and the rest are lost
  on export (warned).  A decal frame is a whole alternative projection *and*
  face subset, and a decal is a single projector empty, which has one of each.
  All 10,584 decal meshes across the 240-shape corpus are single-frame.

Two more, both consequences of the object gate above:

- **The preview draws on the target mesh only, and export writes every detail
  level.**  A decal owns a mesh run parallel to its owner object's own slots,
  so the exported file carries a `TSDecalMesh` per LOD; the branch is gated on
  one object, so switching to a coarser detail collection shows no decal even
  though the file has one there.  Nothing is lost — this is the preview being
  narrower than the file, not the file being narrower than the scene.
  `mapping/decals.py:737`
- **`pass_index` on a decal's target belongs to the add-on.**  The gate needs a
  per-object number a shader can read, and Object Index is the only one that
  costs no attribute slot, so targeting a mesh with a decal overwrites whatever
  pass index it had.  The value assigned is recorded on the object as
  `dts_decal_host`, and a pass index edited by hand afterwards is treated as
  the user reclaiming the field: the next refresh takes a fresh number rather
  than trusting the old one.  `mapping/decals.py:360`

The larger decal caveat -- that the covered faces themselves are not preserved
-- is a *dropped* item and is in §4.

---

## 4. Dropped — data is lost

Two of these are *asked for*: the import operator has checkboxes that trade
fidelity for a workable scene, and they are listed here because what they cost
is invisible until you export.

- **Detail levels left out by `Import Detail Levels`, which is off by
  default.**  Every LOD of a shape stands at the same origin, so importing all
  of them stacks overlapping copies — `light_male` arrives as eleven.  Off,
  only the largest visible size is built, and the levels below it have no
  geometry in anything exported from that scene.  The detail *table* survives,
  because it is stored on the armature, so the file still declares its ten
  levels with triangles at one of them.  Collision and LOS details (negative
  sizes) are never treated as LODs and are always imported — dropping one would
  turn a re-exported shape into one the engine cannot collide with.  Warned at
  import.  `mapping/shape_to_blender.py:668`
- **Decals imported by `Import Decals as Meshes`, which is off by default.**
  On, each decal arrives as a copy of the faces the file says it covers, one
  mesh per detail level, and no projector is built.  That is the one thing the
  projector form gives up — coverage is re-derived from the volume at recall
  0.44, so the shipped index list exists in Blender in this form and nowhere
  else — and it is a way to *look at* a file rather than author one: export
  reads projector empties and nothing else, so the decals reach no file, and
  the sequences' `decal_matters` tracks are dropped with them.  The meshes are
  kept out of the object list rather than emitted as phantom objects.  Warned
  at import and again at export, which names `Migrate DTS Scene` as the way to
  turn them into projectors.  `mapping/shape_to_blender.py:191`
- **Skin and multi-frame meshes do not share vertices across detail levels.**
  A shared skin would need `initial_verts`, `vertex_index`, `bone_index`,
  `weight` and `node_index` to be prefixes too (`dtslib/mesh_io.py:107-140`),
  and a multi-frame mesh's array runs past the shared prefix into its frame
  blocks.  A multi-frame mesh can still be a *parent*.  14 meshes in the whole
  corpus share a skin, so this costs almost nothing.
  `mapping/blender_to_shape.py:511`
- **Sequences re-export larger than they were read.**  `gman`'s node rotations
  go from 13,556 to 18,588 entries, which is most of that shape's growth.  This
  predates the geometry work and is not caused by it: the exporter writes a key
  for every node in the stored matters sets rather than for the channels that
  actually exist.
- **The `STANDARD_MESH` type of a mesh on a translucent material.**  Export
  promotes it to `SORTED_MESH`, so a plain import→export changes the mesh type
  and rewrites its primitive order and index buffer.  This is deliberate — see
  §3 — but it is not small: 5462 of the corpus's 34077 standard meshes are on a
  translucent material, against 367 sorted meshes in the whole corpus, so
  round-tripping any of the 319 shapes affected produces a structurally
  different file.  The geometry is unchanged and the engine draws it in a
  better order; what is lost is the original's own answer to the question.
  Set the mode to `FLAT` to keep the type without partitioning anything, or
  make the material opaque.  `mapping/blender_to_shape.py:720`
- **`merge_indices` naming a vertex no face uses.**  A strip-packed source mesh
  carries vertices that only ever appear in a degenerate stitch triangle.  Once
  the mesh is edited and re-derived as triangle lists those vertices are gone,
  so a merge entry pointing at one has nothing left to name and is dropped with
  a warning — 15 of 61 entries on `weapon_energy_vehicle`'s first mesh.  The
  entries that survive are remapped exactly.  An unedited mesh still round-trips
  the whole table through the payload. `mapping/blender_to_shape.py:659`
- **Which faces a decal covers.**  This is the big one.  A `TSDecalMesh`
  stores an authored list of target triangles (`dtslib/mesh_io.py:196`), and a
  decal is a projector empty, which cannot hold one.  Export therefore
  *recomputes* coverage from the projector volume, and that does not reproduce
  what the shipped files say.

  It was never going to.  The original exporter decided coverage with a
  conjunction of three tests (`ShapeMimic.cc:5762-5764`): a unit-square overlap
  test in *texture* space including the cases where the face swallows or merely
  clips the square; a per-vertex test that the target vertex projects inside
  the decal mesh's own faces, gated on a normal cone; and a **filter bitmap**
  that lived in the Max scene and is in no `.dts`.  See `DECALS.md`.

  Only the normal cone needs nothing but the shape, and it is implemented as
  `Max Angle` on the empty, same 90-degree default as the exporter's
  `DECAL::MAX_ANGLE`.  Measured over the corpus's 27,243 decal mesh slots, as
  recall (precision) of the faces the file covers:

  | rule | facing on | facing off |
  | --- | --- | --- |
  | centre, depth 1 | 0.251 (0.432) | 0.436 (0.402) |
  | **centre, depth 4** | 0.303 (0.516) | 0.521 (0.476) |
  | any, depth 4 | 0.346 (0.433) | 0.606 (0.392) |
  | all, depth 4 | 0.092 (0.263) | 0.163 (0.257) |

  **0.4%** of slots come back identical.  Import does not apply a fixed rule:
  it fits rule, depth and angle per decal against the list the file carries
  (`fit_coverage`), with 180 degrees in the search so facing can be switched
  off where it hurts.  On `bioderm_light` that returns coverage at recall
  0.444, precision 0.589.

  Everything else about that round trip is exact: all 24 decals, their names,
  owners, states, materials, `decal_matters` bits and texgen planes.  What the
  loss costs in the engine is a burn mark covering a different patch of the
  same surface, not a missing decal.  `mapping/decals.py` (`covered_faces`)
- **Decal faces that do not sit on the target mesh.**  A decal can only cover
  its target's own geometry, since the engine indexes the target's vertex
  array; a face that cannot be matched is dropped with a warning.
  `mapping/decals.py` (`_decal_mesh_from_faces`)
- **Scale animation in a DSQ, both directions.**  `dtslib` reads and writes
  the DSQ scale tables, but `mapping/dsq.py` never touches them, so importing
  a scale-animated DSQ loses the scale and exporting one never writes it.  The
  DTS path keyframes it onto the bones — see §3 — so this is now a gap in the
  DSQ path alone.
- **DSQ channels for nodes the armature lacks.**  `DSQ node 'X' not found in
  armature; its channels are dropped` — expected when applying a sequence to a
  different skeleton. `mapping/dsq.py:60`
- **Bone channels with no DTS node.**  A bone you add in Blender animates
  nothing on export. `mapping/sequences.py:394`, `mapping/dsq.py:179`
- **Duplicate detail sizes for one object.**  `duplicate detail 'X' for object
  'Y'; 'Z' skipped`. `mapping/blender_to_shape.py:154`
- **`dts_bump_map` and `dts_detail_map` on a material created in Blender.**
  The export path decides whether a material carries map references by testing
  for `dts_reflectance_map` *alone* — `has_refs = _MAP_PROPS[0] in bmat` — and
  when it is absent it writes engine-safe defaults instead of reading the other
  two props.  So a new material given only `dts_bump_map` and `dts_detail_map`
  exports as `bump=NO_MAP, detail=NO_MAP`, with no warning.  Adding
  `dts_reflectance_map` as well makes all three take effect; imported materials
  always carry all three, so they are unaffected.
  `mapping/materials.py:1145`  Reflectance is the exception now that the shader
  owns it: an image feeding `Metallic` sets that slot whether or not any of the
  props are there.
- **Whatever texture was already sitting where an export lands.**  Export
  writes a `.png` for every texture the shape names — made in Blender or loaded
  from disk, painted on or untouched — and overwrites without warning.
  Exporting into a game's `textures/` tree therefore rewrites the art there,
  re-encoded as PNG through Blender, and for a `.dds` or a `.bm8` source that
  re-encode is not reversible (§7).  The add-on cannot tell a game tree from a
  mod's own, so the only protection is the Export Textures checkbox, which
  suppresses every image (but not the `.ifl`, which is shape data rather than
  art).  This is a deliberate trade against the previous rule, which copied
  only images with no file behind them and so produced shapes that rendered
  nowhere but the machine that made them: the engine looks for a texture beside
  the `.dts` by bare filename, and a stale one left in place is a wrong render
  that looks like a right one.  `mapping/texture_io.py:53`
- **The authored size of a non-power-of-two texture.**  Written resampled to
  the nearest power of two, so a `100x60` lands as `128x64` and an `80x80` is
  *reduced* to `64x64`.  Torque's texture loader assumes power-of-two
  dimensions and nothing in the `.dts` records a size, so an unscaled one is a
  material that renders white in-game and correct in Blender, with nothing to
  diagnose it by.  A warning names every texture that was resized.  Untick
  **Scale Textures to Power of Two** to write the authored size instead.  The
  `.blend` is never touched: the resample is done on a copy that is removed
  again, because `Image.scale` works in place.  `mapping/texture_io.py:36`
- **The second of two textures that would write the same file.**  Skipped with
  a warning rather than overwritten — within one export neither of the two is
  the stale one, so there is no basis for picking.  Material names are not
  unique in real shapes: 104 of 630 corpus files reuse one.
  `mapping/texture_io.py:87`
- **`firstFrame` and `firstFrameOffTime` on an IFL entry.**  Written as zeros
  rather than round-tripped.  They are engine load-time scratch, filled from
  the `.ifl` after it is read, and 53 of the corpus's 64 entries carry
  uninitialised memory in them — `0xCDCDCDCD`, float bit patterns, a
  `numFrames` of -2147483648 for a 120-line file.  The pre-v18 upgrade path
  already writes zeros and 11 shipped entries already are zero, so this is a
  shape the engine demonstrably accepts; it is still a byte change on
  re-export.  `numFrames` is the one of the three that is real, and it becomes
  the length of the frame list.  `mapping/materials.py:901`
- **An IFL material whose `.ifl` cannot be found imports with no frames.**  The
  material keeps its checkbox and still gets its table entry, so the shape is
  not silently un-animated, but there is nothing to preview and export writes
  no `.ifl`.  All 35 the corpus names do resolve — but only from a tree where
  `shapes/` and `textures/` are siblings, which is not how every game lays them
  out. `mapping/materials.py:647`
- **The identity of a map target whose name is not unique.**  Map slots are
  stored as material *names* so they survive reordering, and resolved back
  through `index_by_name = {m.name.lower(): i ...}`, where a later entry
  overwrites an earlier one.  Material names are not unique in real shapes —
  the importer says so itself, and keys material identity on
  `dts_material_index` for that reason (`mapping/materials.py:821`) — so a slot
  pointing at the *first* of two materials named `glass` comes back pointing at
  the last, silently.  104 of the 630 corpus shapes have duplicate material
  names, but none of them has a map slot targeting a duplicated name, so no
  real file is currently mistranslated. `mapping/materials.py:1126,1135`
  Reflectance slots resolved from the shader do not join this hazard: they are
  matched on the image *datablock*, whose name Blender does keep unique.
  `mapping/materials.py:1067`

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
  `MAT_IFL_FRAME`; the Blender shader has no bearing on them, so a material that
  looks unlit still exports as self-illuminating if the box is ticked.
  `MAT_TRANSLUCENT`, `MAT_ADDITIVE` and `MAT_SUBTRACTIVE` are not on this list
  — they are read off the shader, and the boxes are rewritten to match on
  export, which is why the panel shows the blend mode as a computed label
  rather than three checkboxes.  They are not stored on the material at all:
  a prop beside the graph would be a second source for one value, and older
  scenes have theirs deleted on load (`props/migrate.py:301`).
  `mapping/materials.py:964`  `MAT_NEVER_ENV_MAP` is a fourth exception, but in
  one direction only: a material with an image feeding `Metallic` exports with
  env-mapping *on* however the box is set, because a reflectance map the engine
  is told never to read is not a feature.  With no such image the checkbox
  still wins. `mapping/materials.py:1166`
- **The blend state of a material that fades.**  The visibility and decal
  wiring force `surface_render_method = BLENDED` so a fade renders at all,
  which on an opaque material would otherwise read back as `MAT_TRANSLUCENT`.
  The state from before the fade is recorded once in `dts_blend_before_fade`,
  and *that* is what export reads — so on a faded material, switching the
  render method by hand does not reach the file.  Delete the property to make
  the current setting count.  `mapping/materials.py:351`,
  `mapping/visibility.py:185`.  Decals no longer force a blend state at all:
  the decal branch mixes two opaque shaders, so it never had to.

---

## 6. Not a DTS feature, but missing

- **No warning when a sequence's object-state tracks are lost to a `.dsq`.**
  The loss itself is the format's and is in §7; saying nothing about it is not.
  `mapping/dsq.py`
- **Some scalar `dts_*` properties still have no panel.**  The ones with a
  decision behind them are laid out — mesh flags and sorted mode in Object
  Properties → DTS Mesh, the material flags, map slots and reflectance packing
  in Material Properties → DTS Material, sequence timing in the Dope Sheet's
  DTS tab.  The rest (subshape indices, object detail numbers, default object
  states) are still raw entries in the N-panel's Custom Properties: a real
  place to edit them, but not a designed one.  The DTS Material panel now draws
  for any material rather than only an imported one, because the Combine
  checkbox has to be reachable in a fresh scene. `ui/panels.py:338`
- Sub-shapes have no authoring path: they can be preserved but not created
  without hand-setting `dts_subshape`.
- A scene saved by v1.2 or earlier converts on load (`props/migrate.py`), but
  its **mesh payloads are discarded rather than unpickled** — deliberately, since
  reading one would put `pickle.loads` back on a path fed by an arbitrary
  `.blend`.  Strip packing, merge indices, material frames and cluster tables
  are lost with it; re-import the `.dts` to recover them.  A note on the
  armature says so, and export refuses while a legacy key is still present.

---

## 7. Not gaps — limits of the format, of Blender, and of the source art

Nothing here is a tier, because none of it is work left undone.  These are the
edges of what the problem allows: a DTS file with no field for the data, a
Blender datablock with nowhere to put it, or a texture format that has already
thrown the precision away.  They are written down because "can I do this" has
the same answer either way, and because someone will otherwise try to fix them.

### The DTS format

- **Ground frames cannot be written to v23.**  v23 has no ground-frame storage
  at all.  Exporting a shape that has them is refused unless **Strip Ground
  Frames** is checked, which discards them and costs every movement animation
  its ground speed.  Writing v24 keeps them.
  `dtslib/writer.py:27`, `ops/export_dts.py:40`
- **192 nodes or objects is the ceiling.**  `TSIntegerSet` is 6 dwords wide, so
  a shape cannot name a 193rd node in a matters set — there is no bit for it.
  Refused rather than written short.
  `mapping/blender_to_shape.py:94,280`, `dtslib/primitives.py:14`
- **65535 unique vertices is the ceiling for one mesh.**  The index buffer is
  u16.  Split the mesh.  `mapping/blender_to_shape.py:596`
- **A `.dsq` cannot carry object state.**  `DsqFile` has no `object_states`,
  `decal_states` or IFL tables at all, so a sequence's visibility, frame,
  matframe and decal tracks have nowhere to go.  They round-trip through
  **DTS only**.  Nothing this add-on does can change that; what it *should* do
  is warn, and that gap is in §6.  `dtslib/types.py:345`
- **A mesh cannot be both skinned and vertex-animated.**  `mesh_type` is one
  field, so `frame_*` shape keys on a skinned mesh are ignored with a warning.
  The same field is why a skin cannot also be sorted.
  `mapping/blender_to_shape.py:689`
- **One alpha channel carries two meanings and the file does not say which.**
  On an env-mapped material it is the reflectance mask; otherwise it is
  transparency.  There is no field to disambiguate, so a reader has to choose.
  The engine's own upgrade rule for shapes older than v16 forces
  `MAT_NEVER_ENV_MAP` onto every translucent material (`dtslib/matlist.py:71`),
  which is the format saying the two readings are exclusive — so the env-map
  bit is what this add-on reads it by.  270 of the 3185 corpus materials are
  env-mapped and 6 of those are also translucent; on those 6 transparency wins,
  the texture is not taken apart, and a warning says so.
  `mapping/materials.py:599`

### Blender

- **Arbitrary node scale has no bone equivalent.**  The DTS form is per-axis
  factors *plus* an orientation quaternion naming the axes to measure along; a
  pose bone's scale is three numbers in its own space and cannot express the
  second half.  Refused on export rather than half-written.  No sequence in the
  630-shape corpus uses it, so nothing real is blocked. `mapping/sequences.py:547`
- **EEVEE has no subtractive blend mode.**  `MAT_SUBTRACTIVE` is encoded as the
  additive graph with the emission colour inverted — this add-on's own
  convention, chosen so the flag has somewhere to live that export can read
  back.  It round-trips exactly; it does not render the way the engine draws
  it, and no viewport setting would make it.  No shape in the 630-file corpus
  is subtractive, so the encoding has never been checked against real art.
  `mapping/materials.py:293`
- **`Mesh.uv_layers` caps at 8**, silently — `uv_layers.new()` returns without
  adding — while real shapes reach 62 material frames.  That cap is why frames
  1..n-1 are `FLOAT2` point attributes rather than UV layers, which is in turn
  why nothing previews them (§3). `mapping/matframes.py`

### The source art

- **DXT5 alpha is lossy.**  Export always writes PNG, so a material whose
  texture was `hull.dds` resolves to `hull.png` — harmless, since the engine's
  texture lookup is extension-agnostic.  What is not exact is the data:
  splitting a compressed texture into diffuse and reflectance and recombining
  it cannot reproduce bits the compressor already discarded.
  `mapping/materials.py:429`

---

## Coverage

Two Blender suites, and the difference between them is the point.

`tests/blender/test_operators.py` (93 tests) imports real fixtures, edits them
and exports.  That covers reading files the add-on did not write, and it is the
only way to check a feature no fixture-free scene can produce.

`tests/blender/test_authoring.py` (74 tests) never imports anything.  Every test
builds a shape from nothing — armature, meshes, materials, actions — exports it,
and reads the feature back out of the file.  This is the suite that answers
"can a user *make* one of these", which a round-trip cannot: the exporter may be
leaning on a table, property or payload that only the importer would have
written, and the feature looks supported while being unauthorable.  Billboards
were exactly that, and the flags being conditionally-present ID properties was
invisible until a fresh scene tried to set one.

Between them they cover detail levels and LOD vertex sharing, collision
details, node hierarchies, materials and all fourteen flag bits (eleven
stored, three derived), the three
blend modes through the shader, map slots, reflectance maps in both packings
and both directions, billboards including the Z-axis
variant no shipped shape uses, sorted meshes in both modes and the promotion of translucent ones, skins, vertex
animation, material frames, sequences, triggers, ground frames, object-state
tracks, node scale, DSQ export, IFL flipbooks in both directions including
the .ifl sidecar, decals and both output versions.

`scripts/mutate.py` (52 mutations) disables one capability at a time and checks
the matching test notices.  It has caught its own drift five times — a mutation
that stopped biting when the code moved, two that were never testing what they
claimed, and a redundant guard in the reflectance export path that no mutation
could make fail because the line after it already did the same job.

It also, once, caught *itself*.  `sorted-threading` is the only mutation
verified by pytest rather than Blender, and its runner shelled
`sys.executable` — the system python, since this tool is documented as
`scripts/mutate.py`, whose shebang is `/usr/bin/env python3`, while the fast
test loop lives in `.venv`.  pytest was never importable there, so the run died
with `No module named pytest`, no `FAILED` lines were parsed, and the mutation
reported itself uncaught for as long as it had been run the documented way.  A
missing pytest exits 1, indistinguishable from an honest test failure, so the
runner now demands a summary line naming passed/failed/error before it will
call anything passed.  Run it when adding a feature; a test that survives its
own mutation is not a test, and a harness that certifies itself healthy is
worse than no harness.

`examples/` carries one `.blend` per feature, built from nothing by
`examples/build_examples.py`, and `examples/verify_in_tribes2.sh` loads them
into the real game and screenshots each.  That is the only check that the
files an engine actually reads are the files this add-on writes; it found the
degenerate UV unwrap in the example builder and confirmed decal rendering is
unavailable through a StaticShape (Tribes 2's own shapes behave identically).

The "blind" items are checked at the file level only — no test asserts a preview
exists, because for those items none does.  The two that are no longer blind
are the exception: visibility's drivers and the decal branch are asserted as
node graphs, since a preview that draws the right thing on the wrong mesh fails
nothing at the file level.  Most "dropped" items have no tests: they are known
losses, not regressions to guard.  The exception is the partial `merge_indices`
loss, which is asserted exactly so it cannot quietly get worse.
