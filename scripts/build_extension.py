#!/usr/bin/env python3
"""Build the Blender extension zip without needing Blender.

Produces the same archive layout as `blender --command extension build`:
blender_manifest.toml and the add-on files at the zip root.  Used by CI,
where downloading Blender just to zip six directories is wasteful.

Usage:
    scripts/build_extension.py [--check-version vX.Y.Z] [--out-dir dist]

--check-version fails the build if the manifest version does not match the
given tag's X.Y.Z core (an optional leading "v" and any semver
prerelease/build suffix are ignored for the comparison).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# runtime files only — mirrors [build] paths_exclude_pattern in the manifest
INCLUDE_FILES = ["blender_manifest.toml", "__init__.py", "README.md"]
INCLUDE_DIRS = ["dtslib", "mapping", "ops", "props", "ui"]

SEMVER_RE = re.compile(
    r"^v?(?P<core>\d+\.\d+\.\d+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-version", metavar="TAG", default=None)
    parser.add_argument("--out-dir", default="dist")
    args = parser.parse_args()

    manifest_path = REPO / "blender_manifest.toml"
    manifest = tomllib.loads(manifest_path.read_text())
    ext_id = manifest["id"]
    version = manifest["version"]

    if args.check_version is not None:
        m = SEMVER_RE.match(args.check_version)
        if m is None:
            print(f"error: tag {args.check_version!r} is not a semver tag", file=sys.stderr)
            return 1
        if m.group("core") != version:
            print(
                f"error: tag {args.check_version!r} does not match manifest "
                f"version {version!r} — bump blender_manifest.toml (and "
                f"pyproject.toml) before tagging",
                file=sys.stderr,
            )
            return 1

    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ext_id}-{version}.zip"

    files: list[Path] = [REPO / f for f in INCLUDE_FILES if (REPO / f).is_file()]
    for d in INCLUDE_DIRS:
        files += sorted(p for p in (REPO / d).rglob("*.py") if "__pycache__" not in p.parts)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(REPO).as_posix())

    print(f"created: {out_path} ({out_path.stat().st_size} bytes, {len(files)} files)")
    print(f"version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
