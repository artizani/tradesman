# AgentOS

**Governance for AI agents that write software.** Agents plan, implement, test and review
under roles they cannot step outside of, and every claim they make is backed by a record
a script can check.

Place this folder at your repository root as `agent-os/`.

---

## The problem

An unsupervised coding agent will, given the chance, write the code, write the tests for
its own code, declare both correct, and tell you it is done. Each of those is reasonable
on its own. Together they mean nothing was checked.

AgentOS makes each of those failures impossible rather than discouraged:

| What goes wrong | What stops it |
| --- | --- |
| Agent approves its own work | Roles run as distinct actors; the ledger rejects one actor holding both sides |
| One agent plays coder, tester and reviewer | Each role is a separate agent with its own write policy |
| A test fails, so the assertion gets loosened | Assertion counts are recorded; a drop without a defect fails the build |
| "It works" with nothing to show for it | A record is rejected unless it cites a command, its exit code and a real file |
| Evidence edited after the fact | Artifacts are hashed at attestation and re-hashed on verify |
| Agent writes production code from a testing role | The harness denies the write before it happens |

The last one is not a metaphor. Writes are intercepted and refused.

## How it works

Three layers, each doing what the layer above cannot.

**AOL** — a compact language for governance rules. `core/*.aol` holds the generic
engineering process; `projects/<id>/*.aol` holds what is true about your product. Rules
live here and nowhere else: changing a rule means editing an `.aol` file, never Python.

**The compiler** (`compiler/`) — deterministic Python that reads AOL and checks reality
against it. No LLM, no network. This is your CI gate.

**The runtime** (`runtime/`) — drives work through the roles using the Claude Agent SDK,
denying out-of-role writes as they are attempted and recording evidence the model never
authors, so it cannot fabricate its own attestation.

The compiler works without the runtime. If your team uses a different agent, you still get
the audit.

---

## Install

```bash
git clone https://github.com/artizani/tradesman.git agent-os
cd agent-os

python3 compiler/validate.py --root .        # structure check, no dependencies
python3 compiler/selftest.py                 # proves each ban actually fires

# The runtime needs the Agent SDK:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m runtime.selftest         # 54 policy checks
```

If `validate.py` prints `VALIDATION=PASS` and the selftests pass, you are ready.

---

## Worked example: a clinic booking system

A complete, runnable example lives in [`examples/bookings/`](examples/bookings/). It is a
small appointment service with one rule that genuinely matters: **never double-book a
clinician.** Copy it, change the names, and you have your own project.

### 1. Describe the product — `project.aol`

```aol
AOL/1
PROJECT=bookings
NAME=ClinicBookings
MISSION=LetPatientsBookAndNeverDoubleBookAClinician
STATUS=active
PROD_GLOB=examples/bookings/src/*
TEST_GLOB=examples/bookings/tests/*
RISKPATH:critical=examples/bookings/src/slots*+examples/bookings/src/booking*
RISKPATH:high=examples/bookings/src/auth*+examples/bookings/src/payment*
RISKPATH:default=low
```

`PROD_GLOB` and `TEST_GLOB` are how the harness knows production code from tests — that
one distinction is what lets it refuse a tester writing implementation.

`RISKPATH` says which files are dangerous. Slot-locking code is critical no matter how
small the change; a formatting helper is not.

### 2. Name what must never be false — `invariants.aol`

This is the most important file in your project. Get it right and everything else works.

```aol
AOL/1
INV:INV-101=NO_DOUBLE_BOOKED_SLOT
INV:INV-102=EXPIRED_HOLD_RELEASES_SLOT
INV:INV-103=CANCELLED_BOOKING_FREES_SLOT
INV:INV-104=PATIENT_SEES_ONLY_OWN_BOOKINGS
ASSERT:INV-101=CODE+UNIT+COMPONENT+E2E+MONITOR
```

