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

## Enforcement

Structure and bans are checked by deterministic scripts, not trusted to the agent.

```bash
python3 compiler/validate.py --root . --strict   # structure, then the bans
python3 compiler/enforce.py  --root .            # SOD, DONE gate, tamper, test weakening
python3 compiler/selftest.py                     # proves each ban actually fires
```

Evidence is recorded, never asserted. A record is rejected unless it cites a command
with its exit code and a file that exists; a PASS verdict may not sit on a non-zero exit.

```bash
python3 compiler/evidence.py record --project P --task TASK-001 --role UNIT \
  --kind TEST --verdict PASS --claim "..." --command "pytest -q" --exit-code 0 \
  --artifact path/to/output.log --actor-id unit-01
python3 compiler/evidence.py verify --root .     # re-hash everything; detect tampering
```

## Runtime

`runtime/` drives work through the role flow using the Claude Agent SDK, where the bans
are enforced by the harness rather than requested of the model. See `runtime/README.md`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m runtime.selftest             # policy matrix
.venv/bin/python -m runtime.orchestrator --project P --task TASK-001
```

## Evals

`evals/` measures whether AOL's compression costs compliance: the same 12 scenarios run
under three arms that differ only by governance encoding (AOL, prose, none).

```bash
python3 evals/run_eval.py --arms aol,prose,control --reps 3
```
