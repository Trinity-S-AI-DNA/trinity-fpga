"""Host-side material for the P2 WASM board conformance run.

The 128-byte user image `uprog` is transcribed from the spec
(gHashTag/t27 specs/fpga/wasm_board_ax7203.t27); none of it is in the bitstream.
`interp_user` is an independent interpreter for its programs -- independent of the
engine, and pinned on import to the expected words the spec's test
p2_roundtrip_user asserts, so this file and the spec cannot silently drift apart.

Engine semantics that differ from the WebAssembly standard are matched to the
engine, because the point of the run is to test the engine: i32.div_s of
INT32_MIN by -1 answers 0 (the spec pins seq 57 to 0; the standard traps), and
the comparison family keeps the STANDARD numbering (#9354: 0x4C le_s, 0x4D le_u, 0x4E ge_s, 0x4F ge_u),
which is not the standard's placement.

Requests are the 29 of the spec's p2_roundtrip_user and p2_roundtrip_errors:
four OP_LOAD_PAGE frames, twenty-one OP_RUN_USER runs, two engine traps and two
host-side rejects. SEQ is part of each vector -- the page number rides the SEQ
slot of a load -- so frames are built whole, not stamped by the runner.

    python3 wasm_board_p2_vectors.py --summary    counts, checked against the spec
"""

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from wasm_board_params import SPEC  # noqa: E402

MASK32 = 0xFFFFFFFF
WORD_BITS = SPEC["WORD_BITS"]          # responses carry the low 24 bits
WORD_MASK = (1 << WORD_BITS) - 1
SYNC = bytes([SPEC["SYNC0"], SPEC["SYNC1"]])
X_OFF = SPEC["X_OFF"]
Y_OFF = SPEC["Y_OFF"]
RESP_OK = SPEC["RESP_OK"]
RESP_BADOP = SPEC["RESP_BADOP"]

OP_LOAD_PAGE = 0xFE
OP_RUN_USER = 0xFD
CODE_PAD = 128
PAGES = 4
ERR_DIV = 4
ERR_LEB = 6
ERR_PROG = 7

# Engine opcodes the user image exercises (standard numbering, master #9354).
OP_END = 0x0B
OP_LOCAL_GET = 0x20
OP_I32_CONST = 0x41
OP_CLZ = 0x67
OP_CTZ = 0x68
OP_POPCNT = 0x69
OP_ADD = 0x6A
OP_DIV_S = 0x6D
OP_DIV_U = 0x6E
OP_REM_S = 0x6F
OP_REM_U = 0x70
OP_SHR_U = 0x76
OP_ROTL = 0x77
OP_ROTR = 0x78
OP_LT_U = 0x49
OP_GT_U = 0x4B
OP_LE_S = 0x4C
OP_LE_U = 0x4D
OP_GE_S = 0x4E
OP_GE_U = 0x4F

# Offsets into uprog, transcribed from the spec's pub consts.
UP_LEB3 = 0x00
UP_DIV_U = 0x08
UP_REM_U = 0x0E
UP_DIV_S = 0x14
UP_REM_S = 0x1A
UP_ROTL = 0x20
UP_ROTR = 0x26
UP_SHR_U = 0x2C
UP_POPCNT = 0x32
UP_CLZ = 0x36
UP_CTZ = 0x3A
UP_LT_U = 0x3E
UP_GT_U = 0x44
UP_LE_S = 0x4A
UP_GE_S = 0x50
UP_LEB5 = 0x56
UP_DIV0 = 0x60
UP_LEB6 = 0x66
UP_LEB2 = 0x6E
UP_END = 0x75

