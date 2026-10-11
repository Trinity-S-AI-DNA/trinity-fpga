# The paper's number formats on the AX7203: measured, recorded, missing (2026-09-27)

The owner asked how the formats in the GoldenFloat hardware paper
(`research/goldenfloat-hw-conformance/GOLDENFLOAT_HW_CONFORMANCE_v0.2.md`)
behave on this board. Short answer:

- **Today the board can run only one kind of arithmetic:** the TRI-NET node's
  ternary dot product. Two formats were measured through it today, ternary and
  Z[φ] (GFTernary × Z[φ]), plus int8 activations. All three pass with signed
  receipts.
- **None of the paper's 27 Tier-E cells** (13 decode, 7 GF ADD, 7 GF MUL) can
  be re-measured without reflashing. One has been since, with the owner's yes:
  GF8 ADD, all 65,536 pairs, `HW RESULT: 65536/65536 bit-exact (fails=0)`
  (section 7). Their board results are recorded only as
  quotes in issue comments on gHashTag/trinity-fpga#199. No raw UART log for
  any of them is in a repository on this Mac, and only one of their bitstreams
  is: GF8 ADD.
- **TNF and BNF have never been on a board.** Their LUT and Fmax figures are
  post-route numbers. (Written before section 8. Since 06:08 UTC, one TNF16
  definition has a board run: section 8. BNF still has none, and neither do
  the other three TNF16 definitions.)

Tags: **measured** = a board log in `conformance/board_runs/`, run today.
**recorded** = quoted in an issue comment or a repo file, not re-run.
**derived** = arithmetic from the numbers cited. Comment ids below refer to
`https://github.com/gHashTag/trinity-fpga/issues/199#issuecomment-<id>`.

## 1. Measured today on this board (measured)

All four runs used node 0x5452494e over the CP2102N at 1,142,857 baud on the
wire, with window 24 (8 for the int8 run).

| activations × weights | run | receipts | rows | log |
|---|---|---|---|---|
| ternary × ternary, all 42 matrices | 02:43 UTC | `receipts verified (tag) : 403200/403200` | 33792/33792 | `tern_tc_all_w24.log` |
| same, window 26 | 03:37 UTC | `receipts verified (tag) : 403200/403200` | 33792/33792 | `tern_tc_all_w26.log` |
| Z[φ] (int8 components) × ternary, layer 0 | 03:32 UTC | `receipts verified (tag) : 403200/403200` | 5632/5632 | `gft_zphi_l0.log` |
| int8 × ternary, `down` matrix | 02:29 UTC | `receipts verified (tag) : 51840/51840` | 320/320 | `tern_tc_down_int8_w8.log` |

The failures on the same node were link failures: W30 (149986/403200), W64
(6679/403200), and the int8 run at the then-default window 64 (9886/51840).
They are not arithmetic failures (`TERN_TC_WEAK_POINTS.md`). The node
computes exact integer dot products. A Z[φ] value a + bφ is two such products
combined on the host, so the format only changes what the host sends.

## 2. The paper's 27 Tier-E cells (recorded)

Paper v0.2, section 4, line 59: 13 decode, 7 ADD and 7 MUL, all on the AX7203
(IDCODE 0x13636093), June 28 to July 2.

| column | cells | recorded result | evidence on disk |
|---|---|---|---|
| decode-HW 13 | bf16, int8, nf4, fp8_e4m3, posit8, fp8_e5m2, fp4_e2m1, int4, fp6_e2m3, fp6_e3m2, lns8, tf32, binary16 | N/N per cell (binary16 65536/65536, exhaustive; bf16 and tf32 only 8/8) | none |
| compute ADD 7 | GF4, 6, 8, 12, 16, 20, 24 | 256 to 512 vectors per cell, fails=0 | GF8 bitstream only; GF8 re-measured exhaustively on 2026-09-27 (section 7) |
| compute MUL 7 | GF4, 6, 8, 12, 16, 20, 24 | 256 to 480 vectors per cell, fails=0 | none |

