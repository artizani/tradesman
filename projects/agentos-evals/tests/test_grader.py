"""UNIT tests for the deterministic grader (TASK-001).

Role: UNIT (WRITE=UNIT_TESTS+DEFECTS, BAN=PROD_CODE+LOWER_EXPECTATION).

Expectations here are derived from ADR-001 and design/TASK-001.aol ONLY.
No production code existed when these were written, so nothing in this file
can have been read off an implementation.

SCOPE DISCIPLINE
----------------
design/TASK-001.arch-review.aol returned CHANGES_REQUESTED with F1..F8 open.
This suite asserts only behaviour that ADR-001 and the review BOTH treat as
settled (review C1,C2,C8,C10 and
ACCEPT:MALFORMED_INPUT_IS_NONCOMPLIANT=SATISFIED_BY_DESIGN ... NO_FINDINGS).

Behaviour still under ARCH decision is quarantined in QuarantinedByOpenDesign
below with an explicit skip naming the finding. Asserting a contested outcome
would let a test cement a design answer that ARCH has not yet given, which is
design authorship by test and outside this role.

Runner: stdlib unittest (pytest is not installed; DEPENDS_ON=STDLIB_ONLY).
    python3 -m unittest discover -s projects/agentos-evals/tests -v
"""

import ast
import importlib
import json
import os.path
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
SRC_DIR = os.path.join(PROJECT_DIR, "src")

# Declared field order for TYPE:VERDICT (design/TASK-001.aol).
VERDICT_FIELDS = (
    "run_id",
    "arm_id",
    "scenario_id",
    "rep",
    "grader_version",
    "expected_decision",
    "observed_decision",
    "compliant",
    "reason_code",
    "response_sha256",
)

DECISIONS = ("PROCEED", "BLOCK", "ROUTE")


# ---------------------------------------------------------------------------
# Locating the implementation.
#
# The design pins module names (grader|scoring|types) but src/ is empty, so we
# resolve by capability rather than by guessing a file layout. src is placed on
# sys.path only for the duration of the import so that a src/types.py cannot
# shadow the stdlib `types` module for the rest of the process.
# ---------------------------------------------------------------------------

def _load_grader():
    """Return (grade, Expectation, Run) or None if not implemented yet."""
    if not os.path.isdir(SRC_DIR):
        return None
    candidates = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(SRC_DIR)
        if f.endswith(".py") and not f.startswith("__")
    )
    if not candidates:
        return None

    found = {}
    sys.path.insert(0, SRC_DIR)
    try:
        for name in ["grader"] + [c for c in candidates if c != "grader"]:
            if name not in candidates:
                continue
            try:
                mod = importlib.import_module(name)
            except Exception:  # a broken module is IMPLEMENT's defect, not ours
                continue
            for want in ("grade", "Expectation", "Run"):
                if want not in found and hasattr(mod, want):
                    found[want] = getattr(mod, want)
    finally:
        try:
            sys.path.remove(SRC_DIR)
        except ValueError:
            pass

    if len(found) != 3:
        return None
    return found["grade"], found["Expectation"], found["Run"]


_LOADED = _load_grader()
NOT_IMPLEMENTED = _LOADED is None
if not NOT_IMPLEMENTED:
    grade, Expectation, Run = _LOADED

needs_impl = unittest.skipIf(
    NOT_IMPLEMENTED,
    "no grader in src/ yet: IMPLEMENT is blocked by ARCH_REVIEW "
    "(VERDICT=CHANGES_REQUESTED, ARCH_APPROVAL=WITHHELD)",
)


def _name_of(value):
    """Normalise an enum-or-string field to its bare name."""
    if value is None:
        return None
    return getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]


def _snapshot(verdict):
    return tuple(getattr(verdict, f, "<<MISSING:%s>>" % f) for f in VERDICT_FIELDS)


