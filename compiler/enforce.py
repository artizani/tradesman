#!/usr/bin/env python3
"""Enforce the AgentOS bans against a project's evidence ledger.

core/SYSTEM.aol bans SELF_APPROVAL, ROLE_COLLAPSE and TEST_WEAKENING, and
core/ROLES.aol gives every role an explicit BAN= list. Those are instructions
to a model -- the exact category of thing this framework exists because it
distrusts. This module turns them into checks that fail a build.

Every rule is read from AOL (SOD=, GATE=, WRITES:, PROD_GLOB=, TEST_GLOB=).
Nothing here hardcodes governance: amending a rule means editing AOL.
"""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aol  # noqa: E402


def actor_of(record):
    return (record.get('actor') or {}).get('id') or '<unattributed>'


def check_sod(records, pairs):
    """NO_SELF_APPROVAL / NO_ROLE_COLLAPSE: conflicting roles need distinct actors."""
    violations = []
    by_task = {}
    for r in records:
        by_task.setdefault(r.get('task', '?'), []).append(r)

    for task, rs in sorted(by_task.items()):
        actors = {}
        for r in rs:
            actors.setdefault(r.get('role', '?'), set()).add(actor_of(r))
        for a, b in pairs:
            shared = actors.get(a, set()) & actors.get(b, set())
            for actor in sorted(shared):
                violations.append(
                    f'{task}: SOD violation {a}!={b} -- actor "{actor}" performed both '
                    f'(core/ROLES.aol SOD=, core/SYSTEM.aol BAN=SELF_APPROVAL+ROLE_COLLAPSE)'
                )
    return violations


def check_gate(root, records, required_kinds):
    """DONE gate: a task may not be done without every required kind, all PASS."""
    violations = []
    present = {}
    for r in records:
        present.setdefault(r.get('task', '?'), {}).setdefault(r.get('kind'), []).append(r)

    for parent in ('projects', 'examples'):
        base = Path(root) / parent
        if not base.exists():
            continue
        for task_file in sorted(base.glob('*/tasks/TASK-*.aol')):
            if task_file.stem.endswith('template'):
                continue
            kv = aol.parse_kv(task_file)
            if kv.get('STATUS', '').lower() != 'done':
                continue
            task = kv.get('TASK', task_file.stem)
            got = present.get(task, {})
            for kind in required_kinds:
                rs = got.get(kind, [])
                if not rs:
                    violations.append(
                        f'{task}: STATUS=done but no {kind} evidence '
                        f'(core/ROLES.aol GATE=, core/QUALITY.aol DONE=)'
                    )
                elif not any(r.get('verdict') == 'PASS' for r in rs):
                    violations.append(f'{task}: STATUS=done but no PASSing {kind} evidence')
    return violations


def check_role_writes(records, roles, prod_globs, test_globs):
    """Role BAN= lists applied to the paths each record actually cites."""
    violations = []
    for r in records:
        role = r.get('role', '?')
        bans = roles.get(role, {}).get('BAN', [])
        writes = roles.get(role, {}).get('WRITE', [])
        paths = [a['path'] for a in r.get('artifacts', [])]
        for p in paths:
            is_test = aol.match_globs(p, test_globs)
            is_prod = aol.match_globs(p, prod_globs)
            if 'PROD_CODE' in bans and is_prod:
                violations.append(
                    f'{r.get("id")} ({role}): cites production file {p} but '
                    f'core/ROLES.aol says ROLE {role} BAN=PROD_CODE'
                )
            if is_test and 'PROD_CODE' in writes and not any(
                w.endswith('TESTS') for w in writes
            ):
                violations.append(
                    f'{r.get("id")} ({role}): cites test file {p} but ROLE {role} '
                    f'writes PROD_CODE only -- authoring the tests that judge your own '
                    f'work is BAN=SELF_REVIEW'
                )
    return violations


