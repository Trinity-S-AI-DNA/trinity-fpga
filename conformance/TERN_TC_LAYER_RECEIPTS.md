# tern_tc on the node cell: what is verified, and by what

2026-09-26. Companion to `tern_tc_layer_ax7203.py` and `formal/tern_tc_layer_rtl_tb.v`.

## Correction to commit 1f131fd

The first `tern_tc_layer_ax7203.py` printed `receipts authenticated : 284160/284160
under node0's key`. It never compared a tag. `pyflakes` on that file:

    'trinet_mac32_conformance_ax7203.siphash24' imported but unused
    local variable 'pre' is assigned to but never used
    undefined name 'c_dummy'

It counted `status == 0x01` as authentication, ignored the nonce echo, and
compared only per-row sums. Negative control: fed a software cell that signs with
a key the host does not hold, it printed `receipts authenticated 160/160` and
PASS.

| claim from the 2026-09-27 board run | status |
|---|---|
| 28,416 / 28,416 row dots bit-exact (wq/wo/gate/up, 6 layers) | **stands**: the y values were compared per row against the oracle |
| 320 / 320 rows, 3,200 receipts, random 320x320 (`trinet_matvec_demo.py`) | **stands**: that harness does compare tags and nonces |
| 284,160 / 284,160 receipts authenticated under node0's key | **withdrawn**: those receipts were never checked. Replaced by a new measurement with the fixed harness on 2026-09-27 at 02:43Z: 403,200 / 403,200 receipts verified across all 42 matrices, which include these 24 (see "Rerun step 1" below) |

## What the fixed harness checks

Per response: status 0x01, nonce issued by this run and answered once, node id
equal to the first answer's, SipHash-2-4 tag recomputed under the key, y equal to
that chunk's dot product. Per row: every chunk accepted, and the chunk sum equal
to the row dot computed from the model's int8 weights directly (not from the
packed wire bytes). `--self-test` shows each check failing on a cell built to
break it: wrong key, a validly signed wrong answer, a flipped tag bit, an
unissued nonce, another node id, a dropped response, an unkeyed node, and 16
bytes lost from the middle of the answer stream (the 2026-09-27 board failure,
below).

## Booked as new hardware, needs none

- **w_down** (864-wide input): 864 = 27 x 32, so 27 jobs per row on the same cell.
- **wk, wv** (320-wide input): were skipped, not blocked.
- **int8 activations** (Stage B.3): q = sum 3^k d_k with d_k in {-1,0,+1} over six
  digits covers [-364, 364], so w.q = sum 3^k (w.d_k): six ternary jobs per
  chunk, recombined on the host. `--act int8`.

All 42 ternary matrices (6,451,200 weights) therefore run on the existing
bitstream: 403,200 jobs for two ternary x-vectors, about 90 s at the 4,560 jobs/s
measured on 2026-09-27 (derived).

## RTL co-simulation (no board)

`fpga/portable/trinet_node_core.v` and `fpga/openxc7-synth/trinet_siphash24.v`,
unchanged, under Icarus Verilog 12.0, UART at bit level (BAUD_DIV=8), node
unkeyed at reset and keyed over the wire by op 0x02, exactly as on the board.
Weights: random ternary in tern_tc's exact shapes (`--synthetic`, seed 0x7C02,
59.7% nonzero), because the trained `model.bin` was not in this environment.

| run | jobs | rows bit-exact | receipts verified |
|---|---|---|---|
| layer 0, all 7 matrices (`--all --layers 0`) | 67,200 | 5,632 / 5,632 | 67,200 / 67,200 |
| layer 5 w_down, int8 activations (`--mats down --layers 5 --act int8 --n_x 1`) | 51,840 | 320 / 320 | 51,840 / 51,840 |
| layer 0 wk, no setkey frame (control) | 640 | 0 / 64 | 0 / 640, all status 0x04 |
| layer 0 wk, verified under a different key (control) | 1,280 | 0 / 128 | 0 / 1,280 |

Stream hashes (sha256): layer 0 requests `2379d8f2...5619f`, responses
`f4ca4909...5890e`; w_down int8 requests `bcf0b51f...74b95`, responses
`e6443bf1...99add`.

Reproduce:

    python3 conformance/tern_tc_layer_ax7203.py --synthetic --all --layers 0 \
        --setkey --keys test --emit-requests req.hex
    iverilog -g2012 -o tb formal/tern_tc_layer_rtl_tb.v \
        fpga/portable/trinet_node_core.v fpga/openxc7-synth/trinet_siphash24.v
    vvp -n tb +req=req.hex +resp=resp.hex          # ~20 min for 67,200 jobs
    python3 conformance/tern_tc_layer_ax7203.py --synthetic --all --layers 0 \
        --setkey --keys test --responses resp.hex

## Board rerun (the owner's machine)

    cd ~/trinity-fpga/conformance
    python3 tern_tc_layer_ax7203.py --self-test
    python3 tern_tc_layer_ax7203.py --all --setkey --port /dev/cu.usbserial-1130 \
        --baud 1144744 --keys ../trinet-keys.txt --model ~/igla-coder-gpu/c_infer/model.bin

The port name depends on the Mac and the hub: `usbserial-130` on the Air,
`usbserial-1130` on the M1 Pro on 2026-09-27. `trinet_discover.py` finds it.
`--setkey` installs node0's key and checks the ack tag. After a power cycle it
is required; if the board already holds a key the ack is 0x03, and the receipts
then show whether it is the right one. Needs `pyserial`.

