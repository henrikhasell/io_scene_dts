# DTS features and how far this add-on supports each

Every feature the DTS and DSQ formats carry, and what this add-on does with it.
`UNSUPPORTED.md` is the same territory from the other side — it sorts the gaps
by *how* they hurt.  This file is the inventory: it names each feature once,
including the ones that are simply done.

## How to read the columns

The four conditions come from `CLAUDE.md`, and they are not the same question
asked four ways:

1. **Import** — reading a `.dts` that uses it produces something in Blender.
2. **Edit** — the user can change that something and the change reaches the
   exported file.
3. **Create** — the user can produce it in a *fresh scene*, with no import
   anywhere in the history.
4. **Export** — it is written back in a form the engine reads.

(3) is the one that gets skipped and the one that matters: an exporter leaning
on a table the importer wrote makes a feature look supported while being
unauthorable.  `tests/blender/test_authoring.py` is where that is checked, and
a ● in the Create column means a test in it builds the feature from nothing.

| Mark | Meaning |
| --- | --- |
| ● | Supported. |
| ◐ | Partial — the Notes column says what is missing. |
| ○ | Not supported. |
| – | Not applicable: the add-on derives or recomputes this, so there is nothing to edit or author. |

A ● in **Edit** does not promise a preview.  Many features are correct in the
file and invisible in the viewport; those are marked *blind* in the Notes and
listed in `UNSUPPORTED.md` §3.

---

## 1. Files and versions

| Feature | Read | Write | Notes |
| --- | --- | --- | --- |
| DTS v24 (Torque Game Engine 1.5) | ● | ● | The default output. |
| DTS v23 (Tribes 2) | ● | ● | Selectable in the export dialog.  No ground-frame storage — see §5. |
| DTS v22–v19 | ● | ○ | Normalized to the v24 in-memory layout on load (pre-v22 ground frames migrated out of the node arrays, pre-v23 skin section folded in).  Written back as 23 or 24 only.  `dtslib/writer.py:22` |
| DTS v18–v17 | ● | ○ | The flat-stream format, handled by a separate reader. |
| DTS v16–v15 | ○ | ○ | Refused: the keyframe-table era stores animation in a layout nothing else shares.  `dtslib/old_reader.py:40` |
| DTS v25+ | ○ | ○ | Refused.  Torque 3D–era shapes are not read.  `dtslib/reader.py:71` |
| DSQ v17–v24 | ● | ● | Reads every version, writes the modern layout.  Binds to bones by node-name matching. |
| Buffer padding | ● | ● | The uninitialised bytes original files pad their 16- and 8-bit buffers with are captured on read and reused on write, so a rewrite stays byte-close. |

---

## 2. Shape structure

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Node hierarchy | ● | ● | ● | ● | One DTS node is one bone; `dts_node_index` preserves the file's order. |
| Node default rest transforms | ● | ● | ● | ● | The raw `Quat16` int16s live on the bone (Bone Properties → DTS Node).  Export prefers them while the bone still agrees, so an untouched shape keeps its exact node table; clear **Keep Imported Rest Transform** and the bone's matrix wins. |
| Name table and its order | ● | ● | ● | ● | A UIList on the armature.  The order is load-bearing — every name index in the file is an offset into it — so the panel says so and the reorder buttons respect it. |
| Objects | ● | ● | ● | ● | One DTS object becomes one mesh object *per detail level*, named with Torque's suffix convention (`shape2`, `shape32`). |
| Object order | ● | ◐ | ● | ● | The file's own order is kept, except that everything with a translucent mesh is moved to the end of its sub-shape — objects are drawn in list order and a blended surface only composites over what is already drawn.  3 of 849 corpus shapes are reordered by this; see `UNSUPPORTED.md` §4.  `mapping/blender_to_shape.py:182` |
| Visible detail levels (LODs) | ◐ | ● | ● | ● | `Import Detail Levels` is off by default, because every LOD stands at the same origin and eleven levels import as eleven overlapping copies.  Off, the levels below the largest lose their geometry in anything exported from that scene; the detail *table* still survives.  Warned at import.  `mapping/shape_to_blender.py:668` |
| Collision and LOS details | ● | ● | ● | ● | Negative-size details (`Collision-1`, `LOSCollision-9`).  Never treated as LODs and always imported, whatever `Import Detail Levels` says — dropping one would produce a shape the engine cannot collide with. |
| Empty detail slots | ● | ● | ● | ● | A detail can exist with no geometry at all, which is why the table is kept rather than derived from the objects.  Written back as null meshes. |
| Detail metrics (average error, max error, poly count) | ● | ● | ● | ● | Fields of the detail UIList.  A detail authored in Blender gets the format's own defaults (−1, −1, 0). |
| Detail sizes | ● | ◐ | ● | ● | Taken from the object-name suffix, with `dts_detail_size` overriding.  *Frozen*: renaming a collection does nothing. |
| Sub-shapes | ● | ◐ | ◐ | ● | Preserved through `dts_subshape` on each mesh object, which is a raw custom property with no panel.  Export puts every node in sub-shape 0, so extra sub-shapes carry objects only. |
| `smallestVisibleSize` / `smallestVisibleDL` | ● | ◐ | ● | ● | Raw custom properties on the armature; derived from the detail table when absent. |
| Shape bounds, radius, tube radius, centre | ● | – | – | ● | Recomputed from the geometry on export — a stored copy would only go stale.  `mapping/blender_to_shape.py:814` |
| Per-mesh bounds, centre, radius | ● | – | – | ● | Likewise. |
| Runtime links (`firstObject`, `firstChild`, `nextSibling`, `firstDecal`) | ● | – | – | ● | Engine scratch, recomputed from the hierarchy.  `dtslib/runtime_links.py` |
| Exporter version word | ● | ◐ | ◐ | ● | Raw custom property on the armature. |

