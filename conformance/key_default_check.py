#!/usr/bin/env python3
"""Two rules about receipt keys, checked statically.

This exists because both rules were broken at once and neither failure was
visible. W01 found real receipt keys committed to a public repository. Fixing it
meant nulling the RTL default -- which silently broke the testbench that guarded
the receipt, because the testbench had never passed a key of its own and had
been asserting golden tags derived from the default it no longer got. The
security fix disabled its own regression test and the suite stayed green,
because a testbench that produces no output produces no failure either.

RULE 1  An RTL module's RECEIPT_KEY default must be all zero.
        A plausible-looking default is indistinguishable from a real one at a
        glance, which is exactly how the first one survived review. A null
        default fails loudly the moment someone deploys without configuring it.

RULE 2  Every instantiation in a testbench must pass RECEIPT_KEY explicitly.
        Depending on the default couples the test to a value that security work
        is specifically expected to change.

Exit 0 if both hold. Exit 1 otherwise.

Author: Dmitrii Vasilev (@gHashTag)
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RTL_DIRS = ["fpga"]
TB_DIRS = ["formal"]

# parameter [127:0] RECEIPT_KEY = <literal>;
DEFAULT_RE = re.compile(
    r"parameter\s*(?:\[[^\]]*\]\s*)?RECEIPT_KEY\s*=\s*([^,;)\n]+)", re.I)

# A comment that names a module is not an instantiation of it, and a RECEIPT_KEY
# written in a comment is not a key passed. formal/tern_tc_layer_rtl_tb.v names
# fpga/portable/trinet_node_core.v twice in its header, and this check read both
# as instantiations without a key: red on every scheduled run on main from
# 2026-09-28. Comments and string literals are blanked before matching, newlines
# kept, so line numbers still point into the file.
CODE_ONLY_RE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"', re.S)


def code_only(src: str) -> str:
    """src with every comment and string literal blanked, newlines kept."""
    return CODE_ONLY_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def is_null_literal(text: str) -> bool:
    """True for 128'h0, 128'd0, 0, 128'h0000_0000_..., and nothing else."""
    t = text.strip().rstrip(",").strip()
    m = re.fullmatch(r"(?:(\d+)\s*'\s*[hdbo])?\s*([0-9a-fA-F_]+)", t)
    if not m:
        return False
    digits = m.group(2).replace("_", "")
    return set(digits) == {"0"}


def modules_with_key(path: pathlib.Path):
    """Yield (module_name, default_text) for modules declaring RECEIPT_KEY."""
    src = code_only(path.read_text(errors="replace"))
    for mm in re.finditer(r"\bmodule\s+(\w+)", src):
        name = mm.group(1)
        end = src.find("endmodule", mm.end())
        body = src[mm.end(): end if end != -1 else len(src)]
        for dm in DEFAULT_RE.finditer(body):
            yield name, dm.group(1), path


def instantiations(path: pathlib.Path, known: set):
    """Yield (module, passes_key, line) for instantiations of known modules."""
    src = code_only(path.read_text(errors="replace"))
    for name in known:
        # name #( ... ) inst ( ... )   or   name inst ( ... )
        for m in re.finditer(r"\b" + re.escape(name) + r"\s*(#\s*\()?", src):
            # skip the declaration itself
            before = src.rfind("module", 0, m.start())
            if before != -1 and src[before:m.start()].strip() == "module":
                continue
            line = src[:m.start()].count("\n") + 1
            if not m.group(1):
                yield name, False, line, path
                continue
            depth, i = 1, m.end()
            while i < len(src) and depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            yield name, "RECEIPT_KEY" in src[m.end():i], line, path


def main() -> int:
    rtl = [p for d in RTL_DIRS for p in (ROOT / d).rglob("*.v")]
    tbs = [p for d in TB_DIRS for p in (ROOT / d).rglob("*_tb.v")]

    failures, checked_defaults = [], 0
    keyed_modules = set()

    for path in rtl:
        for name, default, p in modules_with_key(path):
            keyed_modules.add(name)
            checked_defaults += 1
            if not is_null_literal(default):
                failures.append(
                    f"RULE 1  {p.relative_to(ROOT)}: module {name} has a "
                    f"non-null RECEIPT_KEY default ({default.strip()}). "
                    f"A key that looks real is how the last one got committed.")

    checked_insts = 0
    for path in tbs:
        for name, passes, line, p in instantiations(path, keyed_modules):
            checked_insts += 1
            if not passes:
                failures.append(
                    f"RULE 2  {p.relative_to(ROOT)}:{line}: instantiates {name} "
                    f"without passing RECEIPT_KEY. It would silently test "
                    f"whatever the default happens to be.")

    print(f"receipt-key check: {checked_defaults} module default(s), "
          f"{checked_insts} testbench instantiation(s) of "
          f"{len(keyed_modules)} keyed module(s)")

    if not keyed_modules:
        print("FAIL: no module declaring RECEIPT_KEY was found. Either the "
              "parameter was renamed or the search paths are wrong; a check "
              "that checks nothing must not pass.")
        return 1

    if failures:
        print()
        for f in failures:
            print("  " + f)
        print(f"\nFAIL: {len(failures)} problem(s)")
        return 1

    print("OK: every default is null and every testbench states its own key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
