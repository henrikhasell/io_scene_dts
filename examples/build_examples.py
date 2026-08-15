#!/usr/bin/env python3
"""Build the example .blend files, one per implemented DTS feature.

Run:
    blender --background --factory-startup --python examples/build_examples.py

Each example is a shape a user could have made in Blender -- nothing here sets
a property the importer would have written, for the same reason
tests/blender/test_authoring.py does not: an example that only works because
it came from a file is not an example of authoring.

The .blend files are the deliverable and are committed.  This script is how
they are reproduced, so a change to the add-on can be rolled through them
rather than re-made by hand.

``--export DIR`` also writes each example's .dts and its textures, which is
what gets loaded into the game.

``--lifts`` then rewrites the ``$DtsShowcaseLift`` table in the showcase script
from those .dts files, so the shapes keep standing on the terrain when a model
changes height.
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
sys.path.insert(0, str(REPO / "tests" / "blender"))

import io_scene_dts  # noqa: E402

try:
    io_scene_dts.register()
except Exception:  # already registered
    pass

import authoring as A  # noqa: E402
from io_scene_dts.mapping.decals import create_decal  # noqa: E402
from io_scene_dts.mapping.objectstate import ensure_props, path_for  # noqa: E402

EXAMPLES = {}


def box_unwrap(obj, scale=1.0):
    """A per-face planar unwrap onto the dominant axis.

    bpy.ops.uv.smart_project needs a real context this script does not have,
    and mesh.uv_layers.new() on a from_pydata mesh leaves every corner at the
    same coordinate -- which renders as one flat colour and looks exactly like
    a texture that failed to load.
    """
    mesh = obj.data
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        normal = polygon.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        u_axis, v_axis = [i for i in range(3) if i != axis]
        for loop_index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            uv.data[loop_index].uv = (
                co[u_axis] * scale + 0.5,
                co[v_axis] * scale + 0.5,
            )
    return obj


def example(name):
    def register(fn):
        EXAMPLES[name] = fn
        return fn

    return register


# ----------------------------------------------------------------------
# textures
# ----------------------------------------------------------------------


def make_texture(name, rgba_fn, size=64):
    """A generated image saved beside the .dts.

    The engine finds a material's texture by name next to the shape file, so
    the material name *is* the filename -- there is no path in the format.
    """
    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    pixels = []
    for y in range(size):
        for x in range(size):
            pixels.extend(rgba_fn(x / (size - 1), y / (size - 1)))
    image.pixels = pixels
    return image


def checker(u, v, a=(0.8, 0.8, 0.85, 1.0), b=(0.25, 0.28, 0.32, 1.0), n=8):
    return a if (int(u * n) + int(v * n)) % 2 else b


def leaf(u, v):
    """An alpha-cut leaf shape, so translucency and sorting have something to
    actually show."""
    dx, dy = u - 0.5, v - 0.5
    r = math.hypot(dx, dy * 1.6)
    alpha = 1.0 if r < 0.42 else 0.0
    green = 0.35 + 0.35 * (1.0 - r)
    return (0.12, green, 0.12, alpha)


def glow(u, v):
    r = math.hypot(u - 0.5, v - 0.5)
    intensity = max(0.0, 1.0 - r * 2.2)
    return (intensity, intensity * 0.6, 0.2 * intensity, 1.0)


def scorch(u, v):
    r = math.hypot(u - 0.5, v - 0.5)
    alpha = max(0.0, 1.0 - (r / 0.45) ** 2)
    return (0.05, 0.04, 0.04, alpha)


def stripes(u, v):
    band = int(v * 6) % 2
    return (0.9, 0.55, 0.1, 1.0) if band else (0.15, 0.15, 0.2, 1.0)


TEXTURES = {}


def textured_material(name, rgba_fn, **kwargs):
    """A material whose name is the texture filename the engine will look for."""
    mat = A.principled_material(name, **kwargs)
    TEXTURES[name] = rgba_fn
    return mat


# ----------------------------------------------------------------------
# examples
# ----------------------------------------------------------------------


@example("01_detail_levels")
def build_detail_levels():
    """Four LODs plus a collision mesh -- the structure every shape has."""
    arm = A.armature("Crate")
    mat = textured_material("crate", checker)
    for size, subdiv in ((64, 0.6), (32, 0.5), (8, 0.45), (2, 0.4)):
        verts, faces = A.cube_geometry(subdiv)
        A.mesh_object(f"crate{size}", arm, bone="root", verts=verts, faces=faces,
                      material=mat)
    verts, faces = A.cube_geometry(0.62)
    A.mesh_object("Collision-1", arm, bone="root", verts=verts, faces=faces)
    return arm


@example("02_billboards")
def build_billboards():
    """A camera-facing flare and an upright, spinning trunk card.

    Both cards stand in the XZ plane, matching every billboard in the shipped
    Tribes 2 art (see ``A.upright_quad_geometry``).  The flags are correct in
    the file and a stock billboard shape round-tripped through this add-on
    still billboards in Tribes 2, but these two cards do not -- the difference
    has not been isolated.  UNSUPPORTED.md carries the measurements.
    """
    arm = A.armature("Billboards", bones=(("root", None), ("high", "root")))
    flare_mat = textured_material("flare", glow)
    verts, faces = A.upright_quad_geometry(size=1.5)
    flare = A.mesh_object("flare2", arm, bone="root", verts=verts, faces=faces,
                          material=flare_mat)
    flare.dts_mesh.billboard = True

    trunk_mat = textured_material("trunkcard", stripes)
    verts, faces = A.upright_quad_geometry(size=1.5)
    trunk = A.mesh_object("trunk2", arm, bone="high", verts=verts, faces=faces,
                          material=trunk_mat)
    trunk.dts_mesh.billboard = True
    trunk.dts_mesh.billboard_z = True
    trunk.location = Vector((4.0, 0.0, 0.0))
    return arm


@example("03_sorted_foliage")
def build_sorted_foliage():
    """Overlapping translucent cards, which is what sorted meshes exist for."""
    arm = A.armature("Bush")
    mat = textured_material("leafcard", leaf)
    mat.surface_render_method = "BLENDED"
    verts, faces = A.cards_geometry(16)
    obj = A.mesh_object("bush2", arm, bone="root", verts=verts, faces=faces,
                        material=mat)
    obj.dts_mesh.sorted_mode = "BSP"
    obj.dts_mesh.sorted_depth = 2
    return arm


@example("04_blend_modes")
def build_blend_modes():
    """The three the format has, side by side."""
    arm = A.armature("Blends")
    made = []
    for index, (name, kind) in enumerate(
        (("glass", "translucent"), ("plasma", "additive"), ("shade", "subtractive"))
    ):
        mat = textured_material(name, glow if kind != "translucent" else checker)
        mat.surface_render_method = "BLENDED"
        if kind != "translucent":
            _additive_graph(mat, subtractive=(kind == "subtractive"))
        verts, faces = A.quad_geometry()
        obj = A.mesh_object(f"{name}2", arm, bone="root", verts=verts, faces=faces,
                            material=mat)
        obj.location = Vector((index * 1.2, 0.0, 0.0))
        made.append(obj)
    return arm


def _additive_graph(mat, *, subtractive=False):
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    add = tree.nodes.new("ShaderNodeAddShader")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    emission = tree.nodes.new("ShaderNodeEmission")
    colour = (0.3, 0.6, 1.0, 1.0)
    if subtractive:
        invert = tree.nodes.new("ShaderNodeInvert")
        invert.inputs["Color"].default_value = colour
        tree.links.new(invert.outputs["Color"], emission.inputs["Color"])
    else:
        emission.inputs["Color"].default_value = colour
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], out.inputs["Surface"])
    mat.surface_render_method = "BLENDED"
    return mat


@example("05_material_flags")
def build_material_flags():
    """Self-illumination and environment mapping, which the engine reads off
    the material flags rather than the shader."""
    arm = A.armature("Flags")
    lamp = textured_material("lamppanel", glow)
    lamp["dts_self_illuminating"] = True
    chrome = textured_material("chrome", checker)
    chrome["dts_never_env_map"] = False
    chrome.dts_material.reflection_amount = 0.6
    for index, mat in enumerate((lamp, chrome)):
        verts, faces = A.cube_geometry(0.4)
        obj = A.mesh_object(f"panel{index}_2", arm, bone="root", verts=verts,
                            faces=faces, material=mat)
        obj.location = Vector((index * 1.2, 0.0, 0.0))
    return arm


@example("06_skin_animation")
def build_skin_animation():
    """A skinned mesh bending under its bones, with the sequence to bend it."""
    arm = A.armature("Limb", bones=(("root", None), ("mid", "root"), ("tip", "mid")))
    mat = textured_material("limb", stripes)

    verts, faces = [], []
    segments = 6
    for i in range(segments + 1):
        z = i * 0.35
        base = len(verts)
        for corner in ((-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2)):
            verts.append((corner[0], corner[1], z))
        if i:
            prev = base - 4
            for k in range(4):
                a, b = prev + k, prev + (k + 1) % 4
                c, d = base + k, base + (k + 1) % 4
                faces += [(a, b, d), (a, d, c)]
    obj = A.mesh_object("limb2", arm, verts=verts, faces=faces, material=mat)
    obj.parent_type = "OBJECT"
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    groups = {name: obj.vertex_groups.new(name=name) for name in ("root", "mid", "tip")}
    for vertex in obj.data.vertices:
        z = vertex.co.z
        name = "root" if z < 0.7 else ("mid" if z < 1.4 else "tip")
        groups[name].add([vertex.index], 1.0, "REPLACE")

    action = bpy.data.actions.new("Bend")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 2.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    for bone_name in ("mid", "tip"):
        bone = arm.pose.bones[bone_name]
        bone.rotation_mode = "QUATERNION"
        for frame in range(1, 11):
            angle = math.sin((frame - 1) / 9.0 * math.tau) * 0.5
            bone.rotation_quaternion = (math.cos(angle / 2), math.sin(angle / 2), 0.0, 0.0)
            bone.keyframe_insert("rotation_quaternion", frame=frame)
    return arm


@example("07_vertex_animation")
def build_vertex_animation():
    """frame_NNN shape keys, plus the sequence track that steps through them."""
    arm = A.armature("Flag", bones=(("root", None), ("mount", "root")))
    mat = textured_material("flagcloth", stripes)
    verts, faces = [], []
    for i in range(5):
        x = i * 0.25
        base = len(verts)
        verts += [(x, 0.0, 0.0), (x, 0.0, 0.6)]
        if i:
            faces += [(base - 2, base, base + 1), (base - 2, base + 1, base - 1)]
    obj = A.mesh_object("flag2", arm, bone="mount", verts=verts, faces=faces,
                        material=mat)

    obj.shape_key_add(name="Basis")
    for frame in range(1, 5):
        key = obj.shape_key_add(name=f"frame_{frame:03d}", from_mix=False)
        for point in key.data:
            wave = math.sin(point.co.x * 4.0 + frame * 1.4) * 0.12
            point.co = Vector((point.co.x, wave, point.co.z))

    action = bpy.data.actions.new("Wave")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["mount"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 6):
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    ensure_props(arm, "frame", ["flag"])
    bag = A.channelbag(action, arm)
    curve = bag.fcurves.new(data_path=path_for("frame", "flag"), index=0)
    curve.keyframe_points.add(5)
    for index, value in enumerate((0, 1, 2, 3, 4)):
        point = curve.keyframe_points[index]
        point.co = (index + 1, float(value))
        point.interpolation = "CONSTANT"
    curve.update()
    return arm


@example("08_material_frames")
def build_material_frames():
    """A UV flipbook: one mesh, several blocks of texture coordinates."""
    from io_scene_dts.mapping import matframes

    arm = A.armature("Sign", bones=(("root", None), ("face", "root")))
    mat = textured_material("signface", stripes)
    verts, faces = A.quad_geometry()
    obj = A.mesh_object("sign2", arm, bone="face", verts=verts, faces=faces,
                        material=mat)

    uv = obj.data.uv_layers.active
    for loop in obj.data.loops:
        co = obj.data.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x + 0.5, co.y + 0.5)
    for frame in range(1, 4):
        attr = obj.data.attributes.new(
            name=matframes.attr_name(frame), type="FLOAT2", domain="POINT"
        )
        for index, datum in enumerate(attr.data):
            co = obj.data.vertices[index].co
            # slide the window down the texture, one band per frame
            datum.vector = (co.x + 0.5, (co.y + 0.5) * 0.25 + frame * 0.25)

    action = bpy.data.actions.new("Flip")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["face"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 5):
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    ensure_props(arm, "matframe", ["sign"])
    bag = A.channelbag(action, arm)
    curve = bag.fcurves.new(data_path=path_for("matframe", "sign"), index=0)
    curve.keyframe_points.add(4)
    for index in range(4):
        point = curve.keyframe_points[index]
        point.co = (index + 1, float(index))
        point.interpolation = "CONSTANT"
    curve.update()
    return arm


@example("09_sequence_triggers")
def build_sequence_triggers():
    """A spinning turret with triggers, which scripts hang effects off."""
    arm = A.armature("Turret", bones=(("root", None), ("barrel", "root")))
    mat = textured_material("turret", checker)
    verts, faces = A.cube_geometry(0.5)
    A.mesh_object("base2", arm, bone="root", verts=verts, faces=faces, material=mat)
    verts, faces = A.cube_geometry(0.25)
    A.mesh_object("gun2", arm, bone="barrel", verts=verts, faces=faces, material=mat)

    action = bpy.data.actions.new("Spin")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 2.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["barrel"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 13):
        angle = (frame - 1) / 11.0 * math.tau
        bone.rotation_quaternion = (math.cos(angle / 2), 0.0, 0.0, math.sin(angle / 2))
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    for state, pos in ((1, 0.25), (2, 0.75)):
        trigger = action.dts_sequence_props.triggers.add()
        trigger.state, trigger.pos, trigger.on = state, pos, True
    return arm


@example("10_ground_frames")
def build_ground_frames():
    """Root motion: what gives a movement animation its speed."""
    arm = A.armature("Walker", bones=(("root", None), ("hip", "root")))
    mat = textured_material("walker", checker)
    verts, faces = A.cube_geometry(0.4)
    A.mesh_object("body2", arm, bone="hip", verts=verts, faces=faces, material=mat)

    action = bpy.data.actions.new("Run")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["hip"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 9):
        angle = math.sin((frame - 1) / 7.0 * math.tau) * 0.3
        bone.rotation_quaternion = (math.cos(angle / 2), math.sin(angle / 2), 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    for step in range(8):
        item = action.dts_sequence_props.ground.add()
        item.translation = (0.0, step * 0.4, 0.0)
        item.rotation = (0, 0, 0, 32767)
    return arm


@example("11_visibility")
def build_visibility():
    """An object fading out and back over a sequence."""
    arm = A.armature("Beacon", bones=(("root", None), ("mount", "root")))
    body = textured_material("beaconbody", checker)
    lamp = textured_material("beaconlamp", glow)
    verts, faces = A.cube_geometry(0.4)
    A.mesh_object("body2", arm, bone="root", verts=verts, faces=faces, material=body)
    verts, faces = A.cube_geometry(0.2)
    obj = A.mesh_object("lamp2", arm, bone="mount", verts=verts, faces=faces,
                        material=lamp)
    obj.location = Vector((0.0, 0.0, 0.7))

    action = bpy.data.actions.new("Pulse")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.5
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["mount"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 9):
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    ensure_props(arm, "vis", ["lamp"])
    bag = A.channelbag(action, arm)
    curve = bag.fcurves.new(data_path=path_for("vis", "lamp"), index=0)
    curve.keyframe_points.add(8)
    for index in range(8):
        curve.keyframe_points[index].co = (
            index + 1,
            0.5 + 0.5 * math.sin(index / 7.0 * math.tau),
        )
    curve.update()
    return arm


@example("12_node_scale")
def build_node_scale():
    """Node scale animation, on the bones' own scale channels."""
    arm = A.armature("Pump", bones=(("root", None), ("core", "root")))
    mat = textured_material("pump", checker)
    verts, faces = A.cube_geometry(0.4)
    A.mesh_object("core2", arm, bone="core", verts=verts, faces=faces, material=mat)

    action = bpy.data.actions.new("Throb")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.2
    action["dts_cyclic"] = True
    action.dts_sequence_props.scale_mode = "UNIFORM"
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["core"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 9):
        factor = 1.0 + 0.4 * math.sin((frame - 1) / 7.0 * math.tau)
        bone.scale = (factor, factor, factor)
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("scale", frame=frame)
        bone.keyframe_insert("rotation_quaternion", frame=frame)
    return arm