---

## 3. Meshes and geometry

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Standard mesh | ● | ● | ● | ● | Re-derived from the Blender geometry on every export; nothing is replayed from a payload. |
| Positions, UVs, split normals | ● | ● | ● | ● | Corners are deduplicated on (position, UV, normal), since DTS has one index stream. |
| Encoded normals | ● | – | – | ● | Recomputed from the format's 256-entry table.  `dtslib/normals.py` |
| Triangle primitives | ● | ● | ● | ● | Export emits indexed Triangles grouped per material — the same policy as the engine's own `.mdl` exporter. |
| Strip and fan primitives | ● | – | ○ | ○ | Decoded into triangles on import; never written.  Measured across the corpus this costs **×1.00** — 312,733 strip primitives become triangles for no size change, because the u16 index buffer is dwarfed by the float vertex arrays. |
| `parent_mesh` vertex sharing across LODs | ● | – | ● | ● | Re-derived, not carried: each object's levels are interned into one pool lowest-detail-first, so every smaller level occupies a prefix of the larger one.  Skins and multi-frame meshes are excluded — their parallel arrays would have to be prefixes too.  `mapping/vertex_pool.py`, `mapping/blender_to_shape.py:508` |
| `merge_indices` (legacy LOD morph table) | ● | ◐ | ◐ | ◐ | A raw int array on the mesh object, editable only as numbers in the N-panel — order matters and entries repeat, so a vertex group cannot hold it.  Entries naming a vertex no face uses any more are dropped with a warning.  *Blind.*  `mapping/blender_to_shape.py:653` |
| Billboard flag | ● | ● | ● | ● | Checkbox in Object Properties → DTS Mesh.  *Blind* — nothing in the viewport turns a billboard to face you, and authoring one the engine actually turns is not solved (`UNSUPPORTED.md` §3).  A round-tripped billboard keeps working. |
| Z-axis billboard flag | ● | ● | ● | ● | Same, and worse: no shipped Tribes 2 shape sets it, so there is no reference render to compare against. |
| `MESH_HAS_DETAIL_TEXTURE`, `MESH_USE_ENCODED_NORMALS` | ● | ● | ● | ● | Checkboxes.  Neither occurs in the corpus. |
| Mesh-type echo bits | ● | ● | ● | ● | Whether an exporter repeated the mesh type in the flags word varies per mesh, so it is recorded rather than inferred.  Undocumented bits are dropped with a warning. |
| Skinned mesh | ● | ● | ● | ● | Vertex groups named after bones plus an armature modifier; rigid meshes are bone-parented. |
| Sorted mesh (translucency draw order) | ● | ● | ● | ● | The cluster tree is regenerated from the geometry, so the mesh is ordinary editable geometry.  `NONE`/`FLAT`/`BSP` plus depth in Object Properties → DTS Mesh.  A standard mesh on a translucent material is **promoted** to BSP on export, which changes the mesh type of an imported shape on re-export.  *Blind* — nothing previews draw order.  `mapping/blender_to_shape.py:716` |
| Null mesh | ● | ● | ● | ● | A detail slot an object has no geometry for; trailing null slots the source declared are kept. |
| Decal mesh | ● | ● | ● | ● | Not a mesh in Blender at all — see §6. |
| Vertex animation (multi-frame meshes) | ● | ● | ● | ● | Frames arrive as shape keys `frame_001…`.  *Blind*: nothing drives them from the sequence's `frame` track. |
| Material frames (UV flipbooks) | ● | ● | ● | ● | Frame 0 is the active UV map; frames 1..n−1 are `FLOAT2` point attributes (`Mesh.uv_layers` caps at 8 while real shapes reach 62).  *Blind*: only frame 0 renders.  `mapping/matframes.py` |
| 65535 vertices per mesh | – | – | – | ● | The index buffer is u16; a larger mesh is refused rather than written short.  `mapping/blender_to_shape.py:591` |

