"""The two extension builders must agree on what ships.

There are two, for a reason given in ``scripts/build-blender-addon.sh``:
``blender --command extension build`` is authoritative because it parses
``blender_manifest.toml`` the way the extension system will, and
``scripts/build_extension.py`` exists so CI does not download Blender just to
zip six directories.

They express "what ships" in opposite directions, though -- the manifest
excludes (``[build] paths_exclude_pattern``), the script includes
(``INCLUDE_FILES``/``INCLUDE_DIRS``) -- so they can drift apart silently, and
did.  The manifest's exclude list once let ``.claude/settings.local.json``, an
agent config carrying a local path and a shell allowlist, plus three files of
internal notes, into the Blender-built zip, while the script's allowlist kept
them out.  Whichever zip happened to be uploaded decided what users got.

So this compares the archives rather than re-deriving either list, which is the
only version of the check that cannot itself drift.  Names only: zip entries
carry mtimes and the two builders run at different instants.
"""

import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "blender_manifest.toml"


def manifest() -> dict:
    with MANIFEST.open("rb") as f:
        return tomllib.load(f)


def zip_names(directory: Path) -> set[str]:
    archives = sorted(directory.glob("*.zip"))
    assert len(archives) == 1, f"expected one zip in {directory}, got {archives}"
    with zipfile.ZipFile(archives[0]) as zf:
        # drop directory entries: the two builders differ on whether they
        # record them, which is not a difference in what installs
        return {n for n in zf.namelist() if not n.endswith("/")}


def build_with_blender(out: Path) -> set[str]:
    # --output-dir must already exist; Blender exits 1 without saying why
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["blender", "--command", "extension", "build", "--output-dir", str(out)],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    return zip_names(out)


def build_with_script(out: Path) -> set[str]:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["python3", str(REPO / "scripts" / "build_extension.py"), "--out-dir", str(out)],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    return zip_names(out)


# Blender is absent in CI, the same situation the corpus tests are in; skip
# rather than fail, and let the non-Blender assertions below still run.
needs_blender = pytest.mark.skipif(
    shutil.which("blender") is None, reason="blender not on PATH"
)


@needs_blender
def test_both_builders_ship_the_same_files(tmp_path):
    from_blender = build_with_blender(tmp_path / "blender")
    from_script = build_with_script(tmp_path / "script")

    only_blender = sorted(from_blender - from_script)
    only_script = sorted(from_script - from_blender)
    assert not only_blender and not only_script, (
        "the two builders disagree.\n"
        f"  only in `blender --command extension build`: {only_blender}\n"
        f"  only in scripts/build_extension.py:          {only_script}\n"
        "Fix [build] paths_exclude_pattern in blender_manifest.toml or "
        "INCLUDE_FILES/INCLUDE_DIRS in scripts/build_extension.py so they match."
    )


@needs_blender
def test_nothing_but_code_readme_and_licence_ships(tmp_path):
    """The positive form of the same check.

    Comparing the builders catches drift between them but would pass happily if
    *both* shipped something they should not, so name the non-code payload
    outright.
    """
    names = build_with_blender(tmp_path / "blender")
    non_python = sorted(n for n in names if not n.endswith(".py"))
    assert non_python == [
        "COPYING",
        "README.md",
        "blender_manifest.toml",
        "ui/icons/logo-128.png",
    ]


def test_the_menu_icon_is_shaped_the_way_blender_needs():
    """Square, and big enough for a 2x interface.

    Both are measured facts about Blender's preview system rather than taste.
    It fits a preview inside the icon box preserving aspect ratio, so a
    non-square source renders as 32xN and leaves the slot part empty; and the
    box is 32x32 at 1x interface scale, 64x64 at 2x.
    """
    icon = REPO / "ui" / "icons" / "logo-128.png"
    assert icon.is_file()

    header = icon.read_bytes()
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    # IHDR: width, height, bit depth, colour type -- read directly so the test
    # needs neither Blender nor Pillow
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    colour_type = header[25]

    assert width == height, f"icon must be square, got {width}x{height}"
    assert width >= 64, f"icon must be at least 64px for a 2x interface, got {width}"
    # 6 = RGBA, 4 = greyscale+alpha; 3 = palette, which may carry alpha in tRNS
    assert colour_type in (4, 6) or b"tRNS" in header, "icon has no alpha channel"


