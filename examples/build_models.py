#!/usr/bin/env python3
"""Turn the three hand-modelled shapes into the committed examples 16-18.

``build_examples.py`` builds its fifteen shapes from nothing, so that script is
the source of truth for them.  These three were modelled by hand instead: the
source of truth is a ``.blend`` in the author's working tree, and this script
only does what has to happen for a checkout to stand on its own --

* pack every texture, so the ``.blend`` in the repo is not a bundle of absolute
  paths into somebody's home directory;
* drop the one texture that came out of a retail Tribes 2 install, replacing it
  with a generated stand-in of the same size;
* give the crate the armature the exporter asks for, which its working file has
  never had.

Run:
    blender --background --factory-startup --python examples/build_models.py

``--source DIR`` points at the working tree (default ``~/Documents/3D Design``);
``--export DIR`` also writes each shape's ``.dts``.

Unlike ``build_examples.py`` this needs files that are not in the repo, so a
checkout cannot re-run it.  That is the trade for shapes a script did not make.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO.parent))

import io_scene_dts  # noqa: E402

try:
    io_scene_dts.register()
except Exception:  # already registered
    pass


DEFAULT_SOURCE = Path.home() / "Documents" / "3D Design"


# ----------------------------------------------------------------------
# shared
# ----------------------------------------------------------------------


def generated_image(name, size, rgba_fn):
    """An image with no file behind it, ready to be packed."""
    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    pixels = []
    for y in range(size):
        for x in range(size):
            pixels.extend(rgba_fn(x / (size - 1), y / (size - 1)))
    image.pixels = pixels
    return image


def purge_unused_images():
    """Drop image datablocks nothing points at.

    A working file accumulates them -- three superseded crate atlases in this
    case -- and packing them would put megabytes of dead texture in the repo.
    """
    dropped = []
    for image in list(bpy.data.images):
        if image.users == 0 and not image.use_fake_user:
            dropped.append(image.name)
            bpy.data.images.remove(image)
    return dropped


def pack_everything():
    """Put every texture inside the .blend.

    Two things have to happen for this to stick.  An image whose file has never
    been displayed has no pixels in a background session, so packing it silently
    packs nothing until it is reloaded; and a generated image is stored in a
    .blend as the *parameters* that made it, not as pixels, so it has to be
    packed too or it comes back as flat grey.
    """
    for image in bpy.data.images:
        if image.source == "VIEWER" or image.packed_file:
            continue
        if image.source == "FILE" and image.filepath and not image.has_data:
            image.reload()
        try:
            image.pack()
        except RuntimeError as err:
            print(f"  could not pack {image.name!r}: {err}")


# ----------------------------------------------------------------------
# per-model preparation
# ----------------------------------------------------------------------


def sky(u, v):
    """A two-band sky gradient with a sun, which is all an environment map is
    doing for a reflective crate."""
    horizon = (0.55, 0.6, 0.68, 1.0)
    zenith = (0.18, 0.36, 0.72, 1.0)
    t = min(1.0, max(0.0, (v - 0.35) / 0.65))
    r, g, b, _ = tuple(h + (z - h) * t for h, z in zip(horizon, zenith))
    glare = max(0.0, 1.0 - math.hypot(u - 0.32, v - 0.78) / 0.14)
    return (min(1.0, r + glare), min(1.0, g + glare), min(1.0, b + glare), 1.0)


def prepare_test_crate():
    """A single-LOD crate with a collision box.

    The working file is a bare cube with no armature, which the exporter
    refuses -- it wants a shape root to hang the node tree off.  The names here
    reproduce the shape the author actually ships: node ``Start``, objects
    ``TestCrate`` and ``Collision``, details ``detail4`` and ``Collision-1``.
    """
    for name in ("Camera", "Light"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj)

    # the reflection the crate's material asks for came from the game's own
    # lush_day_emap; the shape does not care which sky it is
    emap = bpy.data.images.get("lush_day_emap")
    if emap is not None:
        replacement = generated_image("crate_emap", 128, sky)
        trees = list(bpy.data.node_groups)
        trees += [m.node_tree for m in bpy.data.materials if m.node_tree]
        trees += [w.node_tree for w in bpy.data.worlds if w.node_tree]
        for tree in trees:
            for node in tree.nodes:
                if getattr(node, "image", None) is emap:
                    node.image = replacement
        bpy.data.images.remove(emap)

    cube = bpy.data.objects["Cube"]
    cube.name = cube.data.name = "TestCrate4"

    arm_data = bpy.data.armatures.new("TestCrate")
    arm = bpy.data.objects.new("TestCrate", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm_data.edit_bones.new("Start")
    bone.head = Vector((0.0, 0.0, 0.0))
    bone.tail = Vector((0.0, 0.0, 0.25))
    bpy.ops.object.mode_set(mode="OBJECT")
    cube.parent = arm

    # a collision box the crate fits inside, which is what makes it solid
    verts = [
        (-0.55, -0.55, -0.55), (0.55, -0.55, -0.55), (0.55, 0.55, -0.55),
        (-0.55, 0.55, -0.55), (-0.55, -0.55, 0.55), (0.55, -0.55, 0.55),
        (0.55, 0.55, 0.55), (-0.55, 0.55, 0.55),
    ]
    faces = [
        (0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    mesh = bpy.data.meshes.new("Collision-1")
    mesh.from_pydata([Vector(v) for v in verts], [], faces)
    mesh.update()
    mesh.uv_layers.new(name="UVMap")
    collision = bpy.data.objects.new("Collision-1", mesh)
    bpy.context.scene.collection.objects.link(collision)
    collision.parent = arm

    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    return arm


def prepare_tutorial_player():
    """The tutorial player, unchanged.

    This one is imported Torque SDK sample content -- ``tests/fixtures/NOTES.md``
    says so rather than leaving it to be discovered.  Nothing is stripped: the
    texture is already packed and the shape is what it is.
    """
    return bpy.data.objects["player"]


def prepare_crt_monitor():
    """The CRT monitor, unchanged.

    Every texture is already packed and the high-poly ``CRT Reference``
    collection is already excluded from the view layer, so the exporter sees
    only the three LODs, the collision box and the IFL screen.
    """
    return bpy.data.objects["crt"]


MODELS = {
    "16_test_crate": ("Tribes2/TestCrate.blend", prepare_test_crate),
    "17_tutorial_player": ("TutorialPlayer.blend", prepare_tutorial_player),
    "18_crt_monitor": ("CRT Monitor/crt.blend", prepare_crt_monitor),
}


# ----------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------


def build(name, source_dir: Path, out_dir: Path, export_dir: Path | None):
    relative, prepare = MODELS[name]
    source = source_dir / relative
    if not source.is_file():
        raise SystemExit(f"{name}: no source at {source}")

    bpy.ops.wm.open_mainfile(filepath=str(source))
    arm = prepare()
    dropped = purge_unused_images()
    pack_everything()
    if dropped:
        print(f"  dropped {len(dropped)} unused image(s): {', '.join(sorted(dropped))}")

    arm_name = arm.name
    blend = out_dir / f"{name}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))

    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)
        # reopen rather than export the session that built it: a uv layer added
        # to a mesh made with from_pydata reads back empty until the file has
        # been through a save, and the exporter indexes it by loop
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        arm = bpy.data.objects[arm_name]
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.io_scene_dts.export_dts(
            filepath=str(export_dir / f"{name}.dts"), version="24"
        )
    return blend


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--out", default=str(REPO / "examples"))
    parser.add_argument("--export", default=None, help="also write .dts here")
    parser.add_argument("names", nargs="*", help="models to build (default: all)")
    args = parser.parse_args(argv)

    source_dir = Path(args.source)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    export_dir = Path(args.export) if args.export else None

    names = args.names or list(MODELS)
    for name in names:
        if name not in MODELS:
            print(f"unknown model {name!r}")
            return 2
        print(f"building {name}")
        print(f"built {build(name, source_dir, out_dir, export_dir).name}")
    print(f"\n{len(names)} model(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
