#!/usr/bin/env python3
"""The comparison fleet: eq/ne/lt/gt/le/ge x operand pairs, die vs native (t27 semantics, learned empirically)."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.expanduser('~/trinity-fpga/conformance'))
from wasm_board_params import SPEC
from wasm_board_p2 import SerialLink
from wasm_spec_bridge import extract_entry_body, skip_locals, frames_for
T27B = '/private/var/folders/cm/2n1qdh892xldd1rc2ly1jv8r0000gn/T/opencode/mc-floor/target/release/t27b'
M32 = 0xFFFFFFFF
def s32(v): return v - (1 << 32) if v & 0x80000000 else v

OPS = {
    'eq': ('a == b', lambda a, b: 1 if (a & M32) == (b & M32) else 0),
    'ne': ('a != b', lambda a, b: 1 if (a & M32) != (b & M32) else 0),
    'lt': ('a < b',  lambda a, b: 1 if s32(a) < s32(b) else 0),   # guess signed; die will teach if not
    'gt': ('a > b',  lambda a, b: 1 if s32(a) > s32(b) else 0),
    'le': ('a <= b', lambda a, b: 1 if s32(a) <= s32(b) else 0),
    'ge': ('a >= b', lambda a, b: 1 if s32(a) >= s32(b) else 0),
}
PAIRS = [(7, 3), (-5, 2), (3, 3), (-2147483648, 2147483647), (0, 0), (-7, -3)]
os.makedirs('cmp', exist_ok=True)
ok = fail = 0; mismatches = []
link = SerialLink('/dev/cu.usbserial-130', SPEC['HOST_BAUD'])
for name, (src_op, oracle) in OPS.items():
    src = f'module C{name};\npub fn f(a : i32, b : i32) -> bool {{\n    return {src_op};\n}}\n'
    open(f'cmp/{name}.t27', 'w').write(src)
    r = subprocess.run([T27B, 'build', '--target', 'wasm32', '-o', f'cmp/{name}.wasm', f'cmp/{name}.t27'], capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        print(f'  [skip] {name}: {r.stderr.strip()[:60]}'); fail += 1; continue
    expr = skip_locals(extract_entry_body(open(f'cmp/{name}.wasm','rb').read(), 'f'))
    for a, b in PAIRS:
        want = oracle(a, b) & 0xFFFFFF
        reqs = frames_for(expr, a, b)
        for nm, frame, seq, k, w in reqs[:-1]:
            link.write(frame); link.read(5)
        link.write(reqs[-1][1]); raw = link.read(SPEC['RESP_BYTES'])
        word = int.from_bytes(raw[2:], 'little')
        good = raw[0] == SPEC['RESP_OK'] and word == want
        if good: ok += 1
        else:
            fail += 1; mismatches.append((name, a, b, word, want, expr.hex()))
        print(f'  [{"ok" if good else "FAIL"}] {name}({a},{b}): die {word:06x} want {want:06x} expr {expr.hex()}')
print(f'COMPARE FLEET: {ok} exact, {fail} not')
for m in mismatches[:6]: print('  MISMATCH', m)
