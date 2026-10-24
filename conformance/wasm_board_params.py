# Transcribed from gHashTag/t27 specs/fpga/wasm_board_ax7203.t27, MASTER
# (the #9352+#9354 law: standard compare numbering) -- the spec is the source, this
# file is a copy of its constants.
# Not yet compiler-generated (the spec lives in the t27 repo, not here); every relation
# the transcription must keep is asserted by wasm_board_vectors.check_params(), which
# the vectors module runs on import and the runner's self-test exercises.
"""WASM board cell parameters: wire format, machine limits, the 101-byte program ROM."""

SPEC_FILE = "specs/fpga/wasm_board_ax7203.t27"
SPEC_REPO = "gHashTag/t27"
SPEC_BRANCH = "master"
SPEC_SHA256 = "174da6b89a08fbdcefc0235f6ca3cfb36ae1fca2423ed6f8b99d9585a0b40129"

# The ROM, byte for byte: 13 straight-line WASM bodies plus the illegal-opcode probe.
# add_double, answer, xmul_not, sub_and_or, shl_sar, eq, ne, le_s, ge_s, eqz, gt_s,
# set_get, tee_dup, bad.
ROM = [
    0x20, 0x00, 0x20, 0x01, 0x6A, 0x41, 0x02, 0x6C, 0x0B,
    0x41, 0x2A, 0x0B,
    0x20, 0x00, 0x41, 0x02, 0x6C, 0x41, 0x7F, 0x73, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x6B, 0x20, 0x00, 0x20, 0x01, 0x71, 0x72, 0x0B,
    0x20, 0x00, 0x41, 0x03, 0x74, 0x41, 0x01, 0x75, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x46, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x47, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x4D, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x4F, 0x0B,
    0x20, 0x00, 0x45, 0x0B,
    0x20, 0x00, 0x20, 0x01, 0x4A, 0x0B,
    0x20, 0x00, 0x41, 0x03, 0x6C, 0x21, 0x02, 0x20, 0x02, 0x20, 0x01, 0x6A, 0x0B,
    0x20, 0x00, 0x22, 0x03, 0x20, 0x03, 0x6A, 0x0B,
    0x20, 0x00, 0xFF, 0x0B,
]
PROG_OFF = [0, 9, 12, 21, 33, 42, 48, 54, 60, 66, 70, 76, 89, 97]
PROG_LEN = [9, 3, 9, 12, 9, 6, 6, 6, 6, 4, 6, 13, 8, 4]
PROG_NAME = ["add_double", "answer", "xmul_not", "sub_and_or", "shl_sar", "eq", "ne",
             "le_s", "ge_s", "eqz", "gt_s", "set_get", "tee_dup", "bad"]

SPEC = {
    "SYNC0": 170, "SYNC1": 85,
    "RESP_OK": 165, "RESP_BADOP": 229,
    "REQ_BYTES": 38, "BODY_BYTES": 36, "RESP_BYTES": 5,
    "X_OFF": 4, "Y_OFF": 20, "WORD_BITS": 24,
    "STACK_MAX": 16, "LOCALS": 4, "CODE_PAD": 128,
    "PROGS": 14, "WIRE_PROGS": 13,
    "PINNED_PER_PROG": 2, "RANDOM_PER_PROG": 8, "ERROR_VECS": 2, "WRAP_VECS": 2,
    "TOTAL_REQUESTS": 134,
    "RANDOM_SEED": 20261012,
    "BAUD_DIV": 60, "HOST_BAUD": 1144744, "WIRE_BAUD": 1142857,
    "RUN_NAME": "wasm_board_ax7203", "RUN_LIMIT_S": 600,
    "PASS_LINE": "WASM BOARD RESULT: 134/134 responses exact (fails=0, lost=0)",
    "SIM_PASS_LINE": "WASM BOARD SIM: 11/11 checks green",
    "ROM": ROM, "PROG_OFF": PROG_OFF, "PROG_LEN": PROG_LEN, "PROG_NAME": PROG_NAME,
}
