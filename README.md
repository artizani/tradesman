# AgentOS v1

Place this folder at repository root as `agent-os/`.

## Bootstrap
Add to AGENTS.md, CLAUDE.md or CODEX.md:

```text
AgentOS governs delivery. Read ./agent-os/LLM_README.aol first.
Resolve PROJECT, TASK and ROLE with context_resolver.py.
Perform only the selected role. Never self-approve or collapse roles.
Update state, evidence and handoff before exit.
```

## Existing project
Copy `templates/project` to `projects/<id>`, run retrofit_scan.py, describe current reality, map journeys and invariants, then create gap tasks.

## New project
Copy the template, define mission, domain, architecture, invariants and critical journeys before broad implementation.

## Commands
```bash
python3 agent-os/compiler/validate.py --root agent-os
python3 agent-os/compiler/context_resolver.py --root agent-os --project sample-project --task TASK-001 --role IMPLEMENT
python3 agent-os/compiler/retrofit_scan.py --repo . --output agent-os/projects/my-project/retrofit-report.json
```