---

## 4. Materials

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Material list and its order | ● | ● | ● | ● | A UIList of real datablock pointers on the armature — map slots and IFL entries index into it, so unused materials survive.  Pointers rather than names, because names are not unique: 104 of 630 corpus shapes reuse one. |
| Material name | ● | ● | ● | ● | `dts_name` keeps the stored name (which may carry a legacy path prefix) apart from the Blender datablock name. |
| Diffuse texture | ● | ● | ● | ● | Found next to the `.dts` by material name.  Export writes a `.png` beside the shape for every texture it names, so an exported shape carries its art — and **overwrites** what was there.  Untick **Export Textures** to write the `.dts` alone.  Two more boxes decide the size it is written at, both on by default: **Scale Textures to Power of Two** and **Limit Textures to 512x512** — see §8 and `UNSUPPORTED.md` §4.  `mapping/texture_io.py:80` |
| Reflectance (environment) map | ● | ● | ● | ● | The second image in the material, feeding **Metallic**.  A DTS packs one in the diffuse's alpha channel, so an env-mapped material imports as two images and **Combine Diffuse and Reflectance** says which packing to write back.  The Metallic mapping is a handle, not a render of what the engine does. |
| `reflection_amount` | ● | ◐ | ◐ | ● | Raw custom property, no preview.  1.0 in 265 of the 270 env-mapped corpus materials. |
| Bump map, detail map, `detail_scale` | ● | ◐ | ◐ | ◐ | Raw custom properties; the Principled BSDF ignores both maps.  A material given only `dts_bump_map`/`dts_detail_map` and no `dts_reflectance_map` exports them as `NO_MAP` with no warning.  Neither slot is used anywhere in the corpus.  `mapping/materials.py:1145` |
| IFL flipbook (`.ifl` sidecar) | ● | ● | ● | ● | The `.ifl` list imports as a frame collection on the material, previews as a keyframed image switch, and is written back beside the exported `.dts`.  Ticking **IFL Material** is what puts an entry in the shape's IFL table — the table is derived from the materials that flip. |
| IFL `firstFrame` / `firstFrameOffTime` | ◐ | – | – | ◐ | Written as zeros rather than round-tripped: they are engine load-time scratch, and 53 of the corpus's 64 entries carry uninitialised memory there.  `numFrames` is real and becomes the frame-list length.  `mapping/materials.py:901` |
| A missing `.ifl` | ◐ | – | – | ◐ | The material keeps its checkbox and its table entry, so the shape is not silently un-animated, but there are no frames to preview and none to write.  `mapping/materials.py:647` |

### The fourteen material flag bits