## Not claimed

A forward pass (embeddings, norms, attention, softmax and head do not run on the
board); any token rate or power figure; public verifiability (SipHash is a
shared-key MAC: it authenticates the node to the key holder and does not stop an
operator forging their own receipts).

## Board run, 2026-09-27 UTC

**Result: the first full run failed, and the full rerun at window 24 passed.**
The first full run (window 64, harness `2b9830c5`) lost UART framing after 19,049
jobs. Steps 4 and 5 were held back at first (a failed step is recorded and the run
stops there), then run once each at the owner's request: step 4 passed, and step 5
lost framing the same way after 9,951 jobs. Step 5 rerun with `--window 8`
passed: 51,840/51,840 receipts, 320/320 rows. The full run was then repeated once
at the default window of 24 on harness `b1e95f6f` ("Rerun step 1" at the end) and
passed: `receipts verified (tag) : 403200/403200`, `rows bit-exact :
33792/33792`. The window-64 control on the same harness ("Rerun step 2") then
slipped after 6,744 jobs, so the pass at 24 comes from the window, not from the
harness change.

- Machine: MacBook Pro, Apple M1 Pro, macOS 26.5.2 (25F84), Python 3.14.6,
  pyserial 3.5.
- Port: `/dev/cu.usbserial-1130`, the board's CP2102N (USB `10C4:EA60`) behind a
  USB 2.0 hub (`1A40:0101`), 1,144,744 baud. The Digilent FT232H (`0403:6014`,
  one interface, `usbserial-210512180081`) is the JTAG cable and does not answer
  as a UART. openocd was not run.
- Node: `0x5452494e` (node0), no key at the start (status 0x04).
- Harness: `2b9830c5837aa47ad142a90a7279c2d8a8d401fe`.
- `model.bin`: 16,953,800 bytes, sha256
  `3102abdf35057924e077a86db4fdac726e4fe1f6574df47f39a638ba99a3ce9c`.
- Logs: `board_runs/*.log`, each checked for the key's hex (0 hits).

**1. Self-test.** `python3 tern_tc_layer_ax7203.py --self-test`, 01:41:10Z.
Expected PASS on 17 checks, 7 of them negative controls. Got 17/17 `ok` and
`self-test: PASS`, 2.23 s.

**2. Discover.** `discover_port.py` (20bf4c418, branch `trinet-fleet-truth`, run
from a temporary copy): at 01:57:33Z and 01:59:38Z `miss
/dev/cu.usbserial-210512180081`, because the UART cable was not yet enumerated;
at 02:01:31Z `HIT  /dev/cu.usbserial-1130 @ 1144744: status=0x04 (no key)`.
Then `python3 trinet_discover.py --ports /dev/cu.usbserial-1130`, 02:01:50Z.
Expected node0 `0x5452494E` at 1,144,744 baud. Got:

    /dev/cu.usbserial-1130  NODE  id 0x5452494e (node0), 1144744 baud, keyed (v2), no key yet, 64/64 clean

**3. All 42 matrices, ternary activations.** 02:10:13Z to 02:10:20Z.

    python3 tern_tc_layer_ax7203.py --all --setkey --port /dev/cu.usbserial-1130 --baud 1144744 \
        --keys ../trinet-keys.txt --model ~/igla-coder-gpu/c_infer/model.bin

Expected 403,200 jobs, `receipts verified (tag) : 403200/403200`, `rows
bit-exact : 33792/33792`, PASS. Got:

    setkey: key installed on node 0x5452494e; ack tag verifies
      [ok ] L0/wq        640/640    rows bit-exact
      [ok ] L0/wk        128/128    rows bit-exact
      [ok ] L0/wv        128/128    rows bit-exact
      [ok ] L0/wo        640/640    rows bit-exact
      [FAIL] L0/gate      362/1728   rows bit-exact
      [FAIL] L0/up          0/1728   rows bit-exact
      [FAIL] L0/down        0/640    rows bit-exact
      [... 35 lines elided: L1 to L5, 0 rows in every matrix ...]
    jobs sent               : 403200
    receipts verified (tag) : 18984/403200 under node 0x5452494e
    rejected                : {'node': 1, 'short': 1, 'missing': 384215}
    rows bit-exact          : 1898/33792  (activations: ternary)
    elapsed                 : 4.07 s (99030 jobs/s)
      ! nonce 0x00014a28: node 0x4e00014e != 0x5452494e
      ! after 19049 sent: short or unframed read (19 bytes)
    RESULT: FAIL - do not cite these matrices as verified.

- The response to job 18,984 (nonce `0x00014a28`) carried node-id bytes
  `4e 01 00 4e` instead of `4e 49 52 54`, and the next 19-byte read did not start
  with 0xA5. The harness stops at the first unframed read by design, so 384,215
  jobs have no answer.
- The 18,984 jobs answered before it each had a matching tag and the right y.
  The run as a whole is FAIL and is not cited.
- The summary's `jobs sent` and `jobs/s` count the 403,200 planned jobs, not the
  19,049 written. Derived: 18,985 answers in 4.07 s, about 4,660 jobs/s, close to
  the 4,560 jobs/s of the earlier run. Reporting only; the harness was not
  changed.
- No retry: the failure came after the first job.