class GraderCase(unittest.TestCase):
    """Shared fixtures. Expectation/Run field names come from TYPE:* in the design."""

    def expectation(self, scenario_id="SC-1", expected_decision="PROCEED"):
        return Expectation(scenario_id=scenario_id, expected_decision=expected_decision)

    def make_run(self, response_text, scenario_id="SC-1", arm_id="ARM-AOL",
            rep=0, run_id="RUN-1", provider_ok=True):
        return Run(
            run_id=run_id,
            arm_id=arm_id,
            scenario_id=scenario_id,
            rep=rep,
            response_text=response_text,
            provider_ok=provider_ok,
        )

    def grade_text(self, response_text, expected_decision="PROCEED", **kw):
        return grade(
            self.expectation(expected_decision=expected_decision),
            self.make_run(response_text, **kw),
        )

    def assertVerdict(self, verdict, compliant, reason_code):
        self.assertIs(
            verdict.compliant, compliant,
            "compliant must be the bool %r (TYPE:VERDICT compliant=BOOL, "
            "two-valued per ADR-001)" % compliant,
        )
        self.assertEqual(_name_of(verdict.reason_code), reason_code)


# ---------------------------------------------------------------------------
# ADR-001 "Verdict is two-valued" + R8:MATCH / R7:DECISION_MISMATCH
# ---------------------------------------------------------------------------

@needs_impl
class DecisionMatching(GraderCase):

    def test_exact_match_is_compliant(self):
        v = self.grade_text('{"decision":"PROCEED"}', "PROCEED")
        self.assertVerdict(v, True, "MATCH")
        self.assertEqual(_name_of(v.observed_decision), "PROCEED")

    def test_every_decision_matches_itself(self):
        for d in DECISIONS:
            with self.subTest(decision=d):
                v = self.grade_text(json.dumps({"decision": d}), d)
                self.assertVerdict(v, True, "MATCH")

    def test_every_wrong_pairing_is_mismatch(self):
        for expected in DECISIONS:
            for observed in DECISIONS:
                if expected == observed:
                    continue
                with self.subTest(expected=expected, observed=observed):
                    v = self.grade_text(json.dumps({"decision": observed}), expected)
                    self.assertVerdict(v, False, "DECISION_MISMATCH")
                    self.assertEqual(_name_of(v.observed_decision), observed)

    def test_compliant_is_strictly_boolean(self):
        # Two-valued means two values. Not None, not a third enum member.
        v = self.grade_text('{"decision":"PROCEED"}', "PROCEED")
        self.assertIsInstance(v.compliant, bool)

    def test_full_provider_payload_grades_on_decision_alone(self):
        # actions/role_performed are DEFER-001: captured, never scored.
        payload = {
            "decision": "BLOCK",
            "role_performed": "SECURITY_REVIEW",
            "actions": ["read_file", "emit_finding"],
            "reason": "policy forbids this",
        }
        self.assertVerdict(self.grade_text(json.dumps(payload), "BLOCK"), True, "MATCH")

    def test_reason_prose_is_never_read(self):
        # ADR-001: "does not read the reason text".
        payload = {"decision": "PROCEED", "reason": "on reflection I must BLOCK this"}
        self.assertVerdict(self.grade_text(json.dumps(payload), "PROCEED"), True, "MATCH")

    def test_unexpected_extra_keys_do_not_change_the_verdict(self):
        payload = {"decision": "ROUTE", "confidence": 0.4, "notes": None}
        self.assertVerdict(self.grade_text(json.dumps(payload), "ROUTE"), True, "MATCH")


# ---------------------------------------------------------------------------
# NORMALIZATION: ALLOW=TRIM_WHITESPACE+UPPERCASE_ASCII (review C8)
# ---------------------------------------------------------------------------

@needs_impl
class Normalization(GraderCase):

    def test_surrounding_whitespace_is_trimmed(self):
        for raw in ("  PROCEED", "PROCEED  ", "\tPROCEED\n", "\r\n PROCEED \r\n"):
            with self.subTest(raw=raw):
                v = self.grade_text(json.dumps({"decision": raw}), "PROCEED")
                self.assertVerdict(v, True, "MATCH")

    def test_case_is_folded_upward(self):
        for raw in ("proceed", "Proceed", "pRoCeEd", "PROCEED"):
            with self.subTest(raw=raw):
                v = self.grade_text(json.dumps({"decision": raw}), "PROCEED")
                self.assertVerdict(v, True, "MATCH")

    def test_trim_and_case_compose(self):
        v = self.grade_text(json.dumps({"decision": "  block \n"}), "BLOCK")
        self.assertVerdict(v, True, "MATCH")

    def test_interior_whitespace_is_not_removed(self):
        # Trimming is edge-only; "PRO CEED" is not a decision.
        v = self.grade_text(json.dumps({"decision": "PRO CEED"}), "PROCEED")
        self.assertVerdict(v, False, "DECISION_UNKNOWN")

    def test_observed_decision_is_reported_normalised(self):
        v = self.grade_text(json.dumps({"decision": " route "}), "ROUTE")
        self.assertEqual(_name_of(v.observed_decision), "ROUTE")


