"""The engine's environment map, rebuilt out of shader nodes.

A DTS reflectance map is not a PBR parameter.  The engine puts one 2D image on
a second texture unit, generates coordinates for it with ``GL_SPHERE_MAP``
texgen, and interpolates between the lit surface and that texel by a per-texel
mask -- ``engine/ts/tsMesh.cc:472-484`` in the Torque tree, whose ``INTERPOLATE``
combine is ``env*k + lit*(1-k)``.  Three things follow, and each is why the
Principled BSDF's Metallic input could never show it:

* the reflection *displaces* the diffuse rather than adding to it,
* the env texel arrives **unlit** -- no lighting reaches ``Arg0``,
* the image is fixed, not the scene's own surroundings.

``k`` is built at ``tsMesh.cc:1003``: the mask times ``reflection_amount``
(per material, in the file) times the shape's environment-map alpha, which is
1.0 for every player and static the game renders.

So the Blender side is a ``Mix Shader`` between the Principled and an
``Emission`` of the sphere-mapped texel, which is the same construction
``materials._build_add_shader`` already uses for additive blending -- a mix
against unlit emission is what "blend against something the lights do not
touch" looks like in EEVEE either way.

**The environment image is not in the .dts.**  The engine takes it from the
mission's sky -- entry 6 of the sky's ``.dml`` (``engine/terrain/sky.h:227``),
``day_0007.jpg`` in the stock skies -- so it is a property of where the shape is
standing, not of the shape.  It is scene state here for that reason, it is never
written on export, and per CLAUDE.md it is never imported either: a shape that
has never been in a mission has no answer to give.

Everything the group needs that is *not* per material lives in the group's own
nodes.  A node group's interior is shared by every material that uses it, so
setting the image once retextures the whole shape with no handler, no per
material bookkeeping, and no second copy to fall out of step.
"""

from __future__ import annotations

import bpy

GROUP_NAME = "DTS Environment Map"

# Every node this module puts in a *material* carries this label, so the wiring
# can be found again and unpicked.  mapping/decals.py labels its branches for
# the same reason, and the two must not collide: a decal branch is looked up by
# its own per-index label, never by node type alone.
NODE_LABEL = "dts_envmap"

# Interior nodes, by name.  Names rather than labels because these are looked up
# one at a time and there is exactly one of each.
IMAGE_NODE = "Environment Map"
STRENGTH_NODE = "Strength"

_EPSILON = 1e-4  # tsMesh's own `mm > 0.0001f` guard


# ----------------------------------------------------------------------
# the group
# ----------------------------------------------------------------------


def _new(nt, kind, name, location):
    node = nt.nodes.new(kind)
    node.name = name
    node.label = name
    node.location = location
    return node


def _math(nt, name, operation, location, value=None):
    node = _new(nt, "ShaderNodeMath", name, location)
    node.operation = operation
    if value is not None:
        node.inputs[1].default_value = value
    return node


