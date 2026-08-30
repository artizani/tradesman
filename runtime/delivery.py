#!/usr/bin/env python3
"""Close the loop: run declared commands, and turn a delivered increment into a commit.

DEPLOY and POSTDEPLOY are roles with BAN=SOURCE_CHANGE, and their whole job is
to run something and report what happened. That is not a job for a model. An
agent that "deploys" and then tells you it worked is exactly the assertion this
framework refuses to accept, so these roles execute a command the PROJECT
declared, deterministically, and the evidence is RUNTIME-stamped because the
harness observed it rather than being told.

The git side is deliberately staged: committing to a branch is local and
reversible and happens on --commit; pushing and opening a PR reaches outside
the machine and needs --pr said explicitly.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'compiler'))
import aol  # noqa: E402

# project.aol keys naming what this project actually runs. No defaults are
# guessed: a project that has not said how it deploys cannot produce deploy
# evidence, and should be told so rather than have something invented for it.
COMMAND_KEYS = {
    'TEST': 'TEST_CMD',
    'DEPLOY': 'DEPLOY_CMD',
    'POSTDEPLOY': 'POSTDEPLOY_CMD',
}


def declared_command(root, project, kind):
    kv = aol.parse_kv(aol.project_dir(root, project) / 'project.aol')
    return kv.get(COMMAND_KEYS.get(kind, ''), '').strip()


def run_command_role(root, project, task, role, kind, session='', timeout=1800):
    """Execute the project's declared command and record what actually happened.

    Returns (ok, message). A missing declaration is a hard stop, not a skip:
    silently passing a gate nobody implemented is how a gate becomes theatre.
    """
    root = Path(root)
    command = declared_command(root, project, kind)
    if not command:
        return False, (f'{project}/project.aol declares no {COMMAND_KEYS[kind]}, so '
                       f'{kind} evidence cannot be produced. Add it, or run this task '
                       f'at a risk level whose GATE does not require {kind}.')

    proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                          cwd=root, timeout=timeout)

    d = aol.project_dir(root, project) / 'evidence' / 'artifacts'
    d.mkdir(parents=True, exist_ok=True)
    log = d / f'{task}-{kind.lower()}-{session or "run"}.log'
    log.write_text(f'$ {command}\n{proc.stdout}{proc.stderr}')

    verdict = 'PASS' if proc.returncode == 0 else 'FAIL'
    rc = subprocess.run([
        sys.executable, str(root / 'compiler' / 'evidence.py'), 'record',
        '--root', str(root), '--project', project, '--task', task,
        '--role', role, '--kind', kind, '--verdict', verdict,
        '--claim', f'{kind.lower()}: {command[:150]}',
        '--command', command, '--exit-code', str(proc.returncode),
        '--artifact', str(log.relative_to(root)),
        '--actor-id', f'{role.lower()}-{session or "run"}', '--trust', 'RUNTIME',
    ], capture_output=True, text=True, cwd=root)

    if rc.returncode != 0:
        return False, f'{kind} ran (exit {proc.returncode}) but evidence was rejected: {rc.stdout.strip()}'
    return proc.returncode == 0, f'{kind} exit={proc.returncode} -> {log.relative_to(root)}'


# ---------------------------------------------------------------- git


def _git(root, *args, check=True):
    p = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {p.stderr.strip()}')
    return p.stdout.strip()


def touched_files(root, since=None):
    """Files this increment actually changed, per git rather than per self-report.

    The hook's shell-path detection is best-effort, and check_riskpath can only
    see paths a record chose to cite. git sees what was really written, which is
    what a risk check ought to be comparing against.
    """
    root = Path(root)
    out = set()
    if since:
        out |= set(filter(None, _git(root, 'diff', '--name-only', since).splitlines()))
    out |= set(filter(None, _git(root, 'diff', '--name-only').splitlines()))
    out |= set(filter(None, _git(root, 'diff', '--name-only', '--cached').splitlines()))
    out |= set(filter(None, _git(root, 'ls-files', '--others', '--exclude-standard').splitlines()))
    return sorted(out)


def branch_and_commit(root, project, task, goal, evidence_summary, session=''):
    """Commit the increment on its own branch. Local and reversible."""
    root = Path(root)
    branch = f'agentos/{task.lower()}-{session or "run"}'
    current = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    if current != branch:
        existing = _git(root, 'branch', '--list', branch)
        _git(root, 'checkout', *(['-b'] if not existing else []), branch)

    _git(root, 'add', '-A')
    if not _git(root, 'diff', '--cached', '--name-only'):
        return branch, None

    message = (f'{goal}\n\n'
               f'AgentOS increment {project}/{task}.\n\n'
               f'{evidence_summary}\n\n'
               f'Evidence: projects/{project}/evidence/ledger.ndjson\n'
               f'Verify:   python3 compiler/enforce.py --root . --project {project} --strict\n')
    subprocess.run(['git', 'commit', '-q', '-F', '-'], cwd=root, input=message,
                   text=True, check=True)
    return branch, _git(root, 'rev-parse', 'HEAD')


def open_pr(root, branch, title, body):
    """Push and open a PR. Reaches outside the machine, so it is opt-in only."""
    root = Path(root)
    _git(root, 'push', '-u', 'origin', branch)
    p = subprocess.run(['gh', 'pr', 'create', '--head', branch,
                        '--title', title, '--body', body],
                       cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        return None, p.stderr.strip()
    return p.stdout.strip(), None