@example("13_decals")
def build_decals():
    """Battle damage: decals switched on as a Damage sequence advances, which
    is what 47 of the 49 decal-bearing Tribes 2 shapes do.

    The plate is subdivided so a decal can cover *part* of it.  The face
    selection below only sizes the projector: the decal is the empty
    create_decal leaves behind, and which faces it covers is recomputed from
    that empty on export, so moving or scaling it moves the burn.
    """
    arm = A.armature("Hull", bones=(("root", None), ("shell", "root")))
    hull_mat = textured_material("hullplate", checker)

    # a 6x6 grid, so a decal has faces to pick out
    verts, faces = [], []
    n = 6
    for row in range(n + 1):
        for col in range(n + 1):
            verts.append(((col / n - 0.5) * 2.4, (row / n - 0.5) * 2.4, 0.0))
    for row in range(n):
        for col in range(n):
            a = row * (n + 1) + col
            b, c, d = a + 1, a + n + 2, a + n + 1
            faces += [(a, b, c), (a, c, d)]
    target = A.mesh_object("hull2", arm, bone="shell", verts=verts, faces=faces,
                           material=hull_mat)

    scorch_mat = textured_material("scorchmark", scorch)
    # two burns over different corners of the plate
    patches = (
        [(r * n + c) * 2 + t for r in (1, 2) for c in (1, 2) for t in (0, 1)],
        [(r * n + c) * 2 + t for r in (3, 4) for c in (3, 4) for t in (0, 1)],
    )
    for index, patch in enumerate(patches):
        for polygon in target.data.polygons:
            polygon.select = polygon.index in patch
        create_decal(arm, target, name=f"burn{index}", material=scorch_mat,
                     index=index, all_details=False)

    action = bpy.data.actions.new("Damage")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["shell"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 12):
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)

    from io_scene_dts.mapping.decals import decal_path, decal_prop

    bag = A.channelbag(action, arm)
    # each burn appears further into the damage ramp, the shipped pattern
    for decal_index, first_on in ((0, 3), (1, 7)):
        name = f"burn{decal_index}"
        arm[decal_prop(decal_index, name)] = -1.0
        curve = bag.fcurves.new(data_path=decal_path(decal_index, name), index=0)
        curve.keyframe_points.add(11)
        for kf in range(11):
            point = curve.keyframe_points[kf]
            point.co = (kf + 1, 0.0 if kf >= first_on else -1.0)
            point.interpolation = "CONSTANT"
        curve.update()
    return arm


