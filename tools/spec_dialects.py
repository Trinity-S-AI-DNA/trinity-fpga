#!/usr/bin/env python3
"""Which language is each specs/**/*.tri and *.vibee file written in?

Spec: specs/spec_dialects.t27 (issue #792).

Four languages shared the .tri extension. #793 taught vibee_gen to refuse three
of them by name and left every file where it was. This prints the census and
fails on the two cases #792 decided cannot stay:

  * generated output living in specs/ (vibee_gen's `DO NOT EDIT` banner in a
    file that is not a VIBEE spec);
  * a Markdown document carrying a .tri or .vibee extension.

TOML, t27-style sketches and the comment-led fall-through are REPORTED, not
failed on: each has a decided fate in the spec, and vibee_gen already refuses
the ones it cannot read.

The classifier is vibee_gen's foreignDialect (src/vibeec/vibee_gen.zig) in the
same order, so this census and the generator cannot disagree about a file.
Markdown is checked before t27 because prose can embed code; an ATX `## `
heading is required because a bare `#` is also VIBEE's comment character.

    python3 tools/spec_dialects.py            census, exit 1 on a violation
    python3 tools/spec_dialects.py --list     also print every non-YAML path
    python3 tools/spec_dialects.py --root DIR run against another tree
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys

BANNER = "DO NOT EDIT - This file is auto-generated"
T27_MARKERS = ("\nspec ", "\ninvariant ", "\nnumericformat ", "\npub fn ")
ORDER = ("yaml", "markdown", "toml", "t27_style", "comment_led", "near_empty", "other")
FAILING = ("markdown",)


def has_vibee_name(s: str) -> bool:
    return s.startswith("name:") or "\nname:" in s


def classify(s: str) -> str:
    if has_vibee_name(s):
        return "yaml"
    if s.startswith("## ") or "\n## " in s:
        return "markdown"
    if (s.startswith("[") or "\n[" in s) and " = " in s:
        return "toml"
    if s.startswith("spec ") or any(m in s for m in T27_MARKERS):
        return "t27_style"
    if s.lstrip().startswith("//"):
        return "comment_led"
    if sum(1 for line in s.splitlines() if line.strip()) < 3:
        return "near_empty"
    return "other"


def scan(root: pathlib.Path):
    specs = root / "specs"
    files = sorted(p for p in specs.rglob("*")
                   if p.is_file() and p.suffix in (".tri", ".vibee"))
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    generated: list[str] = []
    for p in files:
        # Bytes, not text: one spec in this tree is not valid UTF-8, and a
        # checker that dies on it reports nothing about the other thousand.
        s = p.read_bytes().decode("utf-8", errors="replace")
        rel = p.relative_to(root).as_posix()
        cls = classify(s)
        buckets[cls].append(rel)
        # A VIBEE spec is a source by definition, even when it quotes the
        # banner: specs/tri/codegen/core_emitter.tri specifies the emitter that
        # WRITES it, and flagging that spec would be a false refusal.
        if BANNER in s and cls != "yaml":
            generated.append(rel)
    return files, buckets, generated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    root = pathlib.Path(a.root)

    files, buckets, generated = scan(root)
    if not files:
        print(f"FAIL: no .tri/.vibee files under {root / 'specs'} -- "
              "a census over nothing is not a pass")
        return 1

    print(f"specs/**/*.tri + *.vibee: {len(files)}")
    for k in ORDER:
        print(f"  {k:<12} {len(buckets.get(k, [])):>5}")
    print(f"  {'generated':<12} {len(generated):>5}  (vibee_gen output banner)")

    if a.list:
        for k in ORDER[1:]:
            for rel in buckets.get(k, []):
                print(f"  {k}: {rel}")

    bad = 0
    for rel in generated:
        print(f"FAIL: {rel} is generated output (`{BANNER}`); "
              "generated files do not belong in specs/")
        bad += 1
    for k in FAILING:
        for rel in buckets.get(k, []):
            print(f"FAIL: {rel} is {k}, not a VIBEE spec; give it the .md extension")
            bad += 1
    if bad:
        print(f"\n{bad} file(s) violate specs/spec_dialects.t27")
        return 1
    print("\nOK: no generated output and no Markdown under .tri/.vibee in specs/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
