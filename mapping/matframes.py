"""Extra material frames as mesh attributes.

A DTS mesh with ``num_mat_frames > 1`` is a texture flipbook: one vertex array
and several blocks of texture coordinates concatenated into ``tverts``, which
the engine addresses as ``tverts[matFrame * numVerts + i]``.  A sequence's
matframe track steps the index, scrolling the texture without touching
geometry.  Four shapes in the Tribes 2 corpus use it, with 17, 23, 45 and 62
frames.

Frame 0 stays in the mesh's active UV map, where it renders and can be edited
in the UV editor.  The rest become ``FLOAT2`` attributes named
``dts_matframe_001..NNN``:

- **Attributes, not UV layers**, because ``Mesh.uv_layers`` caps at 8 while
  real shapes reach 62 -- and it caps *silently*, with ``uv_layers.new()``
  returning without adding, so there is not even an error to react to.
- **POINT domain, not CORNER**, because DTS texture coordinates are per-vertex.
  A corner-domain layer could hold a split that the format cannot express, and
  the exporter would have to silently pick one side.

Both are visible in Object Data Properties -> Attributes and editable in the
Spreadsheet editor.

UVs are stored in Blender's convention (v flipped) so the values read the same
as the UV map sitting next to them; ``dtslib`` sees the flip undone on export.
"""

from __future__ import annotations

PREFIX = "dts_matframe_"


def attr_name(frame: int) -> str:
    """Attribute holding material frame ``frame`` (frame 0 is the UV map)."""
    return f"{PREFIX}{frame:03d}"


def frame_names(me) -> list[str]:
    """Extra matframe attribute names on ``me``, in frame order."""
    return sorted(a.name for a in me.attributes if a.name.startswith(PREFIX))


def frame_count(me) -> int:
    """``num_mat_frames`` for this Blender mesh -- the UV map plus the extras."""
    return 1 + len(frame_names(me))


def store(bm, mesh, warnings=None) -> int:
    """Import material frames 1..n-1 of ``mesh`` onto the Blender mesh ``bm``.

    Returns the number of attributes created.  Frame 0 is left to the caller,
    which writes it to the UV map.
    """
    if mesh.num_mat_frames <= 1 or not mesh.tverts:
        return 0
    block = len(mesh.tverts) // mesh.num_mat_frames
    if block <= 0 or block * mesh.num_mat_frames != len(mesh.tverts):
        if warnings is not None:
            warnings.append(
                f"mesh {bm.name!r} declares {mesh.num_mat_frames} material frames but has "
                f"{len(mesh.tverts)} texture coordinates, which does not divide evenly; "
                f"the extra frames are dropped"
            )
        return 0
    if mesh.num_frames > 1:
        # the two would share one tverts array with different block strides;
        # no corpus shape does this, so refuse to guess at the layout
        if warnings is not None:
            warnings.append(
                f"mesh {bm.name!r} animates both vertices and material frames; "
                f"only the material frames are kept"
            )

    made = 0
    for frame in range(1, mesh.num_mat_frames):
        attr = bm.attributes.new(name=attr_name(frame), type="FLOAT2", domain="POINT")
        base = frame * block
        for i in range(len(bm.vertices)):
            u, v = mesh.tverts[base + i] if i < block else (0.0, 0.0)
            attr.data[i].vector = (u, 1.0 - v)
        made += 1
    return made


def extra_blocks(me, blender_vert_per_dts_vert) -> list[list[tuple[float, float]]]:
    """Texture-coordinate blocks for material frames 1..n-1, on export.

    ``blender_vert_per_dts_vert`` maps each DTS vertex index back to the
    Blender vertex it was deduplicated from, so a mesh whose corners split at a
    UV seam still gets one value per DTS vertex.  An entry of -1 is a vertex
    this mesh does not own -- padding in a detail level's shared prefix -- and
    gets a zero coordinate, which nothing indexes.
    """
    blocks = []
    for name in frame_names(me):
        attr = me.attributes.get(name)
        if attr is None or attr.domain != "POINT":
            continue
        data = attr.data
        block = []
        for bvert in blender_vert_per_dts_vert:
            if 0 <= bvert < len(data):
                u, v = data[bvert].vector
                block.append((u, 1.0 - v))
            else:
                block.append((0.0, 0.0))
        blocks.append(block)
    return blocks
