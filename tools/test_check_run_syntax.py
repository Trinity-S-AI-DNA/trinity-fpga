#!/usr/bin/env python3
"""Self-test for tools/check_run_syntax.py: every case is a one-file tree."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("crs", HERE / "check_run_syntax.py")
crs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crs)

H = "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n"


# (name, expected rc, workflow text)
CASES: list[tuple[str, int, str]] = [
    ("a clean step", 0, H + "    steps:\n      - run: echo ok\n"),
    ("an apostrophe in a comment inside bash -c '...'", 1, H +
     "    steps:\n      - run: |\n          docker run img bash -c '\n"
     "            # this does not matter, it doesn't\n            make\n          '\n"),
    ("'\\'' outside single quotes leaves one open", 1, H +
     "    steps:\n      - run: |\n          norm() { grep -oE '\\''A.+'\\'' \"$1\"; }\n"),
    ("'\\'' inside single quotes is a quote", 0, H +
     "    steps:\n      - run: |\n          bash -c 'grep -oE '\\''A.+'\\'' x'\n"),
    ("an unclosed if", 1, H + "    steps:\n      - run: |\n          if true; then\n            echo x\n"),
    # Inside a single-quoted -c script: one string to the outer bash -n.
    ("an unclosed if inside bash -c '...'", 1, H +
     "    steps:\n      - run: |\n          docker run img bash -c '\n"
     "            if true; then\n              make\n          '\n"),
    ("a clean bash -c '...' script", 0, H +
     "    steps:\n      - run: |\n          docker run img bash -c '\n"
     "            if true; then\n              make\n            fi\n          '\n"),
    ("'\"$X\"' splices an outer value as one word", 0, H +
     "    steps:\n      - run: |\n          docker run img bash -c '\n"
     "            git checkout '\"$REV\"' && make /w/'\"$PART\"'.bin\n          '\n"),
    ("an error after a splice is still seen", 1, H +
     "    steps:\n      - run: |\n          docker run img bash -c '\n"
     "            git checkout '\"$REV\"'\n            if true; then make\n          '\n"),
    ("a bare '$X' splice is one word", 0, H +
     "    steps:\n      - run: |\n          bash -c 'echo '$X' '${Y}' done'\n"),
    ("sh -c '...' is checked with sh", 1, H +
     "    steps:\n      - run: |\n          docker run img sh -c 'cat <(echo x)'\n"),
    ("bash -lc '...' is checked", 1, H +
     "    steps:\n      - run: |\n          bash -lc 'if true; then echo x'\n"),
    ("bash -euo pipefail -c '...' is checked", 1, H +
     "    steps:\n      - run: |\n          bash -euo pipefail -c 'if true; then echo x'\n"),
    ("two scripts in one step: the second is checked too", 1, H +
     "    steps:\n      - run: |\n          bash -c 'echo ok'\n          bash -c 'if true; then echo x'\n"),
    ("a -c script glued to a word = cannot tell", 2, H +
     "    steps:\n      - run: |\n          bash -c 'echo x'y\n"),
    ("zsh -c '...' is not read", 0, H +
     "    steps:\n      - run: |\n          zsh -c 'if true; then'\n"),
    ("a double-quoted bash -c \"...\" is not read", 0, H +
     "    steps:\n      - run: |\n          bash -c \"if true; then\"\n"),
    ("${{ }} is left as written", 0, H +
     "    steps:\n      - run: echo \"${{ github.sha }}\"\n"),
    # `cat <(echo x)` parses in bash and not in sh (dash, or bash in POSIX
    # mode as macOS's /bin/sh), so these two tell the interpreters apart.
    ("shell: sh is checked with sh", 1, H +
     "    steps:\n      - shell: sh\n        run: cat <(echo x)\n"),
    ("shell: bash is checked with bash", 0, H +
     "    steps:\n      - shell: bash\n        run: cat <(echo x)\n"),
    ("no shell is bash", 0, H + "    steps:\n      - run: cat <(echo x)\n"),
    ("a container job with no shell must parse in sh too", 1, H +
     "    container: alpine:3\n    steps:\n      - run: cat <(echo x)\n"),
    ("a container job with no shell must parse in bash too", 1, H +
     "    container: alpine:3\n    steps:\n      - run: 'echo \"x'\n"),
    ("a container job with shell: bash is bash", 0, H +
     "    container: alpine:3\n    steps:\n      - shell: bash\n        run: cat <(echo x)\n"),
    ("a container job with a job default shell: bash is bash", 0, H +
     "    container: alpine:3\n    defaults:\n      run:\n        shell: bash\n"
     "    steps:\n      - run: cat <(echo x)\n"),
    ("shell: /usr/bin/env bash {0} is bash", 1, H +
     "    steps:\n      - shell: /usr/bin/env bash {0}\n        run: 'echo \"x'\n"),
    ("shell: env -i PATH=/bin sh {0} is sh", 1, H +
     "    steps:\n      - shell: env -i PATH=/bin sh {0}\n        run: cat <(echo x)\n"),
    ("shell: env -S 'sh -e {0}' is sh", 1, H +
     "    steps:\n      - shell: env -S 'sh -e {0}'\n        run: cat <(echo x)\n"),
    ("shell: env -S'sh -e {0}' (attached) is sh", 1, H +
     "    steps:\n      - shell: env -S'sh -e {0}'\n        run: cat <(echo x)\n"),
    ("shell: env --split-string='sh -e {0}' is sh", 1, H +
     "    steps:\n      - shell: env --split-string='sh -e {0}'\n        run: cat <(echo x)\n"),
    ("shell: env -u FOO sh {0} is sh, not FOO", 1, H +
     "    steps:\n      - shell: env -u FOO sh {0}\n        run: cat <(echo x)\n"),
    ("shell: env -C /tmp sh {0} is sh, not /tmp", 1, H +
     "    steps:\n      - shell: env -C /tmp sh {0}\n        run: cat <(echo x)\n"),
    ("an expression shell = cannot tell", 2, H +
     "    strategy:\n      matrix:\n        shell: [bash]\n"
     "    defaults:\n      run:\n        shell: ${{ matrix.shell }} {0}\n"
     "    steps:\n      - run: 'echo \"x'\n"),
    ("a runs-on expression that may pick windows = cannot tell", 2,
     "on: workflow_dispatch\njobs:\n  j:\n"
     "    runs-on: ${{ inputs.win && 'windows-latest' || 'ubuntu-latest' }}\n"
     "    steps:\n      - run: 'echo \"x'\n"),
    ("runs-on: ${{ matrix.os }} without windows is bash", 1,
     "on: push\njobs:\n  j:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n"
     "    runs-on: ${{ matrix.os }}\n"
     "    steps:\n      - run: 'echo \"x'\n"),
    ("a matrix that may pick macOS: bash 3.2 there", 1 if crs.bash_major() == 3 else 2,
     "on: push\njobs:\n  j:\n    strategy:\n      matrix:\n"
     "        os: [ubuntu-latest, macos-latest]\n"
     "    runs-on: ${{ matrix.os }}\n"
     "    steps:\n      - run: make 2>&1 |& tee build.log\n"),
    ("a matrix value naming macos with a fixed ubuntu runner is plain bash", 0,
     "on: push\njobs:\n  j:\n    strategy:\n      matrix:\n        target: [macos-cross]\n"
     "    runs-on: ubuntu-latest\n"
     "    steps:\n      - run: echo ok\n"),
    ("defaults that is not a mapping = cannot tell", 2, H +
     "    defaults: bash\n    steps:\n      - run: echo ok\n"),
    ("shell: env python {0} is skipped", 0, H +
     "    steps:\n      - shell: env python {0}\n        run: print('it doesn')\n"),
    ("a shell line with an open quote = cannot tell", 2, H +
     "    steps:\n      - shell: bash -c 'x {0}\n        run: echo ok\n"),
    ("shell: bash -eo pipefail {0} is bash", 1, H +
     "    steps:\n      - shell: bash -eo pipefail {0}\n        run: 'echo \"x'\n"),
    ("job defaults shell: python is skipped", 0, H +
     "    defaults:\n      run:\n        shell: python\n    steps:\n      - run: print('it doesn')\n"),
    ("workflow defaults shell: pwsh is skipped", 0,
     "on: push\ndefaults:\n  run:\n    shell: pwsh\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
     "    steps:\n      - run: Write-Host \"it's\n"),
    ("the step's shell beats the job's", 1, H +
     "    defaults:\n      run:\n        shell: python\n    steps:\n      - shell: bash\n        run: 'echo \"x'\n"),
    ("a windows runner with no shell is skipped", 0,
     "on: push\njobs:\n  j:\n    runs-on: windows-latest\n    steps:\n      - run: echo \"it's\n"),
    ("a step with no run is skipped", 0, H + "    steps:\n      - uses: actions/checkout@v4\n"),
    ("not YAML = cannot tell", 2, "on: [\n"),
    ("no jobs = cannot tell", 2, "on: push\n"),
    ("a step that is not a mapping = cannot tell", 2, H + "    steps:\n      - echo\n"),
]

# The two defects this gate was written for, as they stood on main at e6eac090.
#
# They are files here, not `git show e6eac090:<path>`. main was rewritten after
# that commit (the same tree is 78606c5d on today's main), so a fresh clone no
# longer has e6eac090: the read returned nothing and both cases went BAD on
# every PR that runs this gate, whatever the PR changed. A file in the tree
# does not depend on which history a clone has, or how deep it is.
#
# Each file is pinned to the blob id git gave that path at e6eac090. A fixture
# that is missing or has been edited is an empty input, rc 2, so the case goes
# BAD and says why -- it never quietly tests something else.
FIXTURES = HERE / "run_syntax_fixtures"
PIN_ERRORS: list[str] = []


def pinned(fname: str, blob: str) -> str:
    p = FIXTURES / fname
    try:
        data = p.read_bytes()
    except OSError:
        PIN_ERRORS.append(f"{fname}: missing")
        return ""
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if got != blob:
        PIN_ERRORS.append(f"{fname}: blob {got}, pinned {blob}")
        return ""
    return data.decode()


for name, fname, blob in (
        ("main's build-matrix.yml before the fix",
         "build-matrix.e6eac090.yml.txt", "9d07d6793905814b463d528b7d49d8b5fd2eed50"),
        ("main's iddr-golden-diff.yml before the fix",
         "iddr-golden-diff.e6eac090.yml.txt", "034f4e968cc93b07f6ad08ea17363c1e3e381027")):
    CASES.append((name, 1, pinned(fname, blob)))


def with_bash(major, f):
    """f() with the gate believing its bash is `major`; CannotTell -> a word."""
    saved, crs._BASH_MAJOR = crs._BASH_MAJOR, major
    try:
        return f()
    except crs.CannotTell:
        return "cannot tell"
    finally:
        crs._BASH_MAJOR = saved


# Direct answers where no script can tell the interpreters apart portably:
# on macOS /bin/sh is bash, so "must parse in bash too" cannot be shown by a
# script that only bash rejects. (name, got, want)
DIRECT = [
    ("a container job is checked with bash and sh",
     lambda: crs.shell_of({}, {"container": "alpine:3"}, {}), ("bash", "sh")),
    ("a plain job is checked with bash", lambda: crs.shell_of({}, {}, {}), ("bash",)),
    ("WINDOWS-latest is skipped (case-blind)",
     lambda: crs.shell_of({}, {"runs-on": "WINDOWS-latest"}, {}), ()),
    ("macOS-14 under a bash 5 gate is cannot tell",
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "macOS-14"}, {})), "cannot tell"),
    ("macos-14 under a bash 3 gate is checked with bash",
     lambda: with_bash(3, lambda: crs.shell_of({}, {"runs-on": "macos-14"}, {})), ("bash",)),
    ("macos with shell: sh under a bash 5 gate is cannot tell",
     lambda: with_bash(5, lambda: crs.shell_of({"shell": "sh {0}"}, {"runs-on": "macos-15"}, {})),
     "cannot tell"),
    ("a matrix naming macos under a fixed ubuntu runner is bash, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "ubuntu-latest", "strategy": {"matrix": {"t": ["macos-x"]}}}, {})),
     ("bash",)),
    ("runs-on ${{ matrix.os }} over a matrix naming macos, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["macos-15"]}}}, {})),
     "cannot tell"),
    ('runs-on ${{ inputs.os }} may be macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ inputs.os }}"}, {})), 'cannot tell'),
    ('runs-on ${{ vars.BUILD_RUNNER }} may be macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ vars.BUILD_RUNNER }}"}, {})), 'cannot tell'),
    ('a fromJSON matrix may be macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": "${{ fromJSON(needs.plan.outputs.m) }}"}}, {})), 'cannot tell'),
    ('a matrix value with an expression may be macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["${{ inputs.os }}"]}}}, {})), 'cannot tell'),
    ('matrix [macOS-15] in any case is macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["macOS-15"]}}}, {})), 'cannot tell'),
    ('matrix.os over a literal ubuntu-only matrix is bash, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["ubuntu-latest", "ubuntu-22.04"]}}}, {})), ('bash',)),
    ('matrix.os mixed with another lookup may be macOS, bash 5 gate',
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os || inputs.os }}", "strategy": {"matrix": {"os": ["ubuntu-latest"]}}}, {})), 'cannot tell'),
    ("runs-on ${{ matrix.os }} with no strategy written may be macOS, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": "${{ matrix.os }}"}, {})),
     "cannot tell"),
    ("a matrix lookup next to an input lookup may be macOS, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "${{ matrix.os }}${{ inputs.suffix }}",
              "strategy": {"matrix": {"os": ["ubuntu-latest"]}}}, {})),
     "cannot tell"),
    ("a label glued from two matrix keys may spell macos, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "${{ matrix.family }}${{ matrix.ver }}",
              "strategy": {"matrix": {"include": [{"family": "mac", "ver": "os-14"},
                                                  {"family": "ubuntu", "ver": "-latest"}]}}}, {})),
     "cannot tell"),
    ("a matrix value glued to literal text may spell macos, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "mac${{ matrix.v }}", "strategy": {"matrix": {"v": ["os-14"]}}}, {})),
     "cannot tell"),
    ("one whole matrix lookup with spaces around it is still bash, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "  ${{matrix.os}} ", "strategy": {"matrix": {"os": ["ubuntu-24.04"]}}}, {})),
     ("bash",)),
    ("a self-hosted runner without a linux label may be a Mac, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": ["self-hosted", "arm64"]}, {})),
     "cannot tell"),
    ("a self-hosted linux runner is bash, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of({}, {"runs-on": ["self-hosted", "Linux", "x64"]}, {})),
     ("bash",)),
    ("matrix.os over [self-hosted] may be a Mac, bash 5 gate",
     lambda: with_bash(5, lambda: crs.shell_of(
         {}, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["self-hosted"]}}}, {})),
     "cannot tell"),
    ("a pwsh step on macos is skipped, not cannot tell",
     lambda: with_bash(5, lambda: crs.shell_of({"shell": "pwsh"}, {"runs-on": "macos-15"}, {})), ()),
    # Composite actions, under a bash 5 gate whatever bash runs this test.
    ("a composite action a macOS job calls, bash 5 gate: rc 2",
     lambda: with_bash(5, lambda: run_case("on: push\njobs:\n  j:\n    runs-on: macos-15\n" + CALL,
                                           {"a/action.yml": A + "    - shell: bash\n      run: echo ok\n"})[0]),
     2),
    ("an action reached from a macOS job through another action, bash 5 gate: rc 2",
     lambda: with_bash(5, lambda: run_case(
         "on: push\njobs:\n  j:\n    runs-on: macos-15\n    steps:\n      - uses: ./.github/actions/outer\n",
         {"outer/action.yml": A + "    - uses: ./.github/actions/a\n",
          "a/action.yml": A + "    - shell: bash\n      run: echo ok\n"})[0]),
     2),
    ("the same action from an ubuntu job, bash 5 gate: rc 0",
     lambda: with_bash(5, lambda: run_case(H + CALL, {"a/action.yml": A + "    - shell: bash\n      run: echo ok\n"})[0]),
     0),
    ("uses: ./x/ names the directory x",
     lambda: crs.local_uses([{"uses": "./.github/actions/a/"}, {"uses": "actions/cache@v4"}, "echo"]),
     [".github/actions/a"]),
]


# Composite actions: (name, expected rc, workflow text, {path under .github/actions: text}).
A = "runs:\n  using: composite\n  steps:\n"
CALL = "    steps:\n      - uses: ./.github/actions/a\n"
ACTION_CASES: list[tuple[str, int, str, dict[str, str]]] = [
    ("a clean composite action", 0, H + CALL,
     {"a/action.yml": A + "    - shell: bash\n      run: echo ok\n"}),
    ("an unbalanced quote in a composite run step", 1, H + CALL,
     {"a/action.yml": A + "    - shell: bash\n      run: 'echo \"x'\n"}),
    ("an unclosed if inside bash -c '...' in a composite step", 1, H + CALL,
     {"a/action.yml": A + "    - shell: bash\n      run: |\n        docker run img bash -c '\n"
      "          if true; then\n            make\n        '\n"}),
    ("a composite run step with no shell = cannot tell", 2, H + CALL,
     {"a/action.yml": A + "    - run: echo ok\n"}),
    ("a composite shell: sh is checked with sh", 1, H + CALL,
     {"a/action.yml": A + "    - shell: sh\n      run: cat <(echo x)\n"}),
    ("a composite shell: pwsh is skipped", 0, H + CALL,
     {"a/action.yml": A + "    - shell: pwsh\n      run: Write-Host \"it's\n"}),
    ("a composite uses: step is skipped", 0, H + CALL,
     {"a/action.yml": A + "    - uses: actions/cache/restore@v4\n"}),
    ("a node action is skipped", 0, H + CALL,
     {"a/action.yml": "runs:\n  using: node20\n  main: index.js\n"}),
    ("an action that is not YAML = cannot tell", 2, H + CALL, {"a/action.yml": "runs: [\n"}),
    ("an action with no runs mapping = cannot tell", 2, H + CALL, {"a/action.yml": "name: a\n"}),
    ("a composite action with no steps list = cannot tell", 2, H + CALL,
     {"a/action.yml": "runs:\n  using: composite\n"}),
    ("a composite step that is not a mapping = cannot tell", 2, H + CALL,
     {"a/action.yml": A + "    - echo\n"}),
    ("an action no workflow calls is still parsed", 1, H + "    steps:\n      - run: echo ok\n",
     {"a/action.yml": A + "    - shell: bash\n      run: 'echo \"x'\n"}),
    ("action.yaml is read", 1, H + CALL,
     {"a/action.yaml": A + "    - shell: bash\n      run: 'echo \"x'\n"}),
    ("an action in a nested directory is read", 1, H + "    steps:\n      - uses: ./.github/actions/g/a/\n",
     {"g/a/action.yml": A + "    - shell: bash\n      run: 'echo \"x'\n"}),
    ("a composite action called from a macOS job: bash 3.2 there", 1 if crs.bash_major() == 3 else 2,
     "on: push\njobs:\n  j:\n    runs-on: macos-15\n" + CALL,
     {"a/action.yml": A + "    - shell: bash\n      run: make 2>&1 |& tee build.log\n"}),
    ("an action called only by an action a macOS job calls: bash 3.2 there",
     1 if crs.bash_major() == 3 else 2,
     "on: push\njobs:\n  j:\n    runs-on: macos-15\n    steps:\n      - uses: ./.github/actions/outer\n",
     {"outer/action.yml": A + "    - uses: ./.github/actions/a\n",
      "a/action.yml": A + "    - shell: bash\n      run: make 2>&1 |& tee build.log\n"}),
    # Under a bash 5 gate this and the two above differ only in the caller's runner.
    ("an ubuntu caller is plain bash", 0, H + CALL,
     {"a/action.yml": A + "    - shell: bash\n      run: echo ok\n"}),
    ("two actions that call each other end", 0, H + CALL,
     {"a/action.yml": A + "    - uses: ./.github/actions/b\n    - shell: bash\n      run: echo ok\n",
      "b/action.yml": A + "    - uses: ./.github/actions/a\n"}),
]


def run_case(text: str, actions: dict[str, str] | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "t.yml").write_text(text)
        for rel, body in (actions or {}).items():
            p = root / ".github" / "actions" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        crs.ROOT, crs.WORKFLOWS = root, wf
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = crs.main()
        return rc, out.getvalue()


def main() -> int:
    good = 0
    for e in PIN_ERRORS:
        print(f"BAD  fixture {e}")
    for name, want, text in CASES:
        rc, out = run_case(text)
        if rc == want:
            good += 1
            print(f"ok   {name} (rc {rc})")
        else:
            print(f"BAD  {name}: want rc {want}, got {rc}\n{out}")
    for name, want, text, actions in ACTION_CASES:
        rc, out = run_case(text, actions)
        as_expected = rc == want
        if as_expected:
            good += 1
            print(f"ok   {name} (rc {rc})")
        else:
            print(f"BAD  {name}: want rc {want}, got {rc}\n{out}")
    for name, got, want in DIRECT:
        g = got()
        if g == want:
            good += 1
            print(f"ok   {name} ({g})")
        else:
            print(f"BAD  {name}: want {want}, got {g}")
    total = len(CASES) + len(ACTION_CASES) + len(DIRECT)
    print(f"\n{good} of {total} cases as expected")
    return 0 if good == total else 1


if __name__ == "__main__":
    sys.exit(main())
