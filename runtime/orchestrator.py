#!/usr/bin/env python3
"""Run a task through the AgentOS flow, one distinct agent per role.

core/PROCESS.aol defines FLOW=INTAKE>CONTEXT>...>HANDOFF. This walks that flow
and dispatches each role-bearing step to a separate agent with its own identity,
so NO_ROLE_COLLAPSE and NO_SELF_APPROVAL are structural: the reviewer is a
different actor from the implementer because it is a different agent, not
because a prompt asked it to pretend.

Run: .venv/bin/python -m runtime.orchestrator --project P --task TASK-001
"""
from pathlib import Path
import anyio
import argparse
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'compiler'))
import aol  # noqa: E402
from runtime import agents as agents_mod  # noqa: E402
from runtime.hooks import Governor  # noqa: E402

# FLOW steps that are performed by a role. Steps like INTAKE/CONTEXT/MERGE are
# process markers, not roles, and are skipped.
def flow_roles(root):
    kv = aol.parse_kv(Path(root) / 'core' / 'PROCESS.aol')
    roles = set(aol.parse_roles(Path(root) / 'core' / 'ROLES.aol'))
    steps = []
    for step in kv.get('FLOW', '').split('>'):
        for part in step.split('+'):
            part = part.strip()
            if part in roles and part not in steps:
                steps.append(part)
    return steps


async def run_role(root, project, task, role, brief, model=None, session=''):
    """One role, one agent, one identity."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    actor = f'{role.lower()}-{session or "run"}'
    gov = Governor(root, project, task, default_role=role, session=session,
                   actor_id=actor)
    policy = gov.policies[role]

    options = ClaudeAgentOptions(
        system_prompt=agents_mod.role_prompt(
            policy, agents_mod.core_context(root), actor=actor),
        allowed_tools=policy.tools,
        hooks=gov.hooks(),
        permission_mode='bypassPermissions',  # the hooks are the gate, not a prompt
        setting_sources=[],                   # governance must not inherit user settings
        cwd=str(root),
        model=model,
        max_turns=30,
    )

    text = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(brief)
        async for msg in client.receive_response():
            for block in getattr(msg, 'content', []) or []:
                if getattr(block, 'text', None):
                    text.append(block.text)

    gov.append_handoff(role, actor)
    return {'role': role, 'actor': actor, 'denials': gov.denials, 'wrote': gov.writes,
            'evidence': gov.recorded, 'output': '\n'.join(text)}


async def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(ROOT))
    p.add_argument('--project', required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--roles', help='comma-separated subset; default is the PROCESS.aol flow')
    p.add_argument('--brief', help='what the task needs; defaults to the task file')
    p.add_argument('--model')
    p.add_argument('--session', default='run')
    a = p.parse_args()

    root = Path(a.root)
    roles = ([r.strip().upper() for r in a.roles.split(',')] if a.roles
             else flow_roles(root))

    task_file = aol.project_dir(root, a.project) / 'tasks' / f'{a.task}.aol'
    brief = a.brief or (
        f'Task {a.task} in project {a.project}.\n\n'
        f'{task_file.read_text() if task_file.exists() else ""}\n\n'
        f'Perform your role for this task. Record what you actually verify.')

    print(f'FLOW={">".join(roles)}\n')
    for role in roles:
        print(f'--- {role} ---')
        r = await run_role(root, a.project, a.task, role, brief,
                           model=a.model, session=a.session)
        print(f'    wrote={r["wrote"] or "-"}')
        print(f'    denials={len(r["denials"])} evidence={len(r["evidence"])}')
        if r['output']:
            print('    ' + r['output'].strip().splitlines()[-1][:200])
        for d in r['denials']:
            print(f'    denied {d["tool"]} -> {d["path"]}')
    print('\nrun complete; enforce with:')
    print(f'  python3 compiler/enforce.py --root {root} --project {a.project} --strict')


if __name__ == '__main__':
    anyio.run(main)
