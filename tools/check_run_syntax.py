#!/usr/bin/env python3
r"""Does every workflow step's shell script parse?

A `run:` script with a syntax error does not fail at review time, it fails
when it runs -- and two steps here only run on `workflow_dispatch`, so their
errors sat unseen:

  - build-matrix.yml "Full bitstream build": a `doesn't` in a comment inside
    `bash -c '...'` closed the single-quoted script, and the rest of it was
    read as the outer shell's;
  - iddr-golden-diff.yml "Build the same design with openXC7 and diff the
    ILOGIC config": the `'\''` idiom, which only means "a quote" INSIDE single
    quotes, was used on a line outside them and left one quote open.

Both steps failed on parse every time they were dispatched. This gate runs
`bash -n` (or `sh -n` for `shell: sh`) on every step whose effective shell is
a POSIX one -- the step's `shell:`, else the job's, else the workflow's
`defaults.run.shell`, read through `/usr/bin/env [-opts] [VAR=x]`. With no
`shell:` anywhere it is bash; pwsh on a `windows` runner, which is skipped;
and in a `container:` job, bash only if the image has it and `sh` if not -- so
there the script is checked with BOTH, and a bashism needs an explicit
`shell: bash`. `${{ ... }}` is left as written: `bash -n` does not expand it.
A job that may run on macOS (bash 3.2 for `bash` and `sh`) is "cannot tell"
unless the gate's own bash is 3.x.
A `shell:` line that does not split into words is "cannot tell".

`bash -n` reads syntax only: a script inside a quoted `bash -c '...'` argument
is one string to it, so an error INSIDE that string is not seen -- unless, as
in both cases above, it breaks the quoting of the outer script. So once the
outer script parses, every single-quoted `bash -c '...'` / `sh -c '...'`
argument in it (the `docker run ... bash -c '` scripts, 24 on main) is read
out as the outer shell reads it -- `'\''` is a quote, and a splice of an outer
value, `'"$PART"'` or `'$X'`, is one word -- and parsed with `bash -n` / `sh -n`
in turn. A quote followed by anything else is "cannot tell". A double-quoted
`bash -c "..."` is not read: the outer shell expands it first.

A composite action (`.github/actions/**/action.yml` or `action.yaml`,
`runs.using: composite`) is read the same way: each of its `runs.steps` that
has a `run:` must name its `shell:` (GitHub refuses one that does not, so a
step without it is "cannot tell"), and there are no job or workflow defaults
to inherit. The runner is the caller's: every workflow job that uses the
action by its `./` path, directly or through another local action, and if any
of them may be macOS the step is "cannot tell" unless the gate's bash is 3.x.
An action no workflow here calls is still parsed. Node and Docker actions have
no `run:` steps and are skipped.

Exit 0 clean, 1 a step that does not parse, 2 a workflow this could not read
(cannot tell is not clean). tools/test_check_run_syntax.py plants every case.
"""
from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class CannotTell(Exception):
    pass


POSIX = ("bash", "sh")


# env options whose argument is the next word (GNU and BSD env).
TAKES_ARG = ("-u", "--unset", "-C", "--chdir", "-P")


def command_word(words: list[str]) -> str:
    """The program a `shell:` line runs, looking through `env [-opts] [VAR=x]`."""
    while words:
        word, words = words[0], words[1:]
        if pathlib.PurePosixPath(word).name != "env":
            return word
        while words and (words[0].startswith("-") or "=" in words[0]):
            opt, words = words[0], words[1:]
            if opt in TAKES_ARG and words:
                words = words[1:]  # `-u NAME`, `-C DIR`: the next word is not the program
            elif opt in ("-S", "--split-string") and words:
                words = shlex.split(words[0]) + words[1:]
            elif opt.startswith("-S") and len(opt) > 2:
                words = shlex.split(opt[2:]) + words
            elif opt.startswith("--split-string="):
                words = shlex.split(opt.split("=", 1)[1]) + words
    return ""


def run_defaults(node: dict, where: str) -> dict:
    """`defaults.run` of a job or a workflow; a shape GitHub would reject is cannot-tell."""
    d = node.get("defaults") or {}
    if not isinstance(d, dict) or not isinstance(d.get("run") or {}, dict):
        raise CannotTell(f"{where} defaults is not a mapping with a run mapping")
    return d.get("run") or {}


_BASH_MAJOR: int | None = None


