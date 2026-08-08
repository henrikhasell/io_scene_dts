#!/bin/bash
# Bump the add-on version, commit, tag and push.
#
# Usage:
#   scripts/publish_version.sh <major|minor|patch> [--dry-run] [--skip-tests]
#
# `blender_manifest.toml` is the version: it is what the extension system reads
# and what names the built zip.  Two files restate it in prose and are bumped
# with it, because they start lying the moment the tag lands:
#
#   README.md       the example zip name in the build section
#   UNSUPPORTED.md  "as of vX.Y.Z", a claim about which version the doc describes
#
# `pyproject.toml` and `dtslib/__init__.py` carry versions of their own and are
# deliberately left alone -- they already disagree with the manifest and with
# each other, so folding them in here would be guessing at what they mean.
# The script says so rather than silently picking a side.
#
# Tags match the ones already in the repo: a bare "1.4.0", no "v", lightweight.
#
# Nothing is written until every check has passed, so a refusal leaves the tree
# exactly as it was -- the failure modes worth having are all before the commit.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO/blender_manifest.toml"

die() { echo "publish_version: $*" >&2; exit 1; }

part=""
dry_run=false
skip_tests=false
for arg in "$@"; do
    case "$arg" in
        major|minor|patch) [ -z "$part" ] || die "give one of major/minor/patch, not two"
                           part="$arg" ;;
        --dry-run)         dry_run=true ;;
        --skip-tests)      skip_tests=true ;;
        -h|--help)         sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *)                 die "unknown argument: $arg" ;;
    esac
done
[ -n "$part" ] || die "usage: $(basename "$0") <major|minor|patch> [--dry-run] [--skip-tests]"

cd "$REPO"

# -- checks, all of them before anything is written -----------------------
git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $REPO"
[ -f "$MANIFEST" ] || die "no blender_manifest.toml in $REPO"

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" != "HEAD" ] || die "detached HEAD; check out a branch first"

# A release tags a commit, so the commit has to be the whole story.  Tagging a
# dirty tree points the tag at a state nobody can check out again.
if [ -n "$(git status --porcelain)" ]; then
    git status --short >&2
    die "working tree is not clean; commit or stash first"
fi

git remote get-url origin >/dev/null 2>&1 || die "no 'origin' remote to push to"

current="$(sed -n 's/^version = "\([0-9]\+\.[0-9]\+\.[0-9]\+\)"$/\1/p' "$MANIFEST")"
[ -n "$current" ] || die "no 'version = \"x.y.z\"' line in blender_manifest.toml"

IFS=. read -r major minor patch <<<"$current"
case "$part" in
    major) major=$((major + 1)); minor=0; patch=0 ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    patch) patch=$((patch + 1)) ;;
esac
next="$major.$minor.$patch"

git rev-parse -q --verify "refs/tags/$next" >/dev/null \
    && die "tag $next already exists locally"
if git ls-remote --exit-code --tags origin "refs/tags/$next" >/dev/null 2>&1; then
    die "tag $next already exists on origin"
fi

echo "publish_version: $current -> $next  (${part}, on branch $branch)"

# versions this script does not own, reported so the drift is visible
for other in "pyproject.toml:^version = " "dtslib/__init__.py:^__version__ = "; do
    file="${other%%:*}"; pattern="${other#*:}"
    [ -f "$file" ] || continue
    theirs="$(sed -n "s/$pattern\"\([^\"]*\)\"/\1/p" "$file" | head -1)"
    [ -n "$theirs" ] && [ "$theirs" != "$current" ] \
        && echo "publish_version: note: $file is at $theirs and is not bumped by this script"
done

# -- tests ----------------------------------------------------------------
# CLAUDE.md: the fast loop and the Blender suite both pass before a commit.
# A release is the last place to skip them, so they run unless asked not to.
if [ "$skip_tests" = false ]; then
    echo "publish_version: running tests"
    if [ -x "$REPO/.venv/bin/python" ]; then
        "$REPO/.venv/bin/python" -m pytest -m "not corpus" -q \
            || die "unit tests failed"
    else
        echo "publish_version: no .venv, skipping the pytest loop" >&2
    fi
    "$REPO/scripts/check_citations.py" >/dev/null || die "UNSUPPORTED.md citations do not land"
    if command -v blender >/dev/null 2>&1; then
        blender --background --factory-startup \
                --python tests/blender/run_blender_tests.py 2>&1 \
            | tee /dev/stderr | grep -qE "^[0-9]+/\1 blender integration tests passed" \
            || die "Blender integration tests failed"
    else
        echo "publish_version: blender not on PATH, skipping the integration suite" >&2
    fi
fi

if [ "$dry_run" = true ]; then
    echo "publish_version: --dry-run, stopping before any change"
    echo "  would set  blender_manifest.toml  version = \"$next\""
    echo "  would set  README.md              io_scene_dts-$next.zip"
    echo "  would set  UNSUPPORTED.md         as of v$next"
    echo "  would commit, tag $next, and push both to origin/$branch"
    exit 0
fi

# -- write ----------------------------------------------------------------
# Each substitution is checked: a silent no-op here would tag a version the
# manifest never got, which is the one outcome worse than failing.
subst() {  # file, sed expression, description
    local file="$1" expr="$2" what="$3" before
    before="$(cat "$file")"
    sed -i "$expr" "$file"
    [ "$before" != "$(cat "$file")" ] || die "$file: $what did not change (pattern missed?)"
}

subst "$MANIFEST" "s/^version = \"$current\"$/version = \"$next\"/" "the version line"
subst "$REPO/README.md" "s/io_scene_dts-$current\.zip/io_scene_dts-$next.zip/g" "the zip name"
subst "$REPO/UNSUPPORTED.md" "s/as of v$current\b/as of v$next/g" "the 'as of' version"

git add blender_manifest.toml README.md UNSUPPORTED.md
git commit -q -m "Release $next

blender_manifest.toml is the version the extension system reads; README.md and
UNSUPPORTED.md restate it and are bumped with it."

git tag "$next"

echo "publish_version: pushing $branch and tag $next to origin"
git push origin "$branch"
git push origin "$next"

echo "publish_version: released $next"
