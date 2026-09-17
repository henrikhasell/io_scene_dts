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
#      which is what keeps tests/, scripts/, examples/, dist/, the internal
#      notes and .claude/ out.
#   2. scripts/build_extension.py otherwise.  Same archive layout, no Blender
#      needed, which is why CI uses it -- but it mirrors the exclude list as a
#      Python allowlist rather than reading it, so it cannot catch a bad
#      manifest, and the two can drift.  They did: the manifest once let
#      .claude/settings.local.json and three files of notes into this builder's
#      zip and not the other's.  tests/test_extension_build.py now runs both and
#      diffs the archives, so a drift fails the suite instead of shipping.
#
# The zip is named from the manifest (io_scene_dts-<version>.zip), so a version
# bump leaves the old one behind; stale zips are cleared first rather than
# accumulating into an ambiguous dist/.
#
# The version is the git tag, not a committed value.  The manifest in the repo
# holds a 0.0.0 placeholder, so this fills it in from `git describe` for the
# length of the build and puts the placeholder back on the way out -- including
# on failure and on Ctrl-C, because a build that leaves a real version in a
# tracked file is exactly the second source of truth this arrangement exists to
# avoid.  An exactly-tagged commit builds as that release; anything else gets
# build metadata naming the commit and whether the tree was dirty.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$REPO/dist"
ID="$(sed -n 's/^id = "\(.*\)"/\1/p' "$REPO/blender_manifest.toml")"
: "${ID:?blender_manifest.toml has no id}"

cleanup_paths=()
restore() {
    "$REPO/scripts/set_version.py" --reset >/dev/null
    [ ${#cleanup_paths[@]} -eq 0 ] || rm -f "${cleanup_paths[@]}"
}
trap restore EXIT INT TERM

"$REPO/scripts/set_version.py" --from-git
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/blender_manifest.toml")"

mkdir -p "$OUT"
rm -f "$OUT/$ID"-*.zip

if command -v blender >/dev/null 2>&1; then
    log="$(mktemp)"
    cleanup_paths+=("$log")
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
echo "  version $VERSION, from git"
case "$VERSION" in
    *+*) echo "  (a '+' suffix means this is not a released build: the tag is the"
         echo "   release, this is however many commits past it)" ;;
esac
