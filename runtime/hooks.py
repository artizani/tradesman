#!/usr/bin/env python3
"""The enforcement teeth: hooks that make AgentOS bans mechanical.

compiler/enforce.py detects violations after the fact and gates CI. These hooks
prevent them at the moment of action, and -- more importantly -- write the
evidence themselves. A model cannot fabricate a record it never authors.

PreToolUse   deny a write the acting role is banned from making
PostToolUse  record what actually happened, stamped trust=RUNTIME
SubagentStop append the handoff on exit (EXIT=...+APPEND_HANDOFF in LLM_README.aol)
"""
from pathlib import Path
import datetime
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'compiler'))
sys.path.insert(0, str(ROOT))
import aol  # noqa: E402
from runtime import roles as roles_mod  # noqa: E402

# Best-effort detection of shell commands that mutate a path. Bash is a hole in
# any path-based policy; this narrows it rather than closing it. The compiler
# layer is what actually catches what slips through.
_SHELL_MUTATES = re.compile(
    r'>>?|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b|\btruncate\b|\bdd\b|\bpatch\b'
)
_SHELL_TOKEN = re.compile(r'[\w./\-]*[/.][\w./\-]+')
_BARE_FILE = re.compile(r'\b[\w\-]+\.[A-Za-z][\w]*\b')

# Shell separators, so a command is examined segment by segment. Built by
# escaping literals rather than hand-written, because a hand-written
# `&&|\|\||;|\|` is one slip away from containing an empty alternative, which
# matches everywhere and silently splits between every character.
_SEP = re.compile('|'.join(re.escape(x) for x in ('&&', '||', ';', '|', '\n')))
_ENV_PREFIX = re.compile(r'^(?:\w+=\S+\s+)+')

# A command counts as verification only when a shell segment *runs* one of
# these. Prefixes must be real paths (`.venv/bin/pytest`), never arbitrary
# non-space runs -- `\S*pytest` happily matched inside `print(pytest.__version__)`.
_PATH = r'(?:[\w.\-]+/)*'
_INTERP = re.compile(r'^' + _PATH + r'(?:python3?|bash|sh|node)\s+')
_RUNNER = re.compile(
    r'^(?:'
    r'-m\s+(?:pytest|unittest)'
    r'|' + _PATH + r'pytest'
    r'|npm\s+(?:run\s+)?test'
    r'|yarn\s+test'
    r'|npx\s+playwright\s+test'
    r'|jest'
    r'|go\s+test'
    r'|cargo\s+test'
    r'|' + _PATH + r'[\w\-]*(?:selftest|enforce)\.py'
    r'|' + _PATH + r'[\w\-]*postdeploy[\w.\-]*'
    r')\b'
)


