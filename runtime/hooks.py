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


class Governor:
    """Holds the governed context and produces the SDK hook callbacks."""

    def __init__(self, root, project, task, default_role=None, session=''):
        # Resolved: an unresolved root breaks relative_to() wherever the path
        # crosses a symlink (macOS /var -> /private/var), silently sending every
        # path down the "not source" branch and disabling the policy.
        self.root = Path(root).resolve()
        self.project = project
        self.task = task
        self.default_role = default_role
        self.session = session
        self.policies = roles_mod.load(self.root)
        self.prod_globs, self.test_globs = roles_mod.project_globs(self.root, project)
        self.denials = []
        self.recorded = []

    # -- role resolution ---------------------------------------------------
    def role_for(self, input_data):
        """The acting role. Subagent type is the role name; else the run default."""
        agent_type = input_data.get('agent_type') or ''
        name = agent_type.upper().replace('-', '_')
        if name in self.policies:
            return name
        return self.default_role

    def actor_for(self, input_data):
        return input_data.get('agent_id') or self.default_role or 'runtime'

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
            violation = policy.may_write(self._rel(path), self.prod_globs, self.test_globs)
            if violation:
                self.denials.append({'role': role, 'tool': tool_name,
                                     'path': self._rel(path), 'rule': violation})
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
        return bool(re.search(r'\b(pytest|npm\s+test|jest|playwright|go\s+test|selftest'
                              r'|enforce\.py|postdeploy)\b', command))

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
        """EXIT=...+APPEND_HANDOFF (LLM_README.aol)."""
        role = self.role_for(input_data) or self.default_role or '?'
        path = aol.project_dir(self.root, self.project) / 'memory' / 'handoffs.ndjson'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a') as fh:
            fh.write(json.dumps({
                'ts': __import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc).isoformat(timespec='seconds'),
                'project': self.project, 'task': self.task, 'role': role,
                'actor': self.actor_for(input_data),
                'denials': len(self.denials), 'evidence': len(self.recorded),
            }, sort_keys=True) + '\n')
        return {}

    def hooks(self):
        """The dict handed to ClaudeAgentOptions(hooks=...)."""
        from claude_agent_sdk import HookMatcher
        return {
            'PreToolUse': [HookMatcher(matcher='Write|Edit|NotebookEdit|Bash',
                                       hooks=[self.pre_tool_use])],
            'PostToolUse': [HookMatcher(matcher='Bash', hooks=[self.post_tool_use])],
            'SubagentStop': [HookMatcher(hooks=[self.subagent_stop])],
        }
