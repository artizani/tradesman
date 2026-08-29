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
            # GATE:<risk> or the unconditional GATE= fallback. Without this a
            # low-risk task could never be done: the base GATE demands
            # ARCH_APPROVAL+DEPLOY+POSTDEPLOY of everything.
            project = task_file.parent.parent.name
            risk = aol.effective_risk(root, project, kv)
            for kind in (aol.gate_for(root, risk) or required_kinds):
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


def _task_files(root):
    for parent in ('projects', 'examples'):
        base = Path(root) / parent
        if not base.exists():
            continue
        for f in sorted(base.glob('*/tasks/TASK-*.aol')):
            if not f.stem.endswith('template'):
                yield f.parent.parent.name, f


def check_risk_floor(root):
    """A task may not declare less risk than what it cites implies.

    Static and pre-code: catches under-declaration before anything runs. The
    run itself ignores the declaration and executes at the floor, so this is
    the record that the two disagreed.
    """
    violations = []
    for project, f in _task_files(root):
        kv = aol.parse_kv(f)
        declared = kv.get('RISK', 'low')
        floor = aol.risk_floor(root, project, kv)
        if aol.rank(root, declared) < aol.rank(root, floor):
            violations.append(
                f'{kv.get("TASK", f.stem)}: RISK={declared} but the floor is {floor} '
                f'(core/PROCESS.aol RISK_FLOOR). A declaration may only add '
                f'scrutiny, never remove it.')
    return violations


def check_risk_flow(root, records):
    """A done task's ledger must show every role its risk level requires.

    Skipping a reviewer satisfies SOD= vacuously -- no second record means no
    actors to compare -- so an absent role is invisible to SOD and has to be
    caught as an absence here.
    """
    violations = []
    by_task = {}
    for r in records:
        by_task.setdefault(r.get('task'), set()).add(r.get('role'))
    for project, f in _task_files(root):
        kv = aol.parse_kv(f)
        if kv.get('STATUS', '').lower() != 'done':
            continue
        task = kv.get('TASK', f.stem)
        risk = aol.effective_risk(root, project, kv)
        for role in (aol.flow_for(root, risk) or []):
            if role not in by_task.get(task, set()):
                violations.append(
                    f'{task}: STATUS=done at risk={risk} but no evidence from '
                    f'{role} (core/PROCESS.aol FLOW:{risk})')
    return violations


def check_risk_ratchet(root, records):
    """Risk may be raised at any time and never lowered after evidence exists."""
    violations = []
    risk_records = [r for r in records if r.get('kind') == 'RISK']
    for r in risk_records:
        task = r.get('task')
        project = r.get('project')
        if (r.get('actor') or {}).get('trust') != 'RUNTIME':
            violations.append(f'{r.get("id")}: RISK record is not RUNTIME-stamped')
        f = aol.project_dir(root, project) / 'tasks' / f'{task}.aol'
        if not f.exists():
            continue
        kv = aol.parse_kv(f)
        eff = r.get('effective', 'low')
        if aol.rank(root, kv.get('RISK', 'low')) < aol.rank(root, eff):
            # Only a complaint if the task was demoted below what actually ran.
            if aol.rank(root, aol.risk_floor(root, project, kv)) < aol.rank(root, eff):
                violations.append(
                    f'{task}: ran at risk={eff} but the task and its floor now say '
                    f'{aol.effective_risk(root, project, kv)} -- a registry was '
                    f'demoted after evidence existed '
                    f'(core/PROCESS.aol RISK_RATCHET)')
    return violations


def check_riskpath(root, records):
    """The diff contradicts the declaration: a cited path outranking the run."""
    violations = []
    eff_by_task = {r.get('task'): r.get('effective')
                   for r in records if r.get('kind') == 'RISK'}
    # git's view of the diff, where we have it. The hook's shell detection is
    # best-effort and a record only cites what it chose to cite; git saw
    # everything that was actually written.
    for r in records:
        if r.get('kind') != 'RISK' or not r.get('touched'):
            continue
        run_at, project = r.get('effective'), r.get('project')
        for path in r['touched']:
            lvl = aol.path_risk(root, project, path)
            if lvl and aol.rank(root, lvl) > aol.rank(root, run_at):
                violations.append(
                    f'{r.get("id")}: git shows {path} was changed, which is '
                    f'RISKPATH:{lvl}, but the run was risk={run_at} '
                    f'(core/PROCESS.aol RISK_EFFECTIVE)')

    for r in records:
        task, project = r.get('task'), r.get('project')
        run_at = eff_by_task.get(task)
        if not run_at or not project:
            continue
        for field in ('artifacts', 'subject'):
            for item in r.get(field, []):
                lvl = aol.path_risk(root, project, item['path'])
                if lvl and aol.rank(root, lvl) > aol.rank(root, run_at):
                    violations.append(
                        f'{r.get("id")}: {item["path"]} is RISKPATH:{lvl} but the run '
                        f'was risk={run_at} (core/PROCESS.aol RISK_EFFECTIVE)')
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
        problems += check_risk_ratchet(root, records)
        problems += check_riskpath(root, records)
        problems += check_role_writes(records, roles, prod_globs, test_globs)
        problems += check_tamper(records)
        problems += check_test_weakening(records)
        if args.strict:
            problems += check_trust(records)

    all_records = [r for _, pth in targets for r in aol.read_ledger(pth)[0]]
    if not args.ledger:
        # Repo-wide static checks. Skipped when checking a single fixture
        # ledger, which says nothing about the repo's own task files.
        problems += check_gate(root, all_records, gate)
        problems += check_risk_floor(root)
        problems += check_risk_flow(root, all_records)

    if problems:
        print('ENFORCEMENT=FAIL')
        for x in problems:
            print(' - ' + x)
        sys.exit(1)
    print(f'ENFORCEMENT=PASS ({total} records checked)')


if __name__ == '__main__':
    main()
