#!/bin/bash
# Bump the add-on version, commit, tag and push.
#
# Usage:
#   scripts/publish_version.sh <major|minor|patch> [--dry-run] [--skip-tests]
#
# The git tag is the version.  Nothing in the tree stores it:
# `blender_manifest.toml` holds a 0.0.0 placeholder and CI injects the tag into
# it at build time (scripts/set_version.py), so the next version is computed
# from the latest tag rather than read out of a file that could disagree with
# it.
#
# One file restates the version in prose and is bumped with the tag, because it
# starts lying the moment the tag lands:
#
#   UNSUPPORTED.md  "as of vX.Y.Z", a claim about which version the doc describes
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

# The latest tag is the current version.  Sorted by version, not by date: a
# patch cut from an old branch would otherwise look like the newest release.
current="$(git tag --sort=-v:refname | head -1)"
[ -n "$current" ] || die "no tags in this repository; nothing to bump from"
case "$current" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) die "latest tag $current is not X.Y.Z; bump by hand" ;;
esac

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

# The manifest must still be the placeholder.  A real version committed there
# is a second source of truth, and the one it would disagree with is the tag
# this script is about to create.
committed="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$MANIFEST")"
[ "$committed" = "0.0.0" ] \
    || die "blender_manifest.toml has version \"$committed\", not the 0.0.0 placeholder; run scripts/set_version.py --reset"

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
        # The result line is the evidence, not the exit status: a run that dies
        # before it collects anything can still exit 0, and "nothing failed" is
        # not the same claim as "the tests ran" -- the same reason
        # mutate.py's pytest runner demands a summary line.
        #
        # The two counts are compared in the shell because a back reference
        # cannot do it: `grep -E` has none by definition, and the grep on this
        # machine (ugrep) has none in either dialect, so the pattern this
        # replaces errored out and failed the *release* rather than the tests.
        out="$(blender --background --factory-startup \
                       --python tests/blender/run_blender_tests.py 2>&1 | tee /dev/stderr)" || true
        summary="$(printf '%s\n' "$out" \
                   | grep -oE "[0-9]+/[0-9]+ blender integration tests passed" | tail -1)" || true
        [ -n "$summary" ] || die "Blender integration suite printed no result line"
        ran="${summary%%/*}"
        total="${summary#*/}"; total="${total%% *}"
        [ "$ran" = "$total" ] || die "Blender integration tests failed: $summary"
        echo "publish_version: blender: $summary"
    else
        echo "publish_version: blender not on PATH, skipping the integration suite" >&2
    fi
fi

if [ "$dry_run" = true ]; then
    echo "publish_version: --dry-run, stopping before any change"
    echo "  would set  UNSUPPORTED.md         as of v$next"
    echo "  would commit, tag $next, and push both to origin/$branch"
    echo "  blender_manifest.toml stays at the 0.0.0 placeholder; CI injects $next"
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

subst "$REPO/UNSUPPORTED.md" "s/as of v$current\b/as of v$next/g" "the 'as of' version"

git add UNSUPPORTED.md
git commit -q -m "Release $next

The tag is the version; blender_manifest.toml keeps its 0.0.0 placeholder and
CI injects $next into it at build time.  UNSUPPORTED.md restates the version in
prose and is bumped with the tag."

git tag "$next"

echo "publish_version: pushing $branch and tag $next to origin"
git push origin "$branch"
git push origin "$next"

echo "publish_version: released $next"