def _build_group():
    """The sphere map, node for node.

    ``engine/platform/platformGLES.cc:1099-1135`` is the engine's own software
    emulation of the texgen it asks the driver for, and it spells out what
    ``GL_SPHERE_MAP`` means::

        u = normalize(eye_position)          # eye -> surface
        r = u - n * (2 * dot(n, u))          # reflect about the eye-space normal
        m = 2 * sqrt(rx^2 + ry^2 + (rz+1)^2)
        s = rx/m + 0.5
        t = ry/m + 0.5

    ``Geometry.Incoming`` points from the surface toward the viewer, which is
    -u, hence the scale by -1.

    The two ``Vector Transform`` nodes do not finish the job.  Blender's *camera
    object* looks down -Z, so it is tempting to assume the shading language's
    camera space does too, and it does not: measured against a ray-traced
    reference, both Cycles and EEVEE hand back a space with **+Z forward**.  GL
    eye space is -Z forward, so Z is negated on the way out of each transform --
    without it the reflection is wrong by up to half the map (the check that
    says so is ``tests/blender/test_envmap.py``).
    """
    nt = bpy.data.node_groups.new(GROUP_NAME, "ShaderNodeTree")

    nt.interface.new_socket("Mask", in_out="INPUT", socket_type="NodeSocketFloat")
    nt.interface.new_socket("Amount", in_out="INPUT", socket_type="NodeSocketFloat")
    nt.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    nt.interface.new_socket("Factor", in_out="OUTPUT", socket_type="NodeSocketFloat")
    nt.interface.items_tree["Mask"].default_value = 0.0
    nt.interface.items_tree["Amount"].default_value = 1.0

    group_in = _new(nt, "NodeGroupInput", "Group Input", (-1600, -400))
    group_out = _new(nt, "NodeGroupOutput", "Group Output", (600, -100))

    geometry = _new(nt, "ShaderNodeNewGeometry", "Geometry", (-1600, 200))

    # u = -Incoming, taken to camera space.  VECTOR and NORMAL are different
    # transforms (the normal takes the inverse transpose) and the engine draws
    # the same distinction at platformGLES.cc:1112.
    negate = _new(nt, "ShaderNodeVectorMath", "Negate", (-1400, 120))
    negate.operation = "SCALE"
    negate.inputs["Scale"].default_value = -1.0
    nt.links.new(geometry.outputs["Incoming"], negate.inputs[0])

    eye = _new(nt, "ShaderNodeVectorTransform", "Eye Vector", (-1200, 120))
    eye.vector_type = "VECTOR"
    eye.convert_from = "WORLD"
    eye.convert_to = "CAMERA"
    nt.links.new(negate.outputs[0], eye.inputs[0])

    normal = _new(nt, "ShaderNodeVectorTransform", "Eye Normal", (-1200, -80))
    normal.vector_type = "NORMAL"
    normal.convert_from = "WORLD"
    normal.convert_to = "CAMERA"
    nt.links.new(geometry.outputs["Normal"], normal.inputs[0])

    # +Z forward to -Z forward.  See the docstring: this is the difference
    # between Blender's camera space and GL's eye space, and it is measured
    # rather than assumed.
    eye_gl = _new(nt, "ShaderNodeVectorMath", "Eye Vector GL", (-1020, 120))
    eye_gl.operation = "MULTIPLY"
    eye_gl.inputs[1].default_value = (1.0, 1.0, -1.0)
    nt.links.new(eye.outputs[0], eye_gl.inputs[0])

    normal_gl = _new(nt, "ShaderNodeVectorMath", "Eye Normal GL", (-1020, -80))
    normal_gl.operation = "MULTIPLY"
    normal_gl.inputs[1].default_value = (1.0, 1.0, -1.0)
    nt.links.new(normal.outputs[0], normal_gl.inputs[0])

    # r = u - n*(2*dot(n,u)), which is exactly what Vector Math's Reflect is
    reflect = _new(nt, "ShaderNodeVectorMath", "Reflect", (-840, 20))
    reflect.operation = "REFLECT"
    nt.links.new(eye_gl.outputs[0], reflect.inputs[0])
    nt.links.new(normal_gl.outputs[0], reflect.inputs[1])

    axes = _new(nt, "ShaderNodeSeparateXYZ", "Reflection Axes", (-660, 20))
    nt.links.new(reflect.outputs[0], axes.inputs[0])

    z_plus = _math(nt, "Z Plus One", "ADD", (-640, -180), 1.0)
    nt.links.new(axes.outputs["Z"], z_plus.inputs[0])

    x_sq = _math(nt, "X Squared", "MULTIPLY", (-640, 180))
    nt.links.new(axes.outputs["X"], x_sq.inputs[0])
    nt.links.new(axes.outputs["X"], x_sq.inputs[1])

    y_sq = _math(nt, "Y Squared", "MULTIPLY", (-640, 0))
    nt.links.new(axes.outputs["Y"], y_sq.inputs[0])
    nt.links.new(axes.outputs["Y"], y_sq.inputs[1])

    z_sq = _math(nt, "Z Squared", "MULTIPLY", (-460, -180))
    nt.links.new(z_plus.outputs[0], z_sq.inputs[0])
    nt.links.new(z_plus.outputs[0], z_sq.inputs[1])

    sum_xy = _math(nt, "Sum XY", "ADD", (-460, 100))
    nt.links.new(x_sq.outputs[0], sum_xy.inputs[0])
    nt.links.new(y_sq.outputs[0], sum_xy.inputs[1])

    total = _math(nt, "Sum", "ADD", (-280, 0))
    nt.links.new(sum_xy.outputs[0], total.inputs[0])
    nt.links.new(z_sq.outputs[0], total.inputs[1])

    root = _math(nt, "Root", "SQRT", (-100, 0))
    nt.links.new(total.outputs[0], root.inputs[0])

    m = _math(nt, "M", "MULTIPLY", (60, 0), 2.0)
    nt.links.new(root.outputs[0], m.inputs[0])

    # the engine falls back to the centre of the map when m underflows; a
    # divide by the clamped m is the same answer without a branch, because
    # rx and ry have both gone to zero by the time m has
    safe = _math(nt, "M Guarded", "MAXIMUM", (220, 0), _EPSILON)
    nt.links.new(m.outputs[0], safe.inputs[0])

    s_div = _math(nt, "S Divide", "DIVIDE", (380, 140))
    nt.links.new(axes.outputs["X"], s_div.inputs[0])
    nt.links.new(safe.outputs[0], s_div.inputs[1])
    s = _math(nt, "S", "ADD", (540, 140), 0.5)
    nt.links.new(s_div.outputs[0], s.inputs[0])

    t_div = _math(nt, "T Divide", "DIVIDE", (380, -40))
    nt.links.new(axes.outputs["Y"], t_div.inputs[0])
    nt.links.new(safe.outputs[0], t_div.inputs[1])
    t = _math(nt, "T", "ADD", (540, -40), 0.5)
    nt.links.new(t_div.outputs[0], t.inputs[0])

    uv = _new(nt, "ShaderNodeCombineXYZ", "Sphere Map UV", (700, 60))
    nt.links.new(s.outputs[0], uv.inputs["X"])
    nt.links.new(t.outputs[0], uv.inputs["Y"])

    image = _new(nt, "ShaderNodeTexImage", IMAGE_NODE, (880, 60))
    # A sphere map is the whole visible environment in one disc; there is
    # nothing outside it to tile, and GL clamps it.
    image.extension = "EXTEND"
    nt.links.new(uv.outputs[0], image.inputs["Vector"])

    # k = mask * reflection_amount * strength, and strength carries the "no
    # image chosen" case as a zero so an unset scene renders exactly as it did
    # before this module existed rather than reflecting a black texture.
    strength = _new(nt, "ShaderNodeValue", STRENGTH_NODE, (-1600, -560))
    strength.outputs[0].default_value = 0.0

    masked = _math(nt, "Masked", "MULTIPLY", (-1200, -440))
    nt.links.new(group_in.outputs["Mask"], masked.inputs[0])
    nt.links.new(group_in.outputs["Amount"], masked.inputs[1])

    factor = _math(nt, "Factor", "MULTIPLY", (-1000, -440))
    nt.links.new(masked.outputs[0], factor.inputs[0])
    nt.links.new(strength.outputs[0], factor.inputs[1])

    clamped = _math(nt, "Factor Clamped", "MINIMUM", (-820, -440), 1.0)
    nt.links.new(factor.outputs[0], clamped.inputs[0])

    nt.links.new(image.outputs["Color"], group_out.inputs["Color"])
    nt.links.new(clamped.outputs[0], group_out.inputs["Factor"])
    # Node group layout has the output on the right; the two feeders above are
    # placed for legibility rather than to be read back.
    group_out.location = (1100, 0)
    return nt