The one bitstream is `artifacts/bitstreams/gf8_clean_ax7203.bit` from
99431418. Its sha256 `47d8a076…e369` matches comment 4826403273 (checked).

The paper claims "a full reproducible evidence chain (CI build → bitstream
SHA-256 → JTAG flash → UART verify)" for each cell. On this Mac, the last link
of that chain exists only as a line quoted inside a comment.

## 3. Other formats with a board record (recorded)

- **GF32 ADD and MUL:** 240/240 (comments of 2026-07-13). This is the 16-cell
  GF4–GF32 set in retraction 4959097382.
- **GF64 ADD:** smoke `0+0` passed, then **FAIL, 359/512 (70.1 %)**
  (4965993162, checked). **GF128:** smoke only.
- **Not bit-exact, or too thin to count:**
  - BF16 ADD 245/256, with 11 rounding ties (4957037580);
  - lns16 decode 472/576 (4881130168);
  - a 5-decoder mux top 32/40 (4911803703);
  - the GF16+ quire, two test points (4983936091).
- **Several dozen further decode formats,** from int16 up to binary128,
  posit64 and takum64: N/N per cell, all posted from 2026-07-01 to 07-08.
  Several vector counts changed between runs of the same cell, for example
  GF20 decode 2048 and then 242.
- **Latest format load on this board with a quoted UART line:** 2026-08-01.
  GF8 ADD gave 4096/4096 (5151975389), and GFTernary MUL gave 16/16
  (5151891547), scored against an FP32 proxy. t27 records GF-T designs
  "on a live AX7203" on 08-07/08, but gHashTag/t27#4789 says those bitstreams
  and logs are in no repo.

## 4. Never on this board

Written before sections 8 and 9. Since 2026-09-27 two TNF cells have a board log on
this AX7203: TNF16 add and mul, `TNFFormat(4, 11)` v2-spec, 1,035,886/1,035,886
bit-exact (section 8, `board_runs/tnf16_board_ax7203.log`), and the MXFP4/TNF4 block
dot product, 251,616/251,616 bit-exact (section 9, `board_runs/mxdot4_board_ax7203.log`).
Both are arithmetic exactness only: no clock was measured and no advantage follows. The
bullet below still holds for BNF, for the other three TNF16 definitions and for the LUT
and Fmax figures it cites.

- **TNF and BNF.** `fpga/tnet/MATRIX.md` gives BNF16 519 LUT at 71.05 MHz and
  TNF16 514 LUT at 74.37 MHz (lines 115–116). Its heading says "подтверждённая
  кремнием" (confirmed on silicon), but line 124 says the numbers were measured
  after synthesis. These are post-route figures, not a board load. The same
  file's first table gives TNF16 as 495 LUT at 71.90 MHz (line 24). That fits
  the paper staying closed until TNF beats MXFP4 on the block axis.
- **GF48 to GF1024:** simulation only, by their own
  `conformance/README_gf*_bitexact.md`.
- **The `gft_*` operator waves W973–W990 (2026-08-21/22) were another board.**
  They were read over JTAG from part `xc7a200tfbg676-1`
  (`research/arxiv_tnf/measurements/bitstream_w973.json`, checked). This
  board is FBG484. `~/t27/specs/boards/wukong_v1.t27` names that bench a QMTech
  Wukong.

## 5. Records that disagree

1. **The Tier-E total has five values:**
   - 27/83 in the paper;
   - 71/83, then 21/83 compute, in `fpga/CATALOG_MATRIX_83.md` lines 31 and
     87 (checked);
   - "71/83 was WRONG" in the EPIC body;
   - decode-HW 4/83 and compute-HW 2/83 in
     `~/t27/docs/metrics/NUMERIC_FORMATS_83_METRICS.md`.
2. **The 16-cell total "11392/11392"** was called "unaffected, verified
   correct" in 4959097382 on 07-13. It was then retracted as fabricated in
   4968388589 on 07-14 (checked). The per-cell comments stand; the total
   does not.
