#!/usr/bin/env python3
"""Build one AgentDefinition per AgentOS role.

Each role becomes a separate subagent, so NO_ROLE_COLLAPSE is structural: an
agent cannot perform a role it was not instantiated as, because it does not
have that role's tools or that role's identity.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'compiler'))
sys.path.insert(0, str(ROOT))
import aol  # noqa: E402
from runtime import roles as roles_mod  # noqa: E402

CORE_FILES = ['LLM_README.aol', 'core/SYSTEM.aol', 'core/PROCESS.aol', 'core/ROLES.aol',
              'core/QUALITY.aol', 'core/PRECEDENCE.aol', 'core/EVIDENCE.aol']


def core_context(root=ROOT):
    """The governance half of the context pack -- identical for every role."""
    parts = []
    for f in CORE_FILES:
        p = Path(root) / f
        if p.exists():
            parts.append(f'### {f}\n{p.read_text().strip()}')
    return '\n\n'.join(parts)


def role_prompt(policy, core, actor='<your-actor-id>'):
    banned = '+'.join(policy.bans) or 'none'
    writes = '+'.join(policy.writes) or 'none'
    role = policy.name
    return f"""You are performing exactly one AgentOS role: {policy.name}.

{core}

### YOUR ROLE
ROLE={policy.name}
WRITE={writes}
BAN={banned}

You perform {policy.name} and nothing else. If the work needs another role,
stop and say which role it needs -- do not perform it yourself.

Writes outside your role are denied by the harness, not merely discouraged.
A denial is not an obstacle to work around: it means the work belongs to a
different role.

### EVIDENCE
The harness records every verification command you run, with its real exit code
and a hash of its output. You cannot make a failing command record as passing.

For evidence the harness cannot observe -- a review verdict, an architecture
approval -- record it yourself before you finish:

    python3 compiler/evidence.py record \\
      --project <project> --task <task> --role {role} \\
      --kind REVIEW|ARCH_APPROVAL|DEFECT --verdict PASS|FAIL|BLOCKED \\
      --claim "<what this shows>" --subject <file you reviewed> \\
      --actor-id {actor}

The record is rejected unless it cites something checkable. That is the point:
core/EVIDENCE.aol requires evidence to be recorded, not asserted.

Before you finish, record your evidence. A role that exits with no evidence has
produced nothing the next role can rely on.
"""


def build(root=ROOT, model=None):
    """{role_name: AgentDefinition} for every role in core/ROLES.aol."""
    from claude_agent_sdk import AgentDefinition
    core = core_context(root)
    out = {}
    for name, policy in roles_mod.load(root).items():
        out[name.lower().replace('_', '-')] = AgentDefinition(
            description=f'AgentOS {name} role (WRITE={"+".join(policy.writes) or "none"})',
            prompt=role_prompt(policy, core),
            tools=policy.tools,
            model=model,
            permissionMode='default',
        )
    return out


if __name__ == '__main__':
    defs = build()
    for k, v in sorted(defs.items()):
        print(f'{k:18} tools={",".join(v.tools):32} mode={v.permissionMode}')
    print(f'\ncore context: {len(core_context())} chars')
