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

## What may be imported at all

**Do not preserve what the user cannot reconstruct.**  The test for whether
something may come in is not "would it be lost otherwise" — it is:

> Can it be imported in a form the user could also have *created themselves*?
> If not, it should not be imported.

This is (3) pointed at the importer.  A feature that arrives in a shape no
fresh scene could produce is not supported, it is stored, and storing it is
worse than dropping it: it looks supported, it cannot be edited, and it is a
second source of truth that will eventually disagree with the first.  Drop it
and say so in `UNSUPPORTED.md` instead.

**No opaque JSON blobs and no pickling.**  Both were tried and both were
removed.  Mesh data that Blender could not rebuild used to ride through a
`.blend` as a base64'd `pickle.dumps` of a `dtslib.Mesh`, replayed verbatim on
export — invisible, uneditable, and `pickle.loads` on a path fed by whatever
file the user opens.  Everything it carried is derived on export now: vertex
sharing by `mapping/vertex_pool.py`, cluster trees by `dtslib/sorted_build.py`,
strip packing not at all (measured at ×1.00).  `tests/test_no_pickle.py` keeps
it that way.  The name table, detail table, material order, IFL entries, ground
frames, triggers and node rest transforms were JSON strings for the same
reason; they are typed collections with UILists now (`props/`), because those a
user *can* author.  This is why the **opaque** tier of `UNSUPPORTED.md` is
empty, and it should stay empty.

The corollary cuts the other way too: **if the exporter can compute it, do not
import it.**  A stored copy of something that gets recomputed anyway is the
same two-sources-of-truth bug in a smaller package — the packed `dts_flags`
word beside the named flag props was exactly this, and so is any `dts_*`
property whose value export overwrites before reading.

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

Three rules for the content:

1. **Read the code, don't recall it.**  Every claim carries a `file:line`, and
   those references are the reason the file is trustworthy.  Verify them —
   `sed -n "${line}p" "$file"` over the whole list takes seconds and catches
   drift from unrelated edits.  `scripts/check_citations.py` only proves a line
   is not blank; that a line says the *right* thing is still yours to check.
2. **Say which tier and why.**  "Not supported" is useless to a reader deciding
   whether to attempt something.  Whether it errors, silently drops data, or
   round-trips invisibly is the whole question.
3. **A limit of the format is not an unsupported feature.**  A tier is work
   this add-on could do and has not.  If a `.dsq` has no field for object
   states, or `TSIntegerSet` has no bit for a 193rd node, that is the shape of
   the problem and no amount of work here would change it — the same goes for
   what Blender cannot represent and what a lossy texture format has already
   discarded.  Those belong in §7, which exists so the tiers do not overstate
   what is left to do.  The *behaviour* around such a limit can still be a
   tier: that a `.dsq` cannot carry object states is §7, that the add-on drops
   them without warning is §6.

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
