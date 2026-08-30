#!/bin/sh
# The grader is a library, so "deploy" means: it imports cleanly, standalone.
set -e
python3 -c "import sys; sys.path.insert(0,'projects/agentos-evals/src'); import grader; print('grader importable:', grader.grade.__name__)"