def ensure_group():
    """The one shared group, made on first use.

    A group made now has to be caught up with the scene immediately: importing
    a second shape into a scene that already has an environment map chosen
    builds the group fresh, and without this it would sit at "no image, no
    strength" until someone re-picked the image it already had.
    """
    group = existing_group()
    if group is not None:
        return group
    group = _build_group()
    apply_settings(bpy.context.scene)
    return group


def existing_group():
    """The group if this .blend has one, without making it.

    Checked by type as well as name: ``bpy.data.node_groups`` is one namespace
    for shader, geometry and compositor trees, so a Geometry Nodes group a user
    happened to call this would otherwise be handed back and wired into a
    material.
    """
    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is not None and group.bl_idname != "ShaderNodeTree":
        return None
    return group


# ----------------------------------------------------------------------
# the scene's half: which image, and how much of it
# ----------------------------------------------------------------------


def apply_settings(scene) -> None:
    """Push the scene's environment map into the shared group.

    One direction only.  The scene properties are the source of truth and the
    group's interior mirrors them; nothing reads the mirror back.
    """
    group = existing_group()
    if group is None:
        return
    props = getattr(scene, "dts_scene", None)
    image = getattr(props, "env_map_image", None) if props else None
    strength = float(getattr(props, "env_map_strength", 1.0)) if props else 1.0

    node = group.nodes.get(IMAGE_NODE)
    if node is not None:
        node.image = image
    value = group.nodes.get(STRENGTH_NODE)
    if value is not None:
        # no image is not "reflect black", it is "do not reflect"
        value.outputs[0].default_value = strength if image is not None else 0.0


