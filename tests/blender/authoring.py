"""Building DTS shapes from nothing, for tests/blender/test_authoring.py.

Everything here constructs Blender data the way a user would: an armature, mesh
objects hung off its bones, materials with shader graphs.  Nothing reads a
`.dts`, and nothing sets a property that only the importer would know to set --
if a helper here needs one, that is a finding about the add-on, not a detail of
the harness.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

REPO = Path(__file__).resolve().parents[2]


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def tmp(suffix=".dts"):
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.close()
    return handle.name


def armature(name="Shape", bones=(("root", None),)):
    """An armature with the named bones.

    ``bones`` is a sequence of (name, parent name), in an order where a parent
    always precedes its children.
    """
    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj

    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for index, (bone_name, parent) in enumerate(bones):
        bone = arm_data.edit_bones.new(bone_name)
        bone.head = Vector((0.0, 0.0, index * 0.5))
        bone.tail = Vector((0.0, 0.0, index * 0.5 + 0.25))
        if parent is not None:
            bone.parent = made[parent]
        made[bone_name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def mesh_object(name, arm_obj, *, bone=None, verts=None, faces=None, material=None):
    """A mesh parented to a bone of ``arm_obj``, named for the DTS convention.

    The name carries the detail size ("body2"), which is how a user says which
    level a mesh belongs to -- there is no property to set.
    """
    if verts is None or faces is None:
        verts, faces = cube_geometry()
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([Vector(v) for v in verts], [], faces)
    mesh.update()
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = arm_obj
    if bone is not None:
        obj.parent_type = "BONE"
        obj.parent_bone = bone
    if material is not None:
        mesh.materials.append(material)
    return obj


def cube_geometry(size=0.5):
    """A cube wound outward.

    Worth being careful about: DTS is single-sided and back-face culled with no
    flag to change it (tsMesh.cc:625 hardcodes glEnable(GL_CULL_FACE) and
    glFrontFace(GL_CW)), so a cube wound inward exports without complaint and
    is invisible in the engine.  An earlier version of this helper had every
    face inward, which no test noticed.
    """
    s = size
    verts = [
        (-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s),
        (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s),
    ]
    faces = [
        (0, 3, 2), (0, 2, 1),   # -Z
        (4, 5, 6), (4, 6, 7),   # +Z
        (0, 1, 5), (0, 5, 4),   # -Y
        (1, 2, 6), (1, 6, 5),   # +X
        (2, 3, 7), (2, 7, 6),   # +Y
        (3, 0, 4), (3, 4, 7),   # -X
    ]
    return verts, faces


def quad_geometry(z=0.0, size=0.5):
    """One flat quad -- what a foliage card or a decal target looks like."""
    s = size
    return ([(-s, -s, z), (s, -s, z), (s, s, z), (-s, s, z)], [(0, 1, 2), (0, 2, 3)])


def upright_quad_geometry(y=0.0, size=0.5):
    """One quad standing in the XZ plane, the way shipped billboards are built.

    This matches the shipped art rather than a derived rule: every billboard
    mesh in the Tribes 2 corpus spans x and z with y flat -- all four in
    ``grenade_flare.dts`` are x -2.9..2.9, y 0, z -2.9..2.9 -- and none is
    built in XY.  A card built in XY exports with correct flags and draws in
    Tribes 2 as a smear on the ground.

    Matching the convention is *not* on its own sufficient to get a
    camera-facing card out of Tribes 2; see the billboard note in
    UNSUPPORTED.md for what is and is not established.
    """
    s = size
    return ([(-s, y, -s), (s, y, -s), (s, y, s), (-s, y, s)], [(0, 1, 2), (0, 2, 3)])


def cards_geometry(count=8):
    """Quads at varied angles: geometry a BSP can actually split."""
    verts, faces = [], []
    for i in range(count):
        angle = i * (math.pi / count) * 2.7
        dx, dy = math.cos(angle), math.sin(angle)
        cx, cy = (i % 3) * 1.5, (i // 3) * 1.5
        base = len(verts)
        for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            verts.append((cx + dx * sx, cy + dy * sx, sz * 0.5))
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return verts, faces


def principled_material(name, *, colour=(0.8, 0.2, 0.2, 1.0)):
    """A plain Principled material, as `Add Material` would give you."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = colour
    return mat


def export_dts(path=None, **kwargs):
    path = path or tmp(".dts")
    kwargs.setdefault("version", "24")
    result = bpy.ops.io_scene_dts.export_dts(filepath=path, **kwargs)
    assert result == {"FINISHED"}, result
    return path


def export_dsq(path=None, **kwargs):
    path = path or tmp(".dsq")
    result = bpy.ops.io_scene_dts.export_dsq(filepath=path, **kwargs)
    assert result == {"FINISHED"}, result
    return path


def read(path):
    from io_scene_dts.dtslib import read_shape_file

    return read_shape_file(path)


def read_dsq_file(path):
    from io_scene_dts.dtslib import read_dsq

    return read_dsq(Path(path).read_bytes())


def action_for(arm_obj, name, frames=4):
    """An action assigned to the armature, with a bone actually animated.

    The sequence exporter needs at least one channel it can evaluate, and the
    number of keys is the sequence's length.
    """
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action
    bone = arm_obj.pose.bones[0]
    bone.rotation_mode = "QUATERNION"
    for frame in range(1, frames + 1):
        angle = (frame - 1) * 0.2
        bone.rotation_quaternion = (math.cos(angle / 2), 0.0, 0.0, math.sin(angle / 2))
        bone.keyframe_insert("rotation_quaternion", frame=frame)
    return action


def channelbag(action, arm_obj):
    from io_scene_dts.mapping.sequences import _action_channelbag

    bag, _slot = _action_channelbag(action, arm_obj)
    return bag


def object_names(shape):
    return [shape.name(o.name_index) for o in shape.objects]


def live_meshes(shape):
    return [m for m in shape.meshes if m is not None]
