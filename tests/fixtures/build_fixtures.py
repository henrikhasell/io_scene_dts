#!/usr/bin/env python3
"""Build tests/fixtures/ from the example shapes.

Run:
    blender --background --factory-startup --python tests/fixtures/build_fixtures.py

Two kinds of shape feed this, and the difference is where the ``.blend`` comes
from rather than anything about the fixture.  The four in ``examples/`` were
made by hand and are committed.  The fifteen one-feature showcase shapes are
built from nothing by ``examples/build_examples.py`` and are *not* committed,
so they are rebuilt into a scratch directory here, once each, on the way past.

Everything below is keyed by the shape's name without its number -- the number
orders the showcase, it is not part of the shape's identity, and the two sets
number from 1 independently.

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

sys.path.insert(0, str(EXAMPLES))

import build_examples  # noqa: E402
import build_models  # noqa: E402


def _by_slug(names) -> dict[str, str]:
    """``{"detail_levels": "01_detail_levels", ...}``.

    Both sets number from 1, so the number cannot be part of a fixture's name
    without two shapes colliding on it.  Taking the map from the two builders
    rather than repeating it keeps this file from being a third place a shape
    has to be listed.
    """
    return {name.split("_", 1)[1]: name for name in names}


# built here, into a scratch directory, by examples/build_examples.py
SHOWCASE = _by_slug(build_examples.EXAMPLES)
# committed under examples/, built by examples/build_models.py from files that
# are not in this repository
COMMITTED = _by_slug(build_models.MODELS)

# _blend() resolves a committed shape first, so a slug in both sets would take
# the committed one and quietly stop testing the showcase one -- with the
# fixture still written, still named the same, and still passing.
_clash = sorted(set(SHOWCASE) & set(COMMITTED))
if _clash:
    raise SystemExit(
        f"{', '.join(_clash)}: named by both build_examples.py and "
        f"build_models.py.  A fixture is keyed by the name without its number, "
        f"so the two sets cannot share one."
    )

# The light male is an import of a retail Tribes 2 player.  It is an example --
# it is the biggest thing here and the only one with a real animation library
# on it -- but a fixture is a file this repository checks in, and NOTES.md's
# claim that none of them are game bytes is worth more than one more shape to
# reach for.  Nothing is lost that the corpus tests do not already cover.
NOT_A_FIXTURE = {"light_male"}

# Every other shape at v24, so a test that wants one with some feature in it
# has one to reach for.
EVERY_EXAMPLE = [s for s in list(SHOWCASE) + list(COMMITTED) if s not in NOT_A_FIXTURE]


# fixture name -> (example, version, what it is for).  Older versions only:
# the v24 sweep above covers the rest.
SHAPES = {
    # the keyframe era: animation stored keyframe-major, through a table the
    # engine reads and throws away
    "v15_sequence_triggers.dts": ("sequence_triggers", 15, "keyframe-major animation, mesh index list"),
    "v16_sorted_foliage.dts": ("sorted_foliage", 16, "keyframe era, sorted mesh"),
    "v16_detail_levels.dts": ("detail_levels", 16, "keyframe era with null-mesh type words rather than a mesh index list"),
    # the flat stream: no bounds, no vertex sharing, no merge indices
    "v18_test_crate.dts": ("test_crate", 18, "smallest flat-stream shape"),
    # v19 restores the modern stream
    "v19_test_crate.dts": ("test_crate", 19, "smallest v19"),
    "v19_detail_levels.dts": ("detail_levels", 19, "v19 multi-detail with vertex sharing"),
    "v19_sorted_foliage.dts": ("sorted_foliage", 19, "v19 sorted mesh + translucent material"),
    "v19_decals.dts": ("decals", 19, "the only version that writes an empty mesh header in front of a decal"),
    # v20/v21 pair every node's rotation with a translation
    "v21_sorted_foliage.dts": ("sorted_foliage", 21, "v21 sorted + multi-detail"),
    "v21_material_frames.dts": ("material_frames", 21, "multi-frame material animation"),
    # v22 and v23: Tribes 2's own two
    "v22_test_crate.dts": ("test_crate", 22, "smallest v22"),
    "v22_detail_levels.dts": ("detail_levels", 22, "v22 multi-detail"),
    "v22_ifl_material.dts": ("ifl_material", 22, "v22 animated + IFL material"),
    "v22_crt_monitor.dts": ("crt_monitor", 22, "v22 LODs, collision, IFL and a visibility track"),
    "v22_sequence_triggers.dts": ("sequence_triggers", 22, "rotation and translation animating different nodes"),
    "v22_decals.dts": ("decals", 22, "v22 decal mesh"),
    "v22_skin_animation.dts": ("skin_animation", 22, "pre-v23 keeps skins in a section of their own"),
    "v23_crt_monitor.dts": ("crt_monitor", 23, "v23 animated, LODs, collision, IFL"),
    "v23_decals.dts": ("decals", 23, "v23 standard + decal + null mesh in one shape"),
    "v23_tutorial_player.dts": ("tutorial_player", 23, "v23 many nodes, many sequences"),
    "v23_skin_animation.dts": ("skin_animation", 23, "v23 skinned"),
    "v23_vertex_animation.dts": ("vertex_animation", 23, "v23 mesh frames"),
}

SHAPES.update(
    {f"v24_{slug}.dts": (slug, 24, "the newest version") for slug in EVERY_EXAMPLE}
)

# The one shape exported with its textures beside it, into a directory of its
# own: material-to-texture pairing is the thing being tested and it needs files
# on disk to pair against, including the IFL frames in their own subdirectory.
TEXTURED = {"crt_monitor": "crt_monitor"}

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
    "v24_dsq_animation.dsq": ("dsq_animation", "smallest v24 DSQ"),
    "v24_tutorial_player.dsq": ("tutorial_player", "v24 DSQ, thirteen sequences"),
    "v24_ground_frames.dsq": ("ground_frames", "the only DSQ with ground frames in it"),
}

# fixture name -> (v24 fixture it is written down from, version, what it is for)
DOWNGRADED_SEQUENCES = {
    "v22_dsq_animation.dsq": ("v24_dsq_animation.dsq", 22, "the old DSQ layout"),
}


def _blend(slug: str, scratch: Path) -> Path:
    """The ``.blend`` for a shape, building it first if it is a showcase one.

    Built once and kept: nine of the fixtures below come from three shapes, and
    rebuilding a shape per fixture would run build_examples.py forty times.
    """
    if slug in COMMITTED:
        blend = EXAMPLES / f"{COMMITTED[slug]}.blend"
        if not blend.is_file():
            raise SystemExit(f"no example at {blend}")
        return blend
    if slug not in SHOWCASE:
        raise SystemExit(f"no shape called {slug!r} in examples/")
    blend = scratch / f"{SHOWCASE[slug]}.blend"
    if not blend.is_file():
        print(f"  building {SHOWCASE[slug]}")
        build_examples.build(SHOWCASE[slug], scratch, None)
    return blend


def _open(slug: str, scratch: Path):
    bpy.ops.wm.open_mainfile(filepath=str(_blend(slug, scratch)))
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    return arm


def build(out_dir: Path) -> int:
    written = 0
    # export into a scratch directory and take only the shape: the exporter
    # also writes each material's .ifl beside it, which is art, not a fixture.
    # The showcase .blend files are built in here too, and go the same way.
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        for name, (slug, version, _why) in SHAPES.items():
            _open(slug, scratch)
            path = scratch / name
            bpy.ops.io_scene_dts.export_dts(
                filepath=str(path), version=str(version), export_textures=False
            )
            shutil.copyfile(path, out_dir / name)
            written += 1
            print(f"  {name}  <- {slug} @ v{version}")

        for name, (slug, _why) in SEQUENCES.items():
            _open(slug, scratch)
            path = scratch / name
            bpy.ops.io_scene_dts.export_dsq(filepath=str(path))
            shutil.copyfile(path, out_dir / name)
            written += 1
            print(f"  {name}  <- {slug}")

        for directory, slug in TEXTURED.items():
            target = out_dir / directory
            if target.is_dir():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            _open(slug, scratch)
            bpy.ops.io_scene_dts.export_dts(
                filepath=str(target / f"{slug}.dts"), version="24"
            )
            count = sum(1 for _ in target.rglob("*") if _.is_file())
            written += count
            print(f"  {directory}/  <- {slug} with its textures ({count} files)")

    textures = sorted(EXAMPLE_TEXTURES.glob("*.png"))
    for png in textures:
        shutil.copyfile(png, out_dir / png.name)
        written += 1
    print(f"  {len(textures)} texture(s) copied from {EXAMPLE_TEXTURES.name}/")

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
