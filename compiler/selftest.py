#!/usr/bin/env python3
"""Negative tests for the AgentOS enforcement layer.

Each case constructs a ledger that violates one ban and asserts enforce.py
fails on it. A check that cannot be made to fail is not a check.

Run: python3 compiler/selftest.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'compiler'))
import aol  # noqa: E402

# A real file, so hashes are genuine and the tamper check has something to bite.
REAL = ROOT / 'examples/sample-project/evidence/artifacts/TASK-001-unit.log'
SUBJ = ROOT / 'examples/sample-project/tasks/TASK-001.aol'


def rec(rid, role, kind, actor, verdict='PASS', artifacts=(), subject=(), task='TASK-001',
        trust='DECLARED', ts='2026-01-01T00:00:00+00:00'):
    r = {'id': rid, 'ts': ts, 'project': 'sample-project', 'task': task, 'role': role,
         'actor': {'id': actor, 'model': 'test', 'session': 's', 'trust': trust},
         'kind': kind, 'claim': 'c', 'verdict': verdict}
    if artifacts:
        r['artifacts'] = list(artifacts)
    if subject:
        r['subject'] = list(subject)
    if kind in aol.EXECUTED_KINDS:
        r['command'] = 'pytest -q'
        r['exit_code'] = 0
    return r


def art(path, assertions=None):
    p = Path(path)
    return {'path': str(p.relative_to(ROOT)) if p.is_absolute() else str(p),
            'sha256': aol.sha256_file(p),
            'assertions': aol.count_assertions(p) if assertions is None else assertions}


def run(records, *extra):
    with tempfile.NamedTemporaryFile('w', suffix='.ndjson', delete=False) as fh:
        for r in records:
            fh.write(json.dumps(r) + '\n')
        path = fh.name
    proc = subprocess.run(
        [sys.executable, str(ROOT / 'compiler/enforce.py'), '--root', str(ROOT),
         '--ledger', path, *extra],
        capture_output=True, text=True, cwd=ROOT)
    Path(path).unlink()
    return proc.returncode, proc.stdout + proc.stderr


CASES = []


def case(name, expect_fail, records, extra=(), expect_text=None):
    CASES.append((name, expect_fail, records, extra, expect_text))


case('clean ledger passes', False, [
    rec('EV-1', 'IMPLEMENT', 'REVIEW', 'impl-a', subject=[art(SUBJ)]),
    rec('EV-2', 'CODE_REVIEW', 'REVIEW', 'review-b', subject=[art(SUBJ)]),
])

case('self-approval: implementer reviews own work', True, [
    rec('EV-1', 'IMPLEMENT', 'REVIEW', 'agent-solo', subject=[art(SUBJ)]),
    rec('EV-2', 'CODE_REVIEW', 'REVIEW', 'agent-solo', subject=[art(SUBJ)]),
], expect_text='SOD violation IMPLEMENT!=CODE_REVIEW')

case('role collapse: implementer writes the tests that judge it', True, [
    rec('EV-1', 'IMPLEMENT', 'TEST', 'agent-solo', artifacts=[art(REAL)]),
    rec('EV-2', 'UNIT', 'TEST', 'agent-solo', artifacts=[art(REAL)]),
], expect_text='SOD violation IMPLEMENT!=UNIT')

case('architect approves own design', True, [
    rec('EV-1', 'ARCH', 'ARCH_APPROVAL', 'arch-a', subject=[art(SUBJ)]),
    rec('EV-2', 'ARCH_REVIEW', 'ARCH_APPROVAL', 'arch-a', subject=[art(SUBJ)]),
], expect_text='SOD violation ARCH!=ARCH_REVIEW')

case('test weakening: assertions drop with no DEFECT', True, [
    rec('EV-1', 'UNIT', 'TEST', 'unit-a', artifacts=[art(REAL, assertions=12)],
        ts='2026-01-01T00:00:00+00:00'),
    rec('EV-2', 'UNIT', 'TEST', 'unit-a', artifacts=[art(REAL, assertions=4)],
        ts='2026-01-02T00:00:00+00:00'),
], expect_text='BAN=TEST_WEAKENING')

case('assertions may drop when a DEFECT explains it', False, [
    rec('EV-1', 'UNIT', 'TEST', 'unit-a', artifacts=[art(REAL, assertions=12)],
        ts='2026-01-01T00:00:00+00:00'),
    rec('EV-2', 'UNIT', 'DEFECT', 'unit-a', verdict='FAIL', ts='2026-01-02T00:00:00+00:00'),
    rec('EV-3', 'UNIT', 'TEST', 'unit-a', artifacts=[art(REAL, assertions=4)],
        ts='2026-01-03T00:00:00+00:00'),
])

case('tampered artifact is caught', True, [
    dict(rec('EV-1', 'UNIT', 'TEST', 'unit-a', artifacts=[art(REAL)]),
         artifacts=[{'path': str(REAL.relative_to(ROOT)), 'sha256': '0' * 64, 'assertions': 3}]),
], expect_text='changed after attestation')

case('strict: self-declared reviewer rejected', True, [
    rec('EV-1', 'CODE_REVIEW', 'REVIEW', 'review-b', subject=[art(SUBJ)], trust='DECLARED'),
], extra=('--strict',), expect_text='requires RUNTIME-stamped evidence')

case('strict: runtime-stamped reviewer accepted', False, [
    rec('EV-1', 'CODE_REVIEW', 'REVIEW', 'review-b', subject=[art(SUBJ)], trust='RUNTIME'),
], extra=('--strict',))


def main():
    passed = failed = 0
    for name, expect_fail, records, extra, expect_text in CASES:
        code, out = run(records, *extra)
        did_fail = code != 0
        ok = did_fail == expect_fail
        if ok and expect_text:
            ok = expect_text in out
        if ok:
            passed += 1
            print(f'  ok   {name}')
        else:
            failed += 1
            print(f'  FAIL {name}')
            print(f'       expected {"failure" if expect_fail else "pass"}, got exit {code}')
            if expect_text:
                print(f'       expected text: {expect_text!r}')
            print('       ' + out.strip().replace('\n', '\n       '))

    print(f'\nSELFTEST={"PASS" if not failed else "FAIL"} ({passed} passed, {failed} failed)')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
