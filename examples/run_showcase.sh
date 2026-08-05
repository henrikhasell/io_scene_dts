#!/bin/bash
# Boot Tribes 2 with the DtsExamples mod and join the showcase map.
#
# Usage: examples/run_showcase.sh <port>
#
# The mod has to be in place before the game boots -- the engine reads its mod
# list at startup and the container is --rm with no volume -- so this creates
# the container, copies the mod in, and only then starts it.
set -euo pipefail

PORT="${1:?usage: run_showcase.sh <port>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="${CLAUDE_PLUGIN_ROOT:-/home/henrik/.claude/plugins/cache/tribes-2-modding-skill/tribes2-modding/1.0.0}"
T2="$PLUGIN/scripts/t2console.py"
SHOT="$PLUGIN/container/take-screenshot.sh"
GAMEDATA=/opt/tribes2/prefix/drive_c/Dynamix/Tribes2/GameData
C="tribes2-$PORT"

docker stop "$C" >/dev/null 2>&1 || true
# The mod is switched on at runtime rather than passed as -mod, because the
# two orders both fail: -mod before -telnetParams and the telnet console never
# opens, -mod after it and `-telnetParams <port> <pass> <listenPass>` swallows
# the flag as its third argument.  setModPaths rebuilds the resource index and
# appends base itself.  Nothing here overrides a base script, which is the case
# where a runtime switch would not be equivalent to a relaunch.
docker create --rm -p "$PORT:2323" --device /dev/dri --name "$C" tribes2 \
    -nologin -telnetParams 2323 password >/dev/null
docker cp "$HERE/mod/DtsExamples" "$C:$GAMEDATA/DtsExamples" >/dev/null
docker start "$C" >/dev/null
echo "container $C started"

python3 "$T2" --port "$PORT" --connect-timeout 180 \
    'setModPaths("DtsExamples");' 'echo("mods: " @ getModPaths());'

python3 "$T2" --port "$PORT" \
    '$pref::sceneLighting::terrainGenerateLevel = 0;' \
    'CreateServer("DtsShowcase", "SinglePlayer");' \
    --until '$missionRunning' --until-timeout 400 >/dev/null 2>&1
echo "showcase hosted"

python3 "$T2" --port "$PORT" \
    'localConnect("DTS Tester", "Human Male", "swolf", "Male1");' \
    --until 'isObject(ServerConnection)' --until-timeout 700 >/dev/null 2>&1
echo "joined"

# The whole row, then each third of it close enough to read.  Fifteen waypoint
# labels never fit across one frame -- the HUD draws them at a fixed size -- so
# the wide shot is for the layout and the three station shots are for the text.
shoot() {   # shoot <station-arg> <output-name>
    python3 "$T2" --port "$PORT" "DtsShowcaseView($1);" >/dev/null 2>&1
    "$SHOT" "$PORT" >/dev/null 2>&1
    mv "screenshot-$PORT.png" "$HERE/screenshots/$2.png"
    echo "  $HERE/screenshots/$2.png"
}

echo "screenshots:"
shoot -1 showcase_overview
shoot 0 showcase_station_1
shoot 1 showcase_station_2
shoot 2 showcase_station_3

echo
echo "the console is live on port $PORT; useful calls:"
echo "  DtsShowcaseView(0);     stand in front of shapes 1-5 (0..2, -1 = wide)"
echo "  DtsShowcasePlay();      restart every sequence"
