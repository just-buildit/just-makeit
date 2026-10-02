"""gh-1687: one slow benchmark must cost that benchmark, not the run.

Reported on an Arduino UNO Q (Cortex-A53-class cores): `jm bench` passed a
hard-coded ``timeout=600`` to every subprocess, a benchmark that took longer
raised ``TimeoutExpired`` out of `_collect_c`, and the traceback took every
result already collected down with it -- ``benchmarks/history/`` came out
empty. The same binary takes 62 s on a desktop core, so no fixed budget sized
there holds on the hardware an embedded-capable library targets.

Three properties, each asserted where it is decided:

* the budget has ONE reader, `C.bench_timeout`, fed by ``[project.bench]
  timeout`` and overridden by ``jm bench --timeout``; ``0`` is no limit;
* past it, `_collect_c` skips that binary, names it, and keeps the rest --
  and `run` still saves the rest, then exits 1;
* the builds `jm bench` drives carry no budget at all (a cold build on that
  board took 603 s; slow is not failed).

The binaries here are shell scripts standing in for ``bench_<comp>_core``:
what is under test is what jm does around a run that is slow, and a script
that sleeps is a faithful slow run without a twenty-second C build per case.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _bench as B  # noqa: E402
from just_makeit import _cli  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake bench binaries are sh scripts"
)

#: A fast benchmark: writes the JSON `jm_bench_write_json` would, one entry.
_FAST = """#!/bin/sh
cat > {stem}.json <<'EOF'
{{"benchmarks": [{{"name": "step", "stats": {{"min": 1e-6, "max": 2e-6,
 "mean": 1.5e-6, "stddev": 1e-7, "median": 1.5e-6, "ops": 6.6e5}}}}]}}
