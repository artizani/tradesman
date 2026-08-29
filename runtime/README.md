# AgentOS Runtime

The compiler layer (`compiler/enforce.py`) detects ban violations after the fact and gates CI.
This layer prevents them at the moment of action, and writes the evidence itself.

Both are needed. The compiler layer still works when AgentOS governs a runtime that is not the
Agent SDK — Codex, plain Claude Code, a human. The runtime layer is what makes the bans
mechanical when AgentOS drives the work itself.

## Why the Claude Agent SDK

Four ways exist to build an agent. They differ on who supplies the harness and who supplies the
deployment:

| Surface | Harness | Deployment | Fit for AgentOS |
| --- | --- | --- | --- |
| Claude API, manual loop | you | you | No built-in file/bash tools to govern |
| Claude API Tool Runner | SDK | you | Only tools you define; no filesystem |
| Managed Agents | Anthropic | Anthropic | Anthropic hosts the sandbox; AgentOS wants local repo governance |
| **Claude Agent SDK** | **SDK** | **you** | **Built-in Read/Write/Edit/Bash + hooks + subagents** |

AgentOS governs writes to a real repository, so it needs a harness whose file and shell tools it
can intercept. That is the Agent SDK. The Tool Runner sounds similar and is not: it loops over
tools you define and has no filesystem surface to police.

## How each ban becomes a mechanism

| AOL rule | Mechanism |
| --- | --- |
| `ROLE X: BAN=PROD_CODE` | `PreToolUse` returns `permissionDecision: "deny"` for writes matching `PROD_GLOB` |
| `BAN=ROLE_COLLAPSE` | One `AgentDefinition` per role; a role's tools are its own (review roles get no `Write`) |
| `BAN=SELF_APPROVAL` | Each role runs as a distinct agent with a distinct actor id; `enforce.py` `SOD=` rejects overlap |
| `EVIDENCE>ASSERTION` | `PostToolUse` records the command, its real exit code and hashed output — the model never authors it |
| `EXIT=...+APPEND_HANDOFF` | `SubagentStop` appends the handoff |

Denials quote the AOL rule back to the agent. A denial is not an obstacle to route around; it means
the work belongs to another role, and saying so is how the agent learns the boundary.

## Layout

| File | Role |
| --- | --- |
| `roles.py` | Parses `core/ROLES.aol` into an executable write policy. Reads the rules; never restates them. |
| `agents.py` | One `AgentDefinition` per role, prompt built from the core AOL files. |
| `hooks.py` | `Governor`: the `PreToolUse` / `PostToolUse` / `SubagentStop` callbacks. |
| `orchestrator.py` | Walks `core/PROCESS.aol` `FLOW=`, one distinct agent per role. |
| `selftest.py` | Proves the bans are mechanical. |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python runtime/roles.py            # show the policy derived from AOL
.venv/bin/python runtime/agents.py           # show the generated agent definitions
.venv/bin/python -m runtime.selftest         # policy matrix (free)
.venv/bin/python -m runtime.selftest --live  # drive a real agent at a banned write (costs tokens)

.venv/bin/python -m runtime.orchestrator --project my-project --task TASK-001
python3 compiler/enforce.py --root . --project my-project --strict
```

## Known limits

- **Bash is the weak edge.** Shell cannot be parsed reliably, so `_bash_candidates` is deliberately
  over-broad: it recombines directory and filename tokens because agents reach production code via
  `cd <dir> && sed -i ... <file>`. A false denial routes work to the right role; a false allow lets a
  banned write through. `compiler/enforce.py` is the backstop for what slips past.
- **`setting_sources=[]` is deliberate.** Governance must not inherit the user's `CLAUDE.md` or
  project settings, or the arms of any comparison stop being comparable.
- **The live selftest deliberately omits the governance prompt.** A compliant model would hide a
  broken hook — in the first run of this test the agent refused on its own and the hook was never
  exercised, which is exactly the false confidence the design has to avoid.