# ----------------------------------------------------------------------
# the material's half
# ----------------------------------------------------------------------


def group_node(bmat):
    """The env-map group node on this material, or None."""
    nt = getattr(bmat, "node_tree", None)
    if nt is None:
        return None
    return next(
        (
            n
            for n in nt.nodes
            if n.type == "GROUP" and n.node_tree is not None
            and n.node_tree.name == GROUP_NAME
        ),
        None,
    )


def mask_socket(bmat):
    """Whatever feeds the group's Mask input, or None."""
    node = group_node(bmat)
    if node is None:
        return None
    socket = node.inputs["Mask"]
    return socket.links[0].from_socket if socket.is_linked else None


def wire(bmat, mask_source) -> bool:
    """Insert the reflection between the Principled and whatever it feeds.

    ``mask_source`` is a socket, not a node: an env-mapped translucent material
    takes its mask from the *alpha* of the diffuse node that also feeds Base
    Color, and a separate reflectance texture takes the colour of a node of its
    own.  Both are one socket to this function.

    Inserted at the Principled's output rather than at the Material Output, so
    it composes with the decal chain in the order the engine draws: a decal is
    separate geometry over the top, and reflection belongs under it.  That holds
    whichever of the two is wired first.
    """
    nt = getattr(bmat, "node_tree", None)
    if nt is None:
        return False
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False
    if group_node(bmat) is not None:
        unwire(bmat)

    group = ensure_group()
    x, y = bsdf.location.x, bsdf.location.y

    node = nt.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    node.label = NODE_LABEL
    node.location = (x + 40, y - 420)
    node.width = 200
    nt.links.new(mask_source, node.inputs["Mask"])

    emission = nt.nodes.new("ShaderNodeEmission")
    emission.label = NODE_LABEL
    emission.location = (x + 300, y - 300)
    nt.links.new(node.outputs["Color"], emission.inputs["Color"])

    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.label = NODE_LABEL
    mix.location = (x + 300, y)

    downstream = [link.to_socket for link in bsdf.outputs["BSDF"].links]
    for link in list(bsdf.outputs["BSDF"].links):
        nt.links.remove(link)
    nt.links.new(node.outputs["Factor"], mix.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    for socket in downstream:
        nt.links.new(mix.outputs["Shader"], socket)

    sync_amount(bmat)
    return True


def unwire(bmat) -> bool:
    """Take the reflection back out, closing the surface chain behind it."""
    nt = getattr(bmat, "node_tree", None)
    if nt is None:
        return False
    nodes = [n for n in nt.nodes if n.label == NODE_LABEL]
    if not nodes:
        return False
    mix = next((n for n in nodes if n.type == "MIX_SHADER"), None)
    if mix is not None:
        upstream = mix.inputs[1].links[0].from_socket if mix.inputs[1].is_linked else None
        downstream = [link.to_socket for link in mix.outputs[0].links]
        for link in list(mix.outputs[0].links):
            nt.links.remove(link)
        if upstream is not None:
            for socket in downstream:
                nt.links.new(upstream, socket)
    for node in nodes:
        nt.nodes.remove(node)
    return True


def sync_amount(bmat) -> None:
    """Copy ``reflection_amount`` into the group node's Amount input.

    The property is the source of truth -- it is what export writes -- and this
    is a mirror kept for the viewport.  Nothing reads it back, which is the only
    thing that stops it being a second copy of the value.
    """
    node = group_node(bmat)
    if node is None:
        return
    props = getattr(bmat, "dts_material", None)
    if props is None:
        return
    node.inputs["Amount"].default_value = float(props.reflection_amount)