3. **SUB:** the paper calls SUB 7/7 "prepared", while `CATALOG_MATRIX_83.md`
   line 87 says ADD, MUL and SUB were "measured on AX7203 (2026-07-01)".
4. **Host frame regression:** from 6f3001b1 (07-17) to 64c3459f (08-01) the
   GF compute hosts did not send their operands. Board runs in that window
   measured `gf_add(0,0)`.

## 6. Re-measuring the paper on this board (derived; each flash needs the owner's yes)

- **Cost.** One cell is one bitstream. A JTAG load at 100 kHz takes about
  778 s (`NODE_TOOLCHAIN_RESTORE.md`), so 27 cells is about 5.8 h of loads.
  Each load replaces the node, which then has to be reloaded and re-keyed. One
  bitstream carrying several format cores behind a selector byte would take a
  single load. The record has one such top, and it scored 32/40.
- **Link rate.** The GF8 design (`fpga/vivado/gf8_clean_ax7203.v`) clocks its
  UART from CFGMCLK with divider 434. On this board CFGMCLK is bounded to
  65.6–68.7 MHz (`TERN_TC_WEAK_POINTS.md`, point 6). That puts the design's
  rate at 151,152–158,295 baud. The host default is 160,000, which is 1.1 %
  to 5.9 % fast. A re-run could therefore fail on the link, not on the
  arithmetic.
  - Pre-register 154,839 baud, which is exact on the CP2102N (N=155, per
    `tri fpga-wire`) and sits at the middle of the bound.
  - If that fails, sweep the rate before reading anything into the result.
- **Ready without the board:** `conformance/gf8_add_conformance_ax7203.py
  --self-test` passes today: 65536 pairs, 0 inconsistencies.

## 7. Pre-registered: GF8 ADD at 154,839 baud (written before the run)

The owner said yes to this flash and run on 2026-09-27: «да, прошей GF8 ADD и
запусти на 154839».

- **Bitstream:** `artifacts/bitstreams/gf8_clean_ax7203.bit`, sha256
  `47d8a076…e369`. The load is logged in `board_runs/gf8_add_flash.log`.
- **Command,** from `conformance/`: `tri fpga-run gf8_add_exhaustive_154839
  --limit 1800 -- python3 gf8_add_conformance_ax7203.py --port
  /dev/cu.usbserial-1130 --baud 154839 --exhaustive`.
- **Vectors:** all 256 × 256 = 65,536 pairs, checked against `gf_ref.py`
  (GF8 = 1S+3E+4M, bias 3, HAS_INF 0). The run recorded on 2026-08-01 used
  4,096 pairs. Exhaustive covers every input.
- **PASS** means the log has the line `HW RESULT: 65536/65536 bit-exact
  (fails=0)` and exit 0. Anything else is a FAIL and is recorded as it stands.
  If the run is stopped at the 1,800 s limit, it is recorded as incomplete.
- **Telling link from arithmetic.** The host sends no sequence number, so a
  lost byte shows up as `hw=None` in the mismatch lines. A `None` points at
  the link. A wrong value that is not `None` points at the arithmetic.
- **One attempt.** A second run is allowed only if the first fails before its
  first pair (port busy, no answer to pair 1). Both logs are then kept.

### Result, 04:44 UTC (measured)

**PASS.** `board_runs/gf8_add_exhaustive_154839.log` ends with `HW RESULT:
65536/65536 bit-exact (fails=0)` and `# exit=0`.

- **Load.** `board_runs/gf8_add_flash.log`: IDCODE 0x13636093, "loaded file
  ... in 778s", exit 0, 04:26:37 to 04:39:59 UTC.
- **Run.** One attempt, 04:40:22 to 04:44:37 UTC, 254 s, no `MISMATCH` line.
- **What this adds to the record.** Section 3 has 4,096 pairs from 2026-08-01
  as a quoted line. This run covers every input pair, and its raw log is in the
  repository.
- **Link.** 65,536 pairs at 7 bytes out and 4 bytes back went through with no
  `hw=None`. So 154,839 baud against divider 434 on CFGMCLK held for 720,896
  bytes. The run does not measure the clock: any rate the design tolerates
  would pass.
