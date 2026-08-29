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

KINDS = ['TEST', 'REVIEW', 'ARCH_APPROVAL', 'DEPLOY', 'POSTDEPLOY', 'DEFECT']
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
