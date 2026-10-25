#!/usr/bin/env python3
"""The super-fleet: const + arith + compare on the CURRENT bitstream, one session.

Runs the three proven fleets in sequence, sums their verdicts, and prints the
bitstream's full sha256 plus a ready die_witness candidate (the eq(3,3) answer,
the comparison floor's signature). For t27os#3.
"""
import os, re, subprocess, sys, hashlib, json

HERE = os.path.dirname(os.path.abspath(__file__))
FLEETS = ['fleet_crossing.py', 'arith_fleet.py', 'cmp_fleet.py']

def main():
    total_ok = total_fail = 0
    eq33 = None
    for f in FLEETS:
        r = subprocess.run([sys.executable, os.path.join(HERE, f)],
                           capture_output=True, text=True, cwd=HERE)
        lines = (r.stdout or '').strip().splitlines()
        summ = [l for l in lines if re.search(r'\d+ exact, \d+ not', l)]
        if summ:
            ok, fail = map(int, re.search(r'(\d+) exact, (\d+) not', summ[-1]).groups())
            total_ok += ok
            total_fail += fail
            print(f'== {f}: {summ[-1].strip()}')
        else:
            total_fail += 1
            print(f'== {f}: NO SUMMARY rc={r.returncode} :: {(r.stderr or "")[-120:]}')
        m = re.search(r'\[ok\] eq\(3,3\): die (\w+) want', r.stdout or '')
        if m:
            eq33 = m.group(1)
    bit = os.path.join(HERE, 'out-p4', 'wasm_board_p4.bit')
    sha = hashlib.sha256(open(bit, 'rb').read()).hexdigest() if os.path.exists(bit) else 'NO-BIT'
    print(f'SUPER-FLEET: {total_ok} exact, {total_fail} not')
    print(f'bitstream sha256: {sha}')
    if eq33:
        print('die_witness candidate: ' + json.dumps({
            'spec': 'eq3', 'entry': 'f', 'x': 3, 'y': 3,
            'die_word': eq33, 'native': 1, 'bit_exact': True,
            'bitstream_sha256': sha}))

if __name__ == '__main__':
    main()