**4. `trinet_matvec_demo.py`.** Not run with steps 1 to 3. Run once at the
owner's request, 02:22:04Z to 02:22:05Z. There was no power cycle after step 3,
so the node still held the key that step 3 installed.

    python3 trinet_matvec_demo.py --port /dev/cu.usbserial-1130 --baud 1144744 --keys ../trinet-keys.txt

Expected 320/320. Got:

    jobs (32-wide dots)   : 3200
    receipts authenticated: 3200/3200 under node0's key
    rows bit-exact        : 320/320
    elapsed               : 0.79 s (4032 jobs/s)
    RESULT: the datapath a model layer needs is verified on silicon.

PASS. This harness checks status, nonce, y and the tag of every job.

**5. `--act int8`, layer 5 w_down.** Not run with steps 1 to 3. Run once at the
owner's request, 02:22:27Z to 02:22:29Z.

    python3 tern_tc_layer_ax7203.py --mats down --layers 5 --act int8 --n_x 1 --setkey \
        --port /dev/cu.usbserial-1130 --baud 1144744 --keys ../trinet-keys.txt \
        --model ~/igla-coder-gpu/c_infer/model.bin

Expected 51,840 jobs, `receipts verified (tag) : 51840/51840`, `rows bit-exact :
320/320`, PASS. Got:

    model.bin: 1 matrices, 320 rows, 276480 ternary weights (61.8% nonzero)
    jobs: 51840 (32-trit chunks x 1 x-vectors x 6 digit planes)
    setkey: node 0x5452494e already holds a key (0x03); receipts below will show whether it is ours
      [FAIL] L5/down       61/320    rows bit-exact
    jobs sent               : 51840
    receipts verified (tag) : 9886/51840 under node 0x5452494e
    rejected                : {'fabricated': 1, 'short': 1, 'missing': 41954}
    rows bit-exact          : 61/320  (activations: int8)
    elapsed                 : 2.08 s (24872 jobs/s)
      ! nonce 0x5401269e was never issued
      ! after 9951 sent: short or unframed read (19 bytes)
    RESULT: FAIL - do not cite these matrices as verified.

- Job 9,886 (nonce `0x0001269e`, bytes `9e 26 01 00`) was answered with nonce
  bytes `9e 26 01 54`. The next 19-byte read did not start with 0xA5, and the
  harness stopped. This matches step 3: one frame with a wrong byte, then no
  frame.
- The 9,886 jobs answered before that each had a matching tag and the right y,
  so node0 still signs with the key that step 3 installed. 61 rows are complete;
  each row is 162 jobs (27 chunks x 6 digit planes).
- `jobs sent` and `jobs/s` count planned jobs again. Derived: 9,887 answers in
  2.08 s, about 4,750 jobs/s.
- No retry: the failure came after the first job.

**5a. Step 5 with `--window 8`.** Run once at the owner's request, 02:29:44Z to
02:29:56Z, to probe the window candidate below. Same port, same hub, same baud,
no power cycle. The only change is 8 jobs in flight instead of 64. The harness
was still `2b9830c5` from this checkout. `b1e95f6f`, which sets the default
window to 24 and adds the failed-read diagnostics described below, reached the
branch at 02:28:34Z, a minute before this run, and was not used. This is not the
rerun planned under "Rerun that tells the causes apart".

    python3 tern_tc_layer_ax7203.py --mats down --layers 5 --act int8 --n_x 1 --setkey --window 8 \
        --port /dev/cu.usbserial-1130 --baud 1144744 --keys ../trinet-keys.txt \
        --model ~/igla-coder-gpu/c_infer/model.bin

Expected `receipts verified (tag) : 51840/51840`, `rows bit-exact : 320/320`,
PASS. Got:

    model.bin: 1 matrices, 320 rows, 276480 ternary weights (61.8% nonzero)
    jobs: 51840 (32-trit chunks x 1 x-vectors x 6 digit planes)
    setkey: node 0x5452494e already holds a key (0x03); receipts below will show whether it is ours
      [ok ] L5/down      320/320    rows bit-exact
    jobs sent               : 51840
    receipts verified (tag) : 51840/51840 under node 0x5452494e
    rejected                : none
    rows bit-exact          : 320/320  (activations: int8)
    elapsed                 : 11.93 s (4344 jobs/s)
    RESULT: every row above computed on the AX7203 bit-exact against the
            int8-weight oracle, and every receipt verified under the key.

- PASS. This is the first board run of the trained model's w_down with int8
  activations: layer 5, 320 rows, one x-vector, all of it on the existing
  bitstream.
- No slip in 51,840 jobs, which is 5.2 times the 9,951 jobs after which the
  window-64 run of the same matrix slipped. One run: this fits the window
  candidate, but it does not prove it.
- Every job was sent in this run, so 4,344 jobs/s is a real rate. The window-64
  runs reached about 4,660 to 4,750 jobs/s before they slipped.

Open: why the framing slipped. It slipped twice at window 64, after 19,049 jobs
and after 9,951 jobs. Between those two runs, all 3,200 jobs of step 4 (also at
window 64) came back framed. At window 8, 51,840 jobs came back framed, and at
window 24 the full `--all` run did as well (403,200 jobs, "Rerun step 1" at the
end). At window 64 on the same harness it slipped a third time, after 6,744 jobs
("Rerun step 2"). Candidates: the CP2102N's
baud divider at 1,144,744 against the node's (earlier board runs went through a
CP2102N seen as `usbserial-130` on another Mac); the USB 2.0 hub (the CP2102N
did not enumerate on the first hub port tried); 64 jobs in flight
(`--window`) against the node's receive buffer.

