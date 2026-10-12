#!/usr/bin/env python3
"""Does any job restore its Zig cache and then check out over it?

mlugg/setup-zig restores the job's cache into `$GITHUB_WORKSPACE/.zig-cache`
and points both ZIG_GLOBAL_CACHE_DIR and ZIG_LOCAL_CACHE_DIR at it
(`getZigCachePath` in its common.js). If an actions/checkout of the workspace
runs after it, checkout logs "Deleting the contents of '<workspace>'" and the
restored cache goes with the rest. The job then builds from nothing and saves a
new entry anyway. brain-ci did this in all 8 of its Zig jobs. In 25 runs it
made 238 restores and used none of them, and its entries took 8.43 of the
repository's 10.83 GB of Actions cache (#844, #890).

A job fails this gate when a caching setup-zig step comes before a checkout
of the workspace itself. Caching means `use-cache` is absent or anything but
a literal `false`. The workspace itself means `path` is absent, `.` or
`${{ github.workspace }}`. A checkout into a subdirectory leaves `.zig-cache`
alone and passes, as does a job that never checks out.

Exit 0 clean, 1 a job checks out over a restored cache, 2 cannot tell (no
PyYAML, no workflows, an unreadable workflow, or no job uses setup-zig, so
there is nothing to guard and a pass would mean nothing).

Known limits: a workspace wiped by a script (`git clean -xdf`, `rm -rf`) or
by another action is not read, and neither is a setup-zig vendored under a
different name. Each such case would need its own rule.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE_PATHS = {"", ".", "./", "${{ github.workspace }}"}


def action_name(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0].strip().lower()


def restores_cache(step: dict) -> bool:
    use_cache = str((step.get("with") or {}).get("use-cache", "true")).strip().lower()
    return use_cache != "false"


def checks_out_workspace(step: dict) -> bool:
    path = str((step.get("with") or {}).get("path") or "").strip()
    return path in WORKSPACE_PATHS


def job_findings(workflow: str, job_id: str, job: dict) -> tuple[bool, list[str]]:
    """(uses setup-zig at all, one line per checkout that wipes a restored cache)."""
    steps = job.get("steps") or []
    restored_at = None
    uses_zig = False
    out = []
    for i, step in enumerate(steps, 1):
        not_a_step = not isinstance(step, dict)
        if not_a_step:
            continue
        name = action_name(step)
        is_zig = name == "mlugg/setup-zig"
        uses_zig = uses_zig or is_zig
        restores = is_zig and restores_cache(step)
        if restores:
            restored_at = i
        wipes = restored_at is not None and name == "actions/checkout" and checks_out_workspace(step)
        if wipes:
            out.append(f"{workflow} :: {job_id} :: setup-zig (step {restored_at}) restores .zig-cache, "
                       f"then checkout (step {i}) deletes it")
    return uses_zig, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflows", default=str(ROOT / ".github" / "workflows"),
                    help="directory of workflow files (default: this tree's)")
    args = ap.parse_args(argv)
    try:
        import yaml
    except ImportError:
        print("cannot tell: PyYAML is not installed")
        return 2
    wf_dir = pathlib.Path(args.workflows)
    files = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
    no_files = not files
    if no_files:
        print(f"cannot tell: no workflow file under {wf_dir}")
        return 2
    findings: list[str] = []
    unreadable: list[str] = []
    zig_jobs = 0
    for wf in files:
        try:
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            unreadable.append(f"{wf.name}: {str(e).splitlines()[0]}")
            continue
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        jobs_ok = isinstance(jobs, dict)
        if not jobs_ok:
            continue
        for job_id, job in jobs.items():
            job_ok = isinstance(job, dict)
            if not job_ok:
                continue
            uses_zig, found = job_findings(wf.name, str(job_id), job)
            zig_jobs += uses_zig
            findings.extend(found)
    print(f"{len(files)} workflows, {zig_jobs} jobs use mlugg/setup-zig, "
          f"{len(findings)} check out over its restored cache")
    for line in findings:
        print(f"  {line}")
    for line in unreadable:
        print(f"  cannot read {line}")
    wiped_any = bool(findings)
    if wiped_any:
        print("Move actions/checkout above setup-zig in each job listed (see #890).")
        return 1
    blind = bool(unreadable) or zig_jobs == 0
    if blind:
        print("cannot tell: " + ("a workflow did not parse" if unreadable else "no job uses mlugg/setup-zig"))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
