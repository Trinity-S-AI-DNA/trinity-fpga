#!/usr/bin/env python3
"""Plants every case tools/check_setup_zig_order.py claims to decide, and
checks its exit code: 0 clean, 1 a checkout over a restored cache, 2 cannot
tell.

Each case writes one workflow into an empty directory and runs the checker's
main() on it. Case 1 is brain-ci's layout before #890.

Exit 0 when every case gives its expected code, 1 otherwise.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("csz", HERE / "check_setup_zig_order.py")
csz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csz)

H = "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
ZIG = "      - uses: mlugg/setup-zig@v2\n        with:\n          version: 0.16.0\n"
CO = "      - uses: actions/checkout@v4\n"
BUILD = "      - run: zig build\n"

# (name, expected rc, workflow text; None writes no workflow at all)
CASES: list[tuple[str, int, str | None]] = [
    ("setup-zig, then checkout (brain-ci before #890)", 1, H + ZIG + CO + BUILD),
    ("checkout, then setup-zig (#890)", 0, H + CO + ZIG + BUILD),
    ("setup-zig and no checkout", 0, H + ZIG + BUILD),
    ("use-cache: false, then checkout", 0,
     H + "      - uses: mlugg/setup-zig@v2\n        with:\n          use-cache: false\n" + CO),
    ("use-cache: 'false' as a string", 0,
     H + "      - uses: mlugg/setup-zig@v2\n        with:\n          use-cache: 'false'\n" + CO),
    ("use-cache from an expression counts as caching", 1,
     H + "      - uses: mlugg/setup-zig@v2\n        with:\n          use-cache: ${{ matrix.c }}\n" + CO),
    ("checkout into a subdirectory", 0,
     H + ZIG + "      - uses: actions/checkout@v4\n        with:\n          path: sub\n"),
    ("checkout with path: .", 1,
     H + ZIG + "      - uses: actions/checkout@v4\n        with:\n          path: .\n"),
    ("checkout with path: ${{ github.workspace }}", 1,
     H + ZIG + "      - uses: actions/checkout@v4\n        with:\n          path: ${{ github.workspace }}\n"),
    ("a pinned SHA is still setup-zig", 1,
     H + "      - uses: mlugg/setup-zig@8d6198c65fb0feaa111df26e6b467fea8345e46f\n" + CO),
    ("a second checkout after setup-zig", 1, H + CO + ZIG + CO),
    ("upper case action name", 1, H + "      - uses: MLugg/Setup-Zig@v2\n" + CO),
    ("the second of two jobs", 1,
     H + CO + ZIG + "  k:\n    runs-on: ubuntu-latest\n    steps:\n" + ZIG + CO),
    ("a reusable-workflow job has no steps", 0,
     H + CO + ZIG + "  k:\n    uses: ./.github/workflows/other.yml\n"),
    ("no job uses setup-zig: nothing to guard", 2, H + CO + BUILD),
    ("a workflow that does not parse", 2, "on: push\njobs: [\n"),
    ("no workflow at all", 2, None),
]


def run_case(text: str | None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        wf_dir = pathlib.Path(d)
        has_text = text is not None
        if has_text:
            (wf_dir / "t.yml").write_text(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = csz.main(["--workflows", str(wf_dir)])
        return rc, out.getvalue()


def main() -> int:
    bad = 0
    for name, want, text in CASES:
        rc, out = run_case(text)
        ok = rc == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} rc {rc} (want {want})  {name}")
        show = not ok
        if show:
            print("     " + out.replace("\n", "\n     ").rstrip())
    print(f"{len(CASES) - bad}/{len(CASES)} planted cases decided as claimed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
