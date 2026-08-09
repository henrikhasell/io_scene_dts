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


def tmp_in_own_dir(name="out.dts"):
    """A path in a directory of its own.

    ``tmp`` shares one directory with every other test, so a test that asks
    *which* files export wrote has to have somewhere empty to write them.
    """
    return str(Path(tempfile.mkdtemp()) / name)


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


def blended_material(name, *, colour=(0.8, 0.2, 0.2, 1.0)):
    """A Principled material set to Blended -- MAT_TRANSLUCENT on export.

    Export reads the blend mode off the shader rather than a stored prop, so
    this is the whole of it.  Decal targets use it because a shape that carries
    decals has to have something translucent in it for the engine to draw them
    against; see `dtslib/translucency.py`.
    """
    mat = principled_material(name, colour=colour)
    mat.surface_render_method = "BLENDED"
    return mat


def generated_image(name, *, size=4, colour=(0.25, 0.5, 0.75), ramp=False):
    """An image made in Blender, with no file behind it -- what a user has.

    ``ramp`` varies the value across the image, which matters for a reflectance
    mask: a mask whose values never change carries nothing, and the add-on
    declines to split one out of a texture's alpha.
    """
    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    pixels = []
    for i in range(size * size):
        value = i / (size * size - 1) if ramp else colour[0]
        pixels.extend((value, value, value, 1.0) if ramp else (*colour, 1.0))
    image.pixels = pixels
    image.update()
    image.pack()
    return image


def image_material(name, *, diffuse=None, reflectance=None, packing=None):
    """A Principled material with images on Base Color and Metallic.

    Metallic is where the add-on reads a reflectance map from, so this is how a
    user gives a material one -- there is no property to set, and that is the
    point: the shader is the only place it can live.

    ``packing`` is the per-material override; left None the material follows the
    export dialog, which is what a material a user made looks like.
    """
    mat = principled_material(name)
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    for image, socket, y in ((diffuse, "Base Color", 300), (reflectance, "Metallic", -80)):
        if image is None:
            continue
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.location = (-350, y)
        nt.links.new(node.outputs["Color"], bsdf.inputs[socket])
    if packing is not None:
        mat.dts_material.reflectance_packing = packing
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
