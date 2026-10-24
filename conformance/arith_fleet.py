#!/usr/bin/env python3
"""The arithmetic fleet: 2-param i32 specs (sub/mul/div) x operand pairs, die vs native."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser('~/trinity-fpga/conformance'))
from wasm_board_params import SPEC
from wasm_board_p2 import SerialLink
from wasm_spec_bridge import extract_entry_body, skip_locals, frames_for

T27B = '/private/var/folders/cm/2n1qdh892xldd1rc2ly1jv8r0000gn/T/opencode/genfix/target/release/t27b'
M32 = 0xFFFFFFFF
def s32(v): return v - (1 << 32) if v & 0x80000000 else v
def wrap(v): return v & M32

OPS = {
    'sub': ('a - b', lambda a, b: wrap(s32(a) - s32(b))),
    'mul': ('a * b', lambda a, b: wrap(s32(a) * s32(b))),
    'div': ('a / b', None),  # special: cell semantics below
}
def div_cell(a, b):
    # the emitter lowers t27's / on i32 to 0x6E i32.div_u: UNSIGNED division
    if (b & M32) == 0: return None      # ERR_DIV trap
    return (a & M32) // (b & M32)

PAIRS = [(7, 3), (-5, 2), (100, -7), (1073741824, 1073741824), (-2147483648, -1), (12, 0)]

os.makedirs('arith', exist_ok=True)
ok = fail = traps = 0
link = SerialLink('/dev/cu.usbserial-130', SPEC['HOST_BAUD'])
for name, (expr_src, _py) in OPS.items():
    src = f'module Arith{name};\npub fn f(a : i32, b : i32) -> i32 {{\n    return {expr_src};\n}}\n'
    open(f'arith/{name}.t27', 'w').write(src)
    r = subprocess.run([T27B, 'build', '--target', 'wasm32', '-o', f'arith/{name}.wasm', f'arith/{name}.t27'],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        print(f'  [skip] {name}: emitter refused: {r.stderr.strip()[:70]}'); fail += 1; continue
    mod = open(f'arith/{name}.wasm', 'rb').read()
    expr = skip_locals(extract_entry_body(mod, 'f'))
    for a, b in PAIRS:
        if name == 'div':
            want = div_cell(a, b)
            if want is None:
                kind_note = 'trap expected (ERR_DIV)'
        else:
            want = OPS[name][1](a, b)
            kind_note = ''
        want24 = None if want is None else want & 0xFFFFFF
        reqs = frames_for(expr, a, b)
        for nm, frame, seq, k, w in reqs[:-1]:
            link.write(frame); link.read(5)
        link.write(reqs[-1][1]); raw = link.read(SPEC['RESP_BYTES'])
        word = int.from_bytes(raw[2:], 'little')
        if want24 is None:
            verdict = "TRAP" if raw[0] == SPEC["RESP_BADOP"] else "UNEXPECTED-OK"
            if verdict == 'TRAP': traps += 1
            else: fail += 1
            print(f'  [{verdict}] {name}({a},{b}): kind {raw[0]:02x} -- {kind_note}')
            continue
        good = raw[0] == SPEC['RESP_OK'] and word == want24
        if good: ok += 1
        else: fail += 1
        print(f'  [{"ok" if good else "FAIL"}] {name}({a},{b}): die {word:06x} want {want24:06x} expr {expr.hex()}')
print(f'ARITH FLEET: {ok} exact, {fail} not, {traps} honest traps')