- **Rate.** At 3.9 ms per pair, the run was 5.5 times slower than the wire
  (0.71 ms per pair). Stop-and-wait over USB sets that pace, not the design.
- **Cost.** The GF8 load replaced the TRI-NET node. Until the node is reloaded
  and re-keyed, no receipt run (section 1) is possible on this board.

## 8. Pre-registered: TNF16 add and mul, every request (written before the run)

The owner, 2026-09-27: «так у на TNF лучший формат !!!», then «сделай его
сразу». TNF has never run on a board (section 4). This is one load and one run.
The owner types the sudo password for the load.

- **Spec, the one source:** `specs/trinet/tnf16_on_board_ax7203.t27`, sha256
  `703b35c0…6889`. It holds the format, the wire, the vector counts, the pass
  line and the guard bits. `conformance/tnf16_board_from_spec.mjs` compiles it
  with the t27 compiler (wasm `bb39b9a5…`), evaluates its 8 test blocks
  (54 asserts, 0 failures), and writes `fpga/tnet/tnf16_board_params.v` and
  `conformance/tnf16_board_params.py`. `--check` reports drift.
- **Format:** `conformance/tnf_ref.py` `TNFFormat(4, 11)`, ladder v2-spec, the
  format the pinned vectors were generated from. The word has 19 bits: sign,
  a 7-bit offset and 11 mantissa bits.
- **RTL:**
  - `fpga/tnet/tnf16_core.v` is a six-stage core, one request per clock. The
    adder keeps 3 guard bits plus a sticky bit.
  - `fpga/vivado/tnf16_board_ax7203.v` is the board top. Its UART is the
    TRI-NET node's, the same divider 60 on the same CFGMCLK.
- **Bitstream:**
  - Build command: `tri fpga-build --top tnf16_board_ax7203 --src
    fpga/tnet/tnf16_board_params.v fpga/tnet/tnf16_core.v
    fpga/vivado/tnf16_board_ax7203.v --no-node-params --nosrl`.
  - File sha256 `1820b114…370b`. Payload `6d4e0dd3…fd47`, the same in two
    builds. `artifacts/bitstreams/tnf16_board_ax7203.manifest.json` records
    the input hashes, the tools and the yosys script.
  - Post-route size is 838 LUTX. Fmax is 126.87 MHz against CFGMCLK's
    65.6–68.7 MHz.
  - Without `--nosrl`, yosys made 19 SRL16E, and router1 did not finish in two
    runs of more than 13 minutes. The builder now stops on SRL cells unless one
    of the two flags is given.
- **Command,** from `conformance/`: `tri fpga-run tnf16_board_ax7203 --limit
  1800 -- python3 tnf16_board_ax7203.py --port /dev/cu.usbserial-1130`. The
  host runs at 1,144,744 baud (1,142,857 on the wire) with window 24.
- **Vectors:** 1,035,886 requests, sha256 `2b1ea80b…2c38`. The host prints
  this and `tnf16_board_vectors.py --write` produces it.
  - 1,038 pinned rows from `tnf16_v2-spec_tnf-vectors-3.vec`.
  - 34,848 edge pairs: 132 codes crossed, for each op.
  - 500,000 uniform random requests.
  - 500,000 near random requests: add near cancellation; mul near underflow,
    unity and overflow.
  - Expected words come from `tnf_ref.py`.
- **PASS** means the log has the line `TNF16 RESULT: 1035886/1035886
  bit-exact (fails=0, lost=0)` and exit 0. Anything else is a FAIL and is
  recorded as it stands. If the run is stopped at the 1,800 s limit, it is
  recorded as incomplete.
- **Telling link from arithmetic.** Every response echoes the request's SEQ.
  - A wrong word counts as a fail, and the run goes on.
  - A short, unframed or out-of-sequence response stops the run with a
    `! LINK:` line. Every request not answered by then counts as lost.
  - So `fails` counts arithmetic and `lost` counts the link.
- **One attempt.** A second run is allowed only if the first fails before its
  first response (port busy, no answer). Both logs are then kept.
