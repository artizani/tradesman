#!/bin/sh
# Post-deploy validation: prove the critical invariant holds in what was deployed.
# This is not a smoke test -- it asserts INV-101 against the running artifact.
set -e
echo "verifying INV-101 (no double-booked slot) against the deployment..."
python3 - <<'PY'
import sys
sys.path.insert(0, 'examples/bookings/src')
import slots
assert slots.claim('S1', 'P1') is not None, "first claim must succeed"
assert slots.claim('S1', 'P2', held_by='P1') is None, "INV-101 breached: double booking"
print("INV-101 holds")
PY
echo "CJ-BOOK-001 postdeploy ok"
