"""Board conformance run for the P2 WASM cell: dynamic programs over the wire.

OP_LOAD_PAGE pushes a 32-byte page of the 128-byte user image into the engine's
code RAM; OP_RUN_USER executes from any offset of it. Frames, expected words and
the pass line come from gHashTag/t27 specs/fpga/wasm_board_ax7203.t27; the golden
answers come from wasm_board_p2_vectors, whose interpreter is pinned to the same
spec's test asserts on import.

Same wire discipline as the MVP run (wasm_board_ax7203.py): stop-and-wait, one
request in flight, a response is 5 bytes RESP_KIND SEQ WORD[3] echoing the
request's SEQ and carrying the answer's low 24 bits. A wrong word is a fail and
the run goes on; a short, unframed or out-of-sequence response means the link
lost bytes and the run stops. SEQ is part of each P2 vector -- the page number
rides the SEQ slot of a load -- so the runner checks the echo against the
frame's own SEQ, not a counter.

Pass: exactly PASS_LINE, and exit 0. A pass says the board stores pages, runs
user code from them, traps divide-by-zero and overlong constants, and rejects
bad pages and runs past the image -- 29 requests, every answer exact.

    python3 wasm_board_p2.py --self-test
    python3 wasm_board_p2.py --port /dev/cu.usbserial-130
"""

import argparse
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from wasm_board_params import SPEC, SPEC_SHA256  # noqa: E402
import wasm_board_p2_vectors as vectors  # noqa: E402

SYNC = bytes([SPEC["SYNC0"], SPEC["SYNC1"]])
RESP_OK, RESP_BADOP = SPEC["RESP_OK"], SPEC["RESP_BADOP"]
RESP_BYTES, REQ_BYTES = SPEC["RESP_BYTES"], SPEC["REQ_BYTES"]
WINDOW = 1          # stop-and-wait: the engine drops a frame that lands while it is busy
SHOW = 8
PASS_LINE = "WASM BOARD P2 RESULT: 29/29 responses exact (fails=0, lost=0)"
TOTAL = 29


def vectors_sha256(reqs):
    """A run names the exact request list it played."""
    h = hashlib.sha256()
    for name, frame, _seq, _kind, _want in reqs:
        h.update(f"{name} {frame.hex()}\n".encode())
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
    for name, _frame, _seq, _kind, _want in reqs:
        per.setdefault(name, [0, 0])[1] += 1
    ok = fails = sent = answered = 0
    first_bad, stop = [], None
    while answered < n:
        while sent < n and sent - answered < window:
            _name, frame, _seq, _kind, _want = reqs[sent]
            link.write(frame)
            sent += 1
        raw = link.read(RESP_BYTES)
        name, _frame, seq, want_kind, want = reqs[answered]
        if len(raw) < RESP_BYTES or raw[0] not in (RESP_OK, RESP_BADOP) or raw[1] != seq:
            waiting = getattr(link, "waiting", lambda: None)()
            stop = (f"after {answered} answers, {sent - answered} in flight: expected "
                    f"{want_kind:02x} {seq:02x} ..., read {len(raw)} bytes [{raw.hex(' ')}]"
                    + (f", {waiting} more waiting" if waiting is not None else ""))
            break
        y_word = int.from_bytes(raw[2:], "little")
        if raw[0] == want_kind and y_word == want:
            ok += 1
            per[name][0] += 1
        else:
            fails += 1
            if len(first_bad) < SHOW:
                first_bad.append(f"request {answered} {name}: "
                                 f"board {raw[0]:02x} y {y_word:06x}, "
                                 f"reference {want_kind:02x} {want:06x}")
        answered += 1
    return dict(n=n, ok=ok, fails=fails, lost=n - answered, sent=sent, answered=answered,
                per=per, first_bad=first_bad, stop=stop, window=window)


def result_line(res):
    return (f"WASM BOARD P2 RESULT: {res['ok']}/{res['n']} responses exact "
            f"(fails={res['fails']}, lost={res['lost']})")


def report(res, elapsed=None, log=print):
    for name, (good, tot) in sorted(res["per"].items()):
        tag = "ok  " if good == tot else "FAIL"
        log(f"  [{tag}] {name:8s} {good}/{tot}")
    log(f"sent {res['sent']}, answered {res['answered']}, window {res['window']}")
    if elapsed:
        log(f"elapsed {elapsed:.1f} s ({res['answered'] / elapsed:.0f} answers/s)")
    for m in res["first_bad"]:
        log("  FAIL " + m)
    if res["stop"]:
        log("  STOP link lost framing: " + res["stop"])
    log(result_line(res))
    return res["fails"] == 0 and res["lost"] == 0


