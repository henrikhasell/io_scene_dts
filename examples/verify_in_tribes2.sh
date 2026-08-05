#!/bin/bash
# Load every exported example into Tribes 2 and screenshot it.
#
# Usage: examples/verify_in_tribes2.sh <port> [example ...]
#
# Three things about this engine cost an afternoon each, so they are spelled
# out rather than left in the shell history:
#
#   1. A datablock created *after* the client joins never reaches the client.
#      Rendering a shape whose datablock the client lacks is an access
#      violation, not a warning -- and it looks exactly like a broken export.
#      So every StaticShapeData is declared and every shape spawned between
#      CreateServer and localConnect.
#   2. Textures are found under GameData/base/textures/, *not* beside the .dts.
#      A material named "crate" wants textures/crate.png however deep in
#      shapes/ the shape itself lives.  A missing one renders flat grey, which
#      is indistinguishable from broken UVs by eye.
#   3. The spawned player falls.  Anything positioned relative to it has moved
#      by the time a screenshot lands, so the viewpoint is a Camera on the
#      stock Observer datablock, which stays put.
#
# Interiors are stripped from the mission: the client lighting pass over them
# never finishes under swrast and wedges the join.
set -euo pipefail

PORT="${1:?usage: verify_in_tribes2.sh <port> [example ...]}"
shift || true

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$HERE/dist"
SHOTS="$HERE/screenshots"
PLUGIN="${CLAUDE_PLUGIN_ROOT:-/home/henrik/.claude/plugins/cache/tribes-2-modding-skill/tribes2-modding/1.0.0}"
T2="$PLUGIN/scripts/t2console.py"
EXEC="$PLUGIN/scripts/t2exec.sh"
SHOT="$PLUGIN/container/take-screenshot.sh"
RUN="$PLUGIN/container/run-container.sh"
ROOT=/opt/tribes2/prefix/drive_c/Dynamix/Tribes2/GameData/base
C="tribes2-$PORT"

EXAMPLES=("$@")
if [ ${#EXAMPLES[@]} -eq 0 ]; then
    mapfile -t EXAMPLES < <(cd "$DIST" && ls *.dts | sed 's/\.dts$//' | sort)
fi

mkdir -p "$SHOTS"
docker stop "$C" >/dev/null 2>&1 || true
"$RUN" "$PORT" >/dev/null 2>&1
echo "container $C up"

docker exec "$C" mkdir -p "$ROOT/shapes/dtsx" "$ROOT/textures" "$ROOT/missions" "$ROOT/terrains"
for f in "$DIST"/*.dts "$DIST"/*.dsq; do
    [ -e "$f" ] && docker cp "$f" "$C:$ROOT/shapes/dtsx/" >/dev/null
done
for f in "$DIST"/*.png; do
    [ -e "$f" ] && docker cp "$f" "$C:$ROOT/textures/" >/dev/null
done
docker cp "$HERE/DtsExamples.mis" "$C:$ROOT/missions/DtsExamples.mis" >/dev/null
docker cp "$HERE/Rimehold.ter" "$C:$ROOT/terrains/Rimehold.ter" >/dev/null
echo "content injected"

python3 "$T2" --port "$PORT" \
    'setModPaths(getModPaths());' \
    '$pref::sceneLighting::terrainGenerateLevel = 0;' \
    'CreateServer("DtsExamples", "SinglePlayer");' \
    --until '$missionRunning' --until-timeout 400 >/dev/null 2>&1
echo "mission hosted"

"$EXEC" "$PORT" "$HERE/stage.cs" >/dev/null 2>&1
slot=0
for name in "${EXAMPLES[@]}"; do
    python3 "$T2" --port "$PORT" "StageShape(\"$name\", $slot, 1);" >/dev/null 2>&1
    slot=$((slot + 1))
done
echo "${#EXAMPLES[@]} shape(s) staged before the join"

python3 "$T2" --port "$PORT" \
    'localConnect("DTS Tester", "Human Male", "swolf", "Male1");' \
    --until 'isObject(ServerConnection)' --until-timeout 700 >/dev/null 2>&1
"$EXEC" "$PORT" "$HERE/cam.cs" >/dev/null 2>&1
echo "joined"

fail=0
slot=0
for name in "${EXAMPLES[@]}"; do
    python3 "$T2" --port "$PORT" "ViewShape($slot, 4, 0);" >/dev/null 2>&1
    if ! python3 "$T2" --port "$PORT" 'echo("PULSE");' 2>&1 | grep -q PULSE; then
        echo "CRASHED rendering $name"
        fail=1
        break
    fi
    "$SHOT" "$PORT" >/dev/null 2>&1
    mv "screenshot-$PORT.png" "$SHOTS/$name.png"
    echo "  captured $name"
    slot=$((slot + 1))
done

echo
if [ "$fail" = 0 ]; then
    echo "all ${#EXAMPLES[@]} example(s) rendered without taking the engine down"
    echo "screenshots in $SHOTS"
fi
exit $fail
