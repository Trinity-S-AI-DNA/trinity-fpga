#!/usr/bin/env python3
"""A WASM interpreter executing real WebAssembly opcode bytes on the ALINX AX7203.

The bitstream is wasm_board_ax7203 (the t27-generated fpga_wasm_board core plus the
STARTUPE2 top). Requests, expected words, the wire format and the pass line come from
specs/fpga/wasm_board_ax7203.t27 in gHashTag/t27 through wasm_board_params; the expected
words come from wasm_board_vectors, whose interp() is an independent WebAssembly MVP
interpreter over the same ROM bytes.

    python3 wasm_board_ax7203.py --self-test
    python3 wasm_board_ax7203.py --port /dev/cu.usbserial-1130

The engine answers one frame at a time: a request that arrives while it is executing or
transmitting is dropped (the spec's drops counter), so the host runs stop-and-wait,
window 1. Each response must be RESP_OK (or RESP_BADOP for the two error vectors), echo
the request's SEQ and carry the interpreter's low 24 bits. A wrong word is a fail and the
run goes on; a short, unframed or out-of-sequence response means the link lost bytes and
every request not answered by then is lost.

Pass: exactly SPEC["PASS_LINE"], and exit 0. A pass says the board fetches, decodes and
executes those WASM opcodes exactly, i32.mul included (a 32-clock shift-add sequence on
the board, one cycle in the interpreter).
"""
import argparse
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from wasm_board_params import SPEC, SPEC_SHA256  # noqa: E402
import wasm_board_vectors as vectors  # noqa: E402

SYNC = bytes([SPEC["SYNC0"], SPEC["SYNC1"]])
RESP_OK, RESP_BADOP = SPEC["RESP_OK"], SPEC["RESP_BADOP"]
RESP_BYTES, REQ_BYTES = SPEC["RESP_BYTES"], SPEC["REQ_BYTES"]
PROG_NAME = SPEC["PROG_NAME"]
WINDOW = 1          # stop-and-wait: the engine drops a frame that lands while it is busy
SHOW = 20


def vectors_sha256(reqs):
    """Same bytes as `wasm_board_vectors.py --write`, so a run names the exact request list."""
    h = hashlib.sha256()
    for _name, op, x, y, kind, want in reqs:
        h.update((vectors.hex_line(op, x, y, kind, want) + "\n").encode())
    return h.hexdigest()


class SerialLink:
    def __init__(self, port, baud):
        import serial
        self.ser = serial.Serial(port, baud, timeout=2)
        self.ser.reset_input_buffer()

    def write(self, b):
        self.ser.write(b)

    def read(self, n):
        return self.ser.read(n)

    def waiting(self):
        return self.ser.in_waiting


def run(link, reqs, window, log=print):
    n = len(reqs)
    per = {}
    for name, prog, *_ in reqs:
        per.setdefault((name, prog), [0, 0])[1] += 1
    ok = fails = sent = answered = 0
    first_bad, stop = [], None
    while answered < n:
        while sent < n and sent - answered < window:
            _name, prog, x, y, _kind, _want = reqs[sent]
            link.write(vectors.frame(prog, sent & 0xFF, x, y))
            sent += 1
        raw = link.read(RESP_BYTES)
        seq = answered & 0xFF
        if len(raw) < RESP_BYTES or raw[0] not in (RESP_OK, RESP_BADOP) or raw[1] != seq:
            waiting = getattr(link, "waiting", lambda: None)()
            stop = (f"after {answered} answers, {sent - answered} in flight: expected "
                    f"{RESP_OK:02x} {seq:02x} ..., read {len(raw)} bytes [{raw.hex(' ')}]"
                    + (f", {waiting} more waiting" if waiting is not None else ""))
            break
        name, prog, x, y, want_kind, want = reqs[answered]
        y_word = int.from_bytes(raw[2:], "little")
        if raw[0] == want_kind and y_word == want:
            ok += 1
            per[(name, prog)][0] += 1
        else:
            fails += 1
            if len(first_bad) < SHOW:
                first_bad.append(f"request {answered} {name} "
                                 f"{PROG_NAME[prog] if prog < len(PROG_NAME) else prog} "
                                 f"x {x:#010x} y {y:#010x}: "
                                 f"board {raw[0]:02x} y {y_word:06x}, reference {want_kind:02x} {want:06x}")
        answered += 1
    return dict(n=n, ok=ok, fails=fails, lost=n - answered, sent=sent, answered=answered,
                per=per, first_bad=first_bad, stop=stop, window=window)


def result_line(res):
    return f"WASM BOARD RESULT: {res['ok']}/{res['n']} responses exact (fails={res['fails']}, lost={res['lost']})"


def report(res, elapsed=None, log=print):
    for (name, prog), (good, tot) in sorted(res["per"].items()):
        tag = "ok  " if good == tot else "FAIL"
        pname = PROG_NAME[prog] if prog < len(PROG_NAME) else f"op{prog}"
        log(f"  [{tag}] {name:8s} {pname:11s} {good:3d}/{tot}")
    log(f"sent {res['sent']}, answered {res['answered']}, window {res['window']}")
    if elapsed:
        log(f"elapsed {elapsed:.1f} s ({res['answered'] / elapsed:.0f} answers/s)")
    for m in res["first_bad"]:
        log(f"  ! {m}")
    if res["stop"]:
        log(f"  ! LINK: {res['stop']}")
    log(result_line(res))
    return res["ok"] == res["n"] and res["fails"] == 0 and res["lost"] == 0


