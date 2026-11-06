#!/usr/bin/env python3
"""Can each paper gate actually fail?

Every other gate here asks whether the paper is right. None asks whether the
gates are. That question is not hypothetical: check_self_consistency passed a
paper claiming "Ninety-nine retractions are marked in place below" and again
one claiming "Three", because its pattern only matched sentences carrying a
retraction verb and its number-word dictionary stopped at twenty — an
unreadable count was skipped in silence rather than reported. It had been
green for weeks over a document whose count it could not read.

This injects a defect each gate is supposed to catch, runs the gate, and
requires it to fail. A gate that passes its own defect is reported here, in
CI, instead of being discovered by whoever eventually trusts it.

THE MISTAKE THIS FILE EXISTS TO PREVENT, MADE THREE TIMES BY ITS AUTHOR:
a string replacement whose target is absent is a no-op, the gate then passes
because nothing was injected, and the output is identical to a gate that has
gone blind. check_withdrawn_live's docstring warned about it in prose; the
warning was ignored twice more anyway. So every injection here asserts the
file actually changed before the gate is allowed to speak, and a no-op
injection is a failure of THIS file, not a pass of the gate.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAPER = ROOT / "research" / "arxiv_tnf" / "tnf_paper.tex"
ANCHOR = "\\section{What this paper argues}"

# Not every gate reads the paper. Those that check documents against the tree
# need their defect injected into a document instead, so the harness carries a
# second target and each case says which file it patches.
SCRATCH_DOC = ROOT / "research" / "GATE_SELFTEST_SCRATCH.md"

# gate -> the defect it must reject. A case is one of:
#
#   str                      text inserted before ANCHOR in the paper
#   ("create", rel, text)    a file the harness writes and then removes
#   ("append", rel, text)    text appended to an existing file, then restored
#
# Three forms because three kinds of gate exist here: those that read the
# paper, those that read the tree for things that should not be in it, and
# those that read an artefact whose content must be corrupted rather than
# created. Each defect is the smallest thing its gate exists to notice.
CASES = {
    "check_self_consistency": (
        "Ninety-nine retractions are marked in place below.\n\n"
    ),
    "check_paper_numbers": (
        "The synthesised design runs at 987.65 MHz on the board.\n\n"
    ),
    "check_ref_kinds": (
        "See Section~\\ref{fig:acc} for the plate.\n\n"
    ),
    "check_latex_hygiene": (
        "A reference to \\ref{sec:does-not-exist-at-all} here.\n\n"
    ),
    # Documents-against-the-tree gates: the defect goes in a scratch document
    # that the harness creates and removes, never in a real one.
    "check_doc_refs": (
        "create", "research/GATE_SELFTEST_SCRATCH.md",
        "# Gate self-test scratch\n\n"
        "This document names `tools/a_file_that_does_not_exist.py`, which is\n"
        "the defect check_doc_refs exists to catch. The harness writes this\n"
        "file, runs the gate, and removes it.\n",
    ),
    # A citation with no entry behind it. The gate reads the paper, so this is
    # a paper injection like the four above.
    "check_bibliography": (
        "This rests on prior work~\\cite{gate-selftest-no-such-key}.\n\n"
    ),
    # An artefact nothing produces: the whole subject of that gate, created
    # where its glob will find it.
    "check_orphan_artefacts": (
        "create", "research/gate_selftest_orphan.json",
        '{"note": "written by check_gates_can_fail; no producer names it"}\n',
    ),
    # A reproduction script naming a module that does not exist. The gate only
    # reads scripts that call read_verilog, so the scratch script must too.
    "check_script_rot": (
        "create", "fpga/gate_selftest_scratch.sh",
        "#!/bin/sh\n"
        "# Written by check_gates_can_fail and removed again.\n"
        "# The gate reads only scripts that call read_verilog, and finds used\n"
        "# modules by Verilog INSTANTIATION syntax -- not by yosys commands. A\n"
        "# first version of this defect used `hierarchy -top NAME` and the gate\n"
        "# passed it, which read as a blind gate and was a bad injection.\n"
        "read_verilog gate_selftest_absent.v\n"
        "cat > /dev/null <<EOF\n"
        "  module_that_is_defined_nowhere_9f3a dut (.clk(clk));\n"
        "EOF\n",
    ),
    # One undefined code in a sweep. Appended rather than created: an empty
    # sweep is a DIFFERENT finding of the same gate, and testing that one
    # would not prove it can see this one.
    "check_undefined_outputs": (
        "append", "fpga/tnet/cf_binary32.txt", "4294967295 xxxxxxxx\n",
    ),
    # A workflow step that pipes a build into tee under the default shell,
    # which has no pipefail: the failure of the build ends green.
    "check_tee_pipefail": (
        "create", ".github/workflows/gate-selftest-tee.yml",
        "# Written by check_gates_can_fail and removed again.\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  selftest:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Synthesis whose failure ends green\n"
        "        run: |\n"
        "          yosys -p synth.ys 2>&1 | tee synth.log\n",
    ),
    # A workflow that runs on every push to main with no concurrency group:
    # a burst of merges queues one run of it per push (#869).
    "check_concurrency_groups": (
        "create", ".github/workflows/gate-selftest-concurrency.yml",
        "# Written by check_gates_can_fail and removed again.\n"
        "on: push\n"
        "jobs:\n"
        "  selftest:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: true\n",
    ),
    "check_withdrawn_live": (
        "\\paragraph{Withdrawn.} The earlier claim of $77.31\\times$ is withdrawn: "
        "it was measured against the broken oracle.\n\n"
        "The rung still reaches $77.31\\times$ in the far band.\n\n"
    ),
}


def run(gate: str) -> bool:
    """True if the gate passed."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / f"{gate}.py")],
        capture_output=True,
        cwd=ROOT,
    )
    return r.returncode == 0