@example("14_ifl_material")
def build_ifl_material():
    """An IFL entry: the engine flips the material's texture through a list."""
    arm = A.armature("Screen", bones=(("root", None), ("face", "root")))
    mat = textured_material("screenframe", stripes)
    verts, faces = A.quad_geometry()
    A.mesh_object("screen2", arm, bone="face", verts=verts, faces=faces, material=mat)

    entry = arm.dts_shape.ifl_materials.add()
    entry.name = "screenframe.ifl"
    entry.material_slot = 0
    entry.num_frames = 4

    action = bpy.data.actions.new("Play")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.0
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["face"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 5):
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)
    action.dts_sequence_props.ifl_matters.add().index = 0
    return arm


@example("15_dsq_animation")
def build_dsq_animation():
    """A shape whose animation ships separately, as .dsq."""
    arm = A.armature("Arm", bones=(("root", None), ("joint", "root")))
    mat = textured_material("armplate", checker)
    verts, faces = A.cube_geometry(0.35)
    A.mesh_object("arm2", arm, bone="joint", verts=verts, faces=faces, material=mat)

    action = bpy.data.actions.new("Swing")
    action.use_fake_user = True
    action["dts_sequence"] = True
    action["dts_duration"] = 1.5
    action["dts_cyclic"] = True
    arm.animation_data_create()
    arm.animation_data.action = action
    bone = arm.pose.bones["joint"]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, 9):
        angle = math.sin((frame - 1) / 7.0 * math.tau) * 0.8
        bone.rotation_quaternion = (math.cos(angle / 2), math.sin(angle / 2), 0.0, 0.0)
        bone.keyframe_insert("rotation_quaternion", frame=frame)
    return arm


