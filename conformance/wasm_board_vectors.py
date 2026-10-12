#!/usr/bin/env python3
"""The WASM board cell's 134 requests and their expected words.

Counts, seed, ROM and wire layout come from wasm_board_params (transcribed from
specs/fpga/wasm_board_ax7203.t27 in gHashTag/t27, branch wasm-board). The golden is
NOT read off the spec: interp() below is an independent WebAssembly MVP interpreter
written from the WASM opcode semantics, and every value the spec's own test block
asserts is re-derived through it by spec_pins(). A transcription error in the ROM,
the offsets or the counts fails check_params() or spec_pins() on import.

    python3 wasm_board_vectors.py --summary    counts per set, checked against the spec
    python3 wasm_board_vectors.py --write FILE one hex line per request, for testbenches
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from wasm_board_params import SPEC, ROM, PROG_OFF, PROG_LEN, PROG_NAME  # noqa: E402

MASK32 = 0xFFFFFFFF
MASK64 = (1 << 64) - 1
PROGS, WIRE_PROGS = SPEC["PROGS"], SPEC["WIRE_PROGS"]


def check_params():
    """Relations the transcription must keep; the spec states them in its test blocks."""
    p = []
    if len(ROM) != 101:
        p.append(f"ROM is {len(ROM)} bytes, the spec's programs sum to 101")
    if sum(PROG_LEN) != len(ROM):
        p.append(f"PROG_LEN sums to {sum(PROG_LEN)}, ROM is {len(ROM)} bytes")
    for i in range(PROGS):
        if PROG_OFF[i] + PROG_LEN[i] > SPEC["CODE_PAD"]:
            p.append(f"program {i} runs past CODE_PAD")
        if i + 1 < PROGS and PROG_OFF[i + 1] != PROG_OFF[i] + PROG_LEN[i]:
            p.append(f"program {i + 1} does not start where program {i} ends")
    total = (WIRE_PROGS * (SPEC["PINNED_PER_PROG"] + SPEC["RANDOM_PER_PROG"])
             + SPEC["ERROR_VECS"] + SPEC["WRAP_VECS"])
    if total != SPEC["TOTAL_REQUESTS"]:
        p.append(f"per-program counts give {total}, spec says {SPEC['TOTAL_REQUESTS']}")
    if SPEC["REQ_BYTES"] != 2 + SPEC["BODY_BYTES"] or SPEC["REQ_BYTES"] != 6 + 16 + 16:
        p.append("REQ_BYTES is not 2 + BODY_BYTES / 6 + 16 + 16")
    return p


def _s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


class BadOpcode(Exception):
    """The engine answers RESP_BADOP for these; the interpreter raises the same cases."""


def interp(prog, x, y):
    """One straight-line WASM function body, executed with WebAssembly MVP i32 semantics.

    locals 0 and 1 carry x and y (the cell's calling convention); the value on top of
    the stack at the end opcode is the result, mod 2^32. i32.const decodes signed
    LEB128 generally (the engine accepts only the single-byte form; the ROM has no
    continuation bytes, so the two agree on every program here).
    """
    st, loc = [], [x & MASK32, y & MASK32, 0, 0]
    pc = 0
    while pc < len(prog):
        op = prog[pc]
        pc += 1
        if op in (0x20, 0x21, 0x22):                       # local.get / set / tee
            i = prog[pc]
            pc += 1
            if i >= 4:
                raise BadOpcode("local index")
            if op == 0x20:
                st.append(loc[i])
            elif op == 0x21:
                loc[i] = st.pop()
            else:
                loc[i] = st[-1]
        elif op == 0x41:                                   # i32.const, signed LEB128
            v, shift = 0, 0
            while True:
                b = prog[pc]
                pc += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            if shift < 32 and v & (1 << (shift - 1)):
                v -= 1 << shift
            st.append(v & MASK32)
        elif op == 0x45:                                   # i32.eqz
            st.append(1 if st.pop() == 0 else 0)
        elif op in (0x46, 0x47, 0x48, 0x4A, 0x4D, 0x4F):   # eq ne lt_s gt_s le_s ge_s
            b, a = st.pop(), st.pop()
            sa, sb = _s32(a), _s32(b)
            st.append({0x46: a == b, 0x47: a != b, 0x48: sa < sb,
                       0x4A: sa > sb, 0x4D: sa <= sb, 0x4F: sa >= sb}[op] and 1 or 0)
        elif op in (0x6A, 0x6B, 0x6C, 0x71, 0x72, 0x73):   # add sub mul and or xor
            b, a = st.pop(), st.pop()
            st.append({0x6A: (a + b) & MASK32, 0x6B: (a - b) & MASK32,
                       0x6C: (a * b) & MASK32, 0x71: a & b, 0x72: a | b, 0x73: a ^ b}[op])
        elif op == 0x74:                                   # i32.shl, count mod 32
            b, a = st.pop(), st.pop()
            st.append((a << (b & 31)) & MASK32)
        elif op == 0x75:                                   # i32.shr_s, count mod 32
            b, a = st.pop(), st.pop()
            st.append((_s32(a) >> (b & 31)) & MASK32)
        elif op in (0x0B, 0x0F):                           # end / return
            return st[-1] if st else 0
        else:
            raise BadOpcode(f"opcode {op:#04x}")
    raise BadOpcode("pc past end without end opcode")


def golden(op, x, y):
    """(response byte, low-24-bit word) the cell must answer for one request."""
    if op >= PROGS:
        return SPEC["RESP_BADOP"], 0
    try:
        r = interp(ROM[PROG_OFF[op]:PROG_OFF[op] + PROG_LEN[op]], x, y)
    except BadOpcode:
        return SPEC["RESP_BADOP"], 0
    return SPEC["RESP_OK"], r & ((1 << SPEC["WORD_BITS"]) - 1)


class SplitMix64:
    def __init__(self, seed):
        self.s = seed & MASK64

    def next(self):
        self.s = (self.s + 0x9E3779B97F4A7C15) & MASK64
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)


def spec_pins():
    """Every full result the spec's own test block asserts, re-derived by interp().

    (program, x, y, full u32 result); the wire carries the low 24 bits of these, and
    roundtrip_wrap pins two whose full result leaves that word.
    """
    return [
        (0, 20, 22, 84),                                   # roundtrip_add_double
        (1, 0, 0, 42),
        (2, 5, 0, 0xFFFFFFF5),
        (3, 12, 10, 10),
        (4, 3, 0, 12),
        (5, 7, 7, 1),
        (6, 7, 8, 1),
        (7, 5, 9, 1),
        (8, 0xFFFFFFFB, 0xFFFFFFF7, 1),
        (9, 0, 0, 1),
        (10, 3, 9, 0),
        (11, 4, 5, 17),
        (12, 21, 0, 42),
        (0, 0x7FFFFFFF, 1, 0),                             # roundtrip_wrap
        (3, 0, 1, 0xFFFFFFFF),
    ]


def pinned():
    """Two per program: the spec's own vector where it has one, and one across the
    signed boundary. The second operand of answer/xmul_not/shl_sar/eqz is unused."""
    second = {0: (0x80000000, 1), 1: (0xFFFFFFFF, 0), 2: (0x80000000, 0),
              3: (0, 1), 4: (0xFFFFFFF8, 0), 5: (0x80000000, 0x7FFFFFFF),
              6: (0xFFFFFFFF, 0xFFFFFFFF), 7: (0x80000000, 0x7FFFFFFF),
              8: (0x7FFFFFFF, 0x80000000), 9: (0xFFFFFFFF, 0),
              10: (0x80000000, 0x7FFFFFFF), 11: (0xFFFFFFFD, 3), 12: (0x80000000, 0)}
    rows = {0: [(20, 22)], 1: [(0, 0)], 2: [(5, 0)], 3: [(12, 10)], 4: [(3, 0)],
            5: [(7, 7)], 6: [(7, 8)], 7: [(5, 9)], 8: [(0xFFFFFFFB, 0xFFFFFFF7)],
            9: [(0, 0)], 10: [(3, 9)], 11: [(4, 5)], 12: [(21, 0)]}
    out = []
    for prog in range(WIRE_PROGS):
        out += [("pinned", prog, x, y) for x, y in rows[prog] + [second[prog]]]
    return out


def random_rows(rng):
    out = []
    for prog in range(WIRE_PROGS):
        for _ in range(SPEC["RANDOM_PER_PROG"]):
            out.append(("random", prog, rng.next() & MASK32, rng.next() & MASK32))
    return out


def errors():
    return [("error", 13, 1, 2), ("error", 99, 1, 2)]


def wrap():
    return [("wrap", 0, 0x7FFFFFFF, 1), ("wrap", 3, 0, 1)]


def requests(log=None):
    """[(set, program, x, y, response byte, expected word)] in the order they are sent."""
    problems = check_params()
    for prog, x, y, want in spec_pins():
        kind, word = golden(prog, x, y)
        if kind != SPEC["RESP_OK"] or word != (want & ((1 << SPEC["WORD_BITS"]) - 1)):
            problems.append(f"interp({PROG_NAME[prog]}, {x:#x}, {y:#x}) gives {kind} {word:#x}, "
                            f"the spec's test block asserts {want:#x}")
    if problems:
        raise SystemExit("spec cross-check failed:\n  " + "\n  ".join(problems))
    rng = SplitMix64(SPEC["RANDOM_SEED"])
    rows = pinned() + random_rows(rng) + errors() + wrap()
    out = [(name, prog, x, y, *golden(prog, x, y)) for name, prog, x, y in rows]
    if len(out) != SPEC["TOTAL_REQUESTS"]:
        raise SystemExit(f"{len(out)} requests, spec says {SPEC['TOTAL_REQUESTS']}")
    return out


def frame(op, seq, x, y):
    """SYNC0 SYNC1 OP SEQ x[4] pad[12] y[4] pad[12]: x and y little-endian at X_OFF/Y_OFF."""
    body = bytearray(SPEC["BODY_BYTES"])
    body[0], body[1] = op, seq
    body[SPEC["X_OFF"]:SPEC["X_OFF"] + 4] = (x & MASK32).to_bytes(4, "little")
    body[SPEC["Y_OFF"]:SPEC["Y_OFF"] + 4] = (y & MASK32).to_bytes(4, "little")
    return bytes([SPEC["SYNC0"], SPEC["SYNC1"]]) + bytes(body)


def hex_line(op, x, y, kind, want):
    return f"{op:02x}{x & MASK32:08x}{y & MASK32:08x}{kind:02x}{want:06x}"


def summary(reqs):
    per = {}
    for name, prog, *_ in reqs:
        pname = PROG_NAME[prog] if prog < len(PROG_NAME) else f"op{prog}"
        per[(name, pname)] = per.get((name, pname), 0) + 1
    for (name, prog), n in sorted(per.items()):
        print(f"{name:8s} {prog:11s} {n}")
    bad = sum(1 for r in reqs if r[4] == SPEC["RESP_BADOP"])
    print(f"total {len(reqs)}  BADOP words {bad}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--write", metavar="FILE")
    args = ap.parse_args()
    reqs = requests()
    if args.summary:
        summary(reqs)
    if args.write:
        h = hashlib.sha256()
        with open(args.write, "w") as f:
            for _name, op, x, y, kind, want in reqs:
                line = hex_line(op, x, y, kind, want) + "\n"
                f.write(line)
                h.update(line.encode())
        print(f"wrote {args.write} ({len(reqs)} lines) sha256 {h.hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