`ASSERT:` says where each invariant must be proven. INV-101 has to hold in the code, in
unit tests, in component tests, end to end, and in production monitoring — because a
double booking discovered by a patient in a waiting room is not a bug report, it is an
incident.

### 3. Map the journeys — `journeys.aol`

```aol
AOL/1
CJ CJ-BOOK-001: NAME=CLAIM_SLOT CRIT=critical ENTRY=SLOT_OFFERED
    EXIT=BOOKED+SLOT_LOCKED+PATIENT_NOTIFIED INV=INV-101+INV-102
    PATH=HAPPY+CONCURRENT_CLAIM+HOLD_EXPIRY+PAYMENT_TIMEOUT+RECOVERY
    TEST=COMPONENT+DEPLOYED_E2E+POSTDEPLOY
UJ UJ-BOOK-001: NAME=FIND_AND_BOOK ACTOR=PATIENT ENTRY=AUTHENTICATED
    EXIT=SEES_CONFIRMATION_WITH_REFERENCE TEST=DEPLOYED_E2E
```

`PATH=` is the list of routes through the journey that must actually run in a test. The
happy path is the one that never breaks; `CONCURRENT_CLAIM` is the one that does.

`CRIT=critical` matters mechanically — see risk levels below.

### 4. Define one increment — `tasks/TASK-101.aol`

```aol
AOL/1
TASK=TASK-101
STATUS=ready
RISK=high
GOAL=ClaimASlotSafelyUnderConcurrency
IN=SLOT_LOCK+BOOKING_CREATE+EVENT
OUT=PAYMENT+NOTIFICATION_TEMPLATES+UI
JOURNEY=CJ-BOOK-001
INV=INV-101+INV-102
ACCEPT=ONE_BOOKING_PER_SLOT+CONCURRENT_CLAIM_LOSES_CLEANLY+EXPIRED_HOLD_FREES_SLOT
```

`IN=` and `OUT=` are the scope fence. `OUT=UI` means an agent that starts improving the
booking screen is out of scope, and a reviewer can say so with a rule to point at.

`JOURNEY=` means this task **delivers** that journey. Use `SERVES=` when a task merely
contributes to one — the difference changes how much process the task gets.

### 5. Say how the project is tested and deployed — back in `project.aol`

```aol
TEST_CMD=python3 -m unittest discover -s examples/bookings/tests
DEPLOY_CMD=sh examples/bookings/scripts/deploy.sh
POSTDEPLOY_CMD=sh examples/bookings/scripts/postdeploy.sh
```

These are run by the harness and their **exit codes are recorded**, so a passing verdict
can never sit on a failing command. No model is involved: an agent that "deploys" and then
reports success is exactly the assertion this framework refuses to take on trust.

Nothing is guessed. A project that has not declared `DEPLOY_CMD` cannot produce deploy
evidence, and is told so — a gate nobody implemented must not quietly pass.

The example's [`postdeploy.sh`](examples/bookings/scripts/postdeploy.sh) is worth copying
in spirit: it does not smoke-test that the service is up, it **asserts INV-101 against
what was deployed**. That is what post-deploy evidence is for.

### 6. Run it

```bash
.venv/bin/python -m runtime.orchestrator --project bookings --task TASK-101

# ...and turn the result into a branch, or a pull request:
.venv/bin/python -m runtime.orchestrator --project bookings --task TASK-101 --commit
.venv/bin/python -m runtime.orchestrator --project bookings --task TASK-101 --commit --pr
```

`--commit` puts the increment on its own branch — local and reversible. `--pr` pushes and
opens a pull request, which reaches outside your machine, so it only ever happens when you
ask for it by name.

The commit records what each role did and what it was refused, and points at the ledger
and the command to verify it.

---

## Risk decides how much process a task gets

Putting a formatting helper through a full architecture review is ceremony that buys no
safety. Putting slot-locking code through no review at all is negligence. So the flow
scales — and, critically, **nobody gets to choose it.**