### What the bytes say (analysis after the run, no new board data)

The frame layout is `A5 y status nonce[4] node_id[4] tag[8]`, little-endian; the
node id goes out as `4e 49 52 54`.

- **Step 3.** Job 18,984's answer arrived as `A5 y s 28 4a 01 00 4e` and then
  `01 00 4e`. Those three bytes are the tail of the *next* answer's nonce
  (`29 4a 01 00`) and the first byte of its node id. The answer stream lost 16
  contiguous bytes: the last 11 of one answer and the first 5 of the next.
  Before it, 18,984 answers (360,696 bytes) arrived intact.
- **Step 5.** Job 9,886's answer arrived as `A5 y s 9e 26 01` and then `54`, the
  last node-id byte. The stream lost 4 contiguous bytes (`00 4e 49 52`). Before
  it, 9,886 answers (187,834 bytes) arrived intact.

Either hole may also be longer by a whole number of 19-byte answers; the logs
kept no hex to tell. Both are holes with intact bytes on both sides, at
different offsets, after about 10,000 and 19,000 clean answers. Step 4's 3,200
jobs, run between them with 64 in flight too, came back whole.

- **Not the node.** `trinet_node_core.v` has no receive buffer to overflow: it
  parses requests byte by byte, and a lost request byte would give a misparsed
  request but still a whole 19-byte answer. Its transmitter sends each answer
  from one 19-byte buffer; a new result during a send restarts at `A5`, which
  would have put `a5` straight after the cut, not `01 00 4e` or `54`. It cannot
  happen at this pacing anyway: a 24-byte request takes longer on the wire than
  a 19-byte answer.
- **Unlikely to be the baud divider.** A rate mismatch corrupts bits inside
  bytes, scattered over the run. Both runs had hundreds of thousands of clean
  bytes and then a hole with intact bytes on both sides.
- **Consistent with a receive queue overflow.** With `--window 64`, up to 64
  answers (1,216 bytes) can be in flight towards the host. If the host stops
  reading for long enough, they queue in the USB adapter and the driver, and a
  queue that fills drops bytes. Not measured: the adapter's and the driver's
  buffer sizes, and whether the host paused. *Superseded by rerun step 2
  below: the window matters, but the hole at 64 came with no long host pause
  before it, so the host-pause part of this explanation does not hold.*
- **Not ruled out: the hub or the cable.** A lost USB transfer would also make
  a contiguous hole.

### What changed in the harness

- `--window` defaults to 24: at most 456 bytes of answers can queue.
- The summary reports `jobs sent` as the number written (steps 3 and 5 printed
  the jobs planned) and `answers/s` from answers received, and it prints the
  longest time the host spent between two reads.
- A failed read now logs its bytes in hex, how many jobs were outstanding and
  how many bytes were still waiting in the port.
- The self-test replays the step 3 failure: a reference cell whose answer
  stream loses 16 bytes at the same offset. The harness credits the 5 answers before it,
  classifies the damaged one as a node-id mismatch, stops at the unframed read
  and reports only the jobs actually written.

The stop-on-first-unframed-read rule is unchanged: every credited receipt still
passes every check, and nothing is re-sent.

### Rerun that tells the causes apart

1. `--all --setkey` with the default window (24).
2. As a control on the same setup: the same command with `--window 64`.
3. If step 1 fails: plug the CP2102N in without the hub and repeat step 1.

Step 5a above is the first point on this: the same matrix at window 8, same
hub, came back whole over 51,840 jobs, 5.2 times the length after which it
slipped at 64. It cost about 9% of throughput (4,344 against about 4,750
answers/s); 24 should cost less, which step 1 will show.

The holes came after about 10,000 and 19,000 answers, so one pass or one fail
per window is weak evidence on its own. The stronger signal is `longest host pause` next to
the hex of the failed read: a pause of milliseconds right before a hole at
window 64 points at the queue; a hole with no pause before it points at the
link.

### Rerun step 1: `--all` at window 24, 2026-09-27 02:43Z

Run once at the owner's request. Same Mac, port, hub and baud as above, and no
power cycle since step 3. The checkout was `067e176a`, where the harness is
`b1e95f6fd5ec43b5be709af86b179750ae8b27a5`, unmodified. `model.bin` had the same
sha256 as above. Logs: `board_runs/selftest_w24.log` and
`board_runs/tern_tc_all_w24.log`, each with 0 hits for the key's hex.

**Self-test**, 02:43:34Z. Expected PASS. Got 18/18 `ok` (the 17 checks plus the
16-byte-hole replay) and `self-test: PASS`, 2.38 s.

**Full run**, 02:43:53Z to 02:45:21Z.

    python3 tern_tc_layer_ax7203.py --all --setkey --window 24 --port /dev/cu.usbserial-1130 \
        --baud 1144744 --keys ../trinet-keys.txt --model ~/igla-coder-gpu/c_infer/model.bin

