#!/usr/bin/env python3
"""Pin every port of a sweep top to a real IOB site, so nextpnr-xilinx can place it.

tnf-cost-sweep never routed an arm on main. Two harness defects stood in front of
the fabric, one behind the other:

  1. The per-arm XDC (W928) was written to the runner's /tmp and handed to a
     container that sees only $PWD as /work: "failed to open XDC file", twelve
     attempts in seconds, routing-pending (run 37263184079, 2026-10-05).
  2. An XDC with IOSTANDARD only and no package pins, the other half of W928,
     was tried on branch tnf-publication-readiness (76f341cb3, run 34448747524,
     image eced1cdd). With the sites left to it, nextpnr-xilinx put an output on
     a GTP pad: "No Bel named 'OPAD_X0Y11/IOB33/OUTBUF'", placing failed.

So each port bit gets a PACKAGE_PIN from the part's own package_pins.csv (the
one inside the toolchain image), restricted to general-purpose IOB sites. A
port named clk goes on the P side of a multi-region clock-capable pair, so it
can reach a global buffer. Bank 34 is left out: on the AX7203 it is the DDR3
bank at 1.5 V. These runs stop at a routed json and never make a bitstream, but
a constraint file that would be wrong on the board is not worth keeping either.

The pins are not the board's, so an Fmax from these runs is a CI-synth number
for comparing arms with each other, never a board number.

usage: sweep_pin_xdc.py <yosys-json> <top> <package_pins.csv> <out.xdc>
       sweep_pin_xdc.py --selftest
"""
import csv
import io
import json
import pathlib
import sys
import tempfile

# Order the banks are filled in. 34 is absent on purpose (see above).
BANKS = ("15", "14", "16", "13", "35")
CLOCK_PORTS = ("clk",)


def pins_by_preference(rows):
    """General-purpose IOB pins in the allowed banks, in a fixed order."""
    usable = []
    for r in rows:
        is_iob = r["site"].startswith("IOB_")
        allowed = r["bank"] in BANKS
        if is_iob and allowed:
            usable.append(r)
    usable.sort(key=lambda r: (BANKS.index(r["bank"]), r["site"]))
    return usable


def is_clock_pin(r):
    """IO_L12P_T1_MRCC_15: multi-region clock-capable, and the P side of its pair."""
    fields = r["pin_function"].split("_")
    mrcc = "MRCC" in fields
    p_side = len(fields) > 1 and fields[1].endswith("P")
    return mrcc and p_side


def port_bits(mod):
    out = []
    for name, port in mod.get("ports", {}).items():
        width = len(port.get("bits", []))
        single = width == 1
        for i in range(width):
            out.append((name, name if single else f"{name}[{i}]"))
    return out


def build(mod, rows):
    """Return (lines, error). error is None on success."""
    pins = pins_by_preference(rows)
    clocks = [r for r in pins if is_clock_pin(r)]
    taken = set()
    lines = []
    bits = port_bits(mod)
    no_ports = not bits
    if no_ports:
        return [], "the top exposes no ports"
    for name, pad in bits:
        wants_clock = name in CLOCK_PORTS
        pool = clocks if wants_clock else pins
        free = [r for r in pool if r["pin"] not in taken]
        exhausted = not free
        if exhausted:
            kind = "clock-capable" if wants_clock else "general-purpose"
            return [], f"ran out of {kind} pins at {pad} ({len(bits)} port bits, {len(pins)} pins)"
        pick = free[0]
        taken.add(pick["pin"])
        lines.append(f"set_property PACKAGE_PIN {pick['pin']} [get_ports {{{pad}}}]")
        lines.append(f"set_property IOSTANDARD LVCMOS33 [get_ports {{{pad}}}]")
    return lines, None


def pick_module(d, top):
    mods = d.get("modules", {})
    mod = mods.get(top)
    missing = mod is None
    if missing:
        return None, f"{top} is not a module in the json ({len(mods)} modules)"
    return mod, None