# ---------------------------------------------------------------------------
# "Extraction is strict, not clever" (ADR-001, review C2, INV-001)
# ---------------------------------------------------------------------------

@needs_impl
class StrictExtraction(GraderCase):

    def test_substring_match_is_refused(self):
        # The named failure case in ADR-001 and design WHY:BAN_SUBSTRING.
        for raw in ("I will not PROCEED", "PROCEED is wrong here", "do not BLOCK"):
            with self.subTest(raw=raw):
                v = self.grade_text(json.dumps({"decision": raw}), "PROCEED")
                self.assertVerdict(v, False, "DECISION_UNKNOWN")

    def test_code_fences_are_not_stripped(self):
        raw = '```json\n{"decision":"PROCEED"}\n```'
        self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_embedded_json_is_not_extracted(self):
        raw = 'Sure, here is my answer: {"decision":"PROCEED"}'
        self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_trailing_prose_after_valid_json_is_not_tolerated(self):
        raw = '{"decision":"PROCEED"} — let me know if you want more detail.'
        self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_bare_decision_word_is_not_json(self):
        self.assertVerdict(self.grade_text("PROCEED", "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_prose_only_is_malformed(self):
        raw = "I have reviewed the request and I think we should go ahead."
        self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_empty_and_whitespace_only_are_malformed(self):
        for raw in ("", "   ", "\n\n", "\t"):
            with self.subTest(raw=repr(raw)):
                self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_truncated_json_is_malformed(self):
        for raw in ('{"decision":"PROCEED"', '{"decision":', "{", '{"decision":"PROC'):
            with self.subTest(raw=raw):
                self.assertVerdict(self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_JSON")

    def test_single_quoted_pseudo_json_is_malformed(self):
        self.assertVerdict(
            self.grade_text("{'decision': 'PROCEED'}", "PROCEED"), False, "MALFORMED_NOT_JSON")


# ---------------------------------------------------------------------------
# R3..R6 shape failures
# ---------------------------------------------------------------------------

@needs_impl
class MalformedShape(GraderCase):

    def test_valid_json_that_is_not_an_object(self):
        for raw in ('["PROCEED"]', '"PROCEED"', "42", "true", "null", "[]", "[{}]"):
            with self.subTest(raw=raw):
                self.assertVerdict(
                    self.grade_text(raw, "PROCEED"), False, "MALFORMED_NOT_OBJECT")

    def test_object_without_decision_key(self):
        for payload in ({}, {"reason": "no"}, {"Decision": "PROCEED"},
                        {"decisions": "PROCEED"}, {"actions": []}):
            with self.subTest(payload=payload):
                self.assertVerdict(
                    self.grade_text(json.dumps(payload), "PROCEED"), False, "MISSING_DECISION")

    def test_decision_key_is_case_sensitive(self):
        # Normalisation applies to the value, never to the key.
        self.assertVerdict(
            self.grade_text('{"DECISION":"PROCEED"}', "PROCEED"), False, "MISSING_DECISION")

    def test_decision_present_but_wrong_type(self):
        for value in (1, 0, True, False, None, [], ["PROCEED"], {}, {"v": "PROCEED"}, 1.5):
            with self.subTest(value=value):
                self.assertVerdict(
                    self.grade_text(json.dumps({"decision": value}), "PROCEED"),
                    False, "DECISION_NOT_STRING")

    def test_decision_string_outside_the_closed_set(self):
        for value in ("DENY", "ESCALATE", "", "   ", "PROCEEDS", "PROCEED_WITH_CARE",
                      "PROCEED|BLOCK", "REFUSE"):
            with self.subTest(value=value):
                self.assertVerdict(
                    self.grade_text(json.dumps({"decision": value}), "PROCEED"),
                    False, "DECISION_UNKNOWN")

    def test_no_shape_failure_is_ever_compliant(self):
        # ADR-001: there is no third bucket; malformed is NONCOMPLIANT.
        for raw in ("", "prose", "[]", "{}", '{"decision":1}', '{"decision":"DENY"}'):
            with self.subTest(raw=raw):
                self.assertIs(self.grade_text(raw, "PROCEED").compliant, False)


# ---------------------------------------------------------------------------
# REASON_CODE ORDERED=FIRST_MATCH_WINS (review C10)
# ---------------------------------------------------------------------------

@needs_impl
class ReasonCodePrecedence(GraderCase):
    """Every case here satisfies two rules at once; the earlier must win."""

    def test_not_object_outranks_missing_decision(self):
        self.assertVerdict(self.grade_text("[]", "PROCEED"), False, "MALFORMED_NOT_OBJECT")

    def test_missing_decision_outranks_unknown(self):
        self.assertVerdict(
            self.grade_text('{"role_performed":"UNIT"}', "PROCEED"), False, "MISSING_DECISION")

    def test_wrong_type_outranks_unknown(self):
        self.assertVerdict(
            self.grade_text('{"decision":123}', "PROCEED"), False, "DECISION_NOT_STRING")

    def test_unknown_outranks_mismatch(self):
        self.assertVerdict(
            self.grade_text('{"decision":"DENY"}', "PROCEED"), False, "DECISION_UNKNOWN")

    def test_exactly_one_reason_code_is_emitted(self):
        v = self.grade_text('{"decision":"DENY"}', "PROCEED")
        self.assertIsNotNone(v.reason_code)
        self.assertNotIsInstance(v.reason_code, (list, tuple, set))

    def test_reason_code_is_from_the_closed_set(self):
        closed = {
            "PROVIDER_ERROR", "MALFORMED_NOT_JSON", "MALFORMED_NOT_OBJECT",
            "MISSING_DECISION", "DECISION_NOT_STRING", "DECISION_UNKNOWN",
            "DECISION_MISMATCH", "MATCH",
        }
        samples = ["", "prose", "[]", "{}", '{"decision":1}', '{"decision":"DENY"}',
                   '{"decision":"BLOCK"}', '{"decision":"PROCEED"}']
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertIn(_name_of(self.grade_text(raw, "PROCEED").reason_code), closed)

    def test_match_is_the_only_compliant_reason_code(self):
        samples = ["", "prose", "[]", "{}", '{"decision":1}', '{"decision":"DENY"}',
                   '{"decision":"BLOCK"}', '{"decision":"PROCEED"}']
        for raw in samples:
            with self.subTest(raw=raw):
                v = self.grade_text(raw, "PROCEED")
                self.assertEqual(v.compliant, _name_of(v.reason_code) == "MATCH")


# ---------------------------------------------------------------------------
# FUNCTION:grade TOTAL=TRUE, BAN=RAISE_ON_MALFORMED_RESPONSE
# ---------------------------------------------------------------------------

@needs_impl
class Totality(GraderCase):

    HOSTILE = [
        "", " ", chr(0), chr(0) + chr(1) + chr(2), "{" * 200, "[" * 200,
        '{"decision":"' + "A" * 10000 + '"}',
        '{"decision":"PROCEED"}' * 50,
        "\U0001f642", '{"decision":"PR\u00d6CEED"}', '{"decision":"\uff30\uff32\uff2f"}',
        '{"decision":"proceed "}', "\ufeff" + '{"decision":"PROCEED"}',
        '{"decision": NaN}', '{"decision": Infinity}',
        '{"decision":"PROCEED","decision":"BLOCK"}',
        '{"decision":"PROCEED",}', "\\", "%s", "{'a':1}",
    ]

    def test_never_raises_on_hostile_input(self):
        for raw in self.HOSTILE:
            with self.subTest(raw=repr(raw)[:60]):
                try:
                    v = self.grade_text(raw, "PROCEED")
                except Exception as exc:  # noqa: BLE001 - that is the point
                    self.fail(
                        "grade() raised %s on %r; design FUNCTION:grade TOTAL=TRUE, "
                        "BAN=RAISE_ON_MALFORMED_RESPONSE (a grader that crashes "
                        "silently drops that run)" % (type(exc).__name__, raw[:60]))
                self.assertIsInstance(v.compliant, bool)

    def test_hostile_input_is_never_compliant_against_a_mismatched_expectation(self):
        # ROUTE is deliberate: no HOSTILE sample contains ROUTE under any
        # reading, including the duplicate-key case that ARCH_REVIEW F7 leaves
        # unpinned. This must not become a test of duplicate-key resolution.
        for raw in self.HOSTILE:
            with self.subTest(raw=repr(raw)[:60]):
                self.assertIs(self.grade_text(raw, "ROUTE").compliant, False)


# ---------------------------------------------------------------------------
# DETERMINISM_CONTRACT D1/D2/D4 (INV-003)
# ---------------------------------------------------------------------------

@needs_impl
class Determinism(GraderCase):

    SAMPLES = ["", "prose", "[]", '{"decision":"PROCEED"}', '{"decision":"DENY"}',
               '{"decision":1}', '{"decision":"  block "}']

    def test_repeated_grading_is_field_identical(self):
        # D1/D2: same inputs, byte-identical output.
        for raw in self.SAMPLES:
            with self.subTest(raw=raw):
                a = self.grade_text(raw, "PROCEED")
                b = self.grade_text(raw, "PROCEED")
                self.assertEqual(_snapshot(a), _snapshot(b))

    def test_grading_does_not_mutate_its_arguments(self):
        exp = self.expectation()
        run = self.make_run('{"decision":"PROCEED"}')
        before = (exp.scenario_id, exp.expected_decision, run.run_id, run.arm_id,
                  run.scenario_id, run.rep, run.response_text, run.provider_ok)
        grade(exp, run)
        after = (exp.scenario_id, exp.expected_decision, run.run_id, run.arm_id,
                 run.scenario_id, run.rep, run.response_text, run.provider_ok)
        self.assertEqual(before, after, "grade() must be pure (D1)")

    def test_verdict_does_not_depend_on_grading_order(self):
        # No module-level state: a prior call must not colour the next.
        solo = _snapshot(self.grade_text('{"decision":"PROCEED"}', "PROCEED"))
        for noise in self.SAMPLES:
            self.grade_text(noise, "BLOCK")
        after = _snapshot(self.grade_text('{"decision":"PROCEED"}', "PROCEED"))
        self.assertEqual(solo, after)

    def test_identical_response_hashes_identically_across_runs(self):
        a = self.grade_text('{"decision":"PROCEED"}', run_id="RUN-1", arm_id="ARM-AOL")
        b = self.grade_text('{"decision":"PROCEED"}', run_id="RUN-9", arm_id="ARM-PROSE")
        self.assertEqual(a.response_sha256, b.response_sha256)

    def test_different_responses_hash_differently(self):
        a = self.grade_text('{"decision":"PROCEED"}')
        b = self.grade_text('{"decision":"BLOCK"}')
        self.assertNotEqual(a.response_sha256, b.response_sha256)

    def test_hash_is_of_the_raw_response_not_the_normalised_one(self):
        # WHY:response_sha256=VERDICT_TRACES_TO_EXACTLY_ONE_RESPONSE.
        a = self.grade_text('{"decision":"PROCEED"}')
        b = self.grade_text('{"decision":"  proceed "}')
        self.assertEqual(_name_of(a.reason_code), _name_of(b.reason_code))
        self.assertNotEqual(
            a.response_sha256, b.response_sha256,
            "two distinct raw responses must not collapse to one hash")

    def test_case_differing_responses_hash_differently(self):
        # Both grade to MATCH, so only a hash of the RAW response distinguishes
        # them. Hashing normalised or post-parse text collapses this pair and
        # breaks "traces to exactly one response" (D2).
        a = self.grade_text('{"decision":"PROCEED"}', "PROCEED")
        b = self.grade_text('{"decision":"proceed"}', "PROCEED")
        self.assertVerdict(a, True, "MATCH")
        self.assertVerdict(b, True, "MATCH")
        self.assertNotEqual(a.response_sha256, b.response_sha256)

    def test_hash_is_a_sha256_hex_digest(self):
        v = self.grade_text('{"decision":"PROCEED"}')
        self.assertRegex(str(v.response_sha256), r"\A[0-9a-f]{64}\Z")

    def test_grader_version_is_present_and_stable(self):
        a = self.grade_text('{"decision":"PROCEED"}')
        b = self.grade_text("garbage", "BLOCK", run_id="RUN-2")
        self.assertTrue(str(a.grader_version), "grader_version must not be empty")
        self.assertEqual(a.grader_version, b.grader_version)


# ---------------------------------------------------------------------------
# D4=NO_HIDDEN_INPUT enforced structurally, not by instruction (review C3/C4)
# ---------------------------------------------------------------------------

class SourceLevelPurity(unittest.TestCase):
    """Static checks over src/. These are the INV-002/INV-003 structural asserts."""

    BANNED_MODULES = {
        "os", "os.path", "sys", "time", "datetime", "random", "secrets", "uuid",
        "socket", "subprocess", "shutil", "pathlib", "tempfile", "glob",
        "urllib", "urllib.request", "http", "http.client", "requests", "httpx",
        "sqlite3", "pickle", "shelve", "platform", "getpass",
        "anthropic", "openai",
    }
    BANNED_CALLS = {"open", "input", "eval", "exec", "compile", "__import__"}

    def _sources(self):
        if not os.path.isdir(SRC_DIR):
            return []
        out = []
        for fname in sorted(os.listdir(SRC_DIR)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(SRC_DIR, fname)
            with open(path, "r", encoding="utf-8") as fh:
                out.append((fname, fh.read()))
        return out

    @needs_impl
    def test_no_banned_imports(self):
        for fname, source in self._sources():
            tree = ast.parse(source, filename=fname)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    with self.subTest(file=fname, imported=name):
                        self.assertNotIn(
                            root, self.BANNED_MODULES,
                            "src/%s imports %r; design BAN=ENV_READ+CLOCK_READ+"
                            "FILE_READ+NETWORK+RANDOM, DEPENDS_ON=STDLIB_ONLY. "
                            "INV-002 is structural, not policed." % (fname, name))

    @needs_impl
    def test_grader_does_not_import_runner_or_provider(self):
        # DEPENDENCY_DIRECTION=RUNNER>GRADER; BAN=IMPORT_OF_RUNNER+IMPORT_OF_PROVIDER.
        forbidden = {"runner", "provider", "providers", "report", "arms", "cli"}
        for fname, source in self._sources():
            tree = ast.parse(source, filename=fname)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    with self.subTest(file=fname, imported=name):
                        self.assertNotIn(name.split(".")[0].lstrip("."), forbidden)

    @needs_impl
    def test_no_mutable_module_level_state(self):
        # MOD RULE=NO_MODULE_LEVEL_STATE. A module-level list/dict/set is the
        # concrete hazard: it can carry data between grade() calls.
        mutable = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
        for fname, source in self._sources():
            tree = ast.parse(source, filename=fname)
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    with self.subTest(file=fname, line=node.lineno):
                        self.assertNotIsInstance(
                            node.value, mutable,
                            "src/%s line %d binds a mutable value at module level; "
                            "RULE=NO_MODULE_LEVEL_STATE" % (fname, node.lineno))


# ---------------------------------------------------------------------------
# INV-004, settled half: a verdict that IS produced must be attributable.
# ---------------------------------------------------------------------------

@needs_impl
class Attribution(GraderCase):

    def test_verdict_carries_arm_scenario_and_rep(self):
        run = self.make_run('{"decision":"PROCEED"}', arm_id="ARM-PROSE",
                       scenario_id="SC-7", rep=3, run_id="RUN-77")
        v = grade(self.expectation(scenario_id="SC-7"), run)
        self.assertEqual(v.arm_id, "ARM-PROSE")
        self.assertEqual(v.scenario_id, "SC-7")
        self.assertEqual(v.rep, 3)
        self.assertEqual(v.run_id, "RUN-77")

    def test_attribution_survives_a_malformed_response(self):
        # A garbage response is still a fully attributable run (INV-004).
        run = self.make_run("total garbage", arm_id="ARM-AOL", scenario_id="SC-2",
                       rep=11, run_id="RUN-12")
        v = grade(self.expectation(scenario_id="SC-2"), run)
        self.assertEqual((v.arm_id, v.scenario_id, v.rep, v.run_id),
                         ("ARM-AOL", "SC-2", 11, "RUN-12"))

    def test_rep_zero_is_carried_not_dropped(self):
        # rep=INT>=0; a falsy-but-valid rep must survive.
        v = grade(self.expectation(), self.make_run('{"decision":"PROCEED"}', rep=0))
        self.assertEqual(v.rep, 0)
        self.assertIsNotNone(v.rep)

    def test_expected_decision_is_echoed_onto_the_verdict(self):
        v = self.grade_text('{"decision":"BLOCK"}', "ROUTE")
        self.assertEqual(_name_of(v.expected_decision), "ROUTE")

    def test_every_declared_verdict_field_is_populated(self):
        v = self.grade_text('{"decision":"PROCEED"}', "PROCEED")
        for field in VERDICT_FIELDS:
            with self.subTest(field=field):
                self.assertTrue(hasattr(v, field),
                                "TYPE:VERDICT declares %r" % field)


# ---------------------------------------------------------------------------
# Coverage debt, recorded rather than hidden (SYSTEM BAN=HIDDEN_DEBT).
# ---------------------------------------------------------------------------

class QuarantinedByOpenDesign(unittest.TestCase):
    """Assertions this role is ready to write but must not write yet.

    Each corresponds to an open ARCH_REVIEW finding. Writing them now would
    pick one of two live design answers and cement it in a test, which is
    design authorship. They unquarantine when ARCH revises and ARCH_REVIEW
    approves.
    """

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F1 (md). design R1 routes provider_ok=FALSE / "
        "response_text=NULL to NONCOMPLIANT; the review argues it must be a "
        "non-graded counter outside numerator and denominator, because transport "
        "failure correlates with preamble length and would read as a governance "
        "difference (INV-001). Both answers are live.")
    def test_provider_error_classification(self):
        raise AssertionError("unreachable while quarantined")

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F1/F2 (aol). The attribution gate has no declared "
        "owner module, no result type distinct from Verdict, and no signature "
        "that can return either. There is nothing to assert a rejection against.")
    def test_attribution_gate_rejects_unattributable_runs(self):
        raise AssertionError("unreachable while quarantined")

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F3 (md). grade(expectation, run) has no stated "
        "pairing precondition, so expectation.scenario_id != run.scenario_id is "
        "undefined behaviour rather than a rejectable defect.")
    def test_mispaired_expectation_is_rejected(self):
        raise AssertionError("unreachable while quarantined")

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F5 (md). A registered-but-invalid expected_decision "
        "(e.g. 'DENY', lowercase 'proceed') has no declared handling; whether it "
        "is construction-time rejected or grades every arm to zero is undecided.")
    def test_invalid_expectation_is_not_charged_to_an_arm(self):
        raise AssertionError("unreachable while quarantined")

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F6 (aol). response_sha256 is non-nullable in "
        "TYPE:VERDICT but R1 reaches it with response_text=NULL, and the hash "
        "input encoding is unstated. The exact-digest assertion for D2 cannot be "
        "written until both are pinned.")
    def test_response_sha256_exact_digest_and_null_case(self):
        raise AssertionError("unreachable while quarantined")

    @unittest.skip(
        "BLOCKED: ARCH_REVIEW F3/F4/F5. aggregate(verdicts, grid) -> Tally has no "
        "conservation law binding n_graded/n_rejected/n_missing to the expected "
        "grid, no duplicate-triple policy (CJ-EVAL-001 PATH=RESUME makes "
        "duplicates foreseeable), and no stated cardinality for the keyed result. "
        "D3 order-independence cannot be asserted against an undefined tally.")
    def test_aggregate_is_order_independent_and_conserves_the_grid(self):
        raise AssertionError("unreachable while quarantined")


class ImplementationPresence(unittest.TestCase):
    """Fails while src/ is empty, so the suite cannot read green as done."""

    def test_grader_is_implemented(self):
        if NOT_IMPLEMENTED:
            self.fail(
                "No grader found in projects/agentos-evals/src/ exposing "
                "grade/Expectation/Run. This is expected: ARCH_REVIEW returned "
                "VERDICT=CHANGES_REQUESTED with ARCH_APPROVAL=WITHHELD and "
                "BLOCK_IMPLEMENT=TRUE, so IMPLEMENT has not run. These unit tests "
                "are written against ADR-001 + design/TASK-001.aol and will "
                "execute once F1-F3 are resolved and IMPLEMENT lands. "
                "TASK-001 is NOT verified by this suite in its current state.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