def main() -> int:
    original = PAPER.read_text()
    if ANCHOR not in original:
        print(f"FAIL: the injection anchor is not in {PAPER.name}; this file cannot test anything")
        return 1

    # A gate that is ALREADY failing rejects anything you inject, so its
    # "rejects its defect" verdict would mean nothing. Establish each gate's
    # clean-tree state first and report the ones that cannot be tested rather
    # than counting them as proven — the same distinction between "no boards"
    # and "cannot tell" that this project keeps rediscovering.
    already_red = [g for g in CASES if not run(g)]

    blind, noop = [], []
    try:
        for gate, defect in CASES.items():
            if gate in already_red:
                continue
            if isinstance(defect, tuple):
                mode, rel, text = defect
                target = ROOT / rel
                if mode == "create":
                    # Refusing to overwrite is the point: if the path already
                    # exists, the harness would be testing somebody's file and
                    # would delete it afterwards.
                    if target.exists():
                        noop.append(gate)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                    if not target.is_file() or target.stat().st_size == 0:
                        noop.append(gate)
                        target.unlink(missing_ok=True)
                        continue
                    if run(gate):
                        blind.append(gate)
                    target.unlink(missing_ok=True)
                else:  # append
                    if not target.is_file():
                        noop.append(gate)
                        continue
                    before = target.read_text()
                    target.write_text(before + text)
                    if len(target.read_text()) <= len(before):
                        noop.append(gate)
                        target.write_text(before)
                        continue
                    if run(gate):
                        blind.append(gate)
                    target.write_text(before)
                continue
            patched = original.replace(ANCHOR, defect + ANCHOR, 1)
            if len(patched) <= len(original):
                # The whole point: a silent no-op reads exactly like a pass.
                noop.append(gate)
                continue
            PAPER.write_text(patched)
            if run(gate):
                blind.append(gate)
            PAPER.write_text(original)
    finally:
        PAPER.write_text(original)
        # Anything a "create" case may have left if the run died mid-way. An
        # "append" case restores inside its own branch; a crash there would
        # leave a modified sweep, which `git status` shows and this cannot fix
        # without knowing the original.
        for _d in CASES.values():
            if isinstance(_d, tuple) and _d[0] == "create":
                (ROOT / _d[1]).unlink(missing_ok=True)

    print(f"gates tested: {len(CASES) - len(already_red)} of {len(CASES)}")
    for g in CASES:
        if g in already_red:
            print(f"  {g}: UNTESTABLE — already failing on a clean tree")
        elif g in noop:
            print(f"  {g}: INJECTION NO-OP")
        elif g in blind:
            print(f"  {g}: PASSED ITS OWN DEFECT")
        else:
            print(f"  {g}: rejects its defect")

    if noop:
        print(f"\nFAIL: {len(noop)} injection(s) changed nothing — this file is broken, not the gate")
        return 1
    if blind:
        print(f"\nFAIL: {len(blind)} gate(s) accept the defect they exist to catch")
        return 1
    if already_red:
        print(f"\n{len(already_red)} gate(s) could not be tested because they are red")
        print("on a clean tree. That is a finding about the tree, not about them —")
        print("fix the tree, then this harness can say whether they work.")
    if already_red:
        print(f"\nOK: every TESTABLE gate rejects its defect "
              f"({len(CASES) - len(already_red)} of {len(CASES)}); "
              f"{len(already_red)} could not be tested")
    else:
        print("\nOK: every gate rejects the defect it exists to catch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
