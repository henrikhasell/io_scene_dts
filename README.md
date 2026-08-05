# io_scene_dts — Torque DTS/DSQ importer/exporter for Blender

A pure-Python Blender extension (4.2+, tested on 5.2 LTS) that imports and
exports Torque three-space shapes (`.dts`) and standalone sequence files
(`.dsq`) — geometry, materials, node hierarchy, detail levels, skinning, and
animation.

- **Reads** DTS versions 17–24 (Tribes 2 through Torque Game Engine 1.5;
  15/16 — the keyframe-table era — are refused).
- **Writes** DTS version **24** ("Torque") and **23** ("Tribes 2") —
  selectable in the export dialog.  v23 has no ground-frame storage; exporting
  a shape with ground frames as v23 is refused unless "Strip Ground Frames"
  is checked.
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
blender --command extension build   # produces io_scene_dts-1.0.0.zip
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
  Sequence metadata lives in action custom props: `dts_cyclic`, `dts_blend`,
  `dts_priority`, `dts_duration`, `dts_tool_begin`, plus JSON blobs for
  ground frames (`dts_ground`), triggers (`dts_triggers`), object
  visibility/frame tracks (`dts_object_anim`) and scale animation
  (`dts_scale_anim`).  Blend sequences store raw blend offsets in the pose.
- **Sequences are NLA strips, always.**  Playback speed is not a scene
  property: a sequence stores its own `dts_duration`, and one shape's sequences
  disagree — across light_male and its DSQs, 13 are authored at 30 fps and 21
  at about 15 — while keyframes are laid one per Blender frame.  An Action
  assigned straight to the armature therefore plays at `scene.render.fps` and
  is wrong for all but the sequences that happen to match, so the importer
  never assigns one.  Each sequence gets a track with

      strip.scale = dts_duration * fps / (dts_keyframes - 1)

  so every strip spans its real duration whatever the scene is set to.  A
  sequence the armature can evaluate nothing from (a decal-only one) gets no
  strip and is reported.
- **Object visibility** animates.  A sequence's `vis` track is keyframed as a
  custom property on the armature, in the same slot as the bones, and each mesh
  built from that object reads it through a driver into alpha (`color[3]`) and
  the hide flags.  One DTS object is one mesh *per detail level*, so the driver
  fans out across LODs — light_male's `Jetfire` is ten meshes.  Keeping the
  value on the armature means one animated ID and one strip, so retiming the
  strip retimes visibility too.  Drivers are display only; export samples
  `dts_object_anim` and never reads them, and `.dsq` cannot carry object states
  at all, so this round-trips through DTS only.  Export is unaffected: `dts_duration` stays the single source of
  truth, so dragging a strip never changes the written file.
- **Materials** are Principled BSDF with the texture found next to the .dts
  by material name; DTS flags are `dts_*` boolean custom props (the props are
  the round-trip source of truth).  The exporter always writes a self-index
  reflectance map (never `0xFFFFFFFF`, which crashes the engine).
- Export emits triangulated, indexed **Triangles** primitives grouped per
  material — the same policy as the engine's own `.mdl` exporter.

### Known limitations

The short list is below; [UNSUPPORTED.md](UNSUPPORTED.md) has the full
inventory, sorted by *how* a feature is unsupported — refused outright,
round-trips but uneditable, correct in the file but invisible in the viewport,
dropped outright, or frozen against Blender-side edits.

- Decal meshes are preserved verbatim (armature JSON) but not editable in Blender.
- Multi-frame (vertex-animated) meshes import as shape keys (`frame_NNN`).
- Sorted and multi-matframe meshes import as frozen payloads
  (`dts_frozen_payload`): they re-export verbatim, and export refuses with a
  clear error if their geometry was edited.
- IFL material entries are preserved (not editable in Blender).
- DTS versions below 17 (the keyframe-table era) are refused.

## Development

```sh
python -m venv .venv && .venv/bin/pip install pytest pytest-cov hypothesis

# fast unit loop (no corpus)
.venv/bin/python -m pytest -m "not corpus" -q

# full suite incl. the on-disk corpus sweep, with coverage
.venv/bin/python -m pytest --cov --cov-report=term-missing

# headless Blender integration tests (writes .coverage.blender.*)
blender --background --factory-startup --python tests/blender/run_blender_tests.py

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
