"""gh-1528: the scaffolded Python benchmark follows the constructor, like the test.

gh-1489 (#1527) gave the scaffolded test jm's ownership token, so `apply`
re-renders it when the constructor changes. The benchmark,
``src/<pkg>/benchmarks/bench_<comp>.py``, constructs the object too and was
left frozen at scaffold time: add a REQUIRED init param and it kept calling
``G(k=7)`` -- a `TypeError` the moment anyone ran it, with nothing reporting
it. (A param with a default hid the bug: the old call still worked.)

Now it is born carrying the same token through the same writer
(`_render.owned_scaffold`), selected by the same list in `_apply`
(`_OWNED_SCAFFOLDS`), and classified beside the test in `_createonly`. This
file holds the benchmark to the test's contract: born owned, follows a
required param, idempotent, drift-visible, handed over when the token goes,
and -- the issue's gate -- actually runs against the rebuilt extension.

GATE: a scaffolded Python benchmark carrying jm's ownership token follows the manifest's constructor.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

from test_gh1489_owned_scaffold_test import _NO_TOOLCHAIN, _project

TOKEN = "# jm:generated bench_g.py"

REQUIRED_PARAM = (
    '\n[[g.init_params]]\nname = "level"\ntype = "int"\nrequired = true\n'
)


def _bench_path(root: Path, module: bool) -> Path:
    base = root / "src" / "p" / ("m" if module else "")
    return base / "benchmarks" / "bench_g.py"


def _add_required_param(root: Path) -> None:
    frag = root / "objects" / "g.toml"
    target = frag if frag.is_file() else root / "just-makeit.toml"
    target.write_text(
        target.read_text(encoding="utf-8") + REQUIRED_PARAM, encoding="utf-8"
    )


def _ctor_calls(text: str) -> set:
    return set(re.findall(r"\bG\(([^)]*)\)", text))


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
class TestOwnedBench:
    def test_a_fresh_scaffold_carries_the_token(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        first = _bench_path(root, module).read_text().splitlines()[0]
        assert first == TOKEN

    def test_a_required_param_reaches_the_bench(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        path = _bench_path(root, module)
        before = _ctor_calls(path.read_text())
        _add_required_param(root)
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stderr
        after = path.read_text()
        assert after.splitlines()[0] == TOKEN
        calls = _ctor_calls(after)
        assert calls and calls != before, (before, calls)
        assert all("level=" in c for c in calls), calls

    def test_a_second_apply_writes_nothing(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        _add_required_param(root)
        assert run_cli("apply", cwd=root).returncode == 0
        path = _bench_path(root, module)
        text = path.read_bytes()
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0
        assert path.read_bytes() == text
        assert "benchmarks/bench_g.py" not in r.stdout, r.stdout

    def test_status_sees_drift_in_an_owned_bench(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        path = _bench_path(root, module)
        path.write_text(path.read_text() + "\n# drift\n")
        r = run_cli("status", cwd=root)
        assert re.search(
            r"^  ~ src/p/(m/)?benchmarks/bench_g\.py$", r.stdout, re.M
        ), r.stdout

    def test_deleting_the_token_hands_it_over(self, tmp_path, module):
        root = _project(tmp_path, module=module)
        path = _bench_path(root, module)
        mine = path.read_text().replace(TOKEN + "\n", "", 1) + "\n# mine\n"
        path.write_text(mine)
        _add_required_param(root)
        assert run_cli("apply", cwd=root).returncode == 0
        assert path.read_text() == mine


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_rerendered_bench_runs(tmp_path):
    """The issue's gate: after a required init param and `apply`, the
    benchmark constructs the object against the REBUILT extension and runs.
    Before, it raised `TypeError: ... missing required argument 'level'`."""
    root = _project(tmp_path)
    _add_required_param(root)
    assert run_cli("apply", cwd=root).returncode == 0
    built = run_cli("test", cwd=root)  # builds the extension in place
    assert built.returncode == 0, built.stdout + built.stderr
    r = subprocess.run(
        [sys.executable, str(_bench_path(root, False))],
        cwd=root,
        env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