| Bit | Flag | Lives in | Notes |
| --- | --- | --- | --- |
| 0 | `MAT_S_WRAP` | checkbox | |
| 1 | `MAT_T_WRAP` | checkbox | |
| 2 | `MAT_TRANSLUCENT` | the shader | `surface_render_method`.  Read back off the graph on export, so editing the material changes the file; there is no `dts_*` prop beside it. |
| 3 | `MAT_ADDITIVE` | the shader | `Transparent BSDF + Emission -> Add Shader`. |
| 4 | `MAT_SUBTRACTIVE` | the shader | The additive graph with the emission colour inverted — this add-on's own convention, since EEVEE has no subtractive blend.  Round-trips exactly; does not render the way the engine draws it. |
| 5 | `MAT_SELF_ILLUMINATING` | checkbox | *Frozen*: a material that looks unlit still exports as self-illuminating if the box is ticked. |
| 6 | `MAT_NEVER_ENV_MAP` | checkbox | One-way exception: an image feeding Metallic exports with env-mapping on however the box is set. |
| 7 | `MAT_NO_MIP_MAP` | checkbox | |
| 8 | `MAT_MIP_MAP_ZERO_BORDER` | checkbox | Does not occur in the corpus. |
| 27 | `MAT_IFL_MATERIAL` | derived | From the IFL checkbox, which also owns the frame list. |
| 28 | `MAT_IFL_FRAME` | checkbox | Does not occur in the corpus. |
| 29 | `MAT_DETAIL_MAP_ONLY` | checkbox | Does not occur in the corpus. |
| 30 | `MAT_BUMP_MAP_ONLY` | checkbox | Does not occur in the corpus. |
| 31 | `MAT_REFLECTANCE_MAP_ONLY` | checkbox + derived | Also set by export on the material-list entries it invents to hold a separate reflectance texture. |

Eleven bits have a checkbox in Material Properties → DTS Material; the three
blend bits are read off the shader and shown there as a computed label.  Nothing
previews what any of the rest do.

---

## 5. Animation

Sequences are Actions, and always arrive as NLA strips — one track each, all
muted but one.  A sequence stores its own `dts_duration` while keyframes are
laid one per Blender frame, so an Action assigned straight to the armature
would play at `scene.render.fps` and be wrong for all but the sequences that
happen to match.

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Sequences | ● | ● | ● | ● | One Action per sequence; keyframe *i* is Blender frame *i*+1. |
| Duration | ● | ● | ● | ● | *Frozen*: `dts_duration` on the action is the single source of truth.  NLA strip scale is display-only, so retiming a strip never changes the file. |
| Keyframe count | ● | ● | ● | ● | Comes from the keys the action actually has — removing a key shortens the sequence. |
| Priority, `toolBegin` | ● | ● | ● | ● | Custom properties on the action, laid out in the Dope Sheet's DTS tab. |
| Cyclic, Blend, MakePath flags | ● | ● | ● | ● | Named custom properties. |
| `SEQ_IFL_INIT`, `SEQ_HAS_TRANSLUCENCY` | ● | ◐ | ◐ | ● | Preserved through the raw `dts_flags` word; nothing derives or previews them. |
| Node rotation channels | ● | ● | ● | ● | Pose-bone fcurves.  DTS quaternions are stored conjugated relative to the standard convention; the add-on converts at the boundary. |
| Node translation channels | ● | ● | ● | ● | |
| Rotation / translation matters sets | ● | – | ● | ● | Inferred from the channels that exist, so adding a bone channel marks its node. |
| Blend sequences | ● | ● | ● | ● | Raw blend offsets are stored in the pose, which is correct for export but does not look like the additive result the engine produces.  *Blind.* |
| Uniform node scale | ● | ● | ● | ● | Rides the pose bones' own `scale` channels; `Scale Mode` on the sequence panel says which DTS form to write.  *Blind*: nothing shows that a scale channel means *node* scale. |
| Aligned node scale | ● | ● | ● | ● | Same. |
| Arbitrary node scale | ● | ○ | ○ | ○ | Per-axis factors *plus* an orientation naming the axes to measure along.  A pose bone's scale cannot express the second half, so it is refused on export rather than half-written.  No corpus sequence uses it.  `mapping/sequences.py:540` |
| Ground frames (root motion) | ● | ● | ● | ● | A collection on the action, in the Dope Sheet / NLA DTS tab, holding raw `Quat16` int16s so a frame round-trips bit-exactly.  *Blind*: nothing shows them as motion.  v23 has nowhere to store them, so exporting as v23 drops them with a warning.  `dtslib/writer.py:74` |
| Triggers | ● | ● | ● | ● | A collection on the action: a state 1..30 and two flags rather than the packed U32 the file holds.  Pose markers show where they fire; nothing plays a sound.  *Blind.* |
| Object visibility (`vis`) track | ● | ● | ● | ● | Keyframed as a custom property on the armature, in the same slot as the bones; each mesh built from that object reads it through a driver into alpha and the hide flags.  **Export samples the curves**, so editing a key changes the file. |
| Vertex-frame (`frame`) track | ● | ● | ● | ● | Keyframed the same way.  *Blind*: nothing drives the shape keys from it. |
| Material-frame (`matframe`) track | ● | ● | ● | ● | Keyframed the same way.  No preview at all. |
| Default object states | ● | ◐ | ◐ | ● | Raw custom properties on the mesh object (`dts_default_vis`, `dts_default_frame`, `dts_default_matframe`). |
| IFL membership (`ifl_matters`) | ● | ● | ● | ● | A collection of material *pointers* on the action; the file's positional bits are resolved against the derived IFL table on export. |
| Decal state tracks | ● | ● | ● | ● | Keyframed like the object states, and read back off the curves.  Lost when decals are imported as meshes. |