def bash_major() -> int:
    """The major version of the bash this gate parses with; 0 if unreadable."""
    global _BASH_MAJOR
    if _BASH_MAJOR is None:
        r = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"],
                           capture_output=True, text=True)
        v = r.stdout.strip()
        _BASH_MAJOR = int(v) if v.isdigit() else 0
    return _BASH_MAJOR


WHOLE_MATRIX_LOOKUP = re.compile(r"\$\{\{\s*matrix\.[a-z0-9_-]+\s*\}\}")


def may_run_on_macos(job: dict) -> bool:
    """Written down as a macOS runner, or picked at run time by anything but a
    plain `matrix.<key>` over a matrix written out in full without macos:
    `inputs.os`, `vars.RUNNER`, a `fromJSON(...)` matrix may all be macOS
    (review 5 of #839), and so may `${{ matrix.a }}${{ matrix.b }}` or
    `mac${{ matrix.v }}` (review 6)."""
    runs_on = str(job.get("runs-on", "")).lower()
    if "macos" in runs_on:
        return True
    # A self-hosted Mac gets the macOS label by itself, so a self-hosted
    # runner is macOS-possible unless its labels say linux (review of #840).
    if "self-hosted" in runs_on and "linux" not in runs_on:
        return True
    if "${{" not in runs_on:
        return False
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict) or "${{" in str(matrix) or "macos" in str(matrix).lower():
        return True
    if "self-hosted" in str(matrix).lower():
        return True
    # The lookup must be the whole label: pieces glued together, or glued to
    # literal text, can spell macos though no single value does (review 6).
    return not WHOLE_MATRIX_LOOKUP.fullmatch(runs_on.strip())


def shell_of(step: dict, job: dict, wf: dict) -> tuple[str, ...]:
    """The interpreters to check the script with; empty to skip it."""
    shells = _shell_named(step, job, wf)
    # GitHub's macOS images run Bash 3.2 for `bash` and for `sh` alike; bash 4
    # syntax (`|&`, `;&`) parses here under bash 5 and fails there on every
    # run (review 4 of #839). Only a bash 3 gate can answer for that leg.
    if shells and may_run_on_macos(job) and bash_major() != 3:
        raise CannotTell(f"a macOS runner parses with bash 3.2; this gate has bash {bash_major()}")
    return shells


def _shell_named(step: dict, job: dict, wf: dict) -> tuple[str, ...]:
    for node in (step, run_defaults(job, "job"), run_defaults(wf, "workflow")):
        sh = node.get("shell")
        if sh:
            break
    else:
        runs_on = str(job.get("runs-on", ""))
        if "windows" in runs_on.lower():
            if "${{" in runs_on:
                # `runs-on: ${{ inputs.win && 'windows-latest' || 'ubuntu-latest' }}`
                # picks the runner at run time; the bash leg would still parse it.
                raise CannotTell(f"runs-on: {runs_on!r} picks windows at run time")
            return ()
        # In a `container:` job the default is bash only if the image has it,
        # and `sh` otherwise -- so the script has to parse in both.
        return POSIX if job.get("container") else ("bash",)
    if "${{" in str(sh):
        # `shell: ${{ matrix.shell }} {0}` is legal, and which shell it is is
        # only known at run time. Skipping it would pass a broken script.
        raise CannotTell(f"shell: {sh!r} is an expression")
    try:
        name = pathlib.PurePosixPath(command_word(shlex.split(str(sh)))).name
    except ValueError as e:
        raise CannotTell(f"shell: {sh!r} does not split ({e})")
    if name in POSIX:
        return (name,)
    return ()  # pwsh, python, cmd, a custom shell: not ours to parse


# `bash -c '`, `sh -lc '`, `bash -euo pipefail -c '`: the opening quote of a
# script argument. `\b` keeps `zsh -c` and `fish -c` out.
INNER_OPEN = re.compile(r"\b(bash|sh)(?:[ \t]+(?:-[a-zA-Z]*o[ \t]+[a-z]+|-[a-zA-Z]+))*?"
                        r"[ \t]+-[a-zA-Z]*c[a-zA-Z]*[ \t]+'")
# What may sit between a closing quote and the reopening one: an outer value.
SPLICE = re.compile(r'"(?:[^"\\]|\\.)*"|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*')
# Stands in for a spliced value: bash -n needs a word there, not its contents.
OUTER_VALUE = "__outer__"
WORD_BREAK = " \t\n;|&)"
# How many -c scripts check() parsed since main() started, for its summary line.
_INNER_SEEN = 0


