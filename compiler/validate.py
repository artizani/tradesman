#!/usr/bin/env python3
"""Validate AgentOS structure, and optionally enforce the bans (--strict)."""
from pathlib import Path
import argparse
import subprocess
import sys

REQ = ['LLM_README.aol', 'core/SYSTEM.aol', 'core/PROCESS.aol', 'core/ROLES.aol',
       'core/QUALITY.aol', 'core/PRECEDENCE.aol', 'core/EVIDENCE.aol']
PREQ = ['project.aol', 'domain.aol', 'product.aol', 'architecture.aol', 'invariants.aol',
        'journeys.aol', 'state.aol']


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', required=True)
    p.add_argument('--strict', action='store_true',
                   help='also run enforce.py, requiring RUNTIME-stamped review evidence')
    a = p.parse_args()
    r = Path(a.root)
    e = []

    for x in REQ:
        if not (r / x).exists():
            e.append('missing ' + x)
    for parent in [r / 'projects', r / 'examples']:
        if parent.exists():
            for d in [x for x in parent.iterdir() if x.is_dir()]:
                for x in PREQ:
                    if not (d / x).exists():
                        e.append(f'{d.name}: missing {x}')

    if e:
        print('VALIDATION=FAIL')
        for x in e:
            print(' - ' + x)
        sys.exit(1)
    print('VALIDATION=PASS')

    if a.strict:
        rc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / 'enforce.py'),
             '--root', str(r), '--strict']).returncode
        sys.exit(rc)


if __name__ == '__main__':
    main()
