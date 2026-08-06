"""gh-772 — the CWD question is asked of both formatter commands.

gh-758 added the detector for `c_format_command`: jm formats its temp scaffold
from *outside* the project and the real tree from *inside* it, so a command
that resolves differently depending on where it runs formats the two compared
sides with two different tools, and no `jm apply` clears the drift.

`py_format_command` has the identical exposure and had no detection at all.

**And a straight generalisation would still have missed doppler's case.** The
two commands do not fail the same way:

* `uv run --group dev clang-format` outside a project → `uv` warns that
  `--group dev has no effect` and falls through to PATH. Two binaries, two
  versions, both of which answer `--version`.
* `uv run --group dev ruff format` outside a project → `Failed to spawn:
  ruff`. Nothing runs at all.

gh-758's check returned "no problem" for the second, because it treated an
unanswerable `--version` as "cannot tell". Measured against a command
contrived to fail exactly that way, before the fix::

    from project   : 'fakefmt 1.2.3'
    from elsewhere : ''
    detector says  : None          <-- silent, and this is doppler's case

For a command that answers perfectly well *inside* the project, that is not an
unknown — it is the finding. So the probe reports it as its own kind, with its
own prose: "your formatter did not run" is a different problem from "your
formatter ran twice as two different tools", even though the cause is one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cfmt  # noqa: E402
from just_makeit import _fmtprobe  # noqa: E402
from just_makeit import _pyfmt  # noqa: E402

# Succeeds only when run from a directory holding `marker.txt` — the shape of
# `uv run --group dev ruff format`, which fails to spawn outside a project.
_SPAWN_FAILS_ELSEWHERE = """\
#!/bin/sh
[ -f ./marker.txt ] || { echo "Failed to spawn: fakefmt" >&2; exit 1; }
echo "fakefmt 1.2.3"
"""

# Answers everywhere, but with a different version outside the project — the
# clang-format shape gh-758 was built for.
_VERSION_DIFFERS = """\
#!/bin/sh
if [ -f ./marker.txt ]; then echo "fakefmt 22.1.8"; else echo "fakefmt 21.1.8"; fi
"""


@pytest.fixture
def fake_formatter(tmp_path, monkeypatch):
    """Install a stub formatter on PATH and return a factory for its body."""

    def _install(script: str) -> Path:
        root = tmp_path / "proj"
        (root / "bin").mkdir(parents=True, exist_ok=True)
        exe = root / "bin" / "fakefmt"
        exe.write_text(script)
        exe.chmod(0o755)
        (root / "marker.txt").write_text("")
        monkeypatch.setenv(
            "PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}"
        )
        return root

    return _install


class TestTheProbe:
    def test_a_spawn_failure_elsewhere_is_reported(self, fake_formatter):
        """The case gh-758 returned None for, and the whole point of gh-772."""
        root = fake_formatter(_SPAWN_FAILS_ELSEWHERE)
        dep = _fmtprobe.cwd_dependence(root, ["fakefmt"])
        assert dep is not None, (
            "a command that works in the project and not outside it is a "
            "finding, not an unknown"
        )
        assert dep.kind == "spawn"
        assert dep.from_project.startswith("fakefmt 1.2.3")
        assert dep.from_elsewhere == ""

    def test_a_version_difference_is_its_own_kind(self, fake_formatter):
        root = fake_formatter(_VERSION_DIFFERS)
        dep = _fmtprobe.cwd_dependence(root, ["fakefmt"])
        assert dep is not None and dep.kind == "version"
        assert "22.1.8" in dep.from_project
        assert "21.1.8" in dep.from_elsewhere

    def test_a_stable_command_is_silent(self, fake_formatter):
        root = fake_formatter('#!/bin/sh\necho "fakefmt 1.0.0"\n')
        assert _fmtprobe.cwd_dependence(root, ["fakefmt"]) is None

    def test_an_absent_command_is_a_genuine_unknown(self, tmp_path):
        """Not the same as CWD-dependent — a formatter that is simply not
        installed is reported by the version line, not escalated here."""
        assert (
            _fmtprobe.cwd_dependence(tmp_path, ["definitely-not-a-binary"])
            is None
        )
        assert _fmtprobe.cwd_dependence(tmp_path, []) is None


class TestTheTwoKindsReadDifferently:
    """ "Your formatter did not run" and "your formatter ran twice as two
    different tools" are different problems with the same cause, and the
    issue asks for them to say so."""

    def test_the_spawn_message_says_it_did_not_run(self, tmp_path):
        dep = _fmtprobe.CwdDependence("spawn", "fakefmt 1.2.3", "")
        text = _fmtprobe.describe(dep, "py_format_command", tmp_path)
        assert "py_format_command" in text
        assert "did not run" in text
        assert "resolves a different formatter" not in text

    def test_the_version_message_says_two_binaries(self, tmp_path):
        dep = _fmtprobe.CwdDependence("version", "fmt 22", "fmt 21")
        text = _fmtprobe.describe(dep, "c_format_command", tmp_path)
        assert "resolves a different formatter" in text
        assert "different binaries" in text
        assert "did not run" not in text

    def test_both_name_the_usual_cause(self, tmp_path):
        for kind in ("spawn", "version"):
            text = _fmtprobe.describe(
                _fmtprobe.CwdDependence(kind, "a", "b"), "k", tmp_path
            )
            assert "uv run --group" in text, (
                "the message must name the cause — gh-758 read as "
                "irreproducible for a day precisely because nothing did"
            )


class TestBothCommandsAreCovered:
    def test_the_python_side_now_has_a_probe(self, fake_formatter):
        root = fake_formatter(_SPAWN_FAILS_ELSEWHERE)
        cfg = {"project": {"py_format_command": ["fakefmt"]}}
        dep = _pyfmt.cwd_dependent(root, cfg)
        assert dep is not None and dep.kind == "spawn"

    def test_the_python_side_has_a_version_too(self, fake_formatter):
        root = fake_formatter(_VERSION_DIFFERS)
        cfg = {"project": {"py_format_command": ["fakefmt"]}}
        assert "22.1.8" in _pyfmt.format_version(cfg, cwd=root)

    def test_an_undeclared_python_command_is_silent(self, tmp_path):
        assert _pyfmt.cwd_dependent(tmp_path, {"project": {}}) is None
        assert _pyfmt.format_version({"project": {}}) == ""

    def test_the_c_wrapper_keeps_its_shape(self, fake_formatter):
        """`cwd_dependent_version` still returns the `(here, there)` pair its
        callers and gh-758's tests use."""
        root = fake_formatter(_VERSION_DIFFERS)
        cfg = {"project": {"c_format_command": ["fakefmt"]}}
        got = _cfmt.cwd_dependent_version(root, cfg)
        assert isinstance(got, tuple) and len(got) == 2
        assert "22.1.8" in got[0] and "21.1.8" in got[1]

    def test_the_c_wrapper_stays_version_only(self, fake_formatter):
        """Deliberate: it is the version-disagreement half, and the spawn
        kind reaches `jm status` through the shared renderer instead. Widening
        it would change what gh-758's callers get back."""
        root = fake_formatter(_SPAWN_FAILS_ELSEWHERE)
        cfg = {"project": {"c_format_command": ["fakefmt"]}}
        assert _cfmt.cwd_dependent_version(root, cfg) is None
        assert _fmtprobe.cwd_dependence(root, ["fakefmt"]).kind == "spawn", (
            "but the probe still sees it, which is what status reports"
        )