- **Before the board (simulated):**
  - **Core RTL (Icarus):** all 1,035,886 requests bit-exact. Latency is 6.
    Idle inputs carried junk, and 10/10 unknown opcodes were flagged.
  - **Python model** of the same datapath: 1,035,886/1,035,886.
  - **UART top from the pins:** 401 frames pass at 0, −43,000 and
    +1,875 ppm, the ends of the CFGMCLK band. The bench fails at −50,000.
  - **Host self-test:** every one of six injected faults is caught.
  - **Gate level:** the yosys netlist was simulated with yosys's
    `xilinx/cells_sim.v`. The yosys command matches the builder's (in the
    manifest). This is before place and route.
    - Core: 1,035,886/1,035,886 bit-exact, in 7 parallel chunks whose
      concatenation has the vector sha above. Latency is 6 and 7/7 bad
      opcodes were flagged.
    - Whole top from the UART pins: 401 frames, 2,005/2,005 response bytes,
      at 0 ppm.
  - **The checks can fail** (controls):
    - one expected word flipped gives `fails=1`;
    - the zero-significand bug fixed during this work gives FAIL;
    - 2 guard bits instead of 3 give 1,358 fails in both the model and the RTL.
- **Link margin (derived).** The slow end of the band is −43,333 ppm, and the
  bench passes at −43,000 and fails at −50,000. The margin at that end is
  thin, and a slow chip would show up as `lost`. The same receiver on this
  chip carried 403,200/403,200 node jobs at this rate.
- **Duration.** 1,035,886 requests of 10 bytes take 91 s on the wire. That is
  the floor for this run, not a prediction of how long it will take.
- **What a PASS says and does not say.**
  - It says this RTL computes the reference TNF16 add and mul bit-exact on
    this board for every listed vector.
  - It says nothing about TNF against other formats. The TNF paper stays
    closed until TNF beats MXFP4 on the block axis.
  - 838 LUTX includes a UART, a parser and six pipeline stages cut for a slow
    clock, so it is not a TNF cost figure.
- **Four TNF16 definitions (found while writing the spec):**
  - t27 `specs/numeric/tnf16.t27`: 4 trits as 2-bit codes, M=11, 20 bits.
  - `tnf_ref.py` v2-spec: 19 bits. This is the one on the board.
  - `tnf16_ref.py` v1-research: M=9.
  - `bnf_decode.v` `tnf16_decode`: M=8, 16 bits. This is the RTL behind the
    TNF16 row of `fpga/tnet/MATRIX.md` on main (514 LUT, 74.37 MHz), so that
    row does not describe this format.
  - The t27 file's header still says "9-bit precision" beside M = 11.
- **Cost.** The load replaces the GF8 design. The TRI-NET node stays off until
  it is reloaded and re-keyed.

### Result, 06:08 UTC (measured)

**PASS.** `board_runs/tnf16_board_ax7203.log` ends with `TNF16 RESULT:
1035886/1035886 bit-exact (fails=0, lost=0)`, which is the spec's `PASS_LINE`,
and `# exit=0`.

- **Load.** `board_runs/tnf16_flash.log`:
  - bit sha256 `1820b114…370b`, the pre-registered file;
  - IDCODE 0x13636093;
  - "loaded file ... in 778s";
  - exit 0, 05:52:47 to 06:06:06 UTC.
- **Run.** One attempt, 06:06:20 to 06:08:33 UTC.
  - The log names the spec sha `703b35c0…6889` and the vector sha
    `2b1ea80b…2c38`, both the pre-registered ones.
  - Every one of the 8 groups (pinned, edge, uniform, near; add and mul) is
    complete.
  - No `!` line.
- **Link.** 10,358,860 bytes went to the board and 5,179,430 came back, with
  window 24, the UART behind the hub, and nothing lost.
  - The requests took 102.4 s. The wire floor is 90.6 s, so the run went at
    88 % of the wire rate: 10,118 answers per second.
  - The host spent 30 s building the expected words before the first request.
