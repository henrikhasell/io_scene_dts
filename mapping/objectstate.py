"""Object-state tracks: visibility, vertex frame, and material frame.

A DTS sequence animates three things per object alongside the bone channels:
``vis`` (0..1), ``frame`` (which vertex-animation frame to show) and
``matframe`` (which block of texture coordinates to use).

None of them belongs to a bone, and none belongs to a single Blender object
either: one DTS object is one Blender mesh *per detail level*, so light_male's
``Jetfire`` is ten of them.  Keyframing the meshes directly would mean a slot
and an NLA track per LOD copy per sequence, all needing to be muted and retimed
in lockstep with the armature's.

So the value is keyframed as a custom property on the armature, in the same
slot as the bones, and the meshes read it through drivers.  One ID stays
animated, the NLA model is untouched, and retiming a strip retimes the object
states for free.

**These fcurves are what gets exported.**  They used to be generated from a
``dts_object_anim`` JSON blob on the action, and export read the blob and never
looked at the curves -- so keyframing visibility in Blender changed the preview
and nothing else.  The blob is gone; the curves are the authored form.
"""

from __future__ import annotations

import bpy

# One prefix per channel.  "mat_frame" rather than "matframe" so an armature
# property cannot be mistaken for the per-vertex mesh attributes in
# mapping/matframes.py, which are named dts_matframe_001 and up.
PREFIXES = {
    "vis": "dts_vis_",
    "frame": "dts_frame_",
    "matframe": "dts_mat_frame_",
}
KINDS = tuple(PREFIXES)

# vis is a real value and fades between keys; a frame index does not, and
# interpolating one would ask for a frame between two frames
INTERPOLATION = {"vis": "LINEAR", "frame": "CONSTANT", "matframe": "CONSTANT"}
DEFAULTS = {"vis": 1.0, "frame": 0.0, "matframe": 0.0}


def prop_name(kind: str, dts_object_name: str) -> str:
    return f"{PREFIXES[kind]}{dts_object_name}"


def path_for(kind: str, dts_object_name: str) -> str:
    # bracketed string key, so DTS names with spaces or punctuation are fine
    return f'["{prop_name(kind, dts_object_name)}"]'


def parse_path(data_path: str) -> tuple[str, str] | None:
    """('vis', 'Jetfire') for an object-state fcurve, else None.

    Longest prefix first: "dts_vis_" is not a prefix of the others, but keeping
    the order explicit means adding a channel later cannot silently shadow one.
    """
    if not (data_path.startswith('["') and data_path.endswith('"]')):
        return None
    key = data_path[2:-2]
    for kind in sorted(KINDS, key=lambda k: -len(PREFIXES[k])):
        prefix = PREFIXES[kind]
        if key.startswith(prefix):
            return kind, key[len(prefix) :]
    return None


def iter_fcurves(action: bpy.types.Action):
    """Every fcurve in the action, whatever the slot/channelbag layout."""
    from .sequences import _iter_fcurves

    return _iter_fcurves(action)


def read_tracks(action: bpy.types.Action, n: int) -> dict[str, dict[str, list]]:
    """Sample the object-state fcurves at frames 1..n.

    The same shape ``dts_object_anim`` had -- {object name: {kind: [values]}} --
    so the exporter's use of it did not have to change when the blob went away.

    Sampled with ``fcurve.evaluate``, exactly as the bone channels are, so an
    object state and a bone pose retime together.
    """
    tracks: dict[str, dict[str, list]] = {}
    for fcurve in iter_fcurves(action):
        parsed = parse_path(fcurve.data_path)
        if parsed is None:
            continue
        kind, base_name = parsed
        tracks.setdefault(base_name, {})[kind] = [
            fcurve.evaluate(kf + 1) for kf in range(n)
        ]
    return tracks


def ensure_props(arm_obj, kind: str, names, default: float | None = None) -> None:
    """The ID property must exist before an fcurve or driver can resolve it."""
    value = DEFAULTS[kind] if default is None else default
    for name in names:
        if prop_name(kind, name) not in arm_obj.keys():
            arm_obj[prop_name(kind, name)] = float(value)


def write_tracks(bag, arm_obj, tracks: dict[str, dict[str, list]]) -> dict[str, set]:
    """Keyframe imported tracks into the action's armature slot.

    Returns the object names that got a track, per kind.
    """
    written: dict[str, set] = {kind: set() for kind in KINDS}
    for base_name, by_kind in tracks.items():
        for kind, values in by_kind.items():
            if kind not in PREFIXES or not values:
                continue
            ensure_props(arm_obj, kind, [base_name])
            fcurve = bag.fcurves.new(data_path=path_for(kind, base_name), index=0)
            fcurve.keyframe_points.add(len(values))
            for i, value in enumerate(values):
                point = fcurve.keyframe_points[i]
                point.co = (i + 1, float(value))
                point.interpolation = INTERPOLATION[kind]
            fcurve.update()
            written[kind].add(base_name)
    return written


def animated_object_names(actions, kind: str = "vis") -> set:
    """DTS object names carrying a track of this kind, across the actions."""
    names = set()
    for action in actions:
        for fcurve in iter_fcurves(action):
            parsed = parse_path(fcurve.data_path)
            if parsed is not None and parsed[0] == kind:
                names.add(parsed[1])
    return names


def fractional_object_names(actions) -> set:
    """Names whose vis track holds values strictly between 0 and 1.

    A binary track is a swap, which the hide drivers cover without making the
    material transparent.  Only a real fade needs alpha -- the generator's power
    lights pulse 0.1..1.0, while its panel swaps are 1/0.
    """
    names = set()
    for action in actions:
        for fcurve in iter_fcurves(action):
            parsed = parse_path(fcurve.data_path)
            if parsed is None or parsed[0] != "vis":
                continue
            if any(0.0 < point.co[1] < 1.0 for point in fcurve.keyframe_points):
                names.add(parsed[1])
    return names
