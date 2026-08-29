#!/usr/bin/env python3
"""Shared AOL parsing and ledger helpers.

The rest of the compiler is intentionally terse, but the modules that enforce
governance are load-bearing, so they are written for legibility instead.
Every governance rule read here lives in core/*.aol -- never hardcode a rule
that AOL already states.
"""
from pathlib import Path
import hashlib
import json
import re

KINDS = ['TEST', 'REVIEW', 'ARCH_APPROVAL', 'DEPLOY', 'POSTDEPLOY', 'DEFECT', 'RISK']
VERDICTS = ['PASS', 'FAIL', 'BLOCKED']
TRUSTS = ['RUNTIME', 'DECLARED']

# Kinds whose records must cite a command, its exit code and at least one artifact.
EXECUTED_KINDS = ['TEST', 'DEPLOY', 'POSTDEPLOY']
# Kinds whose records must name what was reviewed.
SUBJECT_KINDS = ['REVIEW', 'ARCH_APPROVAL']
# Roles whose evidence --strict requires to be harness-stamped.
REVIEW_ROLES = ['ARCH_REVIEW', 'CODE_REVIEW', 'TEST_REVIEW', 'SECURITY_REVIEW']

_ASSERTION = re.compile(
    r'\bassert\b|\bassertThat\s*\(|\bexpect\s*\(|\.should\b|\bXCTAssert\w*\s*\(|\bEXPECT_\w+\s*\('
)


def read_lines(path):
    """Yield non-empty, non-comment lines of an AOL file."""
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and line != 'AOL/1':
            out.append(line)
    return out


def parse_kv(path):
    """Parse KEY=VALUE lines into a dict. Later keys win."""
    kv = {}
    for line in read_lines(path):
        if line.startswith('ROLE '):
            continue
        if '=' in line:
            k, _, v = line.partition('=')
            kv[k.strip()] = v.strip()
    return kv


def parse_roles(roles_path):
    """Parse `ROLE X: WRITE=A+B BAN=C+D` lines from core/ROLES.aol.

    Returns {role: {"WRITE": [...], "BAN": [...], "READ": [...]}}.
    """
    roles = {}
    for line in read_lines(roles_path):
        if not line.startswith('ROLE '):
            continue
        head, _, tail = line.partition(':')
        name = head[len('ROLE '):].strip()
        spec = {}
        for field in tail.split():
            if '=' in field:
                k, _, v = field.partition('=')
                spec[k.strip()] = [x for x in v.strip().split('+') if x]
        roles[name] = spec
    return roles


def parse_sod(roles_path):
    """Parse the SOD= line into [(role_a, role_b), ...] pairs that must differ."""
    raw = parse_kv(roles_path).get('SOD', '')
    pairs = []
    for clause in raw.split('+'):
        if '!=' in clause:
            a, _, b = clause.partition('!=')
            pairs.append((a.strip(), b.strip()))
    return pairs


def parse_gate(roles_path):
    """Parse GATE=DONE_REQUIRES:A+B+C into the list of required evidence kinds."""
    raw = parse_kv(roles_path).get('GATE', '')
    if ':' not in raw:
        return []
    _, _, kinds = raw.partition(':')
    return [k for k in kinds.strip().split('+') if k]


def project_dir(root, project):
    """Resolve a project id to its directory, falling back to examples/.

    Mirrors the lookup already used by compiler/context_resolver.py.
    """
    root = Path(root)
    d = root / 'projects' / project
    return d if d.exists() else root / 'examples' / project


def ledger_path(root, project):
    return project_dir(root, project) / 'evidence' / 'ledger.ndjson'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def count_assertions(path):
    """Best-effort assertion count, used to detect test weakening over time."""
    try:
        text = Path(path).read_text(errors='ignore')
    except OSError:
        return 0
    return len(_ASSERTION.findall(text))


def read_ledger(path):
    """Read an NDJSON ledger. Returns (records, errors)."""
    records, errors = [], []
    if not Path(path).exists():
        return records, errors
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            errors.append(f'{path}:{n}: malformed JSON ({e.msg})')
    return records, errors