- **What this adds.** Before this run, TNF was on a board nowhere (section 4).
  Now one format has a raw log: `tnf_ref.py` `TNFFormat(4, 11)`, v2-spec, add
  and mul, round-to-nearest-even with this spec's underflow rule. It matches
  the Python reference word for word on this board, in the RTL committed at
  `aa4511d6d`.
- **What it does not add.** It does not show TNF beats any other format, and
  838 LUTX is not a TNF cost (see above). It says nothing about the other three
  TNF16 definitions. It does not measure the clock: any CFGMCLK the UART
  tolerates would pass.
- **Cost.** The TRI-NET node is off the board. No receipt run (section 1) is
  possible until it is reloaded and re-keyed.

## 9. MXFP4 against TNF4 in one cell: cost and exactness

The owner, 2026-09-27: «делай вариант 2, TNF против MXFP4». This builds a
32-element block dot product for both formats with the section 8 method and
takes it through simulation. The owner then said yes to the load («да, прошей
MXDOT4 и запусти прогон»), and the board ran it: see "Result on the board" at
the end of this section. The load replaced the TNF16 design.

- **What this axis can and cannot say.** It measures cost (LUT, FF, Fmax) and
  exactness of one hardware cell. It says nothing about quality. The block
  axis on main is unchanged: perplexity MXFP4 21.94 against TNF4 36.72
  (baseline 14.49). Nothing here can flip that verdict. The TNF paper stays
  closed.
- **The two level sets (derived, checked by the spec and the generator).**
  - `tnf_levels(4, 1)` in `research/block/block_tnf.py` is
    {0, 1/6, 1/4, 1/3, 1/2, 2/3, 1}, seven levels for eight magnitude codes.
    Times 12 that is [0, 2, 3, 4, 6, 8, 12].
  - E2M1 in `conformance/mxfp_ref.py` is [0, 0.5, 1, 1.5, 2, 3, 4, 6]. Times 2
    that is [0, 1, 2, 3, 4, 6, 8, 12].
  - So TNF4 is E2M1 without its level 1 (the subnormal 0.5), up to a factor
    of 4. The E8M0 scale absorbs a power of two, so every TNF4 block value is
    an MXFP4 block value, and MXFP4 has one magnitude more. TNF4's eighth code
    is a second zero.
  - In hardware the formats differ only in an 8-entry table of 4-bit
    integers: `MXDOT4_E2M1_TABLE 32'hC8643210`, `MXDOT4_TNF4_TABLE
    32'h0C864320`. Lanes, adders and the result word are shared.
- **Spec, the one source:** `specs/trinet/mxdot4_on_board_ax7203.t27`, sha256
  `e63d4a31…483d`. `conformance/mxdot4_board_from_spec.mjs` compiles it with
  the t27 compiler (wasm `bb39b9a5…`), evaluates 7 test blocks (45 asserts,
  0 failures), checks the tables against both reference files (pinned by
  sha256), and writes `fpga/tnet/mxdot4_board_params.v` and
  `conformance/mxdot4_board_params.py`. `--check` reports drift.
- **Result word.** 24 bits: a NaN bit, a 9-bit exponent sum `sa + sb` and the
  exact 14-bit signed sum of 32 integer products. At most 32 × 12 × 12 = 4608,
  so there is no rounding anywhere. Both formats are exact by construction.
  On exactness they tie.
- **Vectors:** 251,616 requests, 125,808 per format, sha256 `107843f1…cb2d`.
  - 16 pinned blocks per format: ±SUM_MAX, lane 0 and lane 31 alone, TNF4's
    hole code, NaN scales, both exponent ends.
  - 256 edge blocks, 65,536 scale pairs, 50,000 uniform and 10,000
    largest-magnitude blocks per format.
  - Expected words come from the two reference files, not from the spec's
    tables. Changing a spec table is caught.
  - `--fraction-check 3000`: 18,544/18,544 words equal the block value
    computed as a Fraction from the references' element values and scales.