class ModelBoard:
    """wasm_board_p2 in Python: the frame parser, code RAM, and the vectors' golden.

    Faults for the self-test: 'lie' answers a wrong word, 'badop' a wrong kind,
    'seq' a wrong seq echo, 'drop' answers nothing, 'slip' skips a page store.
    """

    def __init__(self, fault=None):
        self.code = bytearray(vectors.CODE_PAD)
        self.fault = fault

    def write(self, frame):
        self.frame = frame

    def read(self, n):
        fr = self.frame
        if len(fr) != REQ_BYTES or fr[:2] != SYNC:
            return b""
        body = fr[2:]
        if self.fault == "lie":
            return bytes([RESP_OK, body[1], 0, 0, 1])
        if self.fault == "badop":
            return bytes([RESP_BADOP, body[1], 0, 0, 0])
        if self.fault == "seq":
            return bytes([RESP_OK, (body[1] + 1) & 0xFF, 0, 0, 0])
        if self.fault == "drop":
            return b""
        if body[0] == vectors.OP_LOAD_PAGE:
            page = body[1]
            if page >= vectors.PAGES:
                return bytes([RESP_BADOP, page, 0, 0, 0])
            if self.fault != "slip":
                self.code[page * 32:(page + 1) * 32] = body[4:36]
            return bytes([RESP_OK, page, *(page + 1).to_bytes(3, "little")])
        if body[0] == vectors.OP_RUN_USER:
            seq, length, off = body[1], body[2], body[3]
            x = int.from_bytes(body[4:8], "little")
            y = int.from_bytes(body[20:24], "little")
            if length < 1 or off + length > vectors.CODE_PAD:
                return bytes([RESP_BADOP, seq, 0, 0, 0])
            kind, word = (lambda g: (RESP_OK, g & vectors.WORD_MASK) if g is not None else (RESP_BADOP, 0))(
                self._golden(off, length, x, y))
            return bytes([kind, seq, *word.to_bytes(3, "little")])
        return bytes([RESP_BADOP, body[1], 0, 0, 0])

    def _golden(self, off, length, x, y):
        try:
            return vectors.interp_user(self.code, off, length, x, y)
        except vectors.UserTrap:
            return None

    def waiting(self):
        return 0


def self_test():
    good = True

    def check(cond, what):
        nonlocal good
        print(("  ok   " if cond else "  FAIL ") + what)
        good = good and cond

    problems = vectors.check_pins()
    check(not problems, "spec pins hold: " + ("; ".join(problems) if problems else "all 29"))
    reqs = vectors.requests()
    check(len(reqs) == TOTAL, f"{len(reqs)} requests == {TOTAL}")
    check(reqs[0][1][:8] == SYNC + bytes([vectors.OP_LOAD_PAGE, 0, 0, 0, 0x41, 0xE5]),
          "load frame: SYNC OP SEQ, code bytes from body[4]")
    runfr = reqs[4][1]
    check(runfr[:10] == SYNC + bytes([vectors.OP_RUN_USER, 40, 8, vectors.UP_LEB3, 1, 0, 0, 0]),
          "run frame: SYNC OP SEQ LEN OFF x[4] LE at X_OFF")

    res = run(ModelBoard(), reqs, WINDOW, log=lambda *_: None)
    check(result_line(res) == PASS_LINE, "model answers the suite exactly: " + result_line(res))
    for fault, what in (("lie", "a wrong word fails"), ("badop", "a wrong kind fails"),
                        ("seq", "a wrong seq echo stops the run"),
                        ("drop", "a dropped answer stops the run"),
                        ("slip", "a skipped page store fails the runs that follow")):
        res = run(ModelBoard(fault), reqs, WINDOW, log=lambda *_: None)
        if fault in ("seq", "drop"):
            check(res["lost"] > 0 and res["stop"], f"{what}: " + (res["stop"] or ""))
        else:
            check(res["fails"] > 0 or res["lost"] > 0, f"{what}: fails={res['fails']} lost={res['lost']}")
    print("SELF-TEST " + ("PASS" if good else "FAIL"))
    return good


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/cu.usbserial-130")
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
    if passed and result_line(res) != PASS_LINE:
        print(f"PROBLEM: a pass must print the spec's line, {PASS_LINE!r}")
        return 1
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
