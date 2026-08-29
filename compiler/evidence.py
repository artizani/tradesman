#!/usr/bin/env python3
"""Record and verify AgentOS evidence.

core/SYSTEM.aol mandates EVIDENCE>ASSERTION. This module is what makes that
mandate real: a record is rejected unless it cites something checkable -- a
command with its exit code, or a file that exists and hashes to what was
claimed. Prose alone cannot satisfy it.

Schema: core/EVIDENCE.aol
"""
from pathlib import Path
import argparse
import datetime
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aol  # noqa: E402


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def _next_id(records):
    n = 0
    for r in records:
        rid = str(r.get('id', ''))
        if rid.startswith('EV-') and rid[3:].isdigit():
            n = max(n, int(rid[3:]))
    return f'EV-{n + 1:04d}'


def build_record(args, roles, records):
    """Validate inputs and build the record. Returns (record, errors)."""
    errors = []

    if args.role not in roles:
        errors.append(f'unknown role {args.role} (not in core/ROLES.aol)')
    if args.kind not in aol.KINDS:
        errors.append(f'unknown kind {args.kind} (expected one of {"|".join(aol.KINDS)})')
    if args.verdict not in aol.VERDICTS:
        errors.append(f'unknown verdict {args.verdict}')
    if args.trust not in aol.TRUSTS:
        errors.append(f'unknown trust {args.trust}')
    if not args.claim:
        errors.append('claim is required (REQUIRE:DEFECT=CLAIM, and every record states what it shows)')

    # A passing verdict may never sit on top of a failing command. This is the
    # single most important check here: it is the mechanical form of "do not
    # report success you did not get".
    if args.verdict == 'PASS' and args.exit_code not in (None, 0):
        errors.append(f'verdict=PASS with exit_code={args.exit_code} (REJECT=PASS_WITH_NONZERO_EXIT)')

    if args.kind in aol.EXECUTED_KINDS:
        if not args.command:
            errors.append(f'kind={args.kind} requires --command (REQUIRE:{args.kind}=COMMAND+EXIT_CODE+ARTIFACT)')
        if args.exit_code is None:
            errors.append(f'kind={args.kind} requires --exit-code')
        if not args.artifact:
            errors.append(f'kind={args.kind} requires at least one --artifact')

    if args.kind in aol.SUBJECT_KINDS and not args.subject:
        errors.append(f'kind={args.kind} requires at least one --subject (what was reviewed)')

    artifacts = []
    for p in args.artifact or []:
        if not Path(p).exists():
            errors.append(f'artifact path does not exist: {p} (REJECT=MISSING_ARTIFACT_PATH)')
            continue
        artifacts.append({
            'path': p,
            'sha256': aol.sha256_file(p),
            'assertions': aol.count_assertions(p),
        })

    subjects = []
    for p in args.subject or []:
        if not Path(p).exists():
            errors.append(f'subject path does not exist: {p}')
            continue
        subjects.append({'path': p, 'sha256': aol.sha256_file(p)})

    if errors:
        return None, errors

    record = {
        'id': _next_id(records),
        'ts': _now(),
        'project': args.project,
        'task': args.task,
        'role': args.role,
        'actor': {
            'id': args.actor_id,
            'model': args.actor_model or '',
            'session': args.actor_session or '',
            'trust': args.trust,
        },
        'kind': args.kind,
        'claim': args.claim,
        'verdict': args.verdict,
    }
    if args.command:
        record['command'] = args.command
    if args.exit_code is not None:
        record['exit_code'] = args.exit_code
    if artifacts:
        record['artifacts'] = artifacts
    if subjects:
        record['subject'] = subjects
    if args.journey:
        record['journey'] = args.journey
    if args.invariant:
        record['invariants'] = args.invariant
    return record, []


def cmd_record(args):
    roles = aol.parse_roles(Path(args.root) / 'core' / 'ROLES.aol')
    path = aol.ledger_path(args.root, args.project)
    records, read_errors = aol.read_ledger(path)
    if read_errors:
        print('EVIDENCE=FAIL')
        for e in read_errors:
            print(' - ' + e)
        return 1

    record, errors = build_record(args, roles, records)
    if errors:
        print('EVIDENCE=REJECTED')
        for e in errors:
            print(' - ' + e)
        return 1

    aol.append_ledger(path, record)
    print(f'EVIDENCE=RECORDED {record["id"]} {record["kind"]} {record["verdict"]} -> {path}')
    return 0


def cmd_verify(args):
    """Re-hash every cited path. Detects evidence edited after the fact."""
    if args.ledger:
        targets = [(None, Path(args.ledger))]
    elif args.project:
        targets = [(args.project, aol.ledger_path(args.root, args.project))]
    else:
        targets = []
        for parent in ('projects', 'examples'):
            base = Path(args.root) / parent
            if base.exists():
                for d in sorted(x for x in base.iterdir() if x.is_dir()):
                    targets.append((d.name, d / 'evidence' / 'ledger.ndjson'))

    problems, checked = [], 0
    for project, path in targets:
        records, read_errors = aol.read_ledger(path)
        problems.extend(read_errors)
        for r in records:
            for field in ('artifacts', 'subject'):
                for item in r.get(field, []):
                    checked += 1
                    p = Path(item['path'])
                    if not p.exists():
                        problems.append(f'{r.get("id")}: {field} path gone: {p}')
                    elif aol.sha256_file(p) != item['sha256']:
                        problems.append(
                            f'{r.get("id")}: {field} {p} hash drift '
                            f'(recorded {item["sha256"][:12]}, now {aol.sha256_file(p)[:12]})'
                        )

    if problems:
        print('EVIDENCE=FAIL')
        for p in problems:
            print(' - ' + p)
        return 1
    print(f'EVIDENCE=PASS ({checked} references verified)')
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='cmd', required=True)

    r = sub.add_parser('record', help='append a validated evidence record')
    r.add_argument('--root', default='.')
    r.add_argument('--project', required=True)
    r.add_argument('--task', required=True)
    r.add_argument('--role', required=True)
    r.add_argument('--kind', required=True)
    r.add_argument('--verdict', required=True)
    r.add_argument('--claim', required=True, help='what this record shows')
    r.add_argument('--command')
    r.add_argument('--exit-code', type=int, default=None)
    r.add_argument('--artifact', action='append', help='repeatable: file produced as proof')
    r.add_argument('--subject', action='append', help='repeatable: file that was reviewed')
    r.add_argument('--journey')
    r.add_argument('--invariant', action='append')
    r.add_argument('--actor-id', required=True)
    r.add_argument('--actor-model', default='')
    r.add_argument('--actor-session', default='')
    r.add_argument('--trust', default='DECLARED', help='RUNTIME (harness-stamped) or DECLARED')
    r.set_defaults(fn=cmd_record)

    v = sub.add_parser('verify', help='re-hash cited paths and report drift')
    v.add_argument('--root', default='.')
    v.add_argument('--project')
    v.add_argument('--ledger', help='verify one ledger file directly')
    v.set_defaults(fn=cmd_verify)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == '__main__':
    main()