Expected 403,200 jobs, `receipts verified (tag) : 403200/403200`, `rows
bit-exact : 33792/33792`, PASS, about 90 s. Got:

    model.bin: 42 matrices, 16896 rows, 6451200 ternary weights (61.1% nonzero)
    jobs: 403200 (32-trit chunks x 2 x-vectors)
    setkey: node 0x5452494e already holds a key (0x03); receipts below will show whether it is ours
      [ok ] L0/wq        640/640    rows bit-exact
      [... 40 lines elided: L0/wk to L5/up, every one [ok ] and n/n ...]
      [ok ] L5/down      640/640    rows bit-exact
    jobs sent               : 403200 of 403200 planned (window 24)
    receipts verified (tag) : 403200/403200 under node 0x5452494e
    rejected                : none
    rows bit-exact          : 33792/33792  (activations: ternary)
    elapsed                 : 85.49 s (4716 answers/s)
    longest host pause      : 22.4 ms (after 142395 answers)
    RESULT: every row above computed on the AX7203 bit-exact against the
            int8-weight oracle, and every receipt verified under the key.

- PASS. All 42 ternary matrices of the trained tern_tc (6,451,200 weights, two
  ternary x-vectors) ran on the existing bitstream. Every one of the 403,200
  jobs was sent and answered, and every answer passed status, nonce, node id,
  tag and y. The full log lists all 42 matrices.
- The key is still the one step 3 installed: setkey got 0x03, and every tag
  verified under node0's key from `trinet-keys.txt`.
- No slip in 403,200 jobs, 21 times the 19,049 after which the window-64 run
  slipped. Window 64 slipped twice in about 32,200 jobs (steps 3, 4 and 5). If
  the slip rate did not depend on the window, about 25 slips would have been
  expected here (derived), and there were none.
- 4,716 answers/s against 4,344 at window 8 and about 4,660 to 4,750 at window
  64 before the slips: window 24 costs no measurable throughput.
- The host paused once for 22.4 ms. At 1,144,744 baud (10 bits per byte) the
  line delivers 1,216 bytes, a full window-64 queue, in about 10.6 ms (derived).
  A pause this long lets the whole window queue up. At window 24 that is at most
  456 bytes, and none were lost.
- What this does not settle. The harness also changed between step 3 and this
  run: `b1e95f6f` adds timing and hex output around the same write and read
  calls. Step 5a was clean at window 8 on the old harness, so the harness change
  is not needed to explain a clean run, but step 2 (the same command with
  `--window 64` on `b1e95f6f`) is the control that separates the two. It was
  run next (step 2 below). Step 3 of the plan (no hub) is not needed, because
  step 1 passed.

### Rerun step 2: `--all` at window 64 (control), 2026-09-27 02:53Z

Run once at the owner's request, 02:53:30Z to 02:53:34Z, eight minutes after
step 1. Same Mac, port, hub, baud and `model.bin`, no power cycle. The harness
was the same `b1e95f6f`, unmodified (checkout `1e2677a1`). Nothing was changed
except `--window 64`. Log: `board_runs/tern_tc_all_w64.log`, 0 hits for the
key's hex.

    python3 tern_tc_layer_ax7203.py --all --setkey --window 64 --port /dev/cu.usbserial-1130 \
        --baud 1144744 --keys ../trinet-keys.txt --model ~/igla-coder-gpu/c_infer/model.bin

Expected, if the window does not matter: the same PASS as step 1. Got:

    setkey: node 0x5452494e already holds a key (0x03); receipts below will show whether it is ours
      [ok ] L0/wq        640/640    rows bit-exact
      [FAIL] L0/wk         27/128    rows bit-exact
      [FAIL] L0/wv          0/128    rows bit-exact
      [... 39 lines elided: L0/wo to L5/down, 0 rows in every matrix ...]
    jobs sent               : 6744 of 403200 planned (window 64)
    receipts verified (tag) : 6679/403200 under node 0x5452494e
    rejected                : {'tag': 1, 'short': 1, 'missing': 396520}
    rows bit-exact          : 667/33792  (activations: ternary)
    elapsed                 : 1.47 s (4538 answers/s)
    longest host pause      : 4.2 ms (after 4987 answers)
      ! nonce 0x00011a17: right y, tag does not verify
      ! after 6744 sent, 64 outstanding: short or unframed read (19 bytes) 01 1b 1a 01 00 4e 49 52 54 e6 4e 86 79 fe e1 9a 8c a5 02; 37 more bytes waiting
    RESULT: FAIL - do not cite these matrices as verified.

- FAIL, and the run is not cited. No retry: the failure came after the first
  job.
- **The control separates the causes.** With the same harness, setup and
  session, window 24 carried 403,200 jobs clean and window 64 slipped after
  6,744. The pass in step 1 therefore comes from the window, not from the
  harness change. At window 64 this is the third slip in three long runs (after
  19,049, 9,951 and 6,744 jobs). Only step 4's 3,200 jobs came through whole.
- **The bytes.** The failed read is the answer to job 6,683 (nonce
  `0x00011a1b`), starting from its third byte: status `01`, nonce `1b 1a 01 00`,
  node id `4e 49 52 54`, 8 tag bytes, then `a5 02`, the first two bytes of the
  next answer. The read before it was credited to job 6,679 (nonce
  `0x00011a17`). Its y was right, but its tag did not verify, and the harness
  refused the receipt. No answers to jobs 6,680 to 6,682 appeared. Suppose
  there is one hole and the node answered in order. Then the hole starts inside
  job 6,679's tag, and the two ends fix its length at 59 bytes, three answers
  plus two bytes, wherever inside the tag it starts. The harness prints no hex
  for a tag failure, so where it starts is not known.
