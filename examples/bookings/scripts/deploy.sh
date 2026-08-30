#!/bin/sh
# Stand-in for a real deployment. Replace with your own -- what matters is that
# it exits non-zero when the deploy failed, because AgentOS records the exit
# code and a PASS verdict may not sit on a non-zero exit.
set -e
echo "packaging bookings..."
python3 -c "import sys; sys.path.insert(0,'examples/bookings/src'); import slots; print('module loads:', slots.__name__)"
echo "deployed to: local"
