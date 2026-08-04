"""Recompute the engine's intra-shape link words.

``Node.runtime`` (firstObject, firstChild, nextSibling) and ``Object.runtime``
(nextSibling, firstDecal) are scratch fields the engine rebuilds in
``TSShape::init``.  They are stored in the file, so a round-trip that leaves
them at their defaults produces a shape that differs from the source -- but
they are fully derivable from the node/object/decal hierarchy, so nothing has
to be carried through Blender to restore them.

The linking is a push-front over a *reverse* iteration, which is what makes the
resulting chains come out in ascending index order; iterating forwards produces
the reverse of what the engine writes.
"""

from __future__ import annotations

from .types import Decal, Node, Object, Shape


def recompute_runtime_links(shape: Shape) -> None:
    """Rebuild Node.runtime / Object.runtime / Decal sibling links in place."""
    num_nodes = len(shape.nodes)
    num_objects = len(shape.objects)

    first_object = [-1] * num_nodes
    first_child = [-1] * num_nodes
    next_sibling = [-1] * num_nodes
    first_decal = [-1] * num_objects
    object_next = [-1] * num_objects
    decal_next = [-1] * len(shape.decals)

    for s in range(len(shape.sub_shape_first_node)):
        start = shape.sub_shape_first_node[s]
        for j in range(start + shape.sub_shape_num_nodes[s] - 1, start - 1, -1):
            parent = shape.nodes[j].parent_index
            if 0 <= parent < num_nodes:
                next_sibling[j] = first_child[parent]
                first_child[parent] = j

    for s in range(len(shape.sub_shape_first_object)):
        start = shape.sub_shape_first_object[s]
        for j in range(start + shape.sub_shape_num_objects[s] - 1, start - 1, -1):
            node = shape.objects[j].node_index
            if 0 <= node < num_nodes:
                object_next[j] = first_object[node]
                first_object[node] = j

    for s in range(len(shape.sub_shape_first_decal)):
        start = shape.sub_shape_first_decal[s]
        for j in range(start + shape.sub_shape_num_decals[s] - 1, start - 1, -1):
            owner = shape.decals[j].raw[3]
            if 0 <= owner < num_objects:
                decal_next[j] = first_decal[owner]
                first_decal[owner] = j

    for i, node in enumerate(shape.nodes):
        shape.nodes[i] = Node(
            name_index=node.name_index,
            parent_index=node.parent_index,
            runtime=(first_object[i], first_child[i], next_sibling[i]),
        )
    for i, obj in enumerate(shape.objects):
        shape.objects[i] = Object(
            name_index=obj.name_index,
            num_meshes=obj.num_meshes,
            start_mesh_index=obj.start_mesh_index,
            node_index=obj.node_index,
            runtime=(object_next[i], first_decal[i]),
        )
    for i, decal in enumerate(shape.decals):
        raw = list(decal.raw)
        raw[4] = decal_next[i]
        shape.decals[i] = Decal(tuple(raw))