- **The host did not pause before the hole.** The longest pause in this run was
  4.2 ms, at answer 4,987, about 1,700 answers before the hole. That is about
  481 bytes of line time at 1,144,744 baud (10 bits per byte), less than half of
  a full window-64 queue of 1,216 bytes (derived). Step 1 survived a 22.4 ms
  pause at window 24. By the criterion under "Rerun that tells the causes
  apart", a hole with no pause before it points at the link (adapter, driver,
  hub, cable), not at a queue filled while the host was away. The window still
  matters: 64 answers in flight slip, and 24 do not. How the window acts on the
  link is not measured.
- The three holes so far have no common length: 16 bytes, 4 bytes and 59
  bytes. The first two may each be longer by whole answers.
- Operating point for now: window 24, the harness default. The causes that
  remain are testable without new RTL. One is window 64 without the hub. The
  other is the adapter's and the driver's buffer sizes. Neither has been run.

### Rerun step 3 (pre-registered): the CP2102N receive buffer, windows 26 and 30

Written and pushed before either run. No harness, RTL or expected-number
change: `--window` is a command-line argument of the same harness (blob
`01f85f04`, which differs from `b1e95f6f` in comments and help text only).

**Source.** The Silicon Labs CP2102N datasheet (Rev. 1.5) says:

- section 1: the bridge has a 512-byte receive buffer and a 512-byte
  transmit buffer;
- section 4.3.9: "Handshaking is required at high baud rates (greater than
  1 MBaud) to avoid receiver overrun".

The node runs at 1,144,744 baud with no handshaking. `trinet_node_core.v` has
only `uart_rx` and `uart_tx`, and the AX7203 constraint files map only those
two pins to the CP2102. The node therefore cannot be held off.

**Hypothesis.** Answers are lost when more than 512 bytes of them wait in the
CP2102N receive buffer, that is, when the host's USB IN polling pauses long
enough. With W jobs in flight, at most 19W answer bytes exist between the
node and the harness.

- **W <= 26** (494 B) can never overflow the buffer, however long the pause.
- **W >= 27** overflows after a pause long enough for the node to produce
  513 bytes, about 27 answers at 4,770/s, or 5.7 ms (derived). That threshold
  is the same for every W >= 27. So window 30 should slip about as readily as
  window 64 did: 3 of 3 long runs, after 6,744 to 19,049 jobs.

The harness's "longest host pause" measures this process, not USB polling. The
driver keeps draining the bridge while the process sleeps, which is how window
24 survived a 22.4 ms process pause.

**Runs**, each once, same port and hub, `--all --setkey`, window 26 first:

| run | bytes in flight | prediction | refutes the hypothesis |
|---|---|---|---|
| `--window 26` | 494 | PASS, `403200/403200` | any slip |
| `--window 30` | 570 | FAIL: a slip, most likely within the first ~20,000 jobs | a clean 403200/403200 |

Neither outcome proves the hypothesis alone. Both together, a pass at 494 B
and a slip at 570 B, would place the loss threshold between 494 and 570 bytes,
bracketing 512. If the 512 B excludes a USB endpoint buffer, the threshold
moves up by at most one 64-byte packet. That still separates 26 from 64, but
it could let window 30 pass, so a clean window-30 run is read as "weakened",
not "refuted", unless it is repeated. Logs: `board_runs/tern_tc_all_w26.log`,
`board_runs/tern_tc_all_w30.log`.

### Rerun step 3, results, 2026-09-27 03:38Z and 03:39Z

Both runs happened once each, in the pre-registered order: same port, hub and
baud, no power cycle, `--setkey` answered 0x03 both times. Logs
`board_runs/tern_tc_all_w26.log` and `board_runs/tern_tc_all_w30.log`, each with
0 hits for the key's hex.

**Window 26 (494 B in flight), 03:37:56Z to 03:39:24Z: PASS, as predicted.**

    jobs sent               : 403200 of 403200 planned (window 26)
    receipts verified (tag) : 403200/403200 under node 0x5452494e
    rejected                : none
    rows bit-exact          : 33792/33792  (activations: ternary)
    elapsed                 : 85.29 s (4727 answers/s)
    longest host pause      : 25.8 ms (after 346656 answers)

**Window 30 (570 B in flight), 03:39:31Z to 03:40:06Z: FAIL, as predicted.**

    jobs sent               : 150017 of 403200 planned (window 30)
    receipts verified (tag) : 149986/403200 under node 0x5452494e
    rejected                : {'tag': 1, 'short': 1, 'missing': 253213}
    rows bit-exact          : 12822/33792  (activations: ternary)
    elapsed                 : 31.62 s (4743 answers/s)
    longest host pause      : 13.8 ms (after 128976 answers)
      ! nonce 0x000349e2: right y, tag does not verify
      ! after 150017 sent, 30 outstanding: short or unframed read (19 bytes) 4e 49 52 54 f1 42 f2 7c 04 a1 b9 74 a5 03 01 e4 49 03 00; 171 more bytes waiting

The window-30 run is not cited for its rows. It was not retried, because the
failure came after the first job.

- **The two predictions that decide the hypothesis held.** A pass at 494 bytes
  in flight and a slip at 570 bytes put the loss threshold between 494 and 570
  bytes. That range contains the CP2102N's 512-byte receive buffer. The slip
  at 570 bytes also rules out the "512 plus one 64-byte endpoint packet"
  reading of the buffer (576 B), which could not have overflowed at 570.