Effective risk is the **highest** of three signals: what the task declared, a floor
derived from what the task cites, and the risk of the paths actually written. A
declaration can only *add* scrutiny, never remove it. Under-declaring is not an argument
anyone has to win — it is simply ignored.

Run the two example tasks through the derivation and you can see it:

```
TASK-101: declared=critical  floor=critical  effective=critical
          flow=ARCH>ARCH_REVIEW>IMPLEMENT>CODE_REVIEW>SECURITY_REVIEW>UNIT>COMPONENT>TEST_REVIEW>JOURNEY_TEST

TASK-102: declared=low       floor=low       effective=low
          flow=IMPLEMENT>CODE_REVIEW>UNIT
```

Same project, same registry, opposite amounts of process — and nobody chose either.
TASK-101 declares `JOURNEY=CJ-BOOK-001`, meaning it *delivers* that journey, and the
journey is `CRIT=critical`. TASK-102 only says `SERVES=CJ-BOOK-001`, so it keeps the
three-role flow and finishes quickly.

**Try under-declaring it.** Edit `examples/bookings/tasks/TASK-101.aol` to `RISK=low` and
run:

```bash
python3 compiler/enforce.py --root .
```

```
ENFORCEMENT=FAIL
 - TASK-101: RISK=low but the floor is critical (core/PROCESS.aol RISK_FLOOR).
   A declaration may only add scrutiny, never remove it.
```

The run would have ignored the declaration and executed at `critical` anyway. This is the
record that the two disagreed.

**Why the floor has to exist:** skipping a reviewer satisfies the separation-of-duties
check *vacuously* — with no second record there are no two actors to compare. So
under-declaring risk does not defeat separation of duties; it moves the hole somewhere
that check structurally cannot look. The floor closes it, and `check_risk_flow` fails the
build if a done task is missing a role its risk level required.

Governance files are frozen while a task runs. No role can edit `core/`, `project.aol`,
`journeys.aol`, `invariants.aol`, or its own task file — because a task that could edit
its own `RISK=` could lower its own floor.

| Level | Flow | Done requires |
| --- | --- | --- |
| `low` | IMPLEMENT → CODE_REVIEW → UNIT | test + review |
| `high` | + ARCH, ARCH_REVIEW, TEST_REVIEW | + architecture approval |
| `critical` | + SECURITY_REVIEW, COMPONENT, JOURNEY_TEST | + deploy + post-deploy evidence |

---

## What a run produces

```
INCREMENT=DELIVERED
```

That is not a claim, it is a check: production files exist, a **passing** test record
exists, and an independent review passed with an actor different from the author.
A pile of design documents reports `NOT_DELIVERED`.

| Output | Location | What it is for |
| --- | --- | --- |
| Working code | your `PROD_GLOB` | the increment |
| Tests | your `TEST_GLOB` | written by a role banned from touching production code |
| `evidence/ledger.ndjson` | append-only | every command, exit code and file hash |
| `evidence/artifacts/*.log` | hashed | the actual output, re-verifiable later |
| `memory/handoffs.ndjson` | append-only | who did what, what was refused, what is next |
| `state.aol` | current | where the project stands |

### Reading the evidence

```bash
python3 compiler/evidence.py verify --root . --project bookings
python3 compiler/enforce.py --root . --project bookings --strict
```

`verify` re-hashes every file the ledger cites and reports drift — so evidence edited after
the fact is detectable. `enforce --strict` additionally refuses review evidence that the
agent wrote about itself rather than the harness recording it.

A real ledger line looks like this:

```json
{"id":"EV-0004","role":"CODE_REVIEW","kind":"REVIEW","verdict":"FAIL",
 "actor":{"id":"code_review-run1","trust":"RUNTIME"},
 "claim":"unhandled error path in claim_slot; missing audit event",
 "subject":[{"path":"src/booking.py","sha256":"9f2a..."}]}
```

