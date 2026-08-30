#!/usr/bin/env python3
"""Build tests/fixtures/ from the example .blend files.

Run:
    blender --background --factory-startup --python tests/fixtures/build_fixtures.py

The fixtures used to be shapes lifted out of Tribes 2 and the Torque SDK.  They
are exports of ``examples/`` now, which costs one thing and buys another.

What it costs: a reader test that reads a file this library wrote is checking
the pair against itself, not against the format.  Nothing here can catch the
two halves agreeing on something the engine does not do.  That is what the
corpus tests are for -- they run against the real game data on a machine that
has it, and they are the reason this trade is affordable.  ``scripts/mutate.py``
carries the other half of the guard: break the writer and a fixture-backed test
must still notice.

What it buys: a checkout with no game bytes in it, and fixtures whose source is
a ``.blend`` in the same repo, so "why does this file have a sorted mesh" has an
answer you can open.

Each entry names the property the fixture exists to carry.  Adding a version to
the sweep is cheap; adding a *property* means finding an example that has it, or
building one in ``examples/build_examples.py`` first.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO.parent))
sys.path.insert(0, str(REPO))

import io_scene_dts  # noqa: E402

try:
    io_scene_dts.register()
except Exception:  # already registered
    pass

EXAMPLES = REPO / "examples"
FIXTURES = REPO / "tests" / "fixtures"


# Every example at v24, so a test that wants a shape with some feature in it
# has one to reach for.  The name is the example without its number: the
# ordering in examples/ is a reading order, and a fixture called
# ``v24_13_decals.dts`` would go stale the moment an example is inserted.
EVERY_EXAMPLE = [
    "01_detail_levels",
    "02_billboards",
    "03_sorted_foliage",
    "04_blend_modes",
    "05_material_flags",
    "06_skin_animation",
    "07_vertex_animation",
    "08_material_frames",
    "09_sequence_triggers",
    "10_ground_frames",
    "11_visibility",
    "12_node_scale",
    "13_decals",
    "14_ifl_material",
    "15_dsq_animation",
    "16_test_crate",
    "17_tutorial_player",
    "18_crt_monitor",
]


def _slug(example: str) -> str:
    return example.split("_", 1)[1]


# fixture name -> (example, version, what it is for).  Older versions only:
# the v24 sweep above covers the rest.
SHAPES = {
    # the keyframe era: animation stored keyframe-major, through a table the
    # engine reads and throws away
    "v15_sequence_triggers.dts": ("09_sequence_triggers", 15, "keyframe-major animation, mesh index list"),
    "v16_sorted_foliage.dts": ("03_sorted_foliage", 16, "keyframe era, sorted mesh"),
    "v16_detail_levels.dts": ("01_detail_levels", 16, "keyframe era with null-mesh type words rather than a mesh index list"),
    # the flat stream: no bounds, no vertex sharing, no merge indices
    "v18_test_crate.dts": ("16_test_crate", 18, "smallest flat-stream shape"),
    # v19 restores the modern stream
    "v19_test_crate.dts": ("16_test_crate", 19, "smallest v19"),
    "v19_detail_levels.dts": ("01_detail_levels", 19, "v19 multi-detail with vertex sharing"),
    "v19_sorted_foliage.dts": ("03_sorted_foliage", 19, "v19 sorted mesh + translucent material"),
    "v19_decals.dts": ("13_decals", 19, "the only version that writes an empty mesh header in front of a decal"),
    # v20/v21 pair every node's rotation with a translation
    "v21_sorted_foliage.dts": ("03_sorted_foliage", 21, "v21 sorted + multi-detail"),
    "v21_material_frames.dts": ("08_material_frames", 21, "multi-frame material animation"),
    # v22 and v23: Tribes 2's own two
    "v22_test_crate.dts": ("16_test_crate", 22, "smallest v22"),
    "v22_detail_levels.dts": ("01_detail_levels", 22, "v22 multi-detail"),
    "v22_ifl_material.dts": ("14_ifl_material", 22, "v22 animated + IFL material"),
    "v22_crt_monitor.dts": ("18_crt_monitor", 22, "v22 LODs, collision, IFL and a visibility track"),
    "v22_sequence_triggers.dts": ("09_sequence_triggers", 22, "rotation and translation animating different nodes"),
    "v22_decals.dts": ("13_decals", 22, "v22 decal mesh"),
    "v22_skin_animation.dts": ("06_skin_animation", 22, "pre-v23 keeps skins in a section of their own"),
    "v23_crt_monitor.dts": ("18_crt_monitor", 23, "v23 animated, LODs, collision, IFL"),
    "v23_decals.dts": ("13_decals", 23, "v23 standard + decal + null mesh in one shape"),
    "v23_tutorial_player.dts": ("17_tutorial_player", 23, "v23 many nodes, many sequences"),
    "v23_skin_animation.dts": ("06_skin_animation", 23, "v23 skinned"),
    "v23_vertex_animation.dts": ("07_vertex_animation", 23, "v23 mesh frames"),
}

SHAPES.update(
    {
        f"v24_{_slug(example)}.dts": (example, 24, "the newest version")
        for example in EVERY_EXAMPLE
    }
)

# The one shape exported with its textures beside it, into a directory of its
# own: material-to-texture pairing is the thing being tested and it needs files
# on disk to pair against, including the IFL frames in their own subdirectory.
TEXTURED = {"crt_monitor": "18_crt_monitor"}

# The engine -- and the importer -- find a material's texture by its name, next
# to the shape, so a fixture whose textures are not beside it imports with bare
# materials and an import test has nothing to look at.  The generated examples'
# textures are already built and committed under the showcase mod; they are
# procedural and 64x64, and the whole set is 108K.
#
# They are copied rather than re-exported because a saved example .blend has no
# image datablocks in it: build_examples.py writes the .png files itself, from
# the same functions that named the materials.  Exporting one produces a shape
# that names `crate` and no crate.png at all.
EXAMPLE_TEXTURES = REPO / "examples" / "mod" / "DtsExamples" / "textures"

# fixture name -> (example, what it is for).  The operator writes the modern
# layout only; the older one is written from it below.
SEQUENCES = {
    "v24_dsq_animation.dsq": ("15_dsq_animation", "smallest v24 DSQ"),
    "v24_tutorial_player.dsq": ("17_tutorial_player", "v24 DSQ, thirteen sequences"),
    "v24_ground_frames.dsq": ("10_ground_frames", "the only DSQ with ground frames in it"),
}

# fixture name -> (v24 fixture it is written down from, version, what it is for)
DOWNGRADED_SEQUENCES = {
    "v22_dsq_animation.dsq": ("v24_dsq_animation.dsq", 22, "the old DSQ layout"),
}


def _open(example: str):
    blend = EXAMPLES / f"{example}.blend"
    if not blend.is_file():
        raise SystemExit(f"no example at {blend}")
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    return arm


def build(out_dir: Path) -> int:
    written = 0
    # export into a scratch directory and take only the shape: the exporter
    # also writes each material's .ifl beside it, which is art, not a fixture
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        for name, (example, version, _why) in SHAPES.items():
            _open(example)
            path = scratch / name
            bpy.ops.io_scene_dts.export_dts(
                filepath=str(path), version=str(version), export_textures=False
            )
            shutil.copyfile(path, out_dir / name)
            written += 1
            print(f"  {name}  <- {example} @ v{version}")

        for name, (example, _why) in SEQUENCES.items():
            _open(example)
            path = scratch / name
            bpy.ops.io_scene_dts.export_dsq(filepath=str(path))
            shutil.copyfile(path, out_dir / name)
            written += 1
            print(f"  {name}  <- {example}")

    textures = sorted(EXAMPLE_TEXTURES.glob("*.png"))
    for png in textures:
        shutil.copyfile(png, out_dir / png.name)
        written += 1
    print(f"  {len(textures)} texture(s) copied from {EXAMPLE_TEXTURES.name}/")

    for directory, example in TEXTURED.items():
        target = out_dir / directory
        if target.is_dir():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        _open(example)
        bpy.ops.io_scene_dts.export_dts(
            filepath=str(target / f"{_slug(example)}.dts"), version="24"
        )
        count = sum(1 for _ in target.rglob("*") if _.is_file())
        written += count
        print(f"  {directory}/  <- {example} with its textures ({count} files)")

    from io_scene_dts.dtslib import read_dsq, write_dsq

    for name, (source, version, _why) in DOWNGRADED_SEQUENCES.items():
        dsq = read_dsq((out_dir / source).read_bytes())
        (out_dir / name).write_bytes(write_dsq(dsq, version))
        written += 1
        print(f"  {name}  <- {source} written back as v{version}")

    return written


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else FIXTURES
    out.mkdir(parents=True, exist_ok=True)
    print(f"{build(out)} fixture(s)")
