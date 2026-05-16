#!/usr/bin/env bash
# Run the just-makeit quickstart — assumes just-makeit is already on PATH.
#
#   bash <(curl -fsSL https://just-buildit.github.io/just-makeit/quickstart.sh)
#
set -euo pipefail

just-makeit new my_project --object engine --state gain:double:1.0
cd my_project
make
make test