`trust:RUNTIME` means the harness stamped it. `trust:DECLARED` means the agent said so, and
`--strict` will not accept that from a reviewer.

---

## Adopting on an existing codebase

```bash
python3 compiler/retrofit_scan.py --repo . --output agent-os/projects/mine/retrofit-report.json
```

Then, in order:

1. Copy `templates/project/` to `projects/<your-id>/`.
2. Set `PROD_GLOB` / `TEST_GLOB` to match your layout. Everything else depends on this.
3. Write `invariants.aol` honestly. Three real invariants beat twelve aspirational ones.
4. Register your critical journeys and mark the genuinely critical ones `CRIT=critical`.
5. Add `RISKPATH:` for the files where mistakes are expensive.
6. Start with one **low** risk task and expand only once the evidence looks right.

Step 3 is the work. The rest is filling in forms.

## Continuous integration

The compiler needs no model and no network, so it runs anywhere:

```yaml
- run: python3 agent-os/compiler/validate.py --root agent-os --strict
```

That single line checks separation of duties, evidence completeness for each task's risk
level, artifact tampering, test weakening, and risk under-declaration. It fails the build
with the AOL rule that was broken.

## Language support

Governance is by path glob, so the codebase can be in any language. Test runners currently
recognised: `pytest`, `unittest`, `npm test`, `yarn test`, `jest`, `playwright`,
`go test`, `cargo test`. Assertion counting covers Python, JavaScript/TypeScript, Java,
Swift and C++. Adding a runner is one line in `runtime/hooks.py`.

The runtime itself is Python because the Claude Agent SDK is. It governs code in any
language.

---

## Known limits

Stated plainly, because a framework about honest evidence should be honest about itself.

- **Deploy commands are yours to write.** AgentOS runs what your project declares and
  records the exit code; it does not know how to deploy your software and will not guess.
  A project with no `DEPLOY_CMD` cannot produce deploy evidence, and is told so rather
  than having the gate silently skipped.
- **Shell is the weak edge.** Path policy on `Bash` commands is deliberately over-broad
  rather than exact, because shell cannot be parsed reliably. Anything that slips past the
  hook is caught after the role by comparing `git diff` against the role's write policy and
  recorded as a defect — so a missed write is late, not invisible.
- **Diligence is not observable.** The harness forces *who* filed a review and refuses a
  review of a file the reviewer never opened. It cannot tell whether the reviewer read it
  carefully. Independence is mechanical; attention is not.
- **Registry completeness is judgment.** If a journey that should be `CRIT=critical` was
  never marked, the derived floor will be too low and no deterministic check can know
  better. `enforce.py` prints registry notes for the gaps it can see — unmarked journeys,
  missing `RISKPATH`, absent commands — but they are notes, because nothing can decide
  them for you.
- **Writing AOL by hand is a real barrier.** It is compact because it has to survive in a
  model's context window, not because it is pleasant to author.

## Repository layout

| Path | Contents |
| --- | --- |
| `core/` | Generic governance: system, process, roles, quality, precedence, evidence |
| `compiler/` | Deterministic checks — `validate`, `enforce`, `evidence`, `selftest` |
| `runtime/` | Agent SDK runtime — roles, agents, hooks, orchestrator, delivery |
| `examples/bookings/` | The worked example in this README |
| `examples/sample-project/` | A payments variant |
| `templates/project/` | Copy this to start |
| `evals/` | Measures whether AOL's compression costs compliance |
| `docs/` | `AOL_REFERENCE.md`, new-project and retrofit guides |

## Further reading

- [`docs/AOL_REFERENCE.md`](docs/AOL_REFERENCE.md) — the language, and which lines are machine-read
- [`runtime/README.md`](runtime/README.md) — how each ban becomes a mechanism
- [`HUMAN_SUMMARY.md`](HUMAN_SUMMARY.md) — the short version for people who will not read this
