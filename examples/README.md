# Examples

One `.blend` per implemented DTS feature, each built from nothing in Blender —
no import anywhere in its history. They are the worked answer to the question
`tests/blender/test_authoring.py` asks in the abstract: *can a user actually
make one of these?*

| Example | Feature |
| --- | --- |
| `01_detail_levels` | Four LODs plus a collision mesh; vertex sharing between levels |
| `02_billboards` | A camera-facing flare and an upright, spinning trunk card |
| `03_sorted_foliage` | Overlapping alpha-cut cards with a re-derived BSP draw order |
| `04_blend_modes` | Translucent, additive and subtractive, side by side |
| `05_material_flags` | Self-illumination and environment mapping |
| `06_skin_animation` | A skinned limb bending under its bones |
| `07_vertex_animation` | `frame_NNN` shape keys, stepped by a sequence track; the pole fades in the same sequence, so its object-state blocks are not all one channel |
| `08_material_frames` | A UV flipbook in mesh attributes |
| `09_sequence_triggers` | A cyclic sequence with two triggers |
| `10_ground_frames` | Root motion — what gives a movement animation its speed |
| `11_visibility` | An object fading out and back |
| `12_node_scale` | Node scale animation on the bones' own scale channels |
| `13_decals` | Battle damage: decals switched on as a Damage sequence advances |
| `14_ifl_material` | An IFL entry the engine flips through |
| `15_dsq_animation` | A shape whose animation ships separately, as `.dsq` |

## The hand-modelled three

`16`–`18` are not built from nothing by a script. They were modelled by hand,
which is the point of having them: the fifteen above are each one feature held
still, and these are what a shape looks like when somebody was making a *thing*
rather than a demonstration.

| Example | What it is |
| --- | --- |
| `16_test_crate` | A crate: one LOD, a collision box, a reflectance map |
| `17_tutorial_player` | 45 bones, 15 meshes, 13 sequences — a player skeleton |
| `18_crt_monitor` | Three LODs, a collision box, an IFL screen, a visibility track and a power sequence |

`17_tutorial_player` is the odd one and is flagged here rather than left to be
found: it is an *import* of the Torque SDK's own tutorial player, `player.dts`
plus the `.dsq` set beside it, and its texture is the SDK's. It is kept because
nothing authored here is anywhere near that size, and the DSQ and matters-set
tests want a shape that is.

## Rebuilding

```sh
blender --background --factory-startup --python examples/build_examples.py -- --export examples/dist
```

`build_examples.py` is the source of truth for `01`–`15`; the `.blend` files are
committed so they can be opened without running it. `--export` also writes each
shape's `.dts` and its generated textures into `examples/dist/`.

`16`–`18` come from `build_models.py` instead, which takes the author's working
files and does only what a checkout needs — packs the textures, drops the one
that came out of a retail game install, and gives the crate the armature the
exporter asks for. Those source files are not in this repository, so unlike
`build_examples.py` a checkout cannot re-run it. That is the trade for shapes a
script did not make.

## Fixtures

`tests/fixtures/build_fixtures.py` exports these shapes at the DTS and DSQ
versions the test suite needs. That is the whole of `tests/fixtures/` — change
an example and the fixtures change with it, so run it afterwards.

## Verifying in Tribes 2

```sh
examples/verify_in_tribes2.sh 2400
```

Boots the game in a container, hosts a terrain-only mission, loads every
example, and screenshots each into `examples/screenshots/`.

Four things about this engine cost real time to find, so they are written down
rather than left in a shell history:

1. **A datablock created after the client joins never reaches the client.**
   Torque transmits datablocks during the connection handshake. Render a shape
   whose datablock the client lacks and you get an access violation, not a
   warning — and it looks exactly like a broken export. Every `StaticShapeData`
   is therefore declared, and every shape spawned, between `CreateServer` and
   `localConnect`. Chasing this cost an afternoon of blaming the exporter.

2. **Textures live in `GameData/base/textures/`, not beside the `.dts`.**
   A material named `crate` wants `textures/crate.png` however deep in
   `shapes/` the shape itself sits. This contradicts a plain reading of
   `MaterialList::load`, which builds `<shape dir>/<material name>` — the
   texture manager evidently falls back to a `textures/` search root. Verified
   by elimination: the same texture in `shapes/dtsx/`, `shapes/` and the mod
   root all render flat grey; in `textures/` it renders.

3. **A missing texture and a broken UV unwrap look identical** — both give a
   flat, evenly lit surface. `mesh.uv_layers.new()` on a mesh built with
   `from_pydata` leaves every corner at the same coordinate, which is why
   `build_examples.py` has its own `box_unwrap`.

4. **The spawned player falls.** Anything positioned relative to it has moved
   by the time a screenshot lands, so the viewpoint is a `Camera` on the stock
   `Observer` datablock, which stays where it is put.

The mission is terrain-only (`DtsExamples.mis`, the Rimehold environment with
every `InteriorInstance` stripped) because the client lighting pass over
interiors never finishes under software rendering and wedges the join.

## What a screenshot does and does not prove

A shape that renders proves the geometry, materials, textures, UVs and detail
structure survived export in a form the engine reads. It does not prove
anything that only shows over *time* — a billboard turning to face you, a
sequence playing, a decal appearing at 70% damage. Those need either a
sequence of screenshots from moving viewpoints or console state that changes;
where a feature is verified that way, the example's entry above says so.

`playThread` is not evidence: it succeeds silently for a sequence name that
does not exist, which was checked before relying on it.

### Decals do not render through a StaticShape

`13_decals` renders its hull plate and no scorch marks, at any point in the
Damage sequence and even with the decal state defaulted to on and no sequence
involved at all.

That is not a fault in the export.  Tribes 2's own `bioderm_light.dts`, spawned
the same way from the game's own data, renders in full and textured, and *its*
24 decals do not appear either.  (The screenshot that showed this was a render
of the game's own art and is no longer in the repository; the measurement
stands, and re-taking it needs a Tribes 2 install.)
`game/player.cc:3980,4030` is the only code that manages
`smRenderData.renderDecals`, which suggests decal rendering is driven by the
Player class rather than being available to any shape — and a StaticShape is
what an imported model has to be, since `PlayerData` preload validates a
Tribes 2 player skeleton and crashes on anything else.

What *is* verified about decals: the exported file carries two decals whose
state tracks ramp -1 to 0 at keyframes 3 and 7 — the shipped damage pattern,
matching 47 of the 49 decal-bearing corpus shapes — and whose texgen planes
put the covered faces inside the 0..1 square.  Both are asserted by
`tests/blender/test_authoring.py`.
