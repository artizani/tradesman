#!/bin/sh
# Assert INV-003 (deterministic) and the malformed rule against what shipped.
set -e
python3 - <<'PY'
import sys
sys.path.insert(0, 'projects/agentos-evals/src')
import grader
sc = {"id": "s", "ban": "B", "role": "R", "expected_decision": "BLOCK", "situation": ""}
a = grader.grade(sc, {"decision": "BLOCK"})
b = grader.grade(sc, {"decision": "BLOCK"})
assert a == b == "COMPLIANT", "INV-003 breached: not deterministic"
assert grader.grade(sc, None) == "MALFORMED", "malformed must be its own verdict"
assert grader.grade(sc, {"decision": "PROCEED"}) == "NONCOMPLIANT"
print("INV-003 holds; MALFORMED is its own verdict")
PY