def inner_scripts(script: str) -> list[tuple[str, str]]:
    """(interpreter, text) of every single-quoted -c script argument in `script`."""
    found = []
    for m in INNER_OPEN.finditer(script):
        i, buf = m.end(), []
        while True:
            j = script.find("'", i)
            unclosed = j < 0
            if unclosed:
                raise CannotTell(f"a {m.group(1)} -c '...' script with no closing quote")
            buf.append(script[i:j])
            k = j + 1
            escaped_quote = script.startswith("\\''", k)  # '\'' : a quote, then reopen
            if escaped_quote:
                buf.append("'")
                i = k + 3
                continue
            spliced = False
            s = SPLICE.match(script, k)
            while s:
                spliced = True
                k = s.end()
                s = SPLICE.match(script, k)
            if spliced:
                buf.append(OUTER_VALUE)
            reopens = script.startswith("'", k)
            if reopens:
                i = k + 1
                continue
            ends = k == len(script) or script[k] in WORD_BREAK
            if not ends:
                raise CannotTell(f"a {m.group(1)} -c '...' script is glued to {script[k:k + 12]!r}")
            break
        found.append((m.group(1), "".join(buf)))
    return found


def check_run(script: str, shells: tuple[str, ...], where: str) -> list[str]:
    """`bash -n` / `sh -n` on one step's script, then on each single-quoted -c
    script in it. CannotTell from inner_scripts goes to the caller."""
    global _INNER_SEEN
    bad = []
    outer_parses = bool(shells)
    for sh in shells:
        r = subprocess.run([sh, "-n"], input=script, capture_output=True, text=True)
        if r.returncode:
            err = " ".join(r.stderr.split())[:240]
            bad.append(f"{where} [{sh} -n]\n    {err}")
            outer_parses = False
            break
    # Only a script whose own quoting is sound can be read for the strings
    # it hands to bash -c; a broken one was reported above.
    if not outer_parses:
        return bad
    inner = inner_scripts(script)
    _INNER_SEEN += len(inner)
    for n, (ish, text) in enumerate(inner, 1):
        r = subprocess.run([ish, "-n"], input=text, capture_output=True, text=True)
        inner_bad = r.returncode != 0
        if inner_bad:
            err = " ".join(r.stderr.split())[:240]
            bad.append(f"{where} [{ish} -n on {ish} -c script {n}, lines from its opening quote]"
                       f"\n    {err}")
    return bad


def check(path: pathlib.Path) -> tuple[list[str], str | None]:
    import yaml  # inside: a missing PyYAML is "cannot tell", not a crash
    try:
        wf = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [], f"{path.name}: not YAML ({e.__class__.__name__})"
    if not isinstance(wf, dict) or not isinstance(wf.get("jobs"), dict):
        return [], f"{path.name}: no jobs mapping"
    bad = []
    for jn, job in wf["jobs"].items():
        if not isinstance(job, dict):
            return [], f"{path.name} :: {jn}: job is not a mapping"
        for k, step in enumerate(job.get("steps") or [], 1):
            if not isinstance(step, dict):
                return [], f"{path.name} :: {jn}: step {k} is not a mapping"
            if "run" not in step:
                continue
            where = f"{path.relative_to(ROOT)} :: {jn} :: {step.get('name') or f'step {k}'}"
            try:
                bad += check_run(str(step["run"]), shell_of(step, job, wf), where)
            except CannotTell as e:
                return [], f"{path.name} :: {jn} :: step {k}: {e}"
    return bad, None


def local_uses(steps) -> list[str]:
    """The `./` actions a steps list calls, as repo-relative directories."""
    found = []
    for step in steps if isinstance(steps, list) else []:
        uses = step.get("uses") if isinstance(step, dict) else None
        local = isinstance(uses, str) and uses.startswith("./")
        if local:
            found.append(uses[2:].rstrip("/"))
    return found


def action_dir(path: pathlib.Path) -> str:
    return path.parent.relative_to(ROOT).as_posix()