def main(argv):
    src, top, csv_path, out = argv
    mod, err = pick_module(json.loads(pathlib.Path(src).read_text()), top)
    failed = err is not None
    if failed:
        print(err, file=sys.stderr)
        return 2
    rows = list(csv.DictReader(io.StringIO(pathlib.Path(csv_path).read_text())))
    lines, err = build(mod, rows)
    failed = err is not None
    if failed:
        print(f"{top}: {err}", file=sys.stderr)
        return 2
    pathlib.Path(out).write_text("\n".join(lines) + "\n")
    print(f"{top}: {len(lines) // 2} pads pinned to IOB sites in banks {','.join(BANKS)}")
    return 0


# A 7-pin package: a GTP pad, a bank-34 clock pin, two clock pins in bank 15 (N
# then P), two plain pins in bank 15, one plain pin in bank 14.
FIXTURE_CSV = """pin,bank,site,tile,pin_function
A4,216,OPAD_X0Y8,GTP_CHANNEL_0_MID_LEFT_X103Y214,MGTPTXN0_216
R4,34,IOB_X1Y124,RIOB33_X105Y124,IO_L13P_T2_MRCC_34
H19,15,IOB_X0Y175,LIOB33_X0Y175,IO_L12N_T1_MRCC_15
J19,15,IOB_X0Y176,LIOB33_X0Y175,IO_L12P_T1_MRCC_15
A13,15,IOB_X0Y150,LIOB33_X0Y149,IO_L1P_T0_15
A14,15,IOB_X0Y149,LIOB33_X0Y149,IO_L1N_T0_15
B1,14,IOB_X0Y100,LIOB33_X0Y99,IO_L1P_T0_14
"""


def selftest():
    rows = list(csv.DictReader(io.StringIO(FIXTURE_CSV)))
    fails = []

    def expect(label, ok):
        bad = not ok
        if bad:
            fails.append(label)

    mod = {"ports": {"clk": {"bits": [2]}, "in_a": {"bits": [3, 4]}, "y": {"bits": [5]}}}
    lines, err = build(mod, rows)
    text = "\n".join(lines)
    expect("builds", err is None)
    expect("clk on the P side of an MRCC pair in an allowed bank", "PACKAGE_PIN J19 [get_ports {clk}]" in text)
    expect("never the N side for clk", "H19 [get_ports {clk}]" not in text)
    expect("never bank 34", "R4" not in text)
    expect("never a GTP pad", "A4" not in text)
    expect("bus bits indexed", "[get_ports {in_a[1]}]" in text)
    expect("scalar not indexed", "[get_ports {y}]" in text)
    expect("one IOSTANDARD per pad", text.count("IOSTANDARD LVCMOS33") == 4)
    expect("no pin used twice", len({l.split()[2] for l in lines if "PACKAGE_PIN" in l}) == 4)

    wide = {"ports": {"d": {"bits": list(range(6))}}}
    _, err = build(wide, rows)
    expect("too many bits is an error, not a short file", err is not None and "ran out" in err)

    two_clocks = {"ports": {"clk": {"bits": [1, 2]}}}
    _, err = build(two_clocks, rows)
    expect("a second clock bit with no P pin left is an error", err is not None and "clock-capable" in err)

    _, err = build({"ports": {}}, rows)
    expect("no ports is an error", err is not None)

    _, err = pick_module({"modules": {"other": {}}}, "top")
    expect("a missing top is an error", err is not None)

    with tempfile.TemporaryDirectory() as t:
        tp = pathlib.Path(t)
        (tp / "d.json").write_text(json.dumps({"modules": {"top": mod}}))
        (tp / "p.csv").write_text(FIXTURE_CSV)
        rc = main([str(tp / "d.json"), "top", str(tp / "p.csv"), str(tp / "o.xdc")])
        wrote = (tp / "o.xdc").exists()
        written = (tp / "o.xdc").read_text() if wrote else ""
        expect("main writes the file", rc == 0 and written == text + "\n")
        rc = main([str(tp / "d.json"), "absent", str(tp / "p.csv"), str(tp / "n.xdc")])
        expect("main fails on a missing top and writes nothing", rc == 2 and not (tp / "n.xdc").exists())

    total = 13
    for f in fails:
        print(f"FAIL {f}")
    print(f"sweep_pin_xdc selftest: {total - len(fails)}/{total}")
    passed = not fails
    return 0 if passed else 1


if __name__ == "__main__":
    want_selftest = sys.argv[1:] == ["--selftest"]
    usage_ok = len(sys.argv) == 5
    if want_selftest:
        sys.exit(selftest())
    if not usage_ok:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
