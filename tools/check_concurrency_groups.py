#!/usr/bin/env python3
r"""Does every workflow that runs on a push to main have a concurrency group?

Without one, a burst of merges queues every run of every workflow: each push
to main starts its own copy, no copy replaces another, and the account-wide
runner pool drains them one at a time. Before gHashTag/trinity-fpga#870, 2 of
the 104 push-to-main workflows here had a group; on 2026-10-04 the repository
had 194 runs queued at once, and figure-names.yml ran three copies on main at
the same time (#869).

A workflow passes when it never runs on a push to main, or does and has a
top-level `concurrency:` with a group. Four things fail:

  - UNGROUPED: it runs on a push to main and has no top-level group. A job's
    own `concurrency:` does not count -- the run still starts and queues; only
    that job waits.
  - BAD_REUSABLE: `workflow_call` and a top-level group. A called workflow
    reads the `github` context of its CALLER, so a group built from
    `github.workflow_ref` or `github.workflow` is the caller's own group, and
    the call can wait on the run that made it (GitHub cancels such a pair as a
    deadlock). The caller's group already covers the call.
  - SHARED_KEY: two files whose groups are the same text once each file's
    `github.workflow_ref` (its path) and `github.workflow` (its `name:`, or its
    path when it has none) are put in. Two files here share a `name:`
    (conformance-selftest.yml and conformance-golden-selftests.yml), so a group
    keyed on `github.workflow` would make one file's push replace the other's
    pending run. Every file with a group is compared, not only push ones.
  - DISPATCH_CANCELS: it runs on a push to main, can be dispatched by hand,
    and its group has no `github.run_id`. A newer push then replaces its
    pending dispatch, or cancels a running one under cancel-in-progress. This
    was a note until the last two such files, bench007-probe-ax7203.yml (a
    bitstream build of up to four hours) and trinity-identity-gate.yml, took
    the #870 group; neither commit that set their old groups gave a reason.

"Runs on a push to main" follows GitHub's filters: `branches:` patterns read
in order, the last match deciding and `!` excluding (`*` stops at `/`, `**`
does not, `?` and `+` repeat the character before them); `branches-ignore:`;
and a filter with only `tags:` / `tags-ignore:` never starts the workflow for
a branch push. `paths:` is ignored: it makes a run rarer, not single.

YAML is read with PyYAML's composer, as the runner reads it: the `on` key stays
the string `on` (a loader turns it into the boolean True), and an alias is the
node its anchor names. Every `if` below tests a named bool.

Exit 0 clean, 1 a defect above, 2 cannot tell: no PyYAML, no workflows, a file
that is not YAML, a key given twice, a merge key (`<<:`). Nothing read is not
clean. tools/test_check_concurrency_groups.py plants every case and runs first.

Not decided here: whether two groups that are different text still meet at run
time (a literal group equal to another file's `name:`), and whether a group's
expression evaluates -- GitHub refuses that run as a startup failure, which is
already red.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

MERGE_TAG = "tag:yaml.org,2002:merge"
NULL_TAG = "tag:yaml.org,2002:null"
WORKFLOW_REF = re.compile(r"\bgithub\.workflow_ref\b")
WORKFLOW_NAME = re.compile(r"\bgithub\.workflow\b(?!_)")
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)


class CannotTell(Exception):
    pass


def mapping(node, where: str) -> dict:
    """A mapping node as {key: value node}. A key that is not a string, a key
    given twice or a merge key is "cannot tell": which value the runner takes
    is then not ours to guess."""
    import yaml

    is_map = isinstance(node, yaml.MappingNode)
    if not is_map:
        raise CannotTell(f"{where} is not a mapping")
    out: dict = {}
    for k, v in node.value:
        is_merge = k.tag == MERGE_TAG
        if is_merge:
            raise CannotTell(f"{where}: a YAML merge key (`<<:`) -- cannot tell what it brings in")
        is_text = isinstance(k, yaml.ScalarNode)
        if not is_text:
            raise CannotTell(f"{where}: a key that is not a string")
        twice = k.value in out
        if twice:
            raise CannotTell(f"{where}: key `{k.value}` given twice")
        out[k.value] = v
    return out


def scalar(node, where: str) -> str:
    import yaml

    is_text = isinstance(node, yaml.ScalarNode)
    if not is_text:
        raise CannotTell(f"{where} is not a string")
    return node.value


def is_empty(node) -> bool:
    """`push:` with nothing after it."""
    import yaml

    return node is None or (isinstance(node, yaml.ScalarNode) and node.tag == NULL_TAG)


def words(node, where: str) -> list[str]:
    """A string or a list of strings, as a list."""
    import yaml

    is_list = isinstance(node, yaml.SequenceNode)
    if is_list:
        return [scalar(n, where) for n in node.value]
    return [scalar(node, where)]


def triggers(node) -> dict:
    """{event: its filter node, or None} of the `on:` value."""
    import yaml

    is_map = isinstance(node, yaml.MappingNode)
    if is_map:
        return mapping(node, "`on:`")
    return {e: None for e in words(node, "`on:`")}


def pattern(glob: str) -> re.Pattern:
    """A GitHub branch filter as a regex: `**` anything, `*` anything but `/`,
    `?` / `+` zero-or-one / one-or-more of the character before, `[..]` a set,
    `\\` quotes the next character."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        double = glob.startswith("**", i)
        is_set = c == "["
        quoted = c == "\\" and i + 1 < len(glob)
        if double:
            out.append(".*")
            i += 2
            continue
        if is_set:
            end = glob.find("]", i + 1)
            unclosed = end < 0
            if unclosed:
                raise CannotTell(f"branch filter `{glob}`: a `[` with no `]`")
            out.append(glob[i:end + 1])
            i = end + 1
            continue
        if quoted:
            out.append(re.escape(glob[i + 1]))
            i += 2
            continue
        star = c == "*"
        repeat = c in "?+"
        if star:
            out.append("[^/]*")
        elif repeat:
            out.append(c)
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def matches(branch: str, globs: list[str]) -> bool:
    """GitHub's rule: the last pattern that matches decides, and a `!` pattern
    excludes again."""
    hit = False
    for g in globs:
        negated = g.startswith("!")
        body = g[1:] if negated else g
        found = pattern(body).match(branch) is not None
        if found:
            hit = not negated
    return hit


