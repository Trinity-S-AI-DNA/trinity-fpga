#!/usr/bin/env python3
"""Self-test for tools/spec_dialects.py: the test_cases of specs/spec_dialects.t27.

Each case is a one-file tree, so a case can only pass or fail on its own file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("spec_dialects", HERE / "spec_dialects.py")
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

# (name, file text or None for an empty tree, expected class or None, expected rc)
CASES = [
    ("VIBEE YAML", "name: x\nversion: 1\n", "yaml", 0),
    ("Markdown with ## heading", "# T\n\n## Module\ntri.x\n", "markdown", 1),
    ("Markdown embedding pub fn", "## A\n```zig\npub fn f() void {}\n```\n", "markdown", 1),
    ("a VIBEE comment `# Adagrad`", "# Adagrad\nname: adagrad\n", "yaml", 0),
    ("TOML cell", "[spec]\nname = \"x\"\n", "toml", 0),
    ("t27-style sketch", "// c\nspec x {\n}\n", "t27_style", 0),
    ("generator banner", "// DO NOT EDIT - This file is auto-generated\nconst a = 1;\n", None, 1),
    ("yaml spec quoting the banner", "name: e\n# DO NOT EDIT - This file is auto-generated\n", "yaml", 0),
    ("empty tree", None, None, 1),
]


def run(text: str | None) -> int:
    with tempfile.TemporaryDirectory() as d:
        specs = pathlib.Path(d) / "specs"
        specs.mkdir()
        if text is not None:
            (specs / "case.tri").write_text(text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return sd.main(["--root", d])


def main() -> int:
    bad = 0
    for name, text, cls, rc in CASES:
        got_cls = sd.classify(text) if (cls is not None and text is not None) else None
        got_rc = run(text)
        ok = got_rc == rc and got_cls == cls
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: rc={got_rc} (want {rc})"
              + (f", class={got_cls} (want {cls})" if cls is not None else ""))

    # Invalid UTF-8 must not kill the census: the tree holds one such file.
    with tempfile.TemporaryDirectory() as d:
        specs = pathlib.Path(d) / "specs"
        specs.mkdir()
        (specs / "bad.vibee").write_bytes(b"name: x\n\xff\xfe\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sd.main(["--root", d])
        ok = rc == 0
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} invalid UTF-8 is read, not fatal: rc={rc} (want 0)")

    print(f"\n{len(CASES) + 1 - bad} of {len(CASES) + 1} cases pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
