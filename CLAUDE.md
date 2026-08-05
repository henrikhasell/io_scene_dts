# Working in this repo

## What "fully implemented" means

A DTS feature is fully implemented when all four of these hold:

1. **Imported** — reading a `.dts` that uses it produces something in Blender.
2. **Edited** — the user can change it in Blender and the change reaches the
   exported file.
3. **Created** — the user can produce it in a *fresh scene*, with no import
   anywhere in the history.
4. **Exported** — it is written back to `.dts` in a form the engine reads.

Decals are the worked example.  They arrive as a subset of the target's faces
plus a projector empty, are authored by moving that empty, and are turned back
into `TSDecalMesh` indices and texgen planes on the way out.  Nothing about the
Blender representation resembles the file's, and that is the point: the user
edits the thing that makes sense in Blender.

**(3) is the one that gets skipped, and it is the one that matters.**  An
import→edit→export test can pass while the exporter is leaning on data that
only exists because something was imported — a stored table, an ID property the
importer happened to write, a payload.  The feature looks supported and cannot
actually be authored.  Billboards were exactly this: the flags round-tripped
perfectly, and a mesh that was not already a billboard had no property for a
checkbox to bind to, so one could be cleared and never set.

`tests/blender/test_authoring.py` is where (3) lives.  Every test in it builds a
shape from nothing, exports, and reads the feature back out of the file.  It
never calls the importer.  **Add to it whenever you touch a feature** — a
round-trip test is not a substitute, and passing one is not evidence of (3).

## Keep UNSUPPORTED.md current

`UNSUPPORTED.md` is the inventory of what this add-on does *not* do, sorted
into five tiers (refused / opaque / blind / dropped / frozen).  It is only
worth having if it is true, so **treat it as part of the change, not as
documentation to write later.**

Update it in the same commit whenever a change:

- **adds or removes a refusal** — a new `ExportError`, a version gate, a limit;
- **moves a feature between tiers** — previewing something that was blind,
  preserving something that was dropped, freezing something that was editable;
- **changes a cited line** enough that the `file:line` reference no longer
  points at the deciding line.

Two rules for the content:

1. **Read the code, don't recall it.**  Every claim carries a `file:line`, and
   those references are the reason the file is trustworthy.  Verify them —
   `sed -n "${line}p" "$file"` over the whole list takes seconds and catches
   drift from unrelated edits.
2. **Say which tier and why.**  "Not supported" is useless to a reader deciding
   whether to attempt something.  Whether it errors, silently drops data, or
   round-trips invisibly is the whole question.

The Coverage section at the bottom names the test count and what is and is not
tested; it goes stale quietly, so check it when adding tests.

## Tests

```sh
.venv/bin/python -m pytest -m "not corpus" -q                                 # fast unit loop
blender --background --factory-startup --python tests/blender/run_blender_tests.py   # integration
scripts/mutate.py                    # break the export path, check a test notices
scripts/check_citations.py           # every file:line in UNSUPPORTED.md still lands
```

The first two must pass before a commit.  Pass `-- <substring>` to the Blender
runner to run a subset.

A round-trip test that passes on its first run deserves a mutation check —
break the export path deliberately and confirm the test fails — because a test
that reads back what it never wrote will pass for the wrong reason.  Add the
mutation to `scripts/mutate.py` so it keeps being checked; it has caught its own
drift more than once.
