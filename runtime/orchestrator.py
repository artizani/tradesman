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
from runtime import delivery  # noqa: E402


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
                   attempt=0, risk=None):
    """One role, one agent, one identity."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    actor = f'{role.lower()}-{session or "run"}'
    if attempt:
        actor += f'-r{attempt}'   # rework keeps the same author, distinct run
    gov = Governor(root, project, task, default_role=role, session=session,
                   actor_id=actor, risk=risk)
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


def record_risk(root, project, task, declared, floor, effective, flow, session,
                touched=None):
    """Stamp the risk decision as trust=RUNTIME evidence before any role runs.

    This is what makes the ratchet checkable later: enforce.py can compare the
    task's current RISK= and a freshly recomputed floor against what was
    actually run, and catch a registry demoted after the fact.
    """
    import subprocess
    subprocess.run([
        sys.executable, str(Path(root) / 'compiler' / 'evidence.py'), 'record',
        '--root', str(root), '--project', project, '--task', task,
        '--role', 'RISK_ASSESS', '--kind', 'RISK', '--verdict', 'PASS',
        '--claim', f'risk resolved: declared={declared} floor={floor} effective={effective}',
        '--actor-id', f'risk-{session or "run"}', '--trust', 'RUNTIME',
        '--declared', declared, '--floor', floor, '--effective', effective,
        '--flow', '>'.join(flow or []),
    ] + [x for t in (touched or []) for x in ('--touched', t)],
        capture_output=True, text=True, cwd=root)


class Run:
    """Accumulates what each role produced, so the next role is not blind."""

    def __init__(self, root, project, task, goal):
        self.root, self.project, self.task, self.goal = root, project, task, goal
        self.history = []
        self.risk = None
        self.declared = self.floor = None
        self.flow = []
        self.session = 'run'
        self.commit = self.pr = False

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
                           run.brief_for(author, extra), model, session, attempt, run.risk)
        run.note(a)
        print(f'    wrote={a["wrote"] or "-"} denials={len(a["denials"])}')

        print(f'--- {reviewer} ---')
        r = await run_role(run.root, run.project, run.task, reviewer,
                           run.brief_for(reviewer, sev), model, session, attempt, run.risk)
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
    # --skip-arch is gone: it was an unbounded way to drop the design gate.
    # FLOW:low subsumes it, and the floor decides whether low is available.
    p.add_argument('--risk', help='RAISE the risk level; it can never lower it')
    p.add_argument('--commit', action='store_true',
                   help='commit the delivered increment to its own branch (local)')
    p.add_argument('--pr', action='store_true',
                   help='push the branch and open a pull request (reaches outside)')
    a = p.parse_args()

    root = Path(a.root)
    max_rework = a.max_rework if a.max_rework is not None else rework_max(root)
    task_file = aol.project_dir(root, a.project) / 'tasks' / f'{a.task}.aol'
    goal = a.goal or (task_file.read_text() if task_file.exists() else '')
    run = Run(root, a.project, a.task, goal)

    task_kv = aol.parse_kv(task_file)
    declared = task_kv.get('RISK', 'low')
    floor = aol.risk_floor(root, a.project, task_kv)
    # RISK_EFFECTIVE=HIGHEST_OF:DECLARED+FLOOR+PATH. --risk joins the max, so it
    # can only add scrutiny -- an operator flag that could lower the level would
    # be the same hole as a self-declared RISK=low.
    risk = aol.highest(root, declared, floor, a.risk or 'low')
    steps = aol.flow_for(root, risk)
    pairs = dict(gate_pairs(root))

    print(f'INCREMENT={a.project}/{a.task}')
    print(f'  declared={declared}  floor={floor}  effective={risk}')
    if aol.rank(root, declared) < aol.rank(root, floor):
        print(f'  NOTE: declared risk is below the floor; running at {risk}. '
              f'Under-declaration is not arbitrated -- it is ignored.')
    print(f'  flow={">".join(steps) if steps else "default"}  rework_max={max_rework}\n')

    run.risk, run.declared, run.floor = risk, declared, floor
    run.flow, run.session = steps or [], a.session
    run.commit, run.pr = a.commit, a.pr
    record_risk(root, a.project, a.task, declared, floor, risk, steps, a.session)

    if steps:
        # Walk the declared flow, pairing each author with its reviewer.
        reviewers = set(pairs.values())
        for role in steps:
            if role in reviewers:
                continue          # runs as the second half of its gate
            if role in ('DEPLOY', 'POSTDEPLOY'):
                ok, msg = delivery.run_command_role(
                    run.root, a.project, a.task, role, role, a.session)
                print(f'--- {role} ---\n    {msg}')
                run.note({'role': role, 'actor': f'{role.lower()}-{a.session}',
                          'denials': [], 'wrote': [], 'evidence': [msg],
                          'breaches': [], 'output': msg})
                if not ok:
                    return finish(run, converged=False)
                continue
            if role in pairs:
                if not await gated(run, role, pairs[role], a.model, a.session,
                                   max_rework if pairs[role] in steps else 0):
                    return finish(run, converged=False)
            else:
                print(f'--- {role} ---')
                r = await run_role(root, a.project, a.task, role,
                                   run.brief_for(role), a.model, a.session,
                                   risk=run.risk)
                run.note(r)
                print(f'    wrote={r["wrote"] or "-"} evidence={len(r["evidence"])}')
        return finish(run, converged=True)

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
                       a.model, a.session, risk=run.risk)
    run.note(u)
    print(f'    wrote={u["wrote"] or "-"} evidence={len(u["evidence"])}')
    return finish(run, converged=True)


def deliver(run, commit, pr):
    """Turn a delivered increment into a branch, and optionally a PR."""
    summary = '\n'.join(
        f'- {h["role"]}: wrote {", ".join(h["wrote"]) or "nothing"}'
        + (f'; {len(h["denials"])} write(s) denied' if h['denials'] else '')
        for h in run.history)
    try:
        branch, sha = delivery.branch_and_commit(
            run.root, run.project, run.task, run.goal.splitlines()[0][:72],
            summary, run.session)
    except Exception as e:  # noqa: BLE001 -- report, never fail the increment on git
        print(f'  commit failed: {e}')
        return
    if sha is None:
        print(f'  nothing to commit on {branch}')
        return
    print(f'  committed {sha[:9]} on {branch}')
    if not pr:
        print(f'  open a PR with: git push -u origin {branch} && gh pr create')
        return
    url, err = delivery.open_pr(
        run.root, branch, f'{run.task}: {run.goal.splitlines()[0][:60]}',
        f'AgentOS increment.\n\n{summary}\n\nVerify:\n'
        f'`python3 compiler/enforce.py --root . --project {run.project} --strict`')
    print(f'  PR: {url}' if url else f'  PR failed: {err}')


def finish(run, converged):
    """DONE_ONE_SHOT=IMPLEMENTATION+PASSING_TEST_EVIDENCE+INDEPENDENT_REVIEW_PASS"""
    records, _ = aol.read_ledger(aol.ledger_path(run.root, run.project))
    mine = [r for r in records if r.get('task') == run.task]
    tests = [r for r in mine if r.get('kind') == 'TEST']

    # Filtering reviews by KIND alone was a self-approval hole: an IMPLEMENT
    # agent that recorded its own REVIEW/PASS satisfied "independent review
    # passed", and SOD= did not catch it because SOD compares actors across
    # roles and only one role was involved. A review counts only if a review
    # role authored it, with an actor distinct from everyone who wrote code.
    authors = {r.get('actor', {}).get('id') for r in mine
               if r.get('role') in ('IMPLEMENT', 'ARCH')}
    reviews = [r for r in mine
               if r.get('kind') in ('REVIEW', 'ARCH_APPROVAL')
               and r.get('role') in aol.REVIEW_ROLES
               and r.get('actor', {}).get('id') not in authors]

    kv = aol.parse_kv(Path(run.root) / 'projects' / run.project / 'project.aol')
    prod = [g for g in kv.get('PROD_GLOB', '').split('+') if g]
    code = [f for h in run.history for f in h['wrote'] if aol.match_globs(f, prod)]

    checks = [
        ('implementation exists', bool(code)),
        ('passing test evidence', any(r.get('verdict') == 'PASS' for r in tests)),
        ('independent review passed', any(r.get('verdict') == 'PASS' for r in reviews)),
        ('review was independent', bool(reviews) or not mine),
        ('gates converged', converged),
    ]
    print('\nINCREMENT CHECK')
    for name, ok in checks:
        print(f'  {"ok  " if ok else "NO  "} {name}')
    if code:
        print(f'  production files: {sorted(set(code))}')
    ok = all(c[1] for c in checks)
    print(f'\nINCREMENT={"DELIVERED" if ok else "NOT_DELIVERED"}')

    # Stamp git's view of the diff, so RISKPATH is judged against what was
    # really written rather than what a record chose to cite.
    try:
        record_risk(run.root, run.project, run.task, run.declared, run.floor,
                    run.risk, run.flow, f'{run.session}-close',
                    touched=delivery.touched_files(run.root))
    except Exception as e:  # noqa: BLE001
        print(f'  (could not stamp touched files: {e})')

    if ok and run.commit:
        deliver(run, run.commit, run.pr)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(anyio.run(main))