# ----------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------


UNWRAP_EXEMPT = {"08_material_frames"}


def build(name, out_dir: Path, export_dir: Path | None):
    TEXTURES.clear()
    A.reset()
    arm = EXAMPLES[name]()
    if name not in UNWRAP_EXEMPT:
        for obj in bpy.data.objects:
            if obj.type != "MESH" or not obj.data.uv_layers:
                continue
            box_unwrap(obj)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)

    blend = out_dir / f"{name}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))

    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)
        dts = export_dir / f"{name}.dts"
        bpy.ops.io_scene_dts.export_dts(filepath=str(dts), version="24")
        if name == "15_dsq_animation":
            bpy.ops.io_scene_dts.export_dsq(filepath=str(export_dir / f"{name}.dsq"))
        for texture_name, rgba_fn in TEXTURES.items():
            image = make_texture(texture_name, rgba_fn)
            image.filepath_raw = str(export_dir / f"{texture_name}.png")
            image.file_format = "PNG"
            image.save()
    return blend


SHOWCASE_CS = REPO / "examples/mod/DtsExamples/scripts/dtsShowcase.cs"
LIFT_LINE = "$DtsShowcaseLift["


def update_lifts(shapes_dir: Path, script: Path = SHOWCASE_CS) -> int:
    """Rewrite the ``$DtsShowcaseLift`` table from the exported shapes.

    The lift is how far a shape's lowest point sits below its origin, which is
    what the showcase adds to the terrain height to set the shape down on the
    ground instead of half into it.  Nothing in the console reads a shape's
    bounding box, so the table has to be baked here.
    """
    from io_scene_dts.dtslib.reader import read_shape_file

    shapes = sorted(shapes_dir.glob("*.dts"))
    if not shapes:
        raise SystemExit(f"no .dts files in {shapes_dir}")

    table = []
    for i, path in enumerate(shapes):
        lift = -read_shape_file(path).bounds[2]
        name = f"$DtsShowcaseLift[{i}]"
        # `+ 0.0` so a shape whose base is exactly at the origin reads 0.00
        # rather than -0.00
        table.append(f"{name:<21}= {lift + 0.0:.2f};".ljust(32) + f"// {path.stem}")

    lines = script.read_text().splitlines(keepends=True)
    first = next(i for i, ln in enumerate(lines) if ln.startswith(LIFT_LINE))
    last = max(i for i, ln in enumerate(lines) if ln.startswith(LIFT_LINE))
    lines[first : last + 1] = [ln + "\n" for ln in table]
    script.write_text("".join(lines))
    return len(table)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO / "examples"))
    parser.add_argument("--export", default=None, help="also write .dts and textures here")
    parser.add_argument(
        "--lifts",
        action="store_true",
        help="rewrite the showcase script's lift table from the exported .dts files",
    )
    parser.add_argument("names", nargs="*", help="examples to build (default: all)")
    args = parser.parse_args(argv)
    if args.lifts and not args.export:
        parser.error("--lifts needs --export: the table is read off the exported .dts")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    export_dir = Path(args.export) if args.export else None

    names = args.names or list(EXAMPLES)
    for name in names:
        if name not in EXAMPLES:
            print(f"unknown example {name!r}")
            return 2
        path = build(name, out_dir, export_dir)
        print(f"built {path.name}")
    print(f"\n{len(names)} example(s)")
    if args.lifts:
        print(f"lift table: {update_lifts(export_dir)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
