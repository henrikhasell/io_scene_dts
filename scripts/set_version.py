#!/usr/bin/env python3
"""Write the extension version into blender_manifest.toml from a git tag.

The git tag is the single source of truth for the add-on version.  The manifest
cannot *be* that source: schema 1.0.0 requires a literal ``version`` string and
Blender does no interpolation, so the value has to be in the file by the time
either builder reads it.  So the repo carries a placeholder and this script
injects the real version at build time -- in CI from ``$GITHUB_REF_NAME``, and
locally from ``git describe``.

    scripts/set_version.py 1.7.1          # from a tag (a leading "v" is fine)
    scripts/set_version.py --from-git     # derive from the repository
    scripts/set_version.py --reset        # back to the placeholder
    scripts/set_version.py --print ...    # show the value, write nothing

Two things this deliberately does not do.  It does not commit: the manifest is
meant to be dirty only for the length of a build, which is why
``scripts/build-blender-addon.sh`` restores the placeholder on exit.  And it
does not invent a version when there are no tags -- it falls back to
``0.0.0+dev``, which is honest about being unreleasable rather than guessing.

Why a build-metadata suffix for dev builds rather than a prerelease: semver
orders ``1.6.0-3.gabc1234`` *before* ``1.6.0``, which is backwards for a commit
that comes after it.  Build metadata is ignored for precedence, so
``1.6.0+3.gabc1234`` reads as "1.6.0, built from something later" and sorts
where it should.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "blender_manifest.toml"

PLACEHOLDER = "0.0.0"
NO_TAGS = "0.0.0+dev"

# semver.org's own regex, which is what Blender validates against --
# RE_MANIFEST_SEMVER in its cli/blender_ext.py.  Anything this rejects would be
# rejected at build and at install.
SEMVER_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$"
)

VERSION_LINE_RE = re.compile(r'^version = ".*"$', re.MULTILINE)


def from_tag(tag: str) -> str:
    """A tag name as a manifest version.  Raises on anything else."""
    version = tag[1:] if tag.startswith("v") else tag
    if not SEMVER_RE.match(version):
        raise SystemExit(
            f"set_version: {tag!r} is not a semantic version.  Tags in this "
            f"repository are bare X.Y.Z (1.6.0, not v1.6.0-final)."
        )
    return version


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def from_git() -> str:
    """The version this working tree represents.

    An exactly-tagged commit is that release.  Anything else is a dev build and
    says so in build metadata, including whether the tree was dirty -- a zip
    built from uncommitted edits should not be mistakable for one that wasn't.
    """
    exact = _git("describe", "--tags", "--exact-match", "HEAD")
    if exact:
        version = from_tag(exact)
        return f"{version}+dirty" if _is_dirty() else version

    described = _git("describe", "--tags", "--long", "--abbrev=7")
    if not described:
        return NO_TAGS

    # rsplit, not split: a prerelease tag like 1.7.0-rc.1 contains hyphens of
    # its own, and only the last two fields are describe's
    try:
        tag, commits, sha = described.rsplit("-", 2)
    except ValueError:
        return NO_TAGS
    version = from_tag(tag)
    suffix = f"{commits}.{sha}"
    if _is_dirty():
        suffix += ".dirty"
    return f"{version}+{suffix}"


def _is_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def write(version: str) -> str:
    """Replace the manifest's version line.  Returns the previous value."""
    text = MANIFEST.read_text()
    found = VERSION_LINE_RE.search(text)
    if found is None:
        raise SystemExit(
            f"set_version: no 'version = \"...\"' line in {MANIFEST.name}; "
            f"refusing to guess where it goes"
        )
    previous = found.group(0).split('"')[1]
    MANIFEST.write_text(VERSION_LINE_RE.sub(f'version = "{version}"', text, count=1))
    return previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", nargs="?", help="tag name, e.g. 1.7.1")
    parser.add_argument("--from-git", action="store_true", help="derive from git describe")
    parser.add_argument("--reset", action="store_true", help=f"write {PLACEHOLDER}")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the version, write nothing")
    args = parser.parse_args()

    chosen = [bool(args.tag), args.from_git, args.reset]
    if sum(chosen) != 1:
        parser.error("give exactly one of: a tag, --from-git, --reset")

    if args.reset:
        version = PLACEHOLDER
    elif args.from_git:
        version = from_git()
    else:
        version = from_tag(args.tag)

    if args.print_only:
        print(version)
        return 0

    previous = write(version)
    print(f"set_version: {MANIFEST.name} {previous} -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
