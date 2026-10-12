#!/usr/bin/env python3
"""Plants every case tools/check_concurrency_groups.py claims to decide, and
checks its exit code: 0 clean, 1 a defect, 2 cannot tell.

"This turns red when X" is a claim to test by planting X (#819). Each case
writes its workflows into an empty tree and runs the checker's main() on it.
The cases began as the fixtures of a second, independent reader of the same
rules (a line-based one, in the loop that measured #869); on this repository's
122 workflows the two give the same class to every file, on main (102
UNGROUPED) and on #870 (none). Three of those fixtures exist because a
mutation run of the line reader let a defect through: a trigger list at its
key's indent (`on:` then `- push`), a `branches:` list the same way, and a
comment shallower than the triggers.

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
spec = importlib.util.spec_from_file_location("ccg", HERE / "check_concurrency_groups.py")
ccg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccg)

# The group #870 gives every push workflow: one per file, a newer push or PR
# commit replaces the pending run, a dispatch gets a group of its own.
G = ("concurrency:\n"
     "  group: ${{ github.workflow_ref }}-${{ github.event_name == 'workflow_dispatch'"
     " && github.run_id || github.event_name }}\n"
     "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n")
J = "jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n"


def one(text: str) -> list[tuple[str, str]]:
    return [("a.yml", text)]


# (name, expected rc, [(file, text)], text the output must contain)
CASES: list[tuple[str, int, list[tuple[str, str]], str]] = [
    ("push to main, no group", 1, one("on:\n  push:\n    branches: [main]\n" + J), "UNGROUPED"),
    ("push and dispatch, the #870 group", 0, one("on:\n  push:\n  workflow_dispatch:\n" + G + J), "OK"),
    ("a group without run_id under dispatch fails", 1,
     one("on:\n  workflow_dispatch: {}\n  push:\n    branches: [main, master]\n"
         "concurrency:\n  group: id-${{ github.ref }}\n  cancel-in-progress: true\n" + J),
     "push cancels a running dispatch"),
    ("... and without cancel-in-progress it replaces a pending one", 1,
     one("on:\n  workflow_dispatch:\n  push:\nconcurrency:\n  group: id-${{ github.ref }}\n" + J),
     "replaces a pending dispatch"),
    ("... and the failure names the class", 1,
     one("on:\n  workflow_dispatch:\n  push:\nconcurrency:\n  group: id-${{ github.ref }}\n" + J),
     "FAIL DISPATCH_CANCELS"),
    ("the same group without dispatch is fine: no hand run to lose", 0,
     one("on:\n  push:\nconcurrency:\n  group: id-${{ github.ref }}\n  cancel-in-progress: true\n" + J),
     "OK"),
    ("dispatch only", 0, one("on:\n  workflow_dispatch:\n" + J), "OK"),
    ("pull_request only", 0, one("on: pull_request\n" + J), "OK"),
    ("push to another branch", 0, one("on:\n  push:\n    branches:\n      - dev\n" + J), "OK"),
    ("tags only: a branch push never starts it", 0, one("on:\n  push:\n    tags: ['v*']\n" + J), "OK"),
    ("tags-ignore only, the same", 0, one("on:\n  push:\n    tags-ignore: ['v*']\n" + J), "OK"),
    ("tags and branches: main again", 1, one("on:\n  push:\n    tags: ['v*']\n    branches: [main]\n" + J),
     "UNGROUPED"),
    ("paths only: a rarer run, not a single one", 1, one("on:\n  push:\n    paths: ['src/**']\n" + J),
     "UNGROUPED"),
    ("inline list of events", 1, one("on: [push, pull_request]\n" + J), "UNGROUPED"),
    ("scalar event", 1, one("on: push\n" + J), "UNGROUPED"),
    ("quoted on key", 1, one('"on":\n  push:\n    branches:\n      - "main"\n' + J), "UNGROUPED"),
    ("events as a list at the key's indent", 1, one("on:\n- push\n- workflow_dispatch\n" + J), "UNGROUPED"),
    ("branches as a list at the key's indent", 1, one("on:\n  push:\n    branches:\n    - main\n" + J),
     "UNGROUPED"),
    ("a comment shallower than the triggers", 1, one("on:\n  # a note\n    push:\n" + J), "UNGROUPED"),
    ("a commented-out group is no group", 1, one("on:\n  push:\n# concurrency:\n#   group: x\n" + J),
     "UNGROUPED"),
    ("a job's group does not stop the run queuing", 1,
     one("on:\n  push:\njobs:\n  x:\n    runs-on: ubuntu-latest\n    concurrency:\n      group: j\n"
         "    steps:\n      - run: true\n"), "UNGROUPED"),
    ("`concurrency:` with no group", 1, one("on: push\nconcurrency:\n  cancel-in-progress: true\n" + J),
     "without a group"),
    ("an empty `concurrency:`", 1, one("on: push\nconcurrency:\n" + J), "without a group"),
    ("the group as a plain string", 0, one("name: A\non: push\nconcurrency: ${{ github.workflow }}\n" + J), "OK"),
    ("branches as one string", 1, one("on:\n  push:\n    branches: main\n" + J), "UNGROUPED"),
    ("`**` reaches main", 1, one("on:\n  push:\n    branches: ['**']\n" + J), "UNGROUPED"),
    ("`*` reaches main", 1, one("on:\n  push:\n    branches: ['*']\n" + J), "UNGROUPED"),
    ("`release/*` does not", 0, one("on:\n  push:\n    branches: ['release/*']\n" + J), "OK"),
    ("`!main` after `**` excludes it", 0, one("on:\n  push:\n    branches: ['**', '!main']\n" + J), "OK"),
    ("`main` after `!main` brings it back: the last match decides", 1,
     one("on:\n  push:\n    branches: ['!main', 'main']\n" + J), "UNGROUPED"),
    ("`+` repeats the character before it, not any character", 1,
     one("on:\n  push:\n    branches: ['mai+n']\n" + J), "UNGROUPED"),
    ("`?` makes the character before it optional", 1,
     one("on:\n  push:\n    branches: ['mainn?']\n" + J), "UNGROUPED"),
    ("`ma?n` is `mn` or `man`, not `ma`, any character, `n`", 0,
     one("on:\n  push:\n    branches: ['ma?n']\n" + J), "OK"),
    ("a character set", 1, one("on:\n  push:\n    branches: ['[lm]ain']\n" + J), "UNGROUPED"),
    ("branches-ignore: main", 0, one("on:\n  push:\n    branches-ignore: [main]\n" + J), "OK"),
    ("branches-ignore of other branches reaches main", 1,
     one("on:\n  push:\n    branches-ignore: ['feat/**']\n" + J), "UNGROUPED"),
    ("an alias for the push filter is the node it names", 1,
     one("on:\n  pull_request: &f\n    branches: [main]\n  push: *f\n" + J), "UNGROUPED"),
    ("called, no group: the caller's group covers it", 0, one("on:\n  push:\n  workflow_call:\n" + J), "OK"),
    ("called, with a group: the caller's own group", 1, one("on:\n  push:\n  workflow_call:\n" + G + J),
     "BAD_REUSABLE"),
    ("called only, with a group: the same", 1, one("on:\n  workflow_call:\n" + G + J), "BAD_REUSABLE"),
    ("a .yaml file is read too", 1, [("a.yaml", "on: push\n" + J)], "a.yaml"),
    ("two files, one name, a group keyed on the name", 1,
     [("a.yml", "name: Same\non: push\nconcurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n" + J),
      ("b.yml", "name: Same\non: push\nconcurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n" + J)],
     "SHARED_KEY"),
    ("... and spacing inside ${{ }} does not separate them", 1,
     [("a.yml", "name: Same\non: push\nconcurrency: ${{github.workflow}}\n" + J),
      ("b.yml", "name: Same\non: push\nconcurrency: ${{ github.workflow }}\n" + J)], "SHARED_KEY"),
    ("two files, one name, the #870 group keyed on the file", 0,
     [("a.yml", "name: Same\non: push\n" + G + J), ("b.yml", "name: Same\non: push\n" + G + J)], "OK"),
    ("two names, a group keyed on the name", 0,
     [("a.yml", "name: One\non: push\nconcurrency: ${{ github.workflow }}\n" + J),
      ("b.yml", "name: Two\non: push\nconcurrency: ${{ github.workflow }}\n" + J)], "OK"),
    ("no name: github.workflow is the path, so two files differ", 0,
     [("a.yml", "on: push\nconcurrency: ${{ github.workflow }}\n" + J),
      ("b.yml", "on: push\nconcurrency: ${{ github.workflow }}\n" + J)], "OK"),
    ("one literal group in two files", 1,
     [("a.yml", "on: push\nconcurrency:\n  group: fixed\n" + J),
      ("b.yml", "on: push\nconcurrency:\n  group: fixed\n" + J)], "SHARED_KEY"),
    ("... in two pull_request-only files too", 1,
     [("a.yml", "on: pull_request\nconcurrency: fixed\n" + J),
      ("b.yml", "on: pull_request\nconcurrency: fixed\n" + J)], "SHARED_KEY"),
    ("a defect beside an unreadable file is still a defect", 1,
     [("a.yml", "on: push\n" + J), ("b.yml", "on: [push\n")], "UNGROUPED"),
    ("not YAML", 2, one("on: [push\n" + J), "not readable as YAML"),
    ("a key given twice", 2, one("on: push\non: pull_request\n" + G + J), "given twice"),
    ("a merge key", 2, one("x: &x\n  push:\non:\n  <<: *x\n" + G + J), "merge key"),
    ("no `on:`", 2, one(J), "no top-level `on:`"),
    ("an empty file", 2, one(""), "empty file"),
    ("branches and branches-ignore together", 2,
     one("on:\n  push:\n    branches: [main]\n    branches-ignore: [dev]\n" + J), "both"),
    ("a `[` with no `]`", 2, one("on:\n  push:\n    branches: ['[main']\n" + J), "no `]`"),
]


def run_case(files: list[tuple[str, str]], make_dir: bool = True) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        wf = root / ".github" / "workflows"
        if make_dir:
            wf.mkdir(parents=True)
        for name, text in files:
            (wf / name).write_text(text)
        ccg.ROOT, ccg.WORKFLOWS = root, wf
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ccg.main()
        return rc, out.getvalue()


def check(label: str, want: int, rc: int, out: str, needle: str) -> bool:
    good = rc == want and needle in out
    if good:
        print(f"ok   {label} (rc {rc})")
    else:
        print(f"BAD  {label}: want rc {want} and {needle!r}, got rc {rc}")
        print("     " + out.strip().replace("\n", "\n     "))
    return good


def main() -> int:
    bad = 0
    for name, want, files, needle in CASES:
        rc, out = run_case(files)
        passed = check(name, want, rc, out, needle)
        if not passed:
            bad += 1

    extra = [
        ("no workflows directory = cannot tell", 2, run_case([], make_dir=False), "no workflows"),
        ("an empty workflows directory = cannot tell", 2, run_case([]), "no workflows"),
    ]
    # Without PyYAML nothing is read, and nothing read is not clean.
    saved = sys.modules.get("yaml")
    sys.modules["yaml"] = None  # `import yaml` now raises ImportError
    try:
        extra.append(("no PyYAML = cannot tell", 2, run_case(one("on: push\n" + J)), "PyYAML"))
    finally:
        if saved is None:
            del sys.modules["yaml"]
        else:
            sys.modules["yaml"] = saved
    for label, want, (rc, out), needle in extra:
        passed = check(label, want, rc, out, needle)
        if not passed:
            bad += 1

    total = len(CASES) + len(extra)
    print(f"\n{total - bad} of {total} cases as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