# The user image: host-side only, pushed by OP_LOAD_PAGE before OP_RUN_USER runs
# it. Transcribed from the spec's `var uprog` initializer, byte for byte.
UPROG = bytes([
    0x41, 0xE5, 0x8E, 0x26, 0x20, 0x00, 0x6A, 0x0B,          # UP_LEB3
    0x20, 0x00, 0x20, 0x01, 0x6E, 0x0B,                      # UP_DIV_U
    0x20, 0x00, 0x20, 0x01, 0x70, 0x0B,                      # UP_REM_U
    0x20, 0x00, 0x20, 0x01, 0x6D, 0x0B,                      # UP_DIV_S
    0x20, 0x00, 0x20, 0x01, 0x6F, 0x0B,                      # UP_REM_S
    0x20, 0x00, 0x41, 0x04, 0x77, 0x0B,                      # UP_ROTL
    0x20, 0x00, 0x41, 0x04, 0x78, 0x0B,                      # UP_ROTR
    0x20, 0x00, 0x41, 0x08, 0x76, 0x0B,                      # UP_SHR_U
    0x20, 0x00, 0x69, 0x0B,                                  # UP_POPCNT
    0x20, 0x00, 0x67, 0x0B,                                  # UP_CLZ
    0x20, 0x00, 0x68, 0x0B,                                  # UP_CTZ
    0x20, 0x00, 0x20, 0x01, 0x49, 0x0B,                      # UP_LT_U
    0x20, 0x00, 0x20, 0x01, 0x4B, 0x0B,                      # UP_GT_U
    0x20, 0x00, 0x20, 0x01, 0x4C, 0x0B,                      # UP_LE_S
    0x20, 0x00, 0x20, 0x01, 0x4E, 0x0B,                      # UP_GE_S
    0x41, 0xF8, 0xAC, 0xD1, 0x91, 0x01, 0x20, 0x00, 0x6A, 0x0B,  # UP_LEB5
    0x20, 0x00, 0x41, 0x00, 0x6E, 0x0B,                      # UP_DIV0
    0x41, 0x80, 0x80, 0x80, 0x80, 0x80, 0x78, 0x0B,          # UP_LEB6
    0x41, 0xC0, 0x7F, 0x20, 0x00, 0x6A, 0x0B,                # UP_LEB2
] + [0] * (CODE_PAD - 117))

# First opcode of each program, so a transcription slip cannot pass as layout.
_FIRST = {
    UP_LEB3: OP_I32_CONST, UP_DIV_U: OP_LOCAL_GET, UP_REM_U: OP_LOCAL_GET,
    UP_DIV_S: OP_LOCAL_GET, UP_REM_S: OP_LOCAL_GET, UP_ROTL: OP_LOCAL_GET,
    UP_ROTR: OP_LOCAL_GET, UP_SHR_U: OP_LOCAL_GET, UP_POPCNT: OP_LOCAL_GET,
    UP_CLZ: OP_LOCAL_GET, UP_CTZ: OP_LOCAL_GET, UP_LT_U: OP_LOCAL_GET,
    UP_GT_U: OP_LOCAL_GET, UP_LE_S: OP_LOCAL_GET, UP_GE_S: OP_LOCAL_GET,
    UP_LEB5: OP_I32_CONST, UP_DIV0: OP_LOCAL_GET, UP_LEB6: OP_I32_CONST,
    UP_LEB2: OP_I32_CONST,
}


class UserTrap(Exception):
    """The engine stopped the run and answered RESP_BADOP: ('div', e) or ('leb', e)."""

    def __init__(self, reason, err):
        super().__init__(reason)
        self.reason = reason
        self.err = err


def _s32(v):
    v &= MASK32
    return v - 0x100000000 if v & 0x80000000 else v


def _clz32(v):
    return 32 - v.bit_length()


def _ctz32(v):
    return 32 if v == 0 else (v & -v).bit_length() - 1