def check_tamper(records):
    """Artifacts must still hash to what was attested."""
    violations = []
    for r in records:
        for field in ('artifacts', 'subject'):
            for item in r.get(field, []):
                p = Path(item['path'])
                if not p.exists():
                    violations.append(f'{r.get("id")}: attested {field} path is gone: {p}')
                elif aol.sha256_file(p) != item['sha256']:
                    violations.append(f'{r.get("id")}: {field} {p} changed after attestation')
    return violations


def check_test_weakening(records):
    """TEST_WEAKENING: assertions may not drop without a DEFECT explaining it."""
    violations = []
    timeline = {}
    for r in sorted(records, key=lambda x: (x.get('ts', ''), x.get('id', ''))):
        for a in r.get('artifacts', []):
            timeline.setdefault(a['path'], []).append((r, a))

    defect_tasks = {r.get('task') for r in records if r.get('kind') == 'DEFECT'}

    for path, entries in sorted(timeline.items()):
        for (prev_r, prev_a), (curr_r, curr_a) in zip(entries, entries[1:]):
            before, after = prev_a.get('assertions', 0), curr_a.get('assertions', 0)
            if after < before and curr_r.get('task') not in defect_tasks:
                violations.append(
                    f'{curr_r.get("id")}: assertions in {path} fell {before} -> {after} '
                    f'with no DEFECT record for {curr_r.get("task")} '
                    f'(core/SYSTEM.aol BAN=TEST_WEAKENING)'
                )
    return violations


def check_trust(records):
    """--strict: review-role evidence must be harness-stamped, not self-declared."""
    violations = []
    for r in records:
        if r.get('role') in aol.REVIEW_ROLES:
            trust = (r.get('actor') or {}).get('trust')
            if trust != 'RUNTIME':
                violations.append(
                    f'{r.get("id")} ({r.get("role")}): trust={trust}, but --strict requires '
                    f'RUNTIME-stamped evidence for review roles '
                    f'(a self-declared reviewer identity is not independent)'
                )
    return violations


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default='.')
    p.add_argument('--project', help='limit to one project')
    p.add_argument('--ledger', help='check one ledger file directly')
    p.add_argument('--strict', action='store_true',
                   help='require trust=RUNTIME for review-role evidence')
    args = p.parse_args()

    root = Path(args.root)
    roles = aol.parse_roles(root / 'core' / 'ROLES.aol')
    pairs = aol.parse_sod(root / 'core' / 'ROLES.aol')
    gate = aol.parse_gate(root / 'core' / 'ROLES.aol')

    if args.ledger:
        targets = [(None, Path(args.ledger))]
    elif args.project:
        targets = [(args.project, aol.ledger_path(root, args.project))]
    else:
        targets = []
        for parent in ('projects', 'examples'):
            base = root / parent
            if base.exists():
                for d in sorted(x for x in base.iterdir() if x.is_dir()):
                    targets.append((d.name, d / 'evidence' / 'ledger.ndjson'))

    problems, total = [], 0
    for project, path in targets:
        records, read_errors = aol.read_ledger(path)
        problems.extend(read_errors)
        total += len(records)

        pkv = aol.parse_kv(aol.project_dir(root, project) / 'project.aol') if project else {}
        prod_globs = [g for g in pkv.get('PROD_GLOB', '').split('+') if g]
        test_globs = [g for g in pkv.get('TEST_GLOB', '').split('+') if g]

        problems += check_sod(records, pairs)
        problems += check_role_writes(records, roles, prod_globs, test_globs)
        problems += check_tamper(records)
        problems += check_test_weakening(records)
        if args.strict:
            problems += check_trust(records)

    problems += check_gate(root, [r for _, pth in targets
                                  for r in aol.read_ledger(pth)[0]], gate)

    if problems:
        print('ENFORCEMENT=FAIL')
        for x in problems:
            print(' - ' + x)
        sys.exit(1)
    print(f'ENFORCEMENT=PASS ({total} records checked)')


if __name__ == '__main__':
    main()