- **RTL:**
  - `fpga/tnet/mxdot4_core.v` is a three-stage core, one request per clock,
    no DSP, no carry chain. `FORMATS` 1 builds MXFP4 only, 2 TNF4 only, 3
    both, with the op choosing.
  - `fpga/vivado/mxdot4_board_ax7203.v` is the board top. Its UART is the
    TRI-NET node's, as in section 8. A request is 38 bytes and a response 5.
- **Simulated (Icarus), not measured:**
  - Core, FORMATS=3: `MXDOT4 SIM: 251616/251616 bit-exact (fails=0)`, the
    spec's `SIM_PASS_LINE`. Three unknown opcodes also come back BADOP.
  - Core, FORMATS=1 and 2: 251,619/251,619 as specified. The format the
    build lacks comes back BADOP with a zero word.
  - Control: one product changed (3 × 3 → 8) gives 1,828 fails.
  - Board top from the UART pins: 41 frames (including one BADOP),
    205/205 response bytes, at 0, −43,000 and +45,000 ppm.
  - Gate level: the yosys netlist of the core (builder flags, FORMATS=3) on
    yosys's `xilinx/cells_sim.v`. A 2,000-request smoke run is bit-exact. The
    full run gives `MXDOT4 SIM: 251616/251616 bit-exact (fails=0)`.
    - It took 1,662 s wall, 07:10:35Z by the log's file time.
    - The netlist is 1,706 LUT instances, the FORMATS=3 row of the cost
      table.
    - The testbench is `fpga/tnet/mxdot4_core_tb.v` without the FORMATS
      override, because the netlist has no parameter.
    - The vector file is the `107843f1…cb2d` above.
  - Host `conformance/mxdot4_board_ax7203.py --self-test`: every one of seven
    injected faults is caught, and so is a board with the tables swapped. The
    host's model core agrees with the reference words on all 251,616
    requests.

### Cost: the prediction, and what was measured

**Pre-registered in the spec before any synthesis.** Measure yosys logic
cells of the core alone, one build per format, with the build script's
`synth_xilinx` flags. Predicted: the two differ by under 5 %
(`PREDICT_LUT_DIFF_PCT`), because the product tables are the same integers
bar one row. The record of "before" is file times, not a commit. The spec was
last written at 06:33:17 UTC, and the first synthesis output is from
06:40:28 UTC.

