#!/usr/bin/env python3
"""Check every file:line citation in UNSUPPORTED.md still points somewhere real.

CLAUDE.md: "Every claim carries a file:line, and those references are the reason
the file is trustworthy.  Verify them."  Doing that by eye is exactly the kind
of job that gets skipped, and a citation that has slid four lines down into a
blank line or a closing bracket is worse than none -- it still looks checked.

A citation is reported when the line it names is blank, a comment, or a bare
piece of syntax.  That does not prove the reference is still *apt* -- only a
reader can say that -- but it catches the drift that unrelated edits cause,
which is the common case.

Usage:
    scripts/check_citations.py [--doc UNSUPPORTED.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CITATION = re.compile(r"`((?:[\w./-]+/)?[\w_]+\.py):(\d+(?:[,-]\d+)*)`")

# lines that carry no claim: a citation landing on one has drifted
EMPTY = {"", "}", ")", "]", "):", "return", "continue", "pass", "else:", "try:", '"""'}


def suspicious(text: str) -> str | None:
    stripped = text.strip()
    if stripped in EMPTY:
        return "blank or bare syntax"
    if stripped.startswith("#"):
        return "a comment"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", default="UNSUPPORTED.md")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    doc = REPO / args.doc
    text = doc.read_text()
    sources: dict[str, list[str]] = {}
    problems = 0
    total = 0

    for match in CITATION.finditer(text):
        name = match.group(1)
        for part in match.group(2).replace("-", ",").split(","):
            total += 1
            line = int(part)
            path = REPO / name
            if not path.exists():
                print(f"{name}:{line}  MISSING FILE")
                problems += 1
                continue
            if name not in sources:
                sources[name] = path.read_text().splitlines()
            lines = sources[name]
            if not 1 <= line <= len(lines):
                print(f"{name}:{line}  out of range (file has {len(lines)} lines)")
                problems += 1
                continue
            why = suspicious(lines[line - 1])
            if why:
                print(f"{name}:{line}  points at {why}: {lines[line - 1].strip()!r}")
                problems += 1
            elif not args.quiet:
                print(f"  ok  {name}:{line}  {lines[line - 1].strip()[:72]}")

    print(f"\n{total - problems}/{total} citations land on a line that says something")
    if problems:
        print("Fix the line numbers, or the claim if the code no longer supports it.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