# ---------------------------------------------------------------------------
# Self-test: a model board behind a fake port, and each check shown able to fail
# ---------------------------------------------------------------------------

class ModelBoard:
    """wasm_board_ax7203 in Python: the frame parser, and vectors.golden as the engine.

    `fault` breaks exactly one thing at response `at`, so the self-test can show every
    check firing.
    """

    def __init__(self, fault=None, at=7):
        self.fault, self.at = fault, at
        self.rx, self.out, self.n = b"", bytearray(), 0

    def write(self, b):
        self.rx += b
        while len(self.rx) >= REQ_BYTES:
            fr, self.rx = self.rx[:REQ_BYTES], self.rx[REQ_BYTES:]
            assert fr[:2] == SYNC
            prog, seq = fr[2], fr[3]
            # X_OFF/Y_OFF index the body (OP SEQ SA SB A0..A15 B0..B15); on the wire
            # that is two sync bytes further in.
            xa, ya = 2 + SPEC["X_OFF"], 2 + SPEC["Y_OFF"]
            x = int.from_bytes(fr[xa:xa + 4], "little")
            y = int.from_bytes(fr[ya:ya + 4], "little")
            kind, word = vectors.golden(prog, x, y)
            resp = bytearray([kind, seq, *word.to_bytes(3, "little")])
            hit = self.n == self.at
            self.n += 1
            if hit and self.fault == "lie":
                resp[2] ^= 1
            if hit and self.fault == "badop":
                resp[0] = RESP_BADOP if kind == RESP_OK else RESP_OK
            if hit and self.fault == "seq":
                resp[1] ^= 0x01
            if hit and self.fault == "drop":
                continue
            if hit and self.fault == "slip":
                resp = resp[2:]
            self.out += resp

    def read(self, n):
        out, self.out = bytes(self.out[:n]), self.out[n:]
        return out

    def waiting(self):
        return len(self.out)


def self_test():
    reqs = vectors.requests()
    quiet = lambda *_: None  # noqa: E731
    good = True

    def check(cond, what):
        nonlocal good
        print(f"  [{'ok  ' if cond else 'FAIL'}] {what}")
        good = good and cond

    print(f"self-test on {len(reqs)} requests, model board, window {WINDOW}")
    res = run(ModelBoard(), reqs, WINDOW, quiet)
    check(report(res, log=quiet) and res["ok"] == len(reqs), "clean model board passes every request")
    for fault, want in (("lie", "fails=1"), ("badop", "fails=1"), ("seq", "lost"),
                        ("drop", "lost"), ("slip", "lost")):
        res = run(ModelBoard(fault=fault), reqs, WINDOW, quiet)
        passed = report(res, log=quiet)
        if want == "fails=1":
            cond = not passed and res["fails"] == 1 and res["lost"] == 0
        else:
            cond = not passed and res["lost"] > 0 and res["stop"] is not None and res["answered"] == 7
        check(cond, f"fault {fault:6s} caught: {result_line(res)}")
    # A board executing the wrong program must fail: shift every program id up by one.
    wrong = ModelBoard()
    _golden = vectors.golden
    vectors.golden = lambda op, x, y: _golden(min(op + 1, SPEC["PROGS"] - 1), x, y)
    try:
        res = run(wrong, reqs, WINDOW, quiet)
    finally:
        vectors.golden = _golden
    check(not report(res, log=quiet) and res["fails"] > 0 and res["lost"] == 0,
          f"wrong program caught: {result_line(res)}")
    # The two error vectors come back BADOP SEQ 0 0 0.
    for prog in (13, 99):
        mb = ModelBoard()
        mb.write(vectors.frame(prog, 9, 1, 2))
        check(mb.read(RESP_BYTES) == bytes([RESP_BADOP, 9, 0, 0, 0]),
              f"program {prog} answered BADOP SEQ 0 0 0")
    check(vectors.frame(5, 2, 0x0A0B0C0D, 0x01020304)[:14]
          == bytes([SPEC["SYNC0"], SPEC["SYNC1"], 5, 2, 0, 0,
                    0x0D, 0x0C, 0x0B, 0x0A, 0, 0, 0, 0]),
          "frame layout SYNC0 SYNC1 OP SEQ SA SB x[4] LE, pad to Y_OFF")
    print("SELF-TEST " + ("PASS" if good else "FAIL"))
    return good


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/cu.usbserial-1130")
    ap.add_argument("--baud", type=int, default=SPEC["HOST_BAUD"])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return 0 if self_test() else 1

    print(f"# spec {SPEC['RUN_NAME']} sha256 {SPEC_SHA256}")
    print(f"# port {args.port} baud {args.baud} (wire {SPEC['WIRE_BAUD']}) window {WINDOW}")
    reqs = vectors.requests()
    print(f"# vectors sha256 {vectors_sha256(reqs)} ({len(reqs)} requests)")
    link = SerialLink(args.port, args.baud)
    t0 = time.monotonic()
    res = run(link, reqs, WINDOW)
    elapsed = time.monotonic() - t0
    passed = report(res, elapsed)
    if elapsed > SPEC["RUN_LIMIT_S"]:
        print(f"PROBLEM: {elapsed:.0f} s is beyond the spec's RUN_LIMIT_S {SPEC['RUN_LIMIT_S']}")
    if passed and result_line(res) != SPEC["PASS_LINE"]:
        print(f"PROBLEM: a pass must print the spec's line, {SPEC['PASS_LINE']!r}")
        return 1
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
