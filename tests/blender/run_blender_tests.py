"""Headless Blender integration-test entry point.

Run with:
    blender --background --factory-startup --python tests/blender/run_blender_tests.py

Starts coverage inside the Blender process (data_suffix so `coverage combine`
can merge it with the pytest run), registers the add-on from this checkout,
runs the scenarios in test_operators.py, and exits non-zero on failure.

Pass substrings after `--` to run only the tests whose names contain one of
them, which is how scripts/mutate.py keeps a mutation run down to seconds:

    blender --background --factory-startup --python tests/blender/run_blender_tests.py \
        -- merge_indices matframes
"""

import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# make the project venv's packages (coverage, pytest) importable; system
# coverage works too since Blender links the system CPython on Arch
for candidate in sorted(REPO.glob(".venv/lib/python*/site-packages")):
    sys.path.insert(0, str(candidate))

cov = None
try:
    import coverage

    cov = coverage.Coverage(
        config_file=str(REPO / "pyproject.toml"),
        data_file=str(REPO / ".coverage"),
        data_suffix="blender",
    )
    cov.start()
except ImportError:
    print("coverage not available inside Blender; running without it")

# import the checkout as the io_scene_dts package and register the add-on
sys.path.insert(0, str(REPO.parent))
import io_scene_dts  # noqa: E402

io_scene_dts.register()

sys.path.insert(0, str(REPO / "tests" / "blender"))
import test_authoring  # noqa: E402
import test_envmap  # noqa: E402
import test_operators  # noqa: E402

# test_operators round-trips real files; test_authoring builds shapes from
# nothing, which is the only check that a feature can actually be *created*
# rather than merely preserved (see CLAUDE.md).  test_envmap is the odd one:
# it renders, because the environment map is a shader and reading a file back
# cannot tell you whether a shader is right.
MODULES = (test_operators, test_authoring, test_envmap)

patterns = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

failures = []
tests = [
    (f"{module.__name__.removeprefix('test_')}:{name}", fn)
    for module in MODULES
    for name, fn in vars(module).items()
    if name.startswith("test_")
    and callable(fn)
    and getattr(fn, "__module__", None) == module.__name__
    and (not patterns or any(p in name for p in patterns))
]
if patterns and not tests:
    print(f"no test matches {patterns}")
    sys.exit(2)
def _result(text):
    """A result line, always starting a line of its own.

    Blender writes operator reports and Python tracebacks straight to the
    process's stdout and stderr, outside this script's buffer.  A `print` that
    lands while one of those is mid-line gets glued onto its end -- and
    `scripts/mutate.py` matches on lines *starting* with PASS/FAIL, so a
    swallowed FAIL reads as "the test did not run at all" and the mutation
    reports itself uncaught.  That is the harness certifying itself healthy,
    which is the one thing it must never do.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    print(f"\n{text}", flush=True)


for name, fn in tests:
    try:
        fn()
        _result(f"PASS {name}")
    except Exception:
        traceback.print_exc()
        _result(f"FAIL {name}")
        failures.append(name)

io_scene_dts.unregister()

if cov is not None:
    cov.stop()
    cov.save()

print(f"\n{len(tests) - len(failures)}/{len(tests)} blender integration tests passed")
if failures:
    print("failed:", ", ".join(failures))
    sys.exit(1)
sys.exit(0)
