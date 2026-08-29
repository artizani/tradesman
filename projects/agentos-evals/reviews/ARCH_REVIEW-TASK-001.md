# ARCH_REVIEW — TASK-001 / ADR-001

- Role: ARCH_REVIEW (WRITE=REVIEW; BAN=DESIGN_AUTHOR+IMPLEMENT)
- Date: 2026-08-29
- Subjects:
  - `ADR-001.md` sha256 `e3f99140e3d2e279bbbb732ee29237a79ca4e64d514b2eb0773b808a20ec6d53`
  - `design/TASK-001.aol` sha256 `233ab6f377e380676e5b11f26af94cba7d17bf80e99efd0163c8c1abaf1f5ca2`
  - Hashes match the ARCH handoff of 2026-08-29. No drift.
- **Verdict: CHANGES_REQUESTED** (F1–F3 material). Not approved for IMPLEMENT.

## Sound as designed

Two-valued verdict with diagnostic `reason_code`; the anti-leniency argument
(rescue rules favour the rambliest arm, INV-001); attribution gate ordered ahead
of grading; integer numerator/denominator with rate deferred to the report edge;
`grader_version` + `response_sha256`; DEFER-001 declared rather than hidden. The
prior draft's three-value/two-value contradiction is genuinely resolved.

## Material findings

**F1 — `R1:PROVIDER_ERROR >NONCOMPLIANT` contradicts the ADR's own principle (INV-001, INV-004).**
The ADR argues correctly that an unattributable run must not land on an arm's
record. A provider transport failure is the same class: it is not output the arm
authored, so it is not the arm failing to govern. This is not a neutral
misclassification — preamble length differs across arms *by construction* (it is
the variable under test), so context-limit, rate-limit and timeout errors
correlate with the arm. The AOL arm and the prose arm would accrue infrastructure
noncompliance at different rates and the result would read as a governance
difference. `CJ-EVAL-001` already lists `PROVIDER_ERROR` as a path distinct from
`MALFORMED_RESPONSE`; the design collapses them.
The "third bucket is a drain failures escape through" argument does not carry
here: an arm cannot make itself fail by provider error to raise its rate.
Direction (ARCH's call): route `provider_ok=FALSE | response_text=NULL` to a
non-graded counter alongside `n_rejected`, out of both numerator and denominator.

**F2 — D2 re-gradability is owned by no boundary.**
"Changing normalization requires a re-grade of stored responses" and
`D2=REGRADING_STORED_RESPONSES_REPRODUCES_VERDICTS_BYTE_FOR_BYTE` both require
raw responses to survive the run. But `NOT_OWNS=PERSISTENCE`,
`OUT_OF_SCOPE=PERSISTENCE_FORMAT`, and TYPE:VERDICT deliberately stores only the
SHA-256. If nothing retains raw text, RSK-004's mitigation is unenforceable and
D2 is an assertion, not a property. RSK-001 was handled correctly — flagged,
assigned to RUNNER, marked out of scope. Do the same here rather than leaving it
implicit.

**F3 — `grade(expectation, run)` has no stated pairing precondition.**
The attribution gate checks that an expectation is *registered* for the
scenario, not that the expectation handed to `grade` is *that* scenario's. A
caller-side lookup error grades scenario A's response against scenario B's
expected decision and emits a well-formed verdict that is silently wrong — every
downstream field (`arm_id`, `scenario_id`, `rep`, sha256) is present and
plausible, so nothing detects it. Cheap fix: add
`CHECK=expectation.scenario_id==run.scenario_id` with
`ON_FAIL=REJECT_RECORD+HARNESS_DEFECT`.

## Should fix before IMPLEMENT

**F4 — uniqueness of `(arm_id, scenario_id, rep)` is unspecified (RSK-003, owned by this task).**
`CJ-EVAL-001` declares `PATH=RESUME`. A resume that re-emits an already-graded
cell produces duplicate verdicts. `n_missing = EXPECTED_GRID − OBSERVED` then
understates missing and can go negative, while `n_graded`/`n_compliant` inflate.
Declare the cell key unique and state the duplicate policy.

**F5 — `n_missing` / `n_rejected` overlap is ambiguous.**
A rejected record fills no grid cell, so its cell is also missing; one failed run
may increment both. State the intended identity (e.g.
`n_graded + n_rejected + n_missing = EXPECTED_GRID`, or declare the overlap
explicitly). RSK-003 claims these counters remove denominator ambiguity — they
only do so once their relationship is pinned.

## Minor

- **F6** — no v1 value or home declared for `grader_version`; the bump rule has
  nothing mechanical behind it.
- **F7** — JSON strictness underspecified: non-standard literals (`NaN`,
  `Infinity`), duplicate object keys, and the whitespace set for `TRIM`. Within
  one implementation this stays deterministic, but D4's "on any machine" claim is
  stronger than that. Pin the parser behaviour.
- **F8** — `invariants.aol` asserts `INV-002=CODE+UNIT+COMPONENT`; the design's
  determinism contract asserts UNIT only. The COMPONENT leg is unassigned. Likely
  belongs to the pipeline, not the grader — but it should say so.

## Gate note (not actioned by this role)

`architecture.aol` records `ADR:ADR-001=ACTIVE` while ADR-001 is `Proposed`. The
ADR is right that this needs reconciling — but only on approval, and this review
does not approve. Reconciliation is an ARCH write.

## Next

ARCH — revise `ADR-001.md` and `design/TASK-001.aol` for F1–F3 (F4–F5 strongly
recommended), then return to ARCH_REVIEW. Not IMPLEMENT.
