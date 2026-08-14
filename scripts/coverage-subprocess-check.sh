#!/bin/sh
# Prove that a test driving the shipped CLI is counted as coverage (gh-978).
#
# It was not. `COVERAGE_PROCESS_START` instruments the `jm` subprocesses these
# tests spawn, but the data file is written to the subprocess's **cwd** — a
# scaffolded project under `tmp_path` — and pytest-cov combines only what sits
# beside its own. So the most faithful tests in the suite, the ones this repo's
# issue-filing doctrine requires, measured nothing and `codecov/patch` failed
# any PR whose new code they covered.
#
# This does not inspect the fix; it PERFORMS the situation the fix is for. It
# writes one test that runs the CLI **with a different cwd** — that difference
# is the whole bug — and asserts the report attributes lines to `_cli.py`.
# Point it at a tree without the absolute COVERAGE_FILE and it fails.
#
# Runs inside `coverage-gate`, where pytest-cov is already present. A pytest
# test could not host it: `make test` runs the suite without that plugin, so
# the check would skip, and a skip is not a pass.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/run" "$WORK/elsewhere"

cat > "$WORK/run/test_cli_probe.py" <<'PY'
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(os.environ["JM_SRC"])
ELSEWHERE = Path(os.environ["JM_ELSEWHERE"])


def test_the_cli_runs_from_another_directory():
    # cwd is the point: a real test scaffolds into tmp_path and runs `jm`
    # there, which is where the subprocess drops its coverage data.
    r = subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()",
         "--version"],
        cwd=ELSEWHERE,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
PY

# The environment is taken exactly as given — no defaults filled in here. That
# is deliberate and it is what makes this a control: supplying an absolute
# COVERAGE_FILE of its own would make the check pass on a tree that has never
# set one, which is the shape of a gate that can only fail by coincidence.
# Unset means unset, and coverage's own default is a relative `.coverage` in
# each process's cwd — precisely the bug.
cd "$WORK/run"
JM_SRC="$ROOT/src" JM_ELSEWHERE="$WORK/elsewhere" \
    python -m pytest test_cli_probe.py -q \
        --cov=just_makeit --cov-report="json:$WORK/cov.json" \
        -p no:cacheprovider >"$WORK/pytest.log" 2>&1 || {
    echo "ERROR: the probe test itself failed:"
    sed 's/^/  /' "$WORK/pytest.log"
    exit 1
}

# No report file at all is the strongest form of the finding, not an error to
# raise a traceback over: pytest-cov writes nothing when it has no data, and
# with the subprocess's measurements orphaned there is none — the probe's own
# process imports nothing from the package.
hits=$(python - "$WORK/cov.json" <<'PY'
import json
import os
import sys

if not os.path.exists(sys.argv[1]):
    print(0)
    raise SystemExit(0)
with open(sys.argv[1]) as fh:
    data = json.load(fh)
print(
    max(
        (f["summary"]["covered_lines"]
         for p, f in data["files"].items() if p.endswith("_cli.py")),
        default=0,
    )
)
PY
)

if [ "$hits" -eq 0 ]; then
    echo "ERROR: a test that drives the CLI contributed NO coverage (gh-978)."
    echo "  _cli.py: 0 covered lines, from a run whose only test invokes it."
    echo ""
    echo "  The subprocess writes its data to its own cwd. COVERAGE_FILE and"
    echo "  COVERAGE_PROCESS_START must both be ABSOLUTE — see COVERAGE_ENV in"
    echo "  the Makefile."
    exit 1
fi

echo "coverage-subprocess-check: CLI-driven tests are counted ($hits lines)"
