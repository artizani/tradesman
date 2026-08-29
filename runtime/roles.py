#!/usr/bin/env python3
"""Turn core/ROLES.aol into an executable write policy.

core/ROLES.aol is the single source of truth for what each role may write.
This module reads it; it does not restate it. If a role's BAN= changes in AOL,
the runtime's behaviour changes with it and no Python edit is required.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'compiler'))
import aol  # noqa: E402

# Tools that can change the repository. Everything else is read-only and always
# permitted -- roles are constrained by what they may *write*, not what they read.
MUTATING_TOOLS = ('Write', 'Edit', 'NotebookEdit', 'MultiEdit')

READ_ONLY_TOOLS = ['Read', 'Grep', 'Glob', 'Bash']
WRITE_TOOLS = ['Read', 'Grep', 'Glob', 'Bash', 'Write', 'Edit']

# WRITE= tokens in ROLES.aol that denote source-tree writes, mapped to the
# path class they may touch. Anything not listed writes only to evidence/state.
WRITE_CLASS = {
    'PROD_CODE': 'PROD',
    'UNIT_TESTS': 'TEST',
    'COMPONENT_TESTS': 'TEST',
    'E2E_TESTS': 'TEST',
    'TASK': 'TASK',
    'EPIC': 'TASK',
}


def _covers(path, globs):
    """True if path matches a glob, or names a directory the glob lives under.

    `cd projects/x/src && sed -i ... f.py` yields the bare directory as a token;
    treating that as a hit keeps the directory form from slipping past a glob
    written for the files inside it.
    """
    if aol.match_globs(path, globs):
        return True
    p = str(path).rstrip('/')
    return any(g.rstrip('/*').rstrip('/') == p for g in globs if g)


class RolePolicy:
    """What one role may do, derived from its AOL line."""

    def __init__(self, name, spec):
        self.name = name
        self.writes = spec.get('WRITE', [])
        self.bans = spec.get('BAN', [])
        self.classes = {WRITE_CLASS[w] for w in self.writes if w in WRITE_CLASS}
        # A role with no source-writing WRITE= token touches no source at all.
        self.may_write_source = bool(self.classes)

    @property
    def tools(self):
        """Every role gets the write tools; the PreToolUse hook is the gate.

        Withholding Write from review roles looked like defence in depth, but it
        also blocked ARCH from writing an ADR -- WRITE=DESIGN+ADR+RISK is a real
        write, just not a source write. One gate, derived from AOL, is both
        correcter and easier to reason about than two that disagree.
        """
        return WRITE_TOOLS

    def may_write(self, path, prod_globs, test_globs, gov_globs=(), task_globs=()):
        """Return None if allowed, else the AOL rule being violated.

        Governance files are frozen for every role: no role carries a WRITE
        token for GOV_GLOB, which is the point. A task that could edit
        journeys.aol or its own project.aol could lower its own risk floor,
        and the floor is the thing standing between an under-declared task and
        a skipped reviewer.
        """
        if _covers(path, gov_globs):
            return (f'ROLE {self.name}: core/ROLES.aol GOV_GLOB -- {path} is a '
                    f'governance input and is frozen while a task runs. Lowering '
                    f'your own risk floor is not a move available to any role.')
        if _covers(path, task_globs) and 'TASK' not in self.classes:
            return (f'ROLE {self.name}: WRITE={"+".join(self.writes)} -- {path} is a '
                    f'task or epic file. A task may not edit its own RISK=.')
        is_prod = _covers(path, prod_globs)
        is_test = _covers(path, test_globs)

        if not is_prod and not is_test:
            return None  # evidence, state, docs -- not source

        if is_prod and 'PROD' not in self.classes:
            return (f'ROLE {self.name}: WRITE={"+".join(self.writes)} '
                    f'BAN={"+".join(self.bans)} -- {path} is production code')
        if is_test and 'TEST' not in self.classes:
            return (f'ROLE {self.name}: WRITE={"+".join(self.writes)} '
                    f'BAN={"+".join(self.bans)} -- {path} is a test, and authoring '
                    f'the tests that judge your own work is self-review')
        return None


def load(root=ROOT):
    """Load every role policy from core/ROLES.aol."""
    parsed = aol.parse_roles(Path(root) / 'core' / 'ROLES.aol')
    return {name: RolePolicy(name, spec) for name, spec in parsed.items()}


def project_globs(root, project):
    kv = aol.parse_kv(aol.project_dir(root, project) / 'project.aol')
    return ([g for g in kv.get('PROD_GLOB', '').split('+') if g],
            [g for g in kv.get('TEST_GLOB', '').split('+') if g])


def governance_globs(root):
    """GOV_GLOB / TASK_GLOB from core/ROLES.aol -- frozen inputs."""
    kv = aol.parse_kv(Path(root) / 'core' / 'ROLES.aol')
    return ([g for g in kv.get('GOV_GLOB', '').split('+') if g],
            [g for g in kv.get('TASK_GLOB', '').split('+') if g])


if __name__ == '__main__':
    for name, p in sorted(load().items()):
        print(f'{name:18} tools={",".join(p.tools):32} classes={sorted(p.classes) or "-"}')