def append_ledger(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as fh:
        fh.write(json.dumps(record, sort_keys=True) + '\n')


def match_globs(path, globs):
    """True if path matches any of the given fnmatch-style globs."""
    from fnmatch import fnmatch
    p = str(path)
    return any(fnmatch(p, g) for g in globs if g)


# ---------------------------------------------------------------- risk
# A declared risk may only ADD scrutiny, never remove it. The floor is derived
# from what the task cites, which the task author also controls -- so the floor
# is not tamper-proof on its own; it is one of three signals (declared, floor,
# path) whose HIGHEST wins. See core/PROCESS.aol RISK_EFFECTIVE.
#
# Why this matters: skipping a reviewer satisfies SOD= vacuously -- with no
# second record there are no actors to compare -- so under-declaring risk does
# not defeat SOD, it moves the hole somewhere SOD structurally cannot look.


def risk_order(root):
    """RISK_ORDER=critical>high>low -> {'critical': 2, 'high': 1, 'low': 0}"""
    raw = parse_kv(Path(root) / 'core' / 'PROCESS.aol').get('RISK_ORDER', 'critical>high>low')
    levels = [x.strip().lower() for x in raw.split('>') if x.strip()]
    levels.reverse()
    return {name: i for i, name in enumerate(levels)}


def rank(root, level):
    return risk_order(root).get((level or '').lower(), 0)


def highest(root, *levels):
    order = risk_order(root)
    best, best_rank = 'low', -1
    for lv in levels:
        r = order.get((lv or '').lower(), -1)
        if r > best_rank:
            best, best_rank = (lv or 'low').lower(), r
    return best


def risk_floor(root, project, task_kv):
    """The lowest risk a task may legitimately run at.

    RISK_FLOOR=CRIT_JOURNEY>critical|INV_REF>high|ELSE>low -- a task citing a
    critical journey floors at critical; one citing any invariant floors at
    high. Pure function of files, computable before any code exists, which is
    what lets the flow be chosen up front.
    """
    d = project_dir(root, project)
    journeys = {}
    for line in read_lines(d / 'journeys.aol'):
        if ':' in line and ('CJ ' in line or 'UJ ' in line):
            head, _, tail = line.partition(':')
            jid = head.split()[-1].strip()
            crit = 'critical' if 'CRIT=critical' in tail else ''
            journeys[jid] = crit

    # JOURNEY= is what the task DELIVERS and inherits criticality from.
    # SERVES= is contribution only -- traceability without the floor.
    delivers = [j.strip() for j in task_kv.get('JOURNEY', '').split('+') if j.strip()]
    if any(journeys.get(j) == 'critical' for j in delivers):
        return 'critical'
    if [i for i in task_kv.get('INV', '').split('+') if i.strip()]:
        return 'high'
    return 'low'


def riskpaths(root, project):
    """RISKPATH:<level>=glob+glob -> [(level, [globs]), ...], highest first."""
    kv = parse_kv(project_dir(root, project) / 'project.aol')
    out = []
    for key, val in kv.items():
        if key.startswith('RISKPATH:'):
            level = key.split(':', 1)[1].strip().lower()
            out.append((level, [g for g in val.split('+') if g]))
    return sorted(out, key=lambda x: rank(root, x[0]), reverse=True)


def path_risk(root, project, path):
    """The risk level a path carries, or None if only the default applies."""
    for level, globs in riskpaths(root, project):
        if level == 'default':
            continue
        if match_globs(path, globs):
            return level
    return None


def effective_risk(root, project, task_kv, declared=None):
    """RISK_EFFECTIVE=HIGHEST_OF:DECLARED+FLOOR+PATH (path added at write time)."""
    return highest(root, declared or task_kv.get('RISK', ''),
                   risk_floor(root, project, task_kv))


def flow_for(root, level):
    """FLOW:<risk>= -> [role, ...]; None when the level declares no flow."""
    raw = parse_kv(Path(root) / 'core' / 'PROCESS.aol').get(f'FLOW:{(level or "").lower()}')
    if not raw:
        return None
    steps = []
    for step in raw.split('>'):
        for part in step.split('+'):
            if part.strip():
                steps.append(part.strip())
    return steps


def gate_for(root, level):
    """GATE:<risk>= -> required evidence kinds, falling back to GATE=."""
    kv = parse_kv(Path(root) / 'core' / 'ROLES.aol')
    raw = kv.get(f'GATE:{(level or "").lower()}') or kv.get('GATE', '')
    if ':' not in raw:
        return []
    return [k for k in raw.partition(':')[2].strip().split('+') if k]
