# io_scene_dts — Torque DTS/DSQ importer/exporter for Blender

A pure-Python Blender extension (4.2+, tested on 5.2 LTS) that imports and
exports Torque three-space shapes (`.dts`) and standalone sequence files
(`.dsq`) — geometry, materials, node hierarchy, detail levels, skinning, and
animation.

- **Reads** DTS versions 17–24 (Tribes 2 through Torque Game Engine 1.5;
  15/16 — the keyframe-table era — are refused).
- **Writes** DTS version **24** ("Torque") and **23** ("Tribes 2") —
  selectable in the export dialog.  v23 has no ground-frame storage, so
  exporting a shape with ground frames as v23 drops them and warns; v24 keeps
  them.
- **DSQ**: reads 17–24, writes the modern layout.  Sequences bind to an
  armature's bones by node-name matching — either onto the active armature
  (File → Import → Torque Sequence), or by selecting `.dsq` files alongside
  the `.dts` in the shape importer, which loads them onto the armature it
  just created.  Sequences always arrive as NLA strips (see below), one track
  each, all muted but one — pick which plays in the NLA editor.

The format core (`dtslib/`) is bpy-free and reads and writes the formats
directly, exercised against a corpus of 407 Tribes 2 / TGE shapes and 1709
DSQ files.  Fields the engine recomputes at load are carried through from the
source rather than regenerated, so a rewrite stays as close to the original as
the data allows.

## Install

```sh
blender --command extension build   # produces io_scene_dts-1.3.0.zip
```

then install the zip via Edit → Preferences → Get Extensions → Install from
Disk.  For development, symlink this checkout into
`~/.config/blender/<ver>/extensions/user_default/io_scene_dts`.

## Mapping conventions

- One **armature** per shape; every DTS node is a bone (`dts_node_index`
  custom prop preserves order).  Rest pose comes from the shape's default
  transforms.  DTS quaternions are stored conjugated relative to the standard
  convention; the add-on converts at the boundary.
- One **mesh object per (object, detail level)**, named with Torque's detail
  suffix convention: `shape2`, `shape32`, `collision-1`.  The trailing number
  is the detail size and is authoritative on export (custom props override
  when present).  Detail levels are organized into collections.
- **Skinned meshes** get vertex groups named after bones + an armature
  modifier.  Rigid meshes are bone-parented.
- **Sequences are Actions** (one per sequence).  Pose-bone fcurves carry node
  rotation/translation keyframes (keyframe *i* = Blender frame *i*+1).
  Sequence metadata lives in action custom props (`dts_cyclic`, `dts_blend`,
  `dts_priority`, `dts_duration`, `dts_tool_begin`) and, for ground frames and
  triggers, in editable collections shown in the **DTS** tab of the Dope Sheet
  and NLA sidebars.  Object-state and node-scale animation are keyframed
  channels rather than stored tables — see below.  Blend sequences store raw
  blend offsets in the pose.
- **Sequences are NLA strips, always.**  Playback speed is not a scene
  property: a sequence stores its own `dts_duration`, and one shape's sequences
  disagree — across light_male and its DSQs, 13 are authored at 30 fps and 21
  at about 15 — while keyframes are laid one per Blender frame.  An Action
  assigned straight to the armature therefore plays at `scene.render.fps` and
  is wrong for all but the sequences that happen to match, so the importer
  never assigns one.  Each sequence gets a track with

      strip.scale = dts_duration * fps / (keyframe_count - 1)

  so every strip spans its real duration whatever the scene is set to.  A
  sequence the armature can evaluate nothing from (a decal-only one) gets no
  strip and is reported.
- **Object visibility** animates.  A sequence's `vis` track is keyframed as a
  custom property on the armature, in the same slot as the bones, and each mesh
  built from that object reads it through a driver into alpha (`color[3]`) and
  the hide flags.  One DTS object is one mesh *per detail level*, so the driver
  fans out across LODs — light_male's `Jetfire` is ten meshes.  Keeping the
  value on the armature means one animated ID and one strip, so retiming the
  strip retimes visibility too.  **Export samples those curves**, so editing a
  key changes the file; the drivers are the preview, not the storage.  The same
  goes for the `frame` and `matframe` tracks and for decal states.  `.dsq`
  cannot carry object states at all, so they round-trip through DTS only.
  Timing is unaffected: `dts_duration` stays the single source of truth, so
  dragging a strip never changes the written file.
- **Node scale animation** rides the pose bones' own `scale` channels, with
  `Scale Mode` on the sequence panel saying which DTS form to write.  The
  arbitrary form — per-axis factors plus an orientation — has no bone equivalent
  and is refused on export.
- **Shape tables** (name table, detail levels, material order) are
  collections on the armature, under Object Properties → **DTS Shape**.  A
  node's stored rest transform is on the bone, under Bone Properties → **DTS
  Node**.
- **Materials** are Principled BSDF with the texture found next to the .dts
  by material name; DTS flags are `dts_*` boolean custom props (the props are
  the round-trip source of truth).  The three blend flags are the exception:
  `MAT_TRANSLUCENT` is the material's `surface_render_method`, and
  `MAT_ADDITIVE` / `MAT_SUBTRACTIVE` are a `Transparent BSDF + Emission ->
  Add Shader` graph, so the shader is what export reads and editing it changes
  the file.  They are not stored beside it either — those three bits have no
  `dts_*` prop at all, and the panel shows the blend mode as a computed label.
  The other eleven have a checkbox, in Material Properties → **DTS Material**.
  A material with no reflectance map exports a self-index one (never
  `0xFFFFFFFF`, which crashes the engine).
