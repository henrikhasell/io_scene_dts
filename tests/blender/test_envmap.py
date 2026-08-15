"""Does the node group compute the sphere map the engine computes?

Everything else about reflection can be checked by reading a file back.  This
cannot: the answer is a shader, and a shader is only right if what it *renders*
is right.  So this renders one, and compares it against a transcription of the
engine's own arithmetic.

The trick that makes the comparison exact is the environment image.  It encodes
its own coordinates -- red is u, green is v -- so a rendered pixel reports the
sphere-map coordinate the shader chose for it, and the reference implementation
in :func:`sphere_map_uv` (a line-for-line port of
``engine/platform/platformGLES.cc:1099-1135``) says what that coordinate should
have been.  No lighting, no tone map, no tolerance for "looks about right".

Cycles rather than EEVEE: it renders on the CPU, so this is the same test on a
machine with no GPU, and the surface is a pure emitter so one sample is exact.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO.parent))
sys.path.insert(0, str(REPO / "tests" / "blender"))

import authoring as A  # noqa: E402
from io_scene_dts.mapping import envmap  # noqa: E402

SIZE = 64
ENV_SIZE = 64
RADIUS = 1.0
CENTRE = (0.0, 0.0, 0.0)

# How far off a rendered coordinate may be.  Two things put it above zero: the
# sphere is a mesh, so its interpolated normals are not quite a sphere's, and
# the environment image is sampled with bilinear filtering.  Both shrink with
# resolution; neither is the shader disagreeing about the formula.
TOLERANCE = 0.02


# ----------------------------------------------------------------------
# the reference: platformGLES.cc:1099-1135, in Python
# ----------------------------------------------------------------------


def sphere_map_uv(eye_position, eye_normal):
    """``GL_SPHERE_MAP`` texgen, given eye-space position and normal."""
    px, py, pz = eye_position
    nx, ny, nz = eye_normal
    nl = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nl > 0.0:
        nx, ny, nz = nx / nl, ny / nl, nz / nl

    ul = math.sqrt(px * px + py * py + pz * pz)
    ux, uy, uz = (px / ul, py / ul, pz / ul) if ul > 0.0 else (px, py, pz)

    dot = 2.0 * (nx * ux + ny * uy + nz * uz)
    rx, ry, rz = ux - nx * dot, uy - ny * dot, uz - nz * dot

    m = 2.0 * math.sqrt(rx * rx + ry * ry + (rz + 1.0) * (rz + 1.0))
    if m <= 0.0001:
        return 0.5, 0.5
    return rx / m + 0.5, ry / m + 0.5


def _ray_hits_sphere(direction, centre, radius):
    """Camera-space ray from the origin; returns the near hit point or None."""
    dx, dy, dz = direction
    cx, cy, cz = centre
    along = dx * cx + dy * cy + dz * cz
    disc = along * along - (cx * cx + cy * cy + cz * cz - radius * radius)
    if disc < 0.0:
        return None
    t = along - math.sqrt(disc)
    if t <= 0.0:
        return None
    return (dx * t, dy * t, dz * t)


# ----------------------------------------------------------------------
# the scene
# ----------------------------------------------------------------------


def _coordinate_image():
    """An environment map whose texels are their own coordinates."""
    image = bpy.data.images.new(
        "env_coords", width=ENV_SIZE, height=ENV_SIZE, alpha=False, float_buffer=True
    )
    image.colorspace_settings.name = "Non-Color"
    pixels = []
    for row in range(ENV_SIZE):
        v = (row + 0.5) / ENV_SIZE
        for col in range(ENV_SIZE):
            u = (col + 0.5) / ENV_SIZE
            pixels.extend((u, v, 0.0, 1.0))
    image.pixels = pixels
    image.update()
    return image


def _reflective_sphere():
    """A sphere that is nothing but environment map: mask and amount both 1."""
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=192, ring_count=96, radius=RADIUS, location=CENTRE
    )
    sphere = bpy.context.object
    bpy.ops.object.shade_smooth()

    mat = A.principled_material("mirror")
    sphere.data.materials.append(mat)

    full = mat.node_tree.nodes.new("ShaderNodeValue")
    full.outputs[0].default_value = 1.0
    full.location = (-700, -400)
    assert envmap.wire(mat, full.outputs[0]), "the material has no Principled to wire to"
    return sphere


def _camera():
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.lens_unit = "FOV"
    camera_data.angle = math.radians(50.0)
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    # off-axis on purpose: with the sphere centred the eye vector is the view
    # direction everywhere on it, and the position half of the formula --
    # `u = normalize(eye_position)` -- would not be under test at all
    camera.location = (2.2, -4.5, 1.4)
    # aimed off-centre too, so the sphere does not sit at the middle of frame
    # where a sign error in the eye vector would cancel out symmetrically
    target = Vector((0.15, 0.0, -0.1))
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    bpy.context.scene.camera = camera
    bpy.context.view_layer.update()
    return camera


def _render_to_exr():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_x = SIZE
    scene.render.resolution_y = SIZE
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    path = A.tmp(".exr")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    rendered = bpy.data.images.load(path)
    pixels = list(rendered.pixels)
    bpy.data.images.remove(rendered)
    return pixels


# ----------------------------------------------------------------------


def test_the_group_computes_the_engines_sphere_map():
    A.reset()
    image = _coordinate_image()
    _reflective_sphere()
    camera = _camera()

    scene = bpy.context.scene
    scene.dts_scene.env_map_image = image
    scene.dts_scene.env_map_strength = 1.0

    pixels = _render_to_exr()

    # everything below stays in camera space, which is the space the engine
    # calls eye space and the space the group's Vector Transform nodes reach
    view = camera.matrix_world.inverted()
    centre = tuple(view @ Vector(CENTRE))
    tan_half = math.tan(camera.data.angle / 2.0)

    checked = 0
    worst = 0.0
    for row in range(SIZE):
        for col in range(SIZE):
            offset = (row * SIZE + col) * 4
            alpha = pixels[offset + 3]
            if alpha < 0.999:
                continue  # background, or a pixel the sphere only partly covers

            x = ((col + 0.5) / SIZE * 2.0 - 1.0) * tan_half
            y = ((row + 0.5) / SIZE * 2.0 - 1.0) * tan_half
            length = math.sqrt(x * x + y * y + 1.0)
            direction = (x / length, y / length, -1.0 / length)

            hit = _ray_hits_sphere(direction, centre, RADIUS)
            if hit is None:
                continue
            normal = (
                hit[0] - centre[0],
                hit[1] - centre[1],
                hit[2] - centre[2],
            )
            # grazing angles: the mesh's interpolated normal and the analytic
            # one diverge fastest at the silhouette, and so does the sphere map
            facing = -(direction[0] * normal[0] + direction[1] * normal[1]
                       + direction[2] * normal[2]) / RADIUS
            if facing < 0.35:
                continue

            want_u, want_v = sphere_map_uv(hit, normal)
            # the border of the image is where EXTEND stops agreeing with a
            # coordinate ramp, so stay a couple of texels inside it
            margin = 2.0 / ENV_SIZE
            if not (margin < want_u < 1.0 - margin and margin < want_v < 1.0 - margin):
                continue

            got_u, got_v = pixels[offset], pixels[offset + 1]
            worst = max(worst, abs(got_u - want_u), abs(got_v - want_v))
            checked += 1

    assert checked > 400, f"only {checked} pixels were comparable; the render is wrong"
    assert worst < TOLERANCE, (
        f"the shader's sphere map is off by {worst:.4f} over {checked} pixels "
        f"(tolerance {TOLERANCE})"
    )


def test_a_group_made_later_catches_up_with_the_scene():
    """Choosing the sky first and importing second has to work too.

    The scene property is set before any material needs a group, so the update
    that pushes it into the group has nothing to push into.  Whatever builds the
    group afterwards -- an import, or the Add Reflectance button -- has to read
    the scene rather than start blank, or the shape arrives not reflecting the
    map that is plainly already chosen.
    """
    A.reset()
    scene = bpy.context.scene
    assert envmap.existing_group() is None, "a fresh file has no group yet"

    image = _coordinate_image()
    scene.dts_scene.env_map_image = image
    scene.dts_scene.env_map_strength = 0.5

    _reflective_sphere()  # the first thing to need a group
    group = envmap.existing_group()
    assert group is not None
    assert group.nodes[envmap.IMAGE_NODE].image == image
    assert abs(group.nodes[envmap.STRENGTH_NODE].outputs[0].default_value - 0.5) < 1e-6


def test_no_environment_image_means_no_reflection():
    """The unset case has to render as though none of this existed.

    An Image Texture with no image samples black, so a group left switched on
    with nothing chosen would turn every reflective material black rather than
    leaving it alone.  The strength value carries that case.
    """
    A.reset()
    _reflective_sphere()
    _camera()

    scene = bpy.context.scene
    scene.dts_scene.env_map_image = None

    group = envmap.existing_group()
    assert group.nodes[envmap.STRENGTH_NODE].outputs[0].default_value == 0.0

    scene.dts_scene.env_map_image = _coordinate_image()
    assert group.nodes[envmap.STRENGTH_NODE].outputs[0].default_value == 1.0
