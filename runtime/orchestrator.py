#!/usr/bin/env python3
"""Deliver one increment of working, tested code in a single run.

The first version of this walked core/PROCESS.aol FLOW= in a straight line.
That is not what PROCESS.aol says. FAIL=...ROUTE_DISTINCT_ROLE+RETEST is a
loop, and without it a review rejection ended the run: the first real dogfood
produced nineteen design and review artifacts and zero lines of production
code. Governance behaving exactly as specified, and still failing the goal.

So this implements the loop:

  author -> review -> (FAIL? -> author with the findings -> review) -> proceed

bounded by REWORK MAX, and it carries context forward (CARRY=) so the reviewer's
findings actually reach the author and the approved design actually reaches the
implementer. Reviews fail only on BLOCKING findings (SEVERITY=), so an increment
is not held hostage to eight nice-to-haves.

Run: .venv/bin/python -m runtime.orchestrator --project P --task TASK-001
"""
from pathlib import Path
import anyio
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'compiler'))
import aol  # noqa: E402
from runtime import agents as agents_mod  # noqa: E402
from runtime.hooks import Governor  # noqa: E402


def gate_pairs(root):
    """GATEPAIR=A:B+C:D -> [(author, reviewer), ...]"""
    raw = aol.parse_kv(Path(root) / 'core' / 'PROCESS.aol').get('GATEPAIR', '')
    out = []
    for clause in raw.split('+'):
        if ':' in clause:
            a, _, b = clause.partition(':')
            out.append((a.strip(), b.strip()))
    return out


def rework_max(root):
    raw = aol.parse_kv(Path(root) / 'core' / 'PROCESS.aol').get('REWORK', '')
    for part in raw.split():
        if part.startswith('MAX='):
            return int(part[4:])
    return 2


def severity_guidance(root):
    kv = aol.parse_kv(Path(root) / 'core' / 'PROCESS.aol')
    return (
        '### REVIEW CONTRACT\n'
        f'A finding is BLOCKING if it is one of: {kv.get("BLOCKING", "")}.\n'
        f'A finding is ADVISORY if it is one of: {kv.get("ADVISORY", "")}.\n'
        '\n'
        'You MUST record exactly one REVIEW record before you finish, with\n'
        'verdict=PASS or verdict=FAIL. A review you did not record did not\n'
        'happen, and is not read as approval -- it stops the increment.\n'
        '\n'
        'Record FAIL only if you found a BLOCKING issue. Advisory findings go in\n'
        'as separate DEFECT records and must NOT block the increment: an\n'
        'increment is not held hostage to preferences. If the work is correct,\n'
        'in scope, and evidenced, record PASS and list the advisories separately.')


def latest_review_verdict(root, project, task, role):
    """The reviewer's own recorded verdict is the gate signal.

    Reading it from the ledger rather than parsing prose means the reviewer
    must record evidence to be heard at all -- EVIDENCE>ASSERTION applied to
    the process itself.
    """
    records, _ = aol.read_ledger(aol.ledger_path(root, project))
    hits = [r for r in records
            if r.get('task') == task and r.get('role') == role
            and r.get('kind') in ('REVIEW', 'ARCH_APPROVAL')]
    return hits[-1].get('verdict') if hits else None


