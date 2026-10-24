"""The first spec->wasm32->crystal crossing: one t27 corpus spec, its exported
fn's code body, two P2 frames, the answer read back off the die and compared
with the spec's native value.

    python3 wasm_spec_bridge.py --self-test
    python3 wasm_spec_bridge.py --port /dev/cu.usbserial-130 --wasm b_c.wasm --entry dummy --expect 1

The body is the wasm code ENTRY (local-decl count + expression): b_c.t27's
pub fn dummy() -> bool lowers to 00 41 01 0B (zero local groups, i32.const 1,
end). The module itself is never sent -- the cell has no section parser; this
bridge IS the linker: parse the module minimally (type/function/export/code
sections), take the entry's code bytes, one OP_LOAD_PAGE per 32-byte page,
one OP_RUN_USER.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "Library", "Mobile Documents"))
CONF = os.path.expanduser("~/trinity-fpga/conformance")
sys.path.insert(0, CONF)

from wasm_board_params import SPEC  # noqa: E402
import wasm_board_p2_vectors as vectors  # noqa: E402
from wasm_board_p2 import SerialLink, ModelBoard, run as p2_run  # noqa: E402

SYNC = bytes([SPEC["SYNC0"], SPEC["SYNC1"]])
RESP_OK = SPEC["RESP_OK"]
CODE_RAM = vectors.CODE_PAD  # 128


def uleb(b, i):
    v = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def extract_entry_body(module, entry):
    """The code entry of the exported fn named `entry` (local groups + expr bytes).
    Section bodies are walked with LOCAL indices (the shape of the pinned parse_wasm probe)."""
    i = 8  # magic + version
    sections = {}
    while i < len(module):
        sid = module[i]
        i += 1
        size, i = uleb(module, i)
        sections[sid] = module[i:i + size]
        i += size
    fidx = None
    body = sections.get(7, b"")  # export
    j, n = uleb(body, 0)
    for _ in range(n):
        ln, j = uleb(body, j)
        name = body[j:j + ln].decode()
        j += ln
        kind = body[j]
        j += 1
        idx, j = uleb(body, j)
        if name == entry and kind == 0:
            fidx = idx
    if fidx is None:
        raise SystemExit(f"export {entry!r} not found")
    body = sections.get(10, b"")  # code
    j, n = uleb(body, 0)
    for k in range(n):
        bsz, j = uleb(body, j)
        if k == fidx:
            return body[j:j + bsz]
        j += bsz
    raise SystemExit("code section missing")


def skip_locals(entry):
    """The module's code entry = local-group decl + expression; the cell's program
    format is the expression alone (its 0x00 would read as unreachable)."""
    n, i = uleb(entry, 0)
    for _ in range(n):
        _cnt, i = uleb(entry, i)
        i += 1  # the valtype byte
    return entry[i:]


def frames_for(body, x=0, y=0):
    """OP_LOAD_PAGE per 32-byte page of the padded image + the final OP_RUN_USER."""
    image = bytes(body) + b"\x00" * ((-len(body)) % 32)
    if len(image) > CODE_RAM:
        raise SystemExit(f"body {len(body)} B does not fit the {CODE_RAM} B image")
    reqs = []
    for page in range(len(image) // 32):
        payload = image[page * 32:(page + 1) * 32]  # body[4:36] = A0..A15 B0..B15: exactly 32
        frame = SYNC + bytes([vectors.OP_LOAD_PAGE, page, 0, 0]) + payload
        reqs.append((f"load{page}", frame, page, RESP_OK, (page + 1) & vectors.WORD_MASK))
    seq = 0x5A
    runframe = (SYNC + bytes([vectors.OP_RUN_USER, seq, len(body), 0])
                + (x & 0xFFFFFFFF).to_bytes(4, "little") + b"\x00" * 12 + (y & 0xFFFFFFFF).to_bytes(4, "little") + b"\x00" * 12)
    reqs.append(("run_user", runframe, seq, None, None))  # want checked by the caller
    return reqs


def self_test():
    ok = True

    def check(c, w):
        nonlocal ok
        print(("  ok   " if c else "  FAIL ") + w)
        ok = ok and c

    # 1) the measured module parses and its entry body is the pinned bytes
    import hashlib
    mod = open(os.path.join(HERE, "b_c.wasm"), "rb").read()
    check(mod[:8] == b"\x00asm\x01\x00\x00\x00" and len(mod) == 38, f"module 38 B, sha256 {hashlib.sha256(mod).hexdigest()[:8]}")
    body = extract_entry_body(mod, "dummy")
    check(body == bytes.fromhex("0041010B"), f"entry body {body.hex()} == 00 41 01 0B")
    expr = skip_locals(body)
    check(expr == bytes.fromhex("41010B"), f"expression {expr.hex()} == 41 01 0B (locals skipped)")
    # 2) the frames have the pinned shapes
    reqs = frames_for(expr)
    check(len(reqs) == 2, "one load page + one run (the body fits one page)")
    check(reqs[0][1][:8] == SYNC + bytes([vectors.OP_LOAD_PAGE, 0, 0, 0, 0x41, 0x01]),
          f"load frame head {reqs[0][1][:8].hex(' ')}")
    check(reqs[1][1][:6] == SYNC + bytes([vectors.OP_RUN_USER, 0x5A, 3, 0x00]),
          f"run frame head {reqs[1][1][:6].hex(' ')}: LEN=3 OFF=0")
    check(len(reqs[0][1]) == SPEC["REQ_BYTES"] and len(reqs[1][1]) == SPEC["REQ_BYTES"],
          f"both frames {SPEC['REQ_BYTES']} B")
    # 3) the model board answers the run with the spec's native value (1)
    model = ModelBoard()
    res = p2_run(model, reqs[:1], 1, log=lambda *_: None)
    check(res["fails"] == 0 and res["lost"] == 0, "model stores the page")
    model.write(reqs[1][1])
    resp = model.read(5)
    check(resp[0] == RESP_OK and int.from_bytes(resp[2:], "little") == 1,
          f"model answers the entry: kind {resp[0]:02x} word {int.from_bytes(resp[2:], 'little')}")
    # 4) negative control: a body the cell rejects (unknown opcode 0xFA) answers BADOP
    bad = frames_for(bytes.fromhex("FA0B"))
    model.write(bad[1][1])
    resp = model.read(5)
    check(resp[0] == SPEC["RESP_BADOP"], f"a bad opcode is refused (kind {resp[0]:02x})")
    print("SELF-TEST " + ("PASS" if ok else "FAIL"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/cu.usbserial-130")
    ap.add_argument("--baud", type=int, default=SPEC["HOST_BAUD"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--wasm", default=os.path.join(HERE, "b_c.wasm"))
    ap.add_argument("--entry", default="dummy")
    ap.add_argument("--expect", type=lambda s: int(s, 0), default=1,
                    help="the spec's native value for the entry (t27b run)")
    ap.add_argument("--x", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--y", type=lambda s: int(s, 0), default=0)
    args = ap.parse_args()
    if args.self_test:
        return 0 if self_test() else 1

    module = open(args.wasm, "rb").read()
    body = skip_locals(extract_entry_body(module, args.entry))
    print(f"# {args.wasm} entry {args.entry}: expression {body.hex()} ({len(body)} B)")
    reqs = frames_for(body, args.x, args.y)
    link = SerialLink(args.port, args.baud)
    # the loads: every page stored
    res = p2_run(link, reqs[:-1], 1)
    for name, (g, t) in sorted(res["per"].items()):
        print(f"  [{'ok  ' if g == t else 'FAIL'}] {name} {g}/{t}")
    if res["fails"] or res["lost"]:
        print("STOP: the image did not load")
        return 1
    # the run: the die answers, the word is the spec's native value
    link.write(reqs[1][1])
    raw = link.read(SPEC["RESP_BYTES"])
    word = int.from_bytes(raw[2:], "little")
    verdict = raw[0] == RESP_OK and word == args.expect
    print(f"  die answer: kind {raw[0]:02x} word {word} / native {args.expect}")
    print(f"WASM SPEC BRIDGE RESULT: {'PASS' if verdict else 'FAIL'} -- {args.entry}() == {args.expect} read off the die")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