- **Reflectance (environment) maps** are the second image in the material,
  feeding **Metallic**.  A DTS packs one there by putting it in the *alpha
  channel* of the diffuse texture, so an env-mapped material imports as two
  images — an RGB diffuse and a greyscale mask — and the **Combine Diffuse and
  Reflectance** checkbox says which packing to write back.  On, the mask goes
  into the diffuse's alpha; off, it becomes its own texture and its own entry
  in the shape's material list.  Export writes a `.png` beside the `.dts` for
  every texture the shape names — made in Blender or loaded from disk — so an
  exported shape carries its art with it.  Existing files are overwritten, so
  exporting *into* a game's `textures/` tree rewrites the art there; untick
  **Export Textures** to write the `.dts` alone.  **Scale Textures to Power of
  Two** (on by default) resizes what is written to the nearest power-of-two
  dimensions — `100x60` becomes `128x64` — because Torque's texture loader
  assumes them and a texture that is not one renders white or garbled in-game
  while looking correct in Blender.  **Limit Textures to 512x512** (also on by
  default) scales anything larger down to fit, dividing both sides so the
  aspect ratio survives — a `1024x256` lands as `512x128`.  That one is a
  budget rather than a correctness rule: an oversized texture renders, it just
  costs, and 512 is the largest the art this format ships with is built to.
  Both act on the file on disk only; the image in your `.blend` is left at
  whatever size it was authored.
- **IFL materials** are animated flipbooks.  The file names a `.ifl` sidecar
  listing `<texture> <hold>` per line; that list imports as a frame collection
  on the material, previews as a keyframed switch between its images, and is
  written back out as the `.ifl` beside the exported `.dts`.  Ticking **IFL
  Material** is what puts an entry in the shape's IFL table — the table is
  derived from the materials that flip, not stored beside them.
- Export emits triangulated, indexed **Triangles** primitives grouped per
  material — the same policy as the engine's own `.mdl` exporter.

### Known limitations

[FEATURES.md](FEATURES.md) is the whole inventory: every DTS and DSQ feature,
scored on whether it imports, can be edited, can be *created* in a fresh scene,
and exports.  The short list is below; [UNSUPPORTED.md](UNSUPPORTED.md) covers
the gaps in detail, sorted by *how* a feature is unsupported — refused
outright, round-trips but uneditable, correct in the file but invisible in the
viewport, dropped outright, or frozen against Blender-side edits.

- Nothing is stored as a pickled payload any more, and every mesh is re-derived
  from the Blender geometry on export, so an edit always reaches the file.
  Strip packing and exact vertex order are not preserved (measured at ×1.00 in
  file size); `parent_mesh` vertex sharing and sorted-mesh cluster trees are
  regenerated rather than carried.
- Decals import as projected UVs with a projector empty, and re-export from it.
  **Add DTS Decal** (Object Properties → DTS Mesh) makes one from the faces you
  have selected, across every detail level of the object.
- Multi-frame (vertex-animated) meshes import as shape keys (`frame_NNN`);
  nothing drives them from the sequence's `frame` track.
- Extra material frames are `FLOAT2` mesh attributes; only frame 0 renders.
- IFL flipbooks import their `.ifl`, preview as a keyframed image switch, and
  are written back out beside the exported `.dts`.
- A `.blend` saved by v1.2 or earlier converts on load, but its mesh payloads
  are discarded rather than unpickled — re-import the `.dts` to recover strip
  packing, merge indices, material frames and cluster tables.
- DTS versions below 17 (the keyframe-table era) are refused.

## Development

```sh
python -m venv .venv && .venv/bin/pip install pytest pytest-cov hypothesis

# fast unit loop (no corpus)
.venv/bin/python -m pytest -m "not corpus" -q

# full suite incl. the on-disk corpus sweep, with coverage
.venv/bin/python -m pytest --cov --cov-report=term-missing

# headless Blender integration tests (writes .coverage.blender.*).  Add
# `-- <substring>` to run only the scenarios whose names match.
blender --background --factory-startup --python tests/blender/run_blender_tests.py

# break the export path on purpose and check the right test notices
scripts/mutate.py            # --list shows what each one disables

# every file:line citation in UNSUPPORTED.md still lands on a real line
scripts/check_citations.py

# combined coverage report across both runs (--append merges the Blender
# data into the pytest data instead of replacing it)
.venv/bin/coverage combine --append && .venv/bin/coverage report && .venv/bin/coverage html
```

The corpus tests reference the local Tribes 2 / TGE game data
(`~/Documents/Repositories/hasell-engine`, `~/Documents/Repositories/agentic-torque`)
and skip automatically when absent; `tests/fixtures/` carries small
self-contained representatives (see `tests/fixtures/NOTES.md`).

Format ground truth: `agentic-torque/engine/ts/` (`tsShape.cc`
`assembleShape`/`disassembleShape`, `tsMesh.cc`, `tsShapeOldRead.cc` for
sequences and the DSQ layout, `tsShapeAlloc.cc` for the three-buffer guard
scheme).