def push_reaches_main(node) -> bool:
    empty = is_empty(node)
    if empty:
        return True
    f = mapping(node, "`on.push`")
    has_branches = "branches" in f
    has_ignore = "branches-ignore" in f
    both = has_branches and has_ignore
    if both:
        raise CannotTell("`on.push` has both `branches` and `branches-ignore`")
    if has_branches:
        return matches("main", words(f["branches"], "`on.push.branches`"))
    if has_ignore:
        return not matches("main", words(f["branches-ignore"], "`on.push.branches-ignore`"))
    tags_only = "tags" in f or "tags-ignore" in f
    if tags_only:  # GitHub: only tags filters, and a branch push never starts it
        return False
    return True


def group_of(node) -> tuple[str, str]:
    """(group, cancel-in-progress) of a top-level `concurrency:` value."""
    import yaml

    is_text = isinstance(node, yaml.ScalarNode)
    if is_text:
        return node.value, "false"
    c = mapping(node, "`concurrency:`")
    has_group = "group" in c
    group = scalar(c["group"], "`concurrency.group`") if has_group else ""
    has_cancel = "cancel-in-progress" in c
    cancel = scalar(c["cancel-in-progress"], "`concurrency.cancel-in-progress`") if has_cancel else "false"
    return group, cancel


def render(group: str, path: str, name: str) -> str:
    """The group as it reads for this file: its own path and name put in, and
    the spacing inside each `${{ }}` made one form."""
    key = WORKFLOW_REF.sub(f"<file {path}>", group)
    key = WORKFLOW_NAME.sub(f"<name {name}>", key)
    key = EXPRESSION.sub(lambda m: "${{ " + " ".join(m.group(1).split()) + " }}", key)
    return key.strip()