async def run_role(root, project, task, role, brief, model=None, session='',
                   attempt=0):
    """One role, one agent, one identity."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    actor = f'{role.lower()}-{session or "run"}'
    if attempt:
        actor += f'-r{attempt}'   # rework keeps the same author, distinct run
    gov = Governor(root, project, task, default_role=role, session=session,
                   actor_id=actor)
    policy = gov.policies[role]

    options = ClaudeAgentOptions(
        system_prompt=agents_mod.role_prompt(
            policy, agents_mod.core_context(root), actor=actor),
        allowed_tools=policy.tools,
        hooks=gov.hooks(),
        permission_mode='bypassPermissions',   # the hooks are the gate
        setting_sources=[],                    # no inherited user settings
        cwd=str(root),
        model=model,
        max_turns=40,
    )

    text = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(brief)
        async for msg in client.receive_response():
            for block in getattr(msg, 'content', []) or []:
                if getattr(block, 'text', None):
                    text.append(block.text)

    gov.append_handoff(role, actor)
    return {'role': role, 'actor': actor, 'denials': gov.denials,
            'wrote': gov.writes, 'evidence': gov.recorded,
            'output': '\n'.join(text)}


class Run:
    """Accumulates what each role produced, so the next role is not blind."""

    def __init__(self, root, project, task, goal):
        self.root, self.project, self.task, self.goal = root, project, task, goal
        self.history = []

    def note(self, result):
        self.history.append(result)

    def carry(self):
        """CARRY=PRIOR_HANDOFFS+PRIOR_FINDINGS+FILES_WRITTEN"""
        if not self.history:
            return ''
        lines = ['### WHAT EARLIER ROLES DID']
        for h in self.history:
            wrote = ', '.join(h['wrote']) or 'nothing'
            lines.append(f'\n{h["role"]} (actor {h["actor"]}) wrote: {wrote}')
            tail = h['output'].strip().splitlines()[-12:]
            if tail:
                lines.append('  said: ' + ' '.join(x.strip() for x in tail)[:1200])
        return '\n'.join(lines)

    def brief_for(self, role, extra=''):
        parts = [f'Project {self.project}, task {self.task}.', '', self.goal, '']
        carried = self.carry()
        if carried:
            parts += [carried, '']
        if extra:
            parts += [extra, '']
        parts.append(
            'Perform ONLY your role, then stop. Do not ask questions -- make\n'
            'reasonable choices and proceed. Record your evidence before you finish.')
        return '\n'.join(parts)


async def gated(run, author, reviewer, model, session, max_rework):
    """author -> reviewer, looping back to the author on a BLOCKING failure."""
    sev = severity_guidance(run.root)
    extra = ''
    for attempt in range(max_rework + 1):
        label = f'{author}' + (f' (rework {attempt})' if attempt else '')
        print(f'--- {label} ---')
        a = await run_role(run.root, run.project, run.task, author,
                           run.brief_for(author, extra), model, session, attempt)
        run.note(a)
        print(f'    wrote={a["wrote"] or "-"} denials={len(a["denials"])}')

        print(f'--- {reviewer} ---')
        r = await run_role(run.root, run.project, run.task, reviewer,
                           run.brief_for(reviewer, sev), model, session, attempt)
        run.note(r)
        verdict = latest_review_verdict(run.root, run.project, run.task, reviewer)
        print(f'    wrote={r["wrote"] or "-"} verdict={verdict}')

        if verdict == 'PASS':
            return True
        if verdict is None:
            # A reviewer that recorded nothing has not reviewed. Treat as a
            # process failure, not a silent approval.
            print(f'    {reviewer} recorded no verdict -- treating as FAIL')
        extra = (f'### REWORK REQUIRED\n{reviewer} returned {verdict or "no verdict"}.\n'
                 f'Their findings are above. Address every BLOCKING finding, then stop.\n'
                 f'Do not redesign beyond what the findings require.')
    print(f'    ESCALATE: {author}/{reviewer} did not converge in {max_rework} reworks')
    return False


async def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', default=str(ROOT))
    p.add_argument('--project', required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--goal', help='what the increment must deliver')
    p.add_argument('--model')
    p.add_argument('--session', default='run')
    p.add_argument('--max-rework', type=int, default=None)
    p.add_argument('--skip-arch', action='store_true',
                   help='design already approved; go straight to implementation')
    a = p.parse_args()

    root = Path(a.root)
    max_rework = a.max_rework if a.max_rework is not None else rework_max(root)
    task_file = aol.project_dir(root, a.project) / 'tasks' / f'{a.task}.aol'
    goal = a.goal or (task_file.read_text() if task_file.exists() else '')
    run = Run(root, a.project, a.task, goal)

    print(f'INCREMENT={a.project}/{a.task}  rework_max={max_rework}\n')

    if not a.skip_arch:
        if not await gated(run, 'ARCH', 'ARCH_REVIEW', a.model, a.session, max_rework):
            return finish(run, converged=False)

    if not await gated(run, 'IMPLEMENT', 'CODE_REVIEW', a.model, a.session, max_rework):
        return finish(run, converged=False)

    print('--- UNIT ---')
    u = await run_role(root, a.project, a.task, 'UNIT',
                       run.brief_for('UNIT',
                                     'The implementation exists. Write tests that assert the\n'
                                     'task invariants, RUN them, and record the result -- including\n'
                                     'a FAIL verdict if they fail. Do not edit production code.'),
                       a.model, a.session)
    run.note(u)
    print(f'    wrote={u["wrote"] or "-"} evidence={len(u["evidence"])}')
    return finish(run, converged=True)


def finish(run, converged):
    """DONE_ONE_SHOT=IMPLEMENTATION+PASSING_TEST_EVIDENCE+INDEPENDENT_REVIEW_PASS"""
    records, _ = aol.read_ledger(aol.ledger_path(run.root, run.project))
    mine = [r for r in records if r.get('task') == run.task]
    tests = [r for r in mine if r.get('kind') == 'TEST']
    reviews = [r for r in mine if r.get('kind') in ('REVIEW', 'ARCH_APPROVAL')]

    kv = aol.parse_kv(Path(run.root) / 'projects' / run.project / 'project.aol')
    prod = [g for g in kv.get('PROD_GLOB', '').split('+') if g]
    code = [f for h in run.history for f in h['wrote'] if aol.match_globs(f, prod)]

    checks = [
        ('implementation exists', bool(code)),
        ('passing test evidence', any(r.get('verdict') == 'PASS' for r in tests)),
        ('independent review passed', any(r.get('verdict') == 'PASS' for r in reviews)),
        ('gates converged', converged),
    ]
    print('\nINCREMENT CHECK')
    for name, ok in checks:
        print(f'  {"ok  " if ok else "NO  "} {name}')
    if code:
        print(f'  production files: {sorted(set(code))}')
    ok = all(c[1] for c in checks)
    print(f'\nINCREMENT={"DELIVERED" if ok else "NOT_DELIVERED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(anyio.run(main))