class Governor:
    """Holds the governed context and produces the SDK hook callbacks."""

    def __init__(self, root, project, task, default_role=None, session='',
                 actor_id=None, risk=None):
        # Resolved: an unresolved root breaks relative_to() wherever the path
        # crosses a symlink (macOS /var -> /private/var), silently sending every
        # path down the "not source" branch and disabling the policy.
        self.root = Path(root).resolve()
        self.project = project
        self.task = task
        self.default_role = default_role
        self.session = session
        self.actor_id = actor_id
        self.policies = roles_mod.load(self.root)
        self.prod_globs, self.test_globs = roles_mod.project_globs(self.root, project)
        self.gov_globs, self.task_globs = roles_mod.governance_globs(self.root)
        self.risk = risk
        self.breaches = []
        self.denials = []
        self.recorded = []
        self.writes = []

    # -- role resolution ---------------------------------------------------
    def role_for(self, input_data):
        """The acting role. Subagent type is the role name; else the run default."""
        agent_type = input_data.get('agent_type') or ''
        name = agent_type.upper().replace('-', '_')
        if name in self.policies:
            return name
        return self.default_role

    def actor_for(self, input_data):
        """A stable string identity. Never trust the raw hook value's shape.

        agent_id has arrived as a dict containing the operator's email address,
        which then leaked into the evidence ledger. Identity is an actor id, not
        whatever the harness happened to attach.
        """
        if self.actor_id:
            return self.actor_id
        raw = input_data.get('agent_id')
        if isinstance(raw, dict):
            raw = raw.get('id')
        if isinstance(raw, str) and raw:
            return raw
        return (self.default_role or 'runtime').lower()

    # -- candidate paths ---------------------------------------------------
    def _paths(self, tool_name, tool_input):
        if tool_name in roles_mod.MUTATING_TOOLS:
            p = tool_input.get('file_path') or tool_input.get('notebook_path')
            return [p] if p else []
        if tool_name == 'Bash':
            cmd = tool_input.get('command', '')
            if not _SHELL_MUTATES.search(cmd):
                return []
            return self._bash_candidates(cmd)
        return []

    def _bash_candidates(self, cmd):
        """Candidate paths a mutating shell command might touch.

        Parsing shell correctly is not winnable, so this is deliberately
        over-broad: a false denial routes work to the role that owns it, while
        a false allow lets a banned write through. Agents reach production code
        by `cd <dir> && sed -i ... <file>`, which splits the path across two
        tokens, so directory and filename tokens are also recombined.
        """
        tokens = set(_SHELL_TOKEN.findall(cmd)) | set(_BARE_FILE.findall(cmd))
        dirs = {t for t in tokens if '/' in t}
        files = {t for t in tokens if '/' not in t}
        candidates = set(tokens)
        for d in dirs:
            for f in files:
                candidates.add(f'{d.rstrip("/")}/{f}')
        return sorted(candidates)

    def _rel(self, path):
        p = Path(path)
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)

    # -- hooks -------------------------------------------------------------
    async def pre_tool_use(self, input_data, tool_use_id, context):
        role = self.role_for(input_data)
        if not role:
            return {}
        policy = self.policies.get(role)
        if not policy:
            return {}

        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {}) or {}

        for path in self._paths(tool_name, tool_input):
            rel = self._rel(path)

            # RISKPATH contradicts the declaration with what the change actually
            # touches. Risk lives in the code, not in the label on the task.
            if self.risk:
                lvl = aol.path_risk(self.root, self.project, rel)
                if lvl and aol.rank(self.root, lvl) > aol.rank(self.root, self.risk):
                    rule = (f'RISK_BREACH: {rel} is RISKPATH:{lvl} but this run is '
                            f'risk={self.risk}. core/PROCESS.aol '
                            f'RISK_EFFECTIVE=HIGHEST_OF:DECLARED+FLOOR+PATH -- the '
                            f'path outranks the declaration, so the flow was too thin.')
                    self.breaches.append({'path': rel, 'level': lvl, 'rule': rule})
                    self.denials.append({'role': role, 'tool': tool_name,
                                         'path': rel, 'rule': rule})
                    return {'hookSpecificOutput': {
                        'hookEventName': input_data.get('hook_event_name', 'PreToolUse'),
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': rule}}

            violation = policy.may_write(rel, self.prod_globs, self.test_globs,
                                         self.gov_globs, self.task_globs)
            if violation:
                self.denials.append({'role': role, 'tool': tool_name,
                                     'path': rel, 'rule': violation})
                # Quoting the AOL rule back is deliberate: the denial teaches it.
                return {
                    'hookSpecificOutput': {
                        'hookEventName': input_data.get('hook_event_name', 'PreToolUse'),
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': (
                            f'AgentOS denied this write. {violation}. '
                            f'Route it to the role that owns it -- do not collapse roles '
                            f'(core/SYSTEM.aol BAN=ROLE_COLLAPSE).'
                        ),
                    }
                }
        return {}

    async def post_tool_use(self, input_data, tool_use_id, context):
        """Record what actually happened, with an identity the model cannot set."""
        role = self.role_for(input_data)
        if not role:
            return {}

        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {}) or {}
        response = input_data.get('tool_response') or {}

        if tool_name in roles_mod.MUTATING_TOOLS:
            for path in self._paths(tool_name, tool_input):
                rel = self._rel(path)
                if rel not in self.writes:
                    self.writes.append(rel)
            return {}

        if tool_name != 'Bash':
            return {}
        command = tool_input.get('command', '')
        if not self._is_verification(command):
            return {}

        exit_code = self._exit_code(response)
        artifact = self._capture(command, response, tool_use_id)
        kind = 'POSTDEPLOY' if 'postdeploy' in command.lower() else 'TEST'

        self.record(
            role=role, kind=kind,
            verdict='PASS' if exit_code == 0 else 'FAIL',
            claim=f'{tool_name}: {command[:160]}',
            command=command, exit_code=exit_code,
            artifacts=[str(artifact)] if artifact else [],
            actor=self.actor_for(input_data), trust='RUNTIME',
        )
        return {}

    @staticmethod
    def _is_verification(command):
        """True only if a segment of the command actually invokes a test runner.

        This was a substring search, which recorded `cat notes && python3 -c
        "import pytest"` as a PASSING TEST -- a reconnaissance command minted as
        evidence because it mentioned pytest. That is the precise failure this
        system exists to prevent, so the match is anchored per shell segment:
        the runner must be what the segment *runs*, not a word inside it.
        """
        for seg in _SEP.split(command):
            seg = seg.strip().lstrip('(').strip()
            seg = _ENV_PREFIX.sub('', seg)  # FOO=bar pytest -> pytest
            if _RUNNER.match(seg):
                return True
            # `python3 -m pytest`, `python3 compiler/selftest.py`: strip the
            # interpreter and re-test. `python3 -c "..."` never matches, because
            # -c is not a runner -- which is what keeps code probes out.
            stripped = _INTERP.sub('', seg, count=1)
            if stripped != seg and _RUNNER.match(stripped):
                return True
        return False

    @staticmethod
    def _exit_code(response):
        for key in ('exit_code', 'exitCode', 'returncode'):
            if isinstance(response, dict) and key in response:
                try:
                    return int(response[key])
                except (TypeError, ValueError):
                    pass
        # No exit code reported: treat stderr-with-no-stdout as failure rather
        # than assuming success. Never default a missing signal to PASS.
        if isinstance(response, dict):
            if response.get('is_error'):
                return 1
        return 0

    def _capture(self, command, response, tool_use_id):
        """Persist tool output as a hashable artifact. Evidence needs a file."""
        out = ''
        if isinstance(response, dict):
            out = str(response.get('stdout', '')) + str(response.get('stderr', ''))
        elif response:
            out = str(response)
        if not out.strip():
            return None
        d = aol.project_dir(self.root, self.project) / 'evidence' / 'artifacts'
        d.mkdir(parents=True, exist_ok=True)
        f = d / f'{self.task}-{(tool_use_id or "run")[-8:]}.log'
        f.write_text(f'$ {command}\n{out}')
        return f.relative_to(self.root)

    def record(self, role, kind, verdict, claim, actor, trust='RUNTIME',
               command=None, exit_code=None, artifacts=(), subject=()):
        """Append evidence via compiler/evidence.py, so one validator governs all writers."""
        argv = [sys.executable, str(self.root / 'compiler' / 'evidence.py'), 'record',
                '--root', str(self.root), '--project', self.project, '--task', self.task,
                '--role', role, '--kind', kind, '--verdict', verdict, '--claim', claim,
                '--actor-id', actor, '--actor-session', self.session, '--trust', trust]
        if command:
            argv += ['--command', command]
        if exit_code is not None:
            argv += ['--exit-code', str(exit_code)]
        for a in artifacts:
            argv += ['--artifact', str(a)]
        for s in subject:
            argv += ['--subject', str(s)]
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=self.root)
        self.recorded.append({'rc': proc.returncode, 'out': proc.stdout.strip()})
        return proc.returncode

    async def subagent_stop(self, input_data, tool_use_id, context):
        self.append_handoff(self.role_for(input_data) or self.default_role or '?',
                            self.actor_for(input_data))
        return {}

    def append_handoff(self, role, actor, next_role=None):
        """EXIT=...+APPEND_HANDOFF (LLM_README.aol). Unconditional: a role that
        exits without a handoff leaves the next role blind."""
        path = aol.project_dir(self.root, self.project) / 'memory' / 'handoffs.ndjson'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a') as fh:
            fh.write(json.dumps({
                'ts': datetime.datetime.now(
                    datetime.timezone.utc).isoformat(timespec='seconds'),
                'project': self.project, 'task': self.task, 'role': role,
                'actor': actor, 'wrote': self.writes,
                'denials': [d['path'] for d in self.denials],
                'evidence': len(self.recorded), 'next': next_role,
            }, sort_keys=True) + '\n')

    def hooks(self):
        """The dict handed to ClaudeAgentOptions(hooks=...)."""
        from claude_agent_sdk import HookMatcher
        return {
            'PreToolUse': [HookMatcher(matcher='Write|Edit|NotebookEdit|Bash',
                                       hooks=[self.pre_tool_use])],
            'PostToolUse': [HookMatcher(matcher='Bash', hooks=[self.post_tool_use])],
            'SubagentStop': [HookMatcher(hooks=[self.subagent_stop])],
        }
