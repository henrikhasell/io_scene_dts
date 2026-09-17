#!/usr/bin/env python3
"""Build the Blender extension zip without needing Blender.

Produces the same archive layout as `blender --command extension build`:
blender_manifest.toml and the add-on files at the zip root.  Used by CI,
where downloading Blender just to zip six directories is wasteful.

Usage:
    scripts/build_extension.py [--out-dir dist]

The version comes from whatever blender_manifest.toml currently says, which on
a plain checkout is the 0.0.0 placeholder.  Injecting the real one is
scripts/set_version.py's job and is deliberately not done here: the Blender
builder reads the manifest off the working tree too, so a version this script
applied on its own would produce two differently-versioned zips.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Runtime files only.  This is an allowlist where the manifest's
# [build] paths_exclude_pattern is a blocklist, so the two can drift -- and did:
# the manifest once let .claude/settings.local.json and three internal notes
# into the Blender-built zip while this builder kept them out.
# tests/test_extension_build.py compares the two archives and fails on a
# mismatch, so adding a runtime file here means adding it there too.
# INCLUDE_DIRS is globbed for *.py below, so a non-Python runtime file has to be
# named here or it silently ships from Blender's builder and not from this one.
INCLUDE_FILES = [
    "blender_manifest.toml",
    "__init__.py",
    "README.md",
    "COPYING",
    "ui/icons/logo-128.png",
]
INCLUDE_DIRS = ["dtslib", "mapping", "ops", "props", "ui"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="dist")
    args = parser.parse_args()

    manifest_path = REPO / "blender_manifest.toml"
    manifest = tomllib.loads(manifest_path.read_text())
    ext_id = manifest["id"]
    version = manifest["version"]

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