EOF
"""

#: A slow one: sleeps far past any budget the tests set, then would write.
_SLOW = """#!/bin/sh
sleep 30
echo '{{"benchmarks": []}}' > {stem}.json
"""


def _fake_binaries(tmp_path: Path, monkeypatch, scripts: dict) -> Path:
    """Stand a script in for each ``bench_<comp>_core`` and skip the builds."""
    bdir = tmp_path / "bins"
    bdir.mkdir()
    for comp, body in scripts.items():
        stem = f"bench_{comp}_core"
        exe = bdir / stem
        exe.write_text(body.format(stem=stem), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(B, "_build_bench_target", lambda *a, **k: True)
    monkeypatch.setattr(
        B, "_find_bench_binary", lambda _bd, comp: bdir / f"bench_{comp}_core"
    )
    return bdir


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A manifest naming three components, the middle one slow."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / C.FILENAME).write_text(
        '[project]\nname = "proj"\nversion = "0.1.0"\n\n'
        '[fast]\narg_type = "float"\n\n'
        '[slow]\narg_type = "float"\n\n'
        '[zfast]\narg_type = "float"\n',
        encoding="utf-8",
    )
    _fake_binaries(
        tmp_path,
        monkeypatch,
        {"fast": _FAST, "slow": _SLOW, "zfast": _FAST},
    )
    monkeypatch.setattr(B, "_ensure_built", lambda *a, **k: None)
    monkeypatch.setattr(B, "_project_python", lambda _r: sys.executable)
    monkeypatch.setattr(B, "_commit_info", lambda: {})
    return root


class TestTheBudgetHasOneReader:
    def test_unset_keeps_the_historical_600(self):
        assert C.bench_timeout({}) == 600.0

    def test_the_manifest_key_sets_it(self):
        cfg = {"project": {"bench": {"timeout": 3600}}}
        assert C.bench_timeout(cfg) == 3600.0

    def test_the_flag_overrides_the_manifest(self):
        cfg = {"project": {"bench": {"timeout": 3600}}}
        assert C.bench_timeout(cfg, 5.0) == 5.0

    @pytest.mark.parametrize("zero", [0, 0.0, "0"])
    def test_zero_is_no_limit_in_the_manifest(self, zero):
        assert (
            C.bench_timeout({"project": {"bench": {"timeout": zero}}}) is None
        )

    def test_zero_is_no_limit_on_the_flag(self):
        cfg = {"project": {"bench": {"timeout": 3600}}}
        assert C.bench_timeout(cfg, 0.0) is None

    @pytest.mark.parametrize("bad", [-1, "soon", True, float("nan")])
    def test_a_malformed_budget_is_refused_not_switched_off(self, bad):
        """A typo must not silently mean "no limit" -- or "1 second", which
        is what a TOML `true` would read as through `float`."""
        with pytest.raises(Refusal, match=r"\[project\.bench\] timeout"):
            C.bench_timeout({"project": {"bench": {"timeout": bad}}})

    def test_a_negative_flag_names_the_flag(self):
        with pytest.raises(Refusal, match="--timeout"):
            C.bench_timeout({}, -5.0)


class TestATimeoutCostsOneBenchmark:
    def test_collect_keeps_the_others_and_names_the_slow_one(
        self, project, capsys
    ):
        timed_out: list[str] = []
        report = B._collect_c(
            project,
            project / "build",
            ["fast", "slow", "zfast"],
            timeout=1.0,
            timed_out=timed_out,
        )
        names = [b["name"] for b in report["benchmarks"]]
        # The component AFTER the slow one ran too: the loop carried on.
        assert names == ["fast::step", "zfast::step"]
        assert timed_out == ["bench_slow_core"]
        assert (
            "timeout    bench_slow_core (over 1 s" in capsys.readouterr().err
        )

    def test_run_saves_the_rest_then_fails_loudly(self, project, capsys):
        with pytest.raises(SystemExit) as exc:
            B.run(project, do_python=False, tag="T", timeout=1.0)
        assert exc.value.code == 1
        snap = project / "benchmarks" / "history" / "T-c.json"
        assert snap.is_file(), "the finished benchmarks were not saved"
        data = json.loads(snap.read_text(encoding="utf-8"))
        assert [b["name"] for b in data["benchmarks"]] == [
            "fast::step",
            "zfast::step",
        ]
        # The committed snapshot says itself that it is partial.
        assert data["timed_out"] == ["bench_slow_core"]
        err = capsys.readouterr().err
        assert "TIMEOUT" in err and "bench_slow_core" in err
        assert "--timeout" in err and "[project.bench] timeout" in err

    def test_the_manifest_budget_reaches_the_run(self, project, capsys):
        """`run` reads the key itself; the flag is only an override."""
        text = (project / C.FILENAME).read_text(encoding="utf-8")
        (project / C.FILENAME).write_text(
            text.replace(
                'version = "0.1.0"\n',
                'version = "0.1.0"\n\n[project.bench]\ntimeout = 1\n',
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            B.run(project, do_python=False, tag="T")
        assert exc.value.code == 1
        assert "over 1 s" in capsys.readouterr().err

    def test_check_mode_fails_on_a_timeout_too(self, project, capsys):
        hist = project / "benchmarks" / "history"
        hist.mkdir(parents=True)
        base = {
            "benchmarks": [
                {"name": f"{c}::step", "stats": {"min": 1e-6, "mean": 1e-6}}
                for c in ("fast", "slow", "zfast")
            ]
        }
        (hist / "A-c.json").write_text(json.dumps(base), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            B.run(
                project, do_python=False, check=True, timeout=1.0, as_json=True
            )
        assert exc.value.code == 1
        # The progress lines (`  run  bench_...`) share stdout with the JSON
        # document today; read the document from where it starts.
        stdout = capsys.readouterr().out
        out = json.loads(stdout[stdout.index("\n{") + 1 :])
        assert out["timed_out"] == ["bench_slow_core"]
        missing = [
            r["name"] for r in out["results"] if r["status"] == "missing"
        ]
        assert missing == ["slow::step"]


class TestTheBuildsAreNotTimed:
    def test_configure_and_build_carry_no_budget(self, tmp_path, monkeypatch):
        """A cold build took 603 s on the reporting board: slow, not broken."""
        calls: list[dict] = []

        class _Done:
            returncode = 0

        def _record(cmd, **kw):
            calls.append(kw)
            return _Done()

        monkeypatch.setattr(B, "_require", lambda exe: exe)
        monkeypatch.setattr(B.subprocess, "run", _record)
        B._ensure_built(tmp_path, tmp_path / "build", sys.executable)
        B._build_bench_target(tmp_path, tmp_path / "build", "x")
        assert len(calls) == 3
        assert all("timeout" not in kw for kw in calls), calls


class TestTheFlag:
    def _captured(self, monkeypatch, *argv):
        seen: dict = {}
        monkeypatch.setattr(B, "run", lambda *a, **k: seen.update(k))
        monkeypatch.setattr(sys, "argv", ["just-makeit", "bench", *argv])
        _cli.main()
        return seen

    def test_timeout_reaches_run_as_a_number(self, monkeypatch):
        assert (
            self._captured(monkeypatch, "--timeout", "90")["timeout"] == 90.0
        )

    def test_no_flag_leaves_it_to_the_manifest(self, monkeypatch):
        assert self._captured(monkeypatch)["timeout"] is None

    def test_a_non_number_is_refused(self, monkeypatch, capsys):
        monkeypatch.setattr(B, "run", lambda *a, **k: None)
        monkeypatch.setattr(
            sys, "argv", ["just-makeit", "bench", "--timeout", "soon"]
        )
        with pytest.raises(SystemExit) as exc:
            _cli.main()
        assert exc.value.code == 1
        assert "--timeout requires a number" in capsys.readouterr().err


def test_fixture_scripts_are_executable(tmp_path, monkeypatch):
    """Guards the harness: a script that cannot run would fake a timeout."""
    bdir = _fake_binaries(tmp_path, monkeypatch, {"a": _FAST})
    assert os.access(bdir / "bench_a_core", os.X_OK)
    with contextlib.redirect_stdout(io.StringIO()):
        report = B._collect_c(tmp_path, tmp_path, ["a"], timeout=10.0)
    assert [b["name"] for b in report["benchmarks"]] == ["a::step"]