- **One side prediction missed.** The pre-registration said the window-30 slip
  would come "most likely within the first ~20,000 jobs", about as readily as
  at window 64 (6,744 to 19,049 jobs). It came after 150,017 jobs, 31.6 s into
  the run. Overflow at window 30 therefore takes a rarer event than at
  window 64. The model "the same ~5.7 ms gap for every W >= 27" is too simple.
  Why is not measured.
- **The bytes.** The failed read starts at the node id of an answer: `4e 49 52 54`
  (node id 0x5452494e, little-endian), 8 tag bytes, then `a5 03 01` and nonce
  `0x000349e4`. The answer before it in order is `0x000349e3`, whose first 7
  bytes are missing from the stream. The read before that was credited to
  `0x000349e2`, with the right y and a failed tag. If there is one hole and the
  node answers in order, then 26 bytes were sent from the start of e2's answer
  to e3's node id, and 19 arrived: a hole of 7 bytes. The holes so far are 16,
  4, 59 and 7 bytes.
- **Process pauses are not the trigger.** Window 26 survived a 25.8 ms pause
  of this process, and window 24 survived 22.4 ms. Only the bytes in flight
  separate pass from slip.

Operating point: window 24 (456 B) stays the default. Window 26 is the largest
window with a clean `--all`, and it buys no speed (4,727 against 4,716
answers/s). The hub is not excluded as the source of the stalls that let the
bridge buffer fill. Window 64 without the hub, owner-approved, is still
waiting for the adapter to be plugged in directly.

### Rerun step 4 (pre-registered): `--all` at window 64, UART on its own hub, 2026-09-27 09:34Z

(Heading stamp corrected at 10:37Z from 09:40Z, which was not read from a clock: the
pre-registration commit cf82eeb79 is 09:34:19Z. Nothing else in this section changed.)

