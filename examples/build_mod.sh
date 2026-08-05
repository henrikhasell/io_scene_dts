#!/bin/bash
# Rebuild examples/mod/DtsExamples from the .blend examples.
#
# Usage: examples/build_mod.sh
#
# Three steps that have to happen together, which is why this exists rather
# than a note in a README:
#
#   1. Blender rebuilds every .blend and exports it to examples/dist.
#   2. The shapes and the textures go to *different* places in the mod: the
#      engine resolves a material named "crate" to <mod>/textures/crate.png
#      wherever the .dts itself lives, so exporting straight into shapes/dtsx
#      leaves 21 textures sitting somewhere nothing reads them.
#   3. The showcase script's lift table is re-baked from the exported .dts, so
#      a model that changes height still stands on the terrain.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$HERE/dist"
MOD="$HERE/mod/DtsExamples"

mkdir -p "$DIST" "$MOD/shapes/dtsx" "$MOD/textures"

blender --background --factory-startup --python "$HERE/build_examples.py" -- \
    --export "$DIST" --lifts

# Mirror rather than copy: a renamed example should not leave its old shape
# behind in the mod, where the showcase would happily keep loading it.
rm -f "$MOD"/shapes/dtsx/*.dts "$MOD"/shapes/dtsx/*.dsq "$MOD"/textures/*.png
cp "$DIST"/*.dts "$MOD/shapes/dtsx/"
cp "$DIST"/*.dsq "$MOD/shapes/dtsx/"
cp "$DIST"/*.png "$MOD/textures/"

echo
echo "mod at $MOD"
echo "  $(ls "$MOD"/shapes/dtsx/*.dts | wc -l) shapes, $(ls "$MOD"/textures/*.png | wc -l) textures"