def test_the_manifest_excludes_the_things_that_leaked(tmp_path):
    """No Blender needed: the patterns that were missing are in the list.

    This is the regression, stated as data.  `.claude/` shipped an agent config;
    `*.md` with `!README.md` is what keeps the next note file out by default
    rather than needing a new pattern per file.
    """
    patterns = manifest()["build"]["paths_exclude_pattern"]
    for required in (".claude/", "*.md", "!README.md", "tests/", "scripts/", "examples/"):
        assert required in patterns, f"{required!r} missing from paths_exclude_pattern"


def test_the_licence_is_the_one_the_platform_requires():
    """"or-later", not "only", and nothing local would catch the difference.

    The platform requires exactly this for add-ons.  Blender's own validator
    checks only that ``license`` is a non-empty list of non-empty strings
    (``blender_ext.py:1980``), so ``extension validate`` is happy with any
    string and the first thing to reject a wrong one is the upload -- which is
    how 1.7.0 shipped as ``GPL-3.0-only`` and bounced.  This test is the local
    stand-in for a check the toolchain does not have.
    """
    data = manifest()
    assert data["license"] == ["SPDX:GPL-3.0-or-later"], (
        "extensions.blender.org requires GPL-3.0-or-later for add-ons; "
        "GPL-3.0-only is rejected at upload"
    )
    assert data["copyright"] == ["2026 Henrik Hasell"]

    copying = REPO / "COPYING"
    assert copying.is_file(), "a GPL is declared but there is no COPYING"
    text = copying.read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text


def test_the_website_is_the_repository_that_holds_this():
    """It pointed at github.com/henrik/... for six releases; the remote is
    github.com/henrikhasell/... .  Nothing would have caught that."""
    assert manifest()["website"] == "https://github.com/henrikhasell/io_scene_dts"


def test_the_committed_manifest_holds_the_placeholder():
    """The git tag is the version, so the tree must not name one.

    A real version committed here is a second source of truth and the thing it
    disagrees with is the tag.  That is not hypothetical: the manifest, an
    unused ``__version__`` in ``dtslib/__init__.py`` and ``pyproject.toml`` once
    read 1.6.0, 1.0.0 and 1.2.0 at the same time.

    scripts/build-blender-addon.sh injects a real version for the length of a
    build and restores this on the way out, including on failure -- so a
    failure here can also mean an interrupted build left the tree dirty.
    """
    assert manifest()["version"] == "0.0.0", (
        "blender_manifest.toml should carry the 0.0.0 placeholder; run "
        "scripts/set_version.py --reset"
    )


def test_pyproject_does_not_restate_the_version():
    with (REPO / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"].get("dynamic", [])


def test_set_version_maps_tags_to_manifest_versions():
    """Tag in, manifest version out -- including the forms CI will hand it."""
    for tag, expected in [
        ("1.7.1", "1.7.1"),
        ("v1.7.1", "1.7.1"),         # a "v" prefix is tolerated, not required
        ("2.0.0-rc.1", "2.0.0-rc.1"),
    ]:
        out = subprocess.run(
            ["python3", str(REPO / "scripts" / "set_version.py"), "--print", tag],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        assert out.stdout.strip() == expected, f"{tag} -> {out.stdout.strip()}"


def test_set_version_refuses_what_blender_would_reject():
    """The tag gate lives here now, so it has to actually reject.

    Each of these fails Blender's own RE_MANIFEST_SEMVER, so letting one
    through would build a zip that cannot install.
    """
    for tag in ("1.7", "01.2.3", "1.7.1-final!", "release-two", ""):
        out = subprocess.run(
            ["python3", str(REPO / "scripts" / "set_version.py"), "--print", tag],
            cwd=REPO, capture_output=True, text=True,
        )
        assert out.returncode != 0, f"{tag!r} was accepted and should not be"


def test_set_version_round_trips_without_disturbing_anything_else():
    """Writing a version must touch the version line and nothing else."""
    before = MANIFEST.read_text()
    try:
        subprocess.run(
            ["python3", str(REPO / "scripts" / "set_version.py"), "9.8.7"],
            cwd=REPO, capture_output=True, text=True, check=True,
        )
        after = MANIFEST.read_text()
        assert 'version = "9.8.7"' in after
        assert after.replace('version = "9.8.7"', 'version = "0.0.0"') == before
    finally:
        MANIFEST.write_text(before)
    assert MANIFEST.read_text() == before