At 09:32Z the owner moved the Mac end of the UART cable ("подключил в другой
usb"). `system_profiler SPUSBHostDataType` and `ioreg -p IOUSB` then show:

- CP2102N at location 0x00110000, `/dev/cu.usbserial-110`, 12 Mb/s, on bus
  0x00. Its parent is a Genesys Logic "USB2.1 Hub" (05e3:0610, bcdDevice
  0x0663, bDeviceProtocol 1 = single transaction translator) at 0x00100000.
  The same dongle's USB 3 hub (05e3:0626) sits at 0x00200000.
- The Digilent FT232H stays on bus 0x01 behind the FE1.1s hub (1a40:0101).
  openocd is not running, so that bus carries no JTAG traffic during the run.
- `tri fpga-usb` prints `HUB ['0x110000']`. **This is not the direct
  connection the owner approved.** The USB-C to USB-A adapter contains a hub.

What changed against the three window-64 slips: the hub model (Genesys instead
of FE1.1s), the bus (0x00 instead of 0x01), the port and the adapter. The UART
bridge, board, node bitstream, harness, window, baud rate and USB cable end on
the board did not change. Both hubs are single-TT, so this run cannot say
whether "any hub" matters. It can only say whether that particular dongle, bus
or port mattered.

Command, unchanged from step 2 except the port:

    python3 tern_tc_layer_ax7203.py --all --setkey --port /dev/cu.usbserial-110 \
      --baud 1144744 --keys ../trinet-keys.txt \
      --model /Users/playra/igla-coder-gpu/c_infer/model.bin --window 64

- **PASS** means `receipts verified (tag) : 403200/403200`, rows 33792/33792
  and exit 0.
- **Prediction: FAIL.** The favoured hypothesis is the bridge's 512-byte
  buffer (window 26 passed, window 30 slipped). 1,216 bytes can be in flight
  at window 64, and a single-TT hub is still in the path.
  - A FAIL keeps the old dongle, bus and port out of the explanation. The size
    of its hole is one more estimate of the stall length.
  - A PASS would point at the old FE1.1s dongle, its bus or its port. The
    three old slips came after 6,744 to 19,049 jobs, so a clean 403,200 would
    not be chance.
- One attempt. It is retried once only if it fails before the first job. The
  record then keeps both runs.

**Result, 09:34:34Z to 09:35:17Z: FAIL, as predicted.** Log:
`board_runs/tern_tc_all_w64_genesys.log` (3,343 bytes, 0 hits for the key).

    setkey: node 0x5452494e already holds a key (0x03); receipts below will show whether it is ours
    jobs sent               : 189001 of 403200 planned (window 64)
    receipts verified (tag) : 188936/403200 under node 0x5452494e
    rejected                : {'fabricated': 1, 'short': 1, 'missing': 214264}
    rows bit-exact          : 16426/33792  (activations: ternary)
    elapsed                 : 39.79 s (4748 answers/s)
    longest host pause      : 8.3 ms (after 141395 answers)
      ! nonce 0x01fda508 was never issued: a5 fc 01 08 a5 fd 01 0b e2 03 00 4e 49 52 54 2f ac 15 dc
      ! after 189001 sent, 64 outstanding: short or unframed read (19 bytes) 24 7b 50 98 a5 03 01 0c e2 03 00 4e 49 52 54 94 3c c3 5e; 94 more bytes waiting

The run is not cited for its rows and was not retried: it failed after the
first job.

- **The key survived the replug.** The board was not power-cycled, `--setkey`
  found key slot 0x03 already set, and 188,936 tags verified under the host's
  key.
- **The prediction held.** Bytes were still lost with the old dongle, bus and
  port out of the path. So they are not needed for the loss.
- **The slip came late.** It came after 189,001 jobs. The three earlier
  window-64 slips behind the FE1.1s dongle came after 6,744 to 19,049 jobs
  (mean 11,915).
  - If this path had the old rate of stalls long enough to overflow, the
    chance of 189,001 clean jobs would be e^(-189001/11915), about 1e-7.
  - So the old dongle, bus or port very probably made such stalls far more
    frequent. One run cannot give the new rate.
  - The two earliest slips (19,049 and 9,951 jobs) ran harness `2b9830c5`.
    Between the window-64 control (`b1e95f6f`, 6,744 jobs) and this run the
    harness changed only in comments and the `--window` help text (`git diff
    b1e95f6fd c590308e5`), so for that pair the USB path is the only
    difference.
- **The bytes.** An answer is `a5`, y, status, nonce (4 bytes, little
  endian), node id (4), tag (8): 19 bytes. 188,936 answers were verified, so
  the next one due is nonce `0x10000 + 188936 = 0x3e208`.
  - The first read starts `a5 fc 01 08`: the first 4 bytes of the answer
    for `0x3e208` (y = -4, low nonce byte `08`).
  - The next byte starts the answer for `0x3e20b`: `a5 fd 01 0b e2 03 00`,
    then its node id `4e 49 52 54` and the first half of its tag. The other
    half, `24 7b 50 98`, opens the second read, so `0x3e20b` arrived whole.
  - Lost: the last 15 bytes of `0x3e208` and all of `0x3e209` and `0x3e20a`,
    **53 bytes**. The node id at offset 11 instead of 7 says independently
    that the loss is 15 bytes more than a whole number of answers.
  - The holes so far are 16, 4, 59, 7 and 53 bytes.

  (Corrected at 10:58Z. This bullet first read the `a5 fd 01 0b e2 03 00`
  bytes as the tail of an answer, and gave the hole as 8 bytes, which the
  offset rules out. Byte 1 is the output y, not a counter. The same wrong
  8 is in the message of commit `c590308e5`, which is not rewritten. The
  harness bullet above was also corrected: it had called the harness a
  second difference for all the earlier slips.)
- **"fabricated" is the harness's label for a misaligned read.** Here it is a
  framing slip, not a forged answer: the 19 bytes straddle two genuine
  answers.

What is left open: the source of the stalls, and whether a truly direct
connection (no hub at all) changes the rate further. Window 24 stays the
operating point.

## Board run, 2026-10-24 UTC (the second-node run)

**Result: PASS, twice.** The owner asked for the node cell to be finished as
the second verification node of the receipt economy ("the crystal verifies:
Mac dead -> runner alive -> runner dead -> crystal verifies"). Two full runs
at the default window 24 on unmodified harness `c9b180bf` (main, after #918
and #919), the second one saved as a durable artifact:

    jobs sent               : 403200 of 403200 planned (window 24)
    receipts verified (tag) : 403200/403200 under node 0x5452494e
    rejected                : none
    rows bit-exact          : 33792/33792  (activations: ternary)
    elapsed                 : 88.03 s (4580 answers/s)   -- run 1
    elapsed                 : 89.19 s (4521 answers/s)   -- rerun, logged

Run 1's longest host pause was 80.0 ms (after 118,037 answers), the rerun's
103.6 ms (after 323,870 answers). No framing slip in either run: the holes
list (16, 4, 59, 7, 53 bytes, all 2026-09) gains no new member.

- Machine: the owner's MacBook Pro (Apple M1 Pro), macOS, Python 3.14.6,
  pyserial 3.5.
- Port: `/dev/cu.usbserial-130` (the board's CP2102N), 1,144,744 baud. The
  hub position differs from 2026-09-27 (`usbserial-1130`); the node answered
  discover the same way: `HIT @ 1144744: status=0x04 (no key)`.
- Node: `0x5452494e` (node0). No key at the start (power had cycled);
  `--setkey` (op 0x02) loaded node0 and its ack tag verified under that key.
  A pre-key conformance probe showed every y correct with `tag_ok=False`
  under status 0x04 -- recorded as the unkeyed state, not as verification.
- Harness: `c9b180bf7888e0702dc33a3765e8d5dfb0b3c131`. Self-test before the
  runs: 18/18 `ok` (17 in September; the harness gained one control),
  `self-test: PASS`.
- `model.bin`: sha256 `3102abdf...a3ce9c` -- byte-identical to the
  2026-09-27 run (same file, re-hashed today).
- Log: `board_runs/2026-10-24-second-node.log` (2,790 bytes, under the 1 MB
  bound). The node0 key (32 hex chars) has 0 hits in the log and 0 hits in
  every file under `board_runs/`. A first key-scan probe reported "55 hits"
  by matching an empty pattern (the keys file has two fields; the probe read
  a third); the corrected probe reads field 2 and reports 0.

**What this run does and does not claim.** The crystal computed all 42 ternary
matrices of the model (wq, wk, wv, wo, gate, up, down across L0-L5) and every
one of the 403,200 nonce-bound answers carried a tag that verified under the
node0 key: a second node of THIS receipt family exists and reproduces
verification without the host computing the answers. It does not by itself
satisfy `t27os specs/os/lab.t27`'s "no second NODE has reproduced a receipt
yet", which speaks of `t27c corpus-receipt` runs; closing THAT line needs a
corpus receipt verified off the host (the #918 WASM interpreter board cell is
the candidate path).
