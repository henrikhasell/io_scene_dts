# Working in this repo

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
```

Both must pass before a commit.  A round-trip test that passes on its first
run deserves a mutation check — break the export path deliberately and confirm
the test fails — because a test that reads back what it never wrote will pass
for the wrong reason.