---

## 6. Decals

A decal is a projector empty.  Nothing about that resembles the file's form —
`TSDecalMesh` indices and texgen planes — and that is the point: the user moves
the projector and export recomputes the rest.

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Decal table, names, owners, indices | ● | ● | ● | ● | The index is a decal's identity — `decal_states[i]` and each sequence's `decal_matters` bits are keyed by it — because names are not unique (`turret_tank_base` gives all fourteen of its decals the same one). |
| Projection (texgen planes) | ● | ● | ● | ● | Derived from the empty's transform.  All 24 of `bioderm_light`'s decals round-trip their planes exactly. |
| Covered faces | ◐ | ● | ● | ● | **The known loss.**  The file stores an authored triangle list (`dtslib/mesh_io.py:197`); a projector cannot hold one, so export recomputes coverage from the volume.  The original exporter also used a filter bitmap that lived in the Max scene and is in no `.dts`, so this was never going to be exact — 0.4% of the corpus's 27,243 decal mesh slots come back identical.  Import fits rule, depth and angle per decal (recall 0.444 on `bioderm_light`).  What it costs in the engine is a burn mark covering a different patch of the same surface.  See `DECALS.md`. |
| Coverage controls (rule, depth, max angle) | – | ● | ● | – | The format stores no depth axis, so `Depth` and `Coverage` are choices the user makes rather than recovered values.  `Max Angle` is the original exporter's `DECAL::MAX_ANGLE`, same 90° default. |
| Per-detail-level decal meshes | ● | ● | ● | ● | A decal owns a mesh run parallel to its owner's slots, so export writes one `TSDecalMesh` per LOD.  The preview draws on the target only. |
| Decal material | ● | ● | ● | ● | A pointer on the empty — it had nowhere to live once a decal stopped being a mesh with a material slot. |
| Something translucent to draw against | ● | ● | ● | ● | The engine needs a blended mesh in a shape that carries decals, so export **refuses** one that has none — see `UNSUPPORTED.md` §1.  Either the decal's own material or the mesh it sits on: every one of the 153 decal-bearing corpus shapes does one (94) or the other (59).  `mapping/blender_to_shape.py:354` |
| Viewport preview | ● | ● | ● | – | A branch in the *target's* material: a Texture Coordinate reading the projector's object space, masked to its box and to the one object the decal targets.  Per-pixel where export decides per-face, so it is close to the exported coverage rather than identical. |
| Authoring from a selection | – | ● | ● | ● | **Add DTS Decal** (Object Properties → DTS Mesh) makes one from the faces you have selected, across every detail level of the object. |
| Decals as meshes (import option) | ◐ | ○ | – | ○ | Off by default.  On, each decal arrives as a copy of the faces the file says it covers and no projector is built — the only way to see the file's own face list, and a way to *look at* a shape rather than author one: export reads projectors and nothing else, so these reach no file.  Warned at import and again at export.  `mapping/shape_to_blender.py:191` |
| Multi-frame decals | ◐ | ○ | ○ | ○ | Only the first frame imports; the rest are lost on export, warned.  A frame is a whole alternative projection *and* face subset, and a decal is one empty.  All 10,584 decal meshes in the 240-shape corpus are single-frame. |
| `pass_index` on a decal's target | – | – | – | – | The preview's object gate needs a per-object number a shader can read, so targeting a mesh with a decal overwrites its Object Index.  Recorded as `dts_decal_host`; editing the pass index by hand is treated as reclaiming the field.  `mapping/decals.py:360` |

---

## 7. DSQ (standalone sequence files)