def interp_user(code, off, length, x, y):
    """Run code[off:off+length] with locals (x, y, 0, 0); return the u32 answer.

    Mirrors the engine: rotate/shift counts mask by 31, i32.const reads signed
    LEB128 with a five-byte ceiling, div/rem by zero traps ERR_DIV, and
    div_s(INT32_MIN, -1) answers 0 rather than trapping.
    """
    st = []
    loc = [x & MASK32, y & MASK32, 0, 0]
    pc = off
    end = off + length
    while pc < end:
        op = code[pc]
        pc += 1
        if op == OP_END:
            return st.pop() if st else 0
        if op == OP_LOCAL_GET:
            st.append(loc[code[pc]])
            pc += 1
        elif op == OP_I32_CONST:
            value = 0
            for i in range(5):
                if pc >= end:
                    raise UserTrap("const runs past length", ERR_LEB)
                b = code[pc]
                pc += 1
                value |= (b & 0x7F) << (7 * i)
                if not b & 0x80:
                    if i < 4 and b & 0x40:
                        value -= 1 << (7 * (i + 1))
                    st.append(value & MASK32)
                    break
            else:
                raise UserTrap("const longer than five bytes", ERR_LEB)
        elif op == OP_ADD:
            b, a = st.pop(), st.pop()
            st.append((a + b) & MASK32)
        elif op == OP_DIV_U:
            b, a = st.pop(), st.pop()
            if b == 0:
                raise UserTrap("divide by zero", ERR_DIV)
            st.append(a // b)
        elif op == OP_REM_U:
            b, a = st.pop(), st.pop()
            if b == 0:
                raise UserTrap("divide by zero", ERR_DIV)
            st.append(a % b)
        elif op == OP_DIV_S:
            b, a = st.pop(), st.pop()
            sa, sb = _s32(a), _s32(b)
            if sb == 0:
                raise UserTrap("divide by zero", ERR_DIV)
            q = abs(sa) // abs(sb)
            if (sa < 0) != (sb < 0):
                q = -q
            st.append(q & MASK32)     # INT32_MIN / -1 wraps to 0x80000000; engine answers 0
            if sa == -0x80000000 and sb == -1:
                st[-1] = 0
        elif op == OP_REM_S:
            b, a = st.pop(), st.pop()
            sa, sb = _s32(a), _s32(b)
            if sb == 0:
                raise UserTrap("divide by zero", ERR_DIV)
            r = abs(sa) % abs(sb)
            if sa < 0:
                r = -r
            st.append(r & MASK32)
        elif op == OP_SHR_U:
            b, a = st.pop(), st.pop()
            st.append(a >> (b & 31))
        elif op in (OP_ROTL, OP_ROTR):
            b, a = st.pop(), st.pop()
            n = b & 31
            st.append(((a << n) | (a >> (32 - n))) & MASK32 if n else a) if op == OP_ROTL else \
                st.append(((a >> n) | (a << (32 - n))) & MASK32 if n else a)
        elif op == OP_CLZ:
            st.append(_clz32(st.pop()))
        elif op == OP_CTZ:
            st.append(_ctz32(st.pop()))
        elif op == OP_POPCNT:
            st.append(bin(st.pop()).count("1"))
        elif op in (OP_LT_U, OP_GT_U, OP_LE_U, OP_GE_U):
            b, a = st.pop(), st.pop()
            st.append(1 if (a < b if op == OP_LT_U else
                            a > b if op == OP_GT_U else
                            a <= b if op == OP_LE_U else a >= b) else 0)
        elif op in (OP_LE_S, OP_GE_S):
            b, a = st.pop(), st.pop()
            sa, sb = _s32(a), _s32(b)
            st.append(1 if (sa <= sb if op == OP_LE_S else sa >= sb) else 0)
        else:
            raise UserTrap(f"opcode {op:02x} not in the user image", ERR_PROG)
    raise UserTrap("length guard fired before end", ERR_PROG)


# The spec's pins, transcribed from test p2_roundtrip_user: seq -> (kind, word).
# Loads answer word = page + 1; runs answer the interpreter result, low 24 bits.
PINS = {
    **{p: (RESP_OK, p + 1) for p in range(PAGES)},
    40: (RESP_OK, 0x098766), 41: (RESP_OK, 14), 42: (RESP_OK, 2),
    43: (RESP_OK, 0xFFFFF2), 44: (RESP_OK, 0xFFFFFE), 45: (RESP_OK, 0x456781),
    46: (RESP_OK, 0x234567), 47: (RESP_OK, 0x123456), 48: (RESP_OK, 13),
    49: (RESP_OK, 3), 50: (RESP_OK, 3), 51: (RESP_OK, 0), 52: (RESP_OK, 1),
    53: (RESP_OK, 0), 54: (RESP_OK, 1), 55: (RESP_OK, 0x345679),
    56: (RESP_OK, 0xFFFFC1), 57: (RESP_OK, 0), 58: (RESP_OK, 0),
    59: (RESP_OK, 32), 60: (RESP_OK, 32),
    61: (RESP_BADOP, 0), 62: (RESP_BADOP, 0),
    4: (RESP_BADOP, 0), 63: (RESP_BADOP, 0),
}

# (seq, offset, length, x, y) for every pinned OP_RUN_USER run.
_RUNS = [
    (40, UP_LEB3, 8, 1, 0),
    (41, UP_DIV_U, 6, 100, 7),
    (42, UP_REM_U, 6, 100, 7),
    (43, UP_DIV_S, 6, 0xFFFFFF9C, 7),
    (44, UP_REM_S, 6, 0xFFFFFF9C, 7),
    (45, UP_ROTL, 6, 0x12345678, 0),
    (46, UP_ROTR, 6, 0x12345678, 0),
    (47, UP_SHR_U, 6, 0x12345678, 0),
    (48, UP_POPCNT, 4, 0x12345678, 0),
    (49, UP_CLZ, 4, 0x12345678, 0),
    (50, UP_CTZ, 4, 0x12345678, 0),
    (51, UP_LT_U, 6, 0x80000000, 1),
    (52, UP_GT_U, 6, 0x80000000, 1),
    (53, UP_LE_S, 6, 1, 0x80000000),
    (54, UP_GE_S, 6, 1, 0x80000000),
    (55, UP_LEB5, 10, 1, 0),
    (56, UP_LEB2, 7, 1, 0),
    (57, UP_DIV_S, 6, 0x80000000, 0xFFFFFFFF),
    (58, UP_REM_S, 6, 0x80000000, 0xFFFFFFFF),
    (59, UP_CLZ, 4, 0, 0),
    (60, UP_CTZ, 4, 0, 0),
    (61, UP_DIV0, 6, 5, 0),
    (62, UP_LEB6, 8, 5, 0),
]


def golden_run(off, length, x, y):
    """(kind, word) the engine answers for an OP_RUN_USER, or None for a host reject."""
    try:
        return RESP_OK, interp_user(UPROG, off, length, x, y) & WORD_MASK
    except UserTrap:
        return RESP_BADOP, 0


def check_pins(pins=PINS, runs=_RUNS, problems=None):
    """Cross-check interp_user against the spec's pins. Returns a problem list."""
    problems = problems if problems is not None else []
    if len(UPROG) != CODE_PAD:
        problems.append(f"uprog is {len(UPROG)} bytes, CODE_PAD is {CODE_PAD}")
    for off, first in _FIRST.items():
        if UPROG[off] != first:
            problems.append(f"uprog[{off:#04x}] is {UPROG[off]:#04x}, expected {first:#04x}")
    for (seq, off, length, x, y) in runs:
        want = pins.get(seq)
        if want is None:
            continue
        got = golden_run(off, length, x, y)
        if got != want:
            problems.append(f"seq {seq}: interpreter says {got}, spec pins {want}")
    return problems


def load_frame(page):
    """OP_LOAD_PAGE: the page number rides the SEQ slot, code bytes from body[4]."""
    body = bytearray(36)
    body[0] = OP_LOAD_PAGE
    body[1] = page
    page_bytes = UPROG[page * 32:(page + 1) * 32]
    body[4:36] = page_bytes + bytes(32 - len(page_bytes))   # pages past the image load zeros
    return SYNC + bytes(body), page      # the engine echoes the SEQ byte, which is the page


def run_frame(seq, length, off, x, y):
    """OP_RUN_USER: length at body[2], offset at body[3], x/y at X_OFF/Y_OFF."""
    body = bytearray(36)
    body[0] = OP_RUN_USER
    body[1] = seq
    body[2] = length
    body[3] = off
    body[X_OFF:X_OFF + 4] = (x & MASK32).to_bytes(4, "little")
    body[Y_OFF:Y_OFF + 4] = (y & MASK32).to_bytes(4, "little")
    return SYNC + bytes(body), seq


def requests():
    """The 29 requests: 4 loads, 21 runs, 2 engine traps, 2 host-side rejects."""
    reqs = []
    for page in range(PAGES):
        reqs.append((f"load{page}",) + load_frame(page) + PINS[page])
    for (seq, off, length, x, y) in _RUNS:
        reqs.append((f"run{seq}",) + run_frame(seq, length, off, x, y) + PINS[seq])
    bad_page = bytearray(load_frame(PAGES)[0])
    bad_page[3] = PAGES                      # page 4 in the SEQ slot
    reqs.append(("load4", bytes(bad_page), PAGES, *PINS[4]))
    reqs.append(("run63",) + run_frame(63, 16, 120, 0, 0) + PINS[63])
    return reqs


def summary(reqs):
    from collections import Counter
    kinds = Counter(r[3] for r in reqs)
    print(f"{len(reqs)} requests: {kinds[RESP_OK]} ok, {kinds[RESP_BADOP]} badop")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    problems = check_pins()
    for p in problems:
        print(f"PROBLEM: {p}")
    if problems:
        return 1
    reqs = requests()
    if args.summary:
        summary(reqs)
    else:
        print(f"pins hold, {len(reqs)} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