def action_callers(workflows: list[pathlib.Path], actions: list[pathlib.Path]) -> dict[str, list[dict]]:
    """Every workflow job that runs each local action, directly or through
    another local action, keyed by the action's directory. A file that does
    not load is skipped here; check() and check_action() report it."""
    import yaml
    direct: dict[str, list[dict]] = {}
    for f in workflows:
        try:
            wf = yaml.safe_load(f.read_text())
        except yaml.YAMLError:
            continue
        jobs = wf.get("jobs") if isinstance(wf, dict) else None
        for job in jobs.values() if isinstance(jobs, dict) else []:
            steps = job.get("steps") if isinstance(job, dict) else None
            for rel in local_uses(steps):
                direct.setdefault(rel, []).append(job)
    parents: dict[str, list[str]] = {}
    for f in actions:
        try:
            act = yaml.safe_load(f.read_text())
        except yaml.YAMLError:
            continue
        runs = act.get("runs") if isinstance(act, dict) else None
        steps = runs.get("steps") if isinstance(runs, dict) else None
        for child in local_uses(steps):
            parents.setdefault(child, []).append(action_dir(f))

    def jobs_of(rel: str, seen: frozenset) -> list[dict]:
        jobs = list(direct.get(rel, []))
        for parent in parents.get(rel, []):
            fresh = parent not in seen
            if fresh:
                jobs += jobs_of(parent, seen | {parent})
        return jobs

    return {action_dir(f): jobs_of(action_dir(f), frozenset({action_dir(f)})) for f in actions}


def check_action(path: pathlib.Path, callers: list[dict]) -> tuple[list[str], str | None]:
    """check() for one action.yml; `callers` are the jobs that run it."""
    import yaml
    rel = path.relative_to(ROOT).as_posix()
    try:
        act = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [], f"{rel}: not YAML ({e.__class__.__name__})"
    runs = act.get("runs") if isinstance(act, dict) else None
    no_runs = not isinstance(runs, dict)
    if no_runs:
        return [], f"{rel}: no runs mapping"
    composite = runs.get("using") == "composite"
    if not composite:
        return [], None  # node and docker actions have no run: steps
    steps = runs.get("steps")
    no_steps = not isinstance(steps, list)
    if no_steps:
        return [], f"{rel}: a composite action with no steps list"
    mac_caller = any(may_run_on_macos(job) for job in callers)
    bad = []
    for k, step in enumerate(steps, 1):
        not_mapping = not isinstance(step, dict)
        if not_mapping:
            return [], f"{rel}: step {k} is not a mapping"
        no_run = "run" not in step
        if no_run:
            continue
        unnamed_shell = not step.get("shell")
        if unnamed_shell:
            return [], f"{rel} :: step {k}: a composite run step must name its shell"
        where = f"{rel} :: {step.get('name') or f'step {k}'}"
        try:
            shells = _shell_named(step, {}, {})
            # The same bash 3.2 question as shell_of, asked of the callers' runners.
            old_bash_unchecked = bool(shells) and mac_caller and bash_major() != 3
            if old_bash_unchecked:
                raise CannotTell(f"a caller may run on macOS (bash 3.2); this gate has bash {bash_major()}")
            bad += check_run(str(step["run"]), shells, where)
        except CannotTell as e:
            return [], f"{rel} :: step {k}: {e}"
    return bad, None


def main() -> int:
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("cannot tell: PyYAML is not installed")
        return 2
    for sh in POSIX:
        if not shutil.which(sh):
            print(f"cannot tell: no {sh} on PATH")
            return 2
    global _INNER_SEEN
    _INNER_SEEN = 0
    files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    bad, unread = [], []
    for f in files:
        b, err = check(f)
        bad += b
        if err:
            unread.append(err)
    wf_unread = len(unread)
    actions_root = ROOT / ".github" / "actions"
    has_actions = actions_root.is_dir()
    actions = sorted(list(actions_root.rglob("action.yml")) + list(actions_root.rglob("action.yaml"))
                     if has_actions else [])
    callers = action_callers(files, actions)
    uncalled = 0
    for a in actions:
        b, err = check_action(a, callers[action_dir(a)])
        bad += b
        no_caller = not callers[action_dir(a)]
        if no_caller:
            uncalled += 1
        if err:
            unread.append(err)
    print(f"workflows read: {len(files) - wf_unread} of {len(files)}; actions read: "
          f"{len(actions) - (len(unread) - wf_unread)} of {len(actions)} ({uncalled} with no caller here); "
          f"bash -c '...' scripts read out of them: {_INNER_SEEN}; steps and scripts that do not parse: {len(bad)}")
    for b in bad:
        print("  " + b)
    for u in unread:
        print("  cannot read: " + u)
    if bad:
        print("\nFAIL: these steps fail on parse every time they run. A quote inside a")
        print("single-quoted `bash -c '...'` (an apostrophe in a comment counts) ends it.")
        return 1
    return 2 if unread else 0


if __name__ == "__main__":
    sys.exit(main())