| Feature | Import | Edit | Create | Export | Notes |
| --- | --- | --- | --- | --- | --- |
| Node rotation / translation channels | ● | ● | ● | ● | Bound to bones by node-name matching, either onto the active armature or alongside a `.dts` in the shape importer. |
| Node scale channels | ○ | – | – | ○ | `dtslib` reads and writes the DSQ scale tables; the Blender mapping never touches them, so a scale-animated DSQ loses its scale in both directions.  The DTS path keyframes scale onto the bones, so this is a gap in the DSQ path alone. |
| Ground frames | ● | ● | ● | ● | The same collection the DTS path uses. |
| Triggers | ● | ● | ● | ● | Likewise. |
| Sequence flags, priority, duration | ● | ● | ● | ● | Likewise. |
| Object-state tracks (`vis`, `frame`, `matframe`), decal states, IFL | – | – | – | – | A `.dsq` has no field for any of them (`dtslib/types.py:345`), so they round-trip through DTS only.  The add-on does not currently warn when a sequence loses them to a `.dsq` — that gap is `UNSUPPORTED.md` §6. |
| Channels for nodes the armature lacks | ◐ | – | – | – | Dropped with a warning, which is expected when applying a sequence to a different skeleton. |

---

## 8. What the add-on refuses

None of these corrupt a file; all stop with an error.

| Refusal | Why | Where |
| --- | --- | --- |
| DTS versions 15–16 and 25+ | Layouts this reader does not share. | `dtslib/old_reader.py:40`, `dtslib/reader.py:71` |
| Writing any version but 23/24 | Older versions keep skins in a separate section. | `dtslib/writer.py:22` |
| Exporting without an armature | The armature *is* the shape. | `mapping/blender_to_shape.py:68` |
| More than 192 nodes or objects | `TSIntegerSet` is 6 dwords wide, so there is no bit for a 193rd in a matters set.  A format limit, not a gap. | `mapping/blender_to_shape.py:94`, `mapping/blender_to_shape.py:279` |
| More than 65535 vertices in one mesh | The index buffer is u16.  Split the mesh. | `mapping/blender_to_shape.py:591` |
| Arbitrary node scale on export | A bone's scale cannot express the orientation half. | `mapping/sequences.py:540` |
| Exporting a scene saved by v1.2 or earlier | Its legacy keys are unread until **Convert DTS Data From an Older Version** runs; exporting first would write a shape missing its name table, details and IFL entries without saying so. | `props/migrate.py` |

---

## 9. Where each thing lives in Blender

| Panel | Holds |
| --- | --- |
| Object Properties → **DTS Shape** (armature) | Name table, detail levels, material order, migration note. |
| Bone Properties → **DTS Node** | The stored rest transform and whether to keep it. |
| Object Properties → **DTS Mesh** | Mesh flags, sorted mode and depth, **Add DTS Decal**. |
| Object Properties → **DTS Decal** (empty) | Target, material, coverage rule, depth, max angle. |
| Material Properties → **DTS Material** | Eleven flag checkboxes, the computed blend-mode label, map slots, reflectance packing, IFL frames. |
| Dope Sheet / NLA sidebar → **DTS** tab | Sequence timing, priority, flags, scale mode, ground frames, triggers, IFL membership. |
| N-panel → Custom Properties | Everything marked ◐ above: sub-shape indices, object detail numbers, default object states, merge indices, `reflection_amount`, the bump/detail slots.  A real place to edit them, but not a designed one. |

---

## 10. What is not in this file

Two things are deliberately absent.

**Limits of the format, of Blender, and of the source art.**  A `.dsq` with no
field for object states, `TSIntegerSet` with no bit for a 193rd node, EEVEE with
no subtractive blend, DXT5 alpha that has already thrown precision away — none
of that is work left undone, and `UNSUPPORTED.md` §7 keeps it out of the tiers
for the same reason.  Where such a limit shapes a row above, the Notes say so.

**How each gap will surprise you.**  A ◐ here says a feature is partial;
`UNSUPPORTED.md` says whether it errors, silently drops data, round-trips
invisibly, or ignores your edit, which is usually the question worth asking.

---

## Keeping this file true

Same rule as `UNSUPPORTED.md`: it is part of the change, not documentation to
write afterwards.  Update a row in the same commit whenever a feature moves
between columns — and remember that moving something into the **Create** column
means adding a test to `tests/blender/test_authoring.py`, because a round-trip
test passing is not evidence of it.

```sh
scripts/check_citations.py --doc FEATURES.md   # every file:line still lands
```

That only proves a cited line is not blank.  That it says the right thing is
still yours to check.
