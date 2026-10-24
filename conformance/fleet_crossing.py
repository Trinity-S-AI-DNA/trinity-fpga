#!/usr/bin/env python3
"""The fleet crossing: many const-leaf corpus specs -> wasm32 -> the die, one by one."""
import os, subprocess, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser('~/trinity-fpga/conformance'))
from wasm_board_params import SPEC
from wasm_board_p2 import SerialLink
from wasm_spec_bridge import extract_entry_body, skip_locals, frames_for

T27B = os.path.expanduser("~/t27-wt-wasmport/target/release/t27b")
SPECS = [
    # (name, t27 type, constant, expected low-24 word)
    ("t_bool_t",  "bool", "true",  1),
    ("t_bool_f",  "bool", "false", 0),
    ("t_u8",      "u8",   "200",   200),
    ("t_u16",     "u16",  "65500", None),   # low24(65500)
    ("t_u32_big", "u32",  "4000000000", None),  # low 24 bits only
    ("t_i32_neg", "i32",  "-123456", None),  # low 24 bits of the two's complement
    ("t_u64",     "u64",  "987654321", None),
    ("t_i8",      "i8",   "-100",  None),
    ("t_i64",     "i64",  "-5",    None),
    ("t_u32_max", "u32",  "4294967295", 0xFFFFFF),  # low 24 bits = all ones
]
# P4 (2026-10-24): 0x43/0x44 joined as raw-bit words -- the float crossing is now
# POSITIVE. The die pushes an f32.const's four IEEE-754 bytes as the u32 word
# wholesale and an f64.const's low 32; the expected low-24 answer is computed
# from the constant's real bits, never hand-typed.
FLOATS = [
    ("f_f32", "f32", "2.5"),              # bits 0x40200000 -> low24 0x200000
    ("f_f64", "f64", "3.141592653589793"),  # bits 0x400921FB54442D18, low32 0x54442D18 -> low24 0x442D18
]

def float_word(ty, const):
    v = float(const)
    fmt = ">f" if ty == "f32" else ">d"
    ints = ">I" if ty == "f32" else ">Q"
    bits = struct.unpack(ints, struct.pack(fmt, v))[0]
    return bits & 0xFFFFFF

os.makedirs("fleet", exist_ok=True)
ok = fail = refused = 0
link = SerialLink("/dev/cu.usbserial-130", SPEC["HOST_BAUD"])
print(f"# fleet: {len(SPECS)} int specs + {len(FLOATS)} float crossings (P4 raw-bit floor)")
for name, ty, const, want in SPECS + [(n, t, c, None) for n, t, c in FLOATS]:
    src = f'module F;\npub fn v() -> {ty} {{\n    return {const};\n}}\n\ntest t {{\n    assert v() == {const};\n}}\n'
    open(f"fleet/{name}.t27", "w").write(src)
    r = subprocess.run([T27B, "build", "--target", "wasm32", "-o", f"fleet/{name}.wasm", f"fleet/{name}.t27"],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        print(f"  [skip] {name}: emitter refused ({r.stderr.strip()[:60]})"); fail += 1; continue
    mod = open(f"fleet/{name}.wasm", "rb").read()
    expr = skip_locals(extract_entry_body(mod, "v"))
    # native value via t27b run
    rn = subprocess.run([T27B, "run", f"fleet/{name}.t27", "v"], capture_output=True, text=True, cwd=HERE)
    # die answer
    reqs = frames_for(expr)
    res_load = None
    for nm, frame, seq, k, w in reqs[:-1]:
        link.write(frame); raw = link.read(5)
        if raw[0] != SPEC["RESP_OK"]: break
    link.write(reqs[-1][1]); raw = link.read(SPEC["RESP_BYTES"])
    word = int.from_bytes(raw[2:], "little")
    if ty in ("f32", "f64"):
        exp = float_word(ty, const)
        verdict = "ok" if (raw[0] == SPEC["RESP_OK"] and word == exp) else "FAIL"
        if verdict == "ok": ok += 1
        else: fail += 1
        print(f"  [{verdict}] {name} ({ty} {const}): die word {word:06x} expect {exp:06x} -- raw-bit floor")
        continue
    exp = want if want is not None else None
    native = rn.stdout.strip()
    verdict = "ok" if (raw[0] == SPEC["RESP_OK"] and exp is not None and word == exp) else "CHECK"
    if verdict == "ok": ok += 1
    else: fail += 1
    print(f"  [{verdict}] {name} ({ty} {const}): die word {word:06x} expect {exp!r} native-run[{rn.returncode}] {native[:30]!r}")
print(f"FLEET RESULT: {ok} exact, {fail} not, {refused} honest refusals")
