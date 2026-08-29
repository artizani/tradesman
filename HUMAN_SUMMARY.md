# Human Summary

AgentOS separates generic engineering governance from product-specific knowledge.

Every agent enters with `PROJECT + TASK + ROLE`, receives a compact context pack,
performs one role, records evidence, updates state and leaves a handoff.

LLMs reason. Deterministic scripts enforce loading, validation and context
boundaries. Humans retain authority over product direction, business invariants,
regulation and material production risk.

## What "enforce" means here

The bans are not requests. Each one has a mechanism behind it:

| Ban | Mechanism |
| --- | --- |
| Self-approval | Roles run as distinct actors; `SOD=` rejects a ledger where one actor holds both sides |
| Role collapse | One agent per role, with only that role's write policy |
| Test weakening | Assertion counts recorded per artifact; a drop without a defect fails the build |
| Assertion over evidence | A record is rejected unless it cites a command, its exit code and a file that exists |
| Hidden tampering | Artifacts hashed at attestation; `verify` re-hashes them |

`compiler/` catches violations after the fact and gates CI. `runtime/` prevents
them at the moment of action and writes the evidence itself, so a model never
authors its own attestation. Both exist because AgentOS also governs runtimes it
does not control.

## What is still open

Whether AOL's compression costs compliance. `evals/` runs the same scenarios
under AOL, under the identical rules in English, and under no governance at all.
Until that has been run, the compression is a design bet, not a finding.