**The prediction is refuted.** With the builder's flags (`-flatten -abc9
-nocarry -nodsp -nosrl`), the core alone takes 1,185 LUTs for MXFP4 and
1,043 for TNF4: TNF4 is 12.0 % smaller.

**The sign of the gap depends on the mapper.** The same core under four
other flag sets (derived from yosys stat, same RTL):

| synth_xilinx flags | MXFP4 LUT | TNF4 LUT | TNF4 − MXFP4 |
|---|---|---|---|
| `-abc9 -nocarry` (builder), flattened or not | 1185 | 1043 | −12.0 % |
| `-abc9`, carry chains on | 1185 | 1227 | +3.5 % |
| abc, `-nocarry` | 1106 | 1114 | +0.7 % |
| abc, carry chains on | 1063 | 1063 | 0.0 % |

FFs are 426 in every row. Both formats in one core take 1,706 LUTs under the
builder's flags, 44 % more than MXFP4 alone.

**Board tops, place and route (nextpnr-xilinx, seeds 1–3):**

| top | LUT | FF | DSP | Fmax, MHz (seeds 1, 2, 3) | payload |
|---|---|---|---|---|---|
| FORMATS=1, MXFP4 | 1186 | 859 | 0 | 133.16, 138.54, 135.34 | `74cae4d9…8965` (seed 1) |
| FORMATS=2, TNF4 | 1134 | 859 | 0 | 123.08, 139.14, 140.47 | `ad7e8b2a…c86f` (seed 1) |
| FORMATS=3, both | 1807 | 859 | 0 | 131.15 | `57676373…1634` |

Fmax is the last "Max frequency" line of each nextpnr log. The slowest,
123.08 MHz, is 1.79 times the fastest CFGMCLK (68.7 MHz).

**Reading (derived).**
- The one-format board tops differ by −4.4 % in LUT, TNF4 smaller.
- The Fmax ranges overlap.
- The cost gap is smaller than the spread the mapper alone produces. It
  changes sign across flag sets on identical RTL.
- There is no robust cost advantage in either direction. A 4-bit table of
  seven entries against one of eight is below the resolution of this
  toolchain.
- What is robust is structural: TNF4 is a subset of MXFP4's values at the
  same bit width and the same datapath.

### Bitstream

- Build command: `tri fpga-build --top mxdot4_board_ax7203 --src
  fpga/tnet/mxdot4_board_params.v fpga/tnet/mxdot4_core.v
  fpga/vivado/mxdot4_board_ax7203.v --xdc
  specs/fpga/constraints/gf16_clean_ax7203.xdc --no-node-params --nosrl
  --param FORMATS=3`.
- File sha256 `dec2ada9…d01a`. Payload `57676373…1634`, one build.
  `artifacts/bitstreams/mxdot4_board_ax7203.manifest.json` has the input
  hashes, the tools and the yosys script.
- **Run command.** `tri fpga-run` runs its command from `conformance/`:
  `tri fpga-run mxdot4_board_ax7203 --limit 1800 -- python3
  mxdot4_board_ax7203.py --port /dev/cu.usbserial-1130`. An earlier version
  of this line had `conformance/` in the script path, which would not have
  been found; it was caught before the run. The window is 6: 6 × 38 = 228
  bytes in flight, under the 240 that section 8 carried.
- **PASS** means `MXDOT4 RESULT: 251616/251616 bit-exact (fails=0, lost=0)`
  and exit 0. One attempt, as in section 8.
- **Duration (derived).** 251,616 × 38 bytes is 83.7 s on the wire at
  1,142,857 baud. That is the floor.
- **What a board PASS would add:** the cell computes both formats exactly on
  silicon. It would not add a quality claim, and it would not change the
  cost reading above.

### Result on the board, 08:02 UTC (measured)

**PASS.** `board_runs/mxdot4_board_ax7203.log` ends with `MXDOT4 RESULT:
251616/251616 bit-exact (fails=0, lost=0)`, which is the spec's `PASS_LINE`,
and `# exit=0`.

- **Load.** `board_runs/mxdot4_flash.log`:
  - bit sha256 `dec2ada9…d01a`, the committed file;
  - IDCODE 0x13636093;
  - "loaded file ... in 778s";
  - exit 0, 07:42:22 to 07:58:35 UTC. That span is 973 s; openocd reports
    778.8 s for the load, and the log does not account for the other 194 s.
- **Run.** One attempt, 07:58:47 to 08:02:02 UTC.
  - The log names the spec sha `e63d4a31…483d` and the vector sha
    `107843f1…cb2d`, both the committed ones.
  - Every one of the 10 groups (pinned, edge, scales, top, uniform; MXFP4 and
    TNF4) is complete.
- **Link.** Window 6, the UART behind the hub, nothing lost.
  - The requests took 178.0 s, 1,413 answers per second. That is 2.1 times
    the 83.7 s wire floor.
  - Derived: six requests in flight per round trip at 1,413 answers per
    second is 4.2 ms per round, of which 2.0 ms is the wire. The rest is the
    USB round trip, so the window, not the board, sets the rate.
  - The host spent 14.9 s building the expected words before the first
    request.
- **What this adds.** One cell on this board computes 32-element block dot
  products for MXFP4 and for TNF4 bit-exactly against `mxfp_ref.py` and
  `block_tnf.py`, through one datapath that differs only in the element
  table.
- **What it does not add.**
  - It is not a quality result. The block-axis perplexity verdict (MXFP4 21.94
    against TNF4 36.72) is unchanged, and so is the closed publication.
  - It does not change the cost reading: there is no robust cost difference.
  - It does not measure the clock.
- **Cost.** The TNF16 design and the TRI-NET node are both off the board. No
  receipt run (section 1) is possible until the node is reloaded and re-keyed.
