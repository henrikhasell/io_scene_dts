#!/bin/bash
# Build the Blender add-on into ./dist.
#
# Usage: scripts/build-blender-addon.sh
#
# Two builders exist and this picks the better one available:
#
#   1. `blender --command extension build`, when Blender is on PATH.  It is
#      the authoritative one -- it parses blender_manifest.toml the way the
#      extension system will, so a manifest this rejects would have failed on
#      install too.  It also honours the manifest's own paths_exclude_pattern,
#      which is what keeps tests/, scripts/, examples/ and dist/ out.
#   2. scripts/build_extension.py otherwise.  Same archive layout, no Blender
#      needed, which is why CI uses it -- but it mirrors the exclude list in
#      Python rather than reading it, so it cannot catch a bad manifest.
#
# The zip is named from the manifest (io_scene_dts-<version>.zip), so a version
# bump leaves the old one behind; stale zips are cleared first rather than
# accumulating into an ambiguous dist/.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$REPO/dist"
ID="$(sed -n 's/^id = "\(.*\)"/\1/p' "$REPO/blender_manifest.toml")"
: "${ID:?blender_manifest.toml has no id}"

mkdir -p "$OUT"
rm -f "$OUT/$ID"-*.zip

if command -v blender >/dev/null 2>&1; then
    log="$(mktemp)"
    trap 'rm -f "$log"' EXIT
    # Blender writes unrelated add-on registration noise to stderr on some
    # installs, so the log is only shown when the build actually fails.
    if ! blender --command extension build \
            --source-dir "$REPO" --output-dir "$OUT" >"$log" 2>&1; then
        echo "blender extension build failed:" >&2
        cat "$log" >&2
        exit 1
    fi
    built_with="blender --command extension build"
else
    echo "blender not on PATH; falling back to the Python builder" >&2
    "$REPO/scripts/build_extension.py" --out-dir dist >/dev/null
    built_with="scripts/build_extension.py"
fi

zip="$(ls -1 "$OUT/$ID"-*.zip 2>/dev/null | head -1)"
if [ -z "$zip" ]; then
    echo "no archive produced in $OUT" >&2
    exit 1
fi

echo "$zip"
echo "  $(du -h "$zip" | cut -f1), $(unzip -l "$zip" | tail -1 | awk '{print $2}') files, via $built_with"