def relpath(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def classify(path: pathlib.Path) -> dict:
    """{file, class, why, key} of one workflow. class None: it never runs on a
    push to main and so needs no group; its key still counts for SHARED_KEY."""
    import yaml

    rel = relpath(path)
    root = yaml.compose(path.read_text(), Loader=yaml.SafeLoader)
    empty_file = root is None
    if empty_file:
        raise CannotTell("an empty file")
    top = mapping(root, "the workflow")
    has_on = "on" in top
    if not has_on:
        raise CannotTell("no top-level `on:`")
    on = triggers(top["on"])
    named = "name" in top
    name = scalar(top["name"], "`name:`") if named else rel
    has_conc = "concurrency" in top
    group, cancel = group_of(top["concurrency"]) if has_conc else ("", "false")
    grouped = bool(group.strip())
    key = render(group, rel, name) if grouped else ""
    has_push = "push" in on
    to_main = has_push and push_reaches_main(on["push"])
    row = {"file": rel, "class": None, "why": "", "key": key, "on_main": to_main}

    reusable = "workflow_call" in on
    bad_reusable = reusable and grouped
    if bad_reusable:
        row.update({"class": "BAD_REUSABLE",
                    "why": "a called workflow's group reads the caller's `github` context "
                           "-- it is the caller's group; give the caller the group instead"})
        return row
    if not to_main:
        return row
    if reusable:
        row.update({"class": "REUSABLE", "why": "called: the caller's group covers it"})
        return row
    if not grouped:
        why = "`concurrency:` without a group" if has_conc else "no top-level `concurrency:`"
        row.update({"class": "UNGROUPED", "why": f"runs on a push to main, {why}"})
        return row
    dispatchable = "workflow_dispatch" in on
    per_run = "github.run_id" in group
    cancels_dispatch = dispatchable and not per_run
    if cancels_dispatch:
        cancels = cancel.strip().strip("'\"") == "true"
        how = "a push cancels a running dispatch" if cancels else "a newer run replaces a pending dispatch"
        row.update({"class": "DISPATCH_CANCELS", "why": f"group has no github.run_id: {how}"})
        return row
    row.update({"class": "GROUPED", "why": ""})
    return row


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("CANNOT TELL: PyYAML is not installed -- no workflow can be read")
        return 2
    has_dir = WORKFLOWS.is_dir()
    files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))) if has_dir else []
    none_found = not files
    if none_found:
        print(f"CANNOT TELL: no workflows under {relpath(WORKFLOWS)}")
        return 2

    rows, unread = [], []
    for wf in files:
        try:
            rows.append(classify(wf))
        except yaml.YAMLError as e:
            unread.append(f"{relpath(wf)}: not readable as YAML -- {str(e).splitlines()[0]}")
        except CannotTell as e:
            unread.append(f"{relpath(wf)}: {e}")

    owners: dict[str, list[str]] = {}
    for r in rows:
        keyed = bool(r["key"])
        if keyed:
            owners.setdefault(r["key"], []).append(r["file"])
    for r in rows:
        others = [f for f in owners.get(r["key"], []) if f != r["file"]]
        shared = bool(r["key"]) and bool(others)
        if shared:
            r["class"], r["why"] = "SHARED_KEY", "the same group as " + ", ".join(others)

    on_main = [r for r in rows if r["on_main"]]
    covered = [r for r in on_main if r["class"] in ("GROUPED", "DISPATCH_CANCELS", "REUSABLE")]
    failing = ("UNGROUPED", "BAD_REUSABLE", "SHARED_KEY", "DISPATCH_CANCELS")
    bad = [r for r in rows if r["class"] in failing]
    print(f"workflows read: {len(rows)} of {len(files)}; on a push to main: {len(on_main)}, "
          f"with a group of their own (or called): {len(covered)}")

    rc = 0
    for cls in failing:
        hits = [r for r in bad if r["class"] == cls]
        found = bool(hits)
        if found:
            print(f"\nFAIL {cls}: {len(hits)} workflow(s)")
            for r in hits:
                print(f"  {r['file']}: {r['why']}")
            rc = 1
    fails = rc == 1
    if fails:
        print("\nA group that replaces a pending run and never cancels a dispatch:\n"
              "  concurrency:\n"
              "    group: ${{ github.workflow_ref }}-${{ github.event_name == 'workflow_dispatch'"
              " && github.run_id || github.event_name }}\n"
              "    cancel-in-progress: ${{ github.event_name == 'pull_request' }}")
    has_unread = bool(unread)
    if has_unread:
        print(f"\nCANNOT TELL: {len(unread)} workflow(s) not read:")
        for e in unread:
            print(f"  {e}")
        rc = rc or 2
    clean = rc == 0
    if clean:
        print("OK: every workflow that runs on a push to main has a group of its own")
    return rc


if __name__ == "__main__":
    sys.exit(main())
