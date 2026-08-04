"""gh-745: the clang-format binary is the project's to pin.

``c_style = "clang-format"`` is correct and correctly scoped, but ``_cfmt``
resolved the formatter with a bare ``shutil.which("clang-format")`` — so the
committed bytes depended on whichever version the machine happened to have.
doppler measured 21.1.8 locally against 22.1.8 in CI: identical input,
different output, and the drift gate flips red across machines on a project
nobody touched. That is not a formatting nit; it is what kept ``c_style`` from
being safely enable-able at all.

The fix is an argv list the project supplies, so the invocation can route
through whatever already pins the version (``uv run``, a pre-commit mirror, an
absolute path).

Two things below are worth more than the happy path:

* the command reaches the **temp scaffold** ``apply`` compares against, not
  just the real tree. Formatting one side with a different binary than the
  other is gh-635 rebuilt from parts;
* an argv list is required rather than a shell string, because splitting a
  string means guessing about quoting and the first thing anyone puts here is
  a path.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cfmt  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_HAS_CLANG_FORMAT = shutil.which("clang-format") is not None


class TestConfigAccessor:
    """The manifest key, including what it refuses."""

    def test_unset_reproduces_the_pre_gh745_invocation(self):
        assert C.c_format_command({}) == ["clang-format"]
        assert C.c_format_command({"project": {}}) == ["clang-format"]

    def test_a_declared_command_is_returned_verbatim(self):
        cfg = {
            "project": {
                "c_format_command": ["uv", "run", "--group", "dev", "cf"]
            }
        }
        assert C.c_format_command(cfg) == ["uv", "run", "--group", "dev", "cf"]

    def test_a_shell_string_is_rejected_rather_than_split(self):
        """Splitting would have to guess about quoting; jm will not.

        The first thing anyone puts here is a path, and a path may contain
        spaces — so a string that "works" for one project silently mangles
        the next one's.
        """
        cfg = {"project": {"c_format_command": "uv run clang-format"}}
        with pytest.raises(
            ValueError, match="list of arguments, not a string"
        ):
            C.c_format_command(cfg)

    def test_an_empty_list_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty list"):
            C.c_format_command({"project": {"c_format_command": []}})

    def test_a_non_string_argument_is_rejected(self):
        with pytest.raises(ValueError, match="only strings"):
            C.c_format_command({"project": {"c_format_command": ["cf", 3]}})

    def test_the_default_constant_is_the_historical_behaviour(self):
        assert C.DEFAULT_C_FORMAT_COMMAND == ["clang-format"]


class TestInvocation:
    """What actually gets executed, without needing a real formatter."""

    @staticmethod
    def _project(tmp_path, command=None):
        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        object_run(
            root, "widget", None, state_vars=[("gain", "double", "1.0")]
        )
        if command is not None:
            cfg = C.load(root)
            cfg["project"]["c_format_command"] = command
            C.save(root, cfg)
        return root

    def test_the_declared_command_is_the_argv_prefix(
        self, tmp_path, monkeypatch, capsys
    ):
        root = self._project(tmp_path)
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv

            class R:
                returncode = 0
                stderr = ""

            return R()

        monkeypatch.setattr(_cfmt.subprocess, "run", fake_run)
        monkeypatch.setattr(_cfmt.shutil, "which", lambda n: "/usr/bin/" + n)
        cfg = {
            "project": {
                "c_style": "clang-format",
                "c_format_command": ["uv", "run", "clang-format"],
            }
        }
        _cfmt.format_project(root, cfg, quiet=True)
        argv = seen["argv"]
        assert argv[:3] == ["uv", "run", "clang-format"]
        # jm still owns the flags: the committed .clang-format decides layout,
        # this key decides only which executable reads it.
        assert argv[3:6] == ["-i", "--style=file", "--fallback-style=LLVM"]
        assert all(a.endswith("_ext.c") for a in argv[6:])
        assert argv[6:], "no files passed to the formatter"

    def test_only_argv0_is_resolved_on_path(
        self, tmp_path, monkeypatch, capsys
    ):
        """`uv run clang-format` must resolve `uv`, not `clang-format`.

        In the setup this key exists to support, `clang-format` is very
        likely *not* on PATH at all — it lives in the pinned environment the
        runner activates. Resolving the wrong argument would reject exactly
        the configuration the issue asks for.
        """
        root = self._project(tmp_path)
        # `new_run(c_style=...)` formats its own scaffold, so on a machine
        # without clang-format it has already written a warning to stderr.
        # Drain it, or this asserts on someone else's output — which is what
        # made this pass on Linux (clang-format present, nothing emitted) and
        # fail on macOS.
        capsys.readouterr()
        looked_up = []

        def fake_which(name):
            looked_up.append(name)
            return "/usr/bin/uv" if name == "uv" else None

        monkeypatch.setattr(_cfmt.shutil, "which", fake_which)
        monkeypatch.setattr(
            _cfmt.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})(),
        )
        cfg = {
            "project": {
                "c_style": "clang-format",
                "c_format_command": ["uv", "run", "clang-format"],
            }
        }
        _cfmt.format_project(root, cfg, quiet=True)
        assert looked_up == ["uv"]
        assert capsys.readouterr().err == ""

    def test_a_missing_command_is_a_soft_failure_naming_it(
        self, tmp_path, monkeypatch, capsys
    ):
        root = self._project(tmp_path)
        monkeypatch.setattr(_cfmt.shutil, "which", lambda n: None)
        cfg = {
            "project": {
                "c_style": "clang-format",
                "c_format_command": ["my-pinned-cf"],
            }
        }
        _cfmt.format_project(root, cfg, quiet=True)  # must not raise
        err = capsys.readouterr().err
        assert "my-pinned-cf" in err, "the warning must name what was missing"
        assert "WARNING" in err

    def test_c_style_unset_never_runs_anything(self, tmp_path, monkeypatch):
        root = self._project(tmp_path)
        monkeypatch.setattr(
            _cfmt.subprocess,
            "run",
            lambda *a, **k: pytest.fail("formatter ran with c_style unset"),
        )
        _cfmt.format_project(
            root, {"project": {"c_format_command": ["cf"]}}, quiet=True
        )


class TestManifestRoundTrip:
    """jm never writes this key, so it must survive everything that does.

    The manifest has three writers (`jm <cmd>`, `jm apply`, `jm script`). A
    hand-added `[project]` key that any of them drops is worse than one that
    was never supported: the project reads as configured and behaves as if it
    is not.
    """

    def test_apply_preserves_the_key(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        object_run(
            root, "widget", None, state_vars=[("gain", "double", "1.0")]
        )
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = ["uv", "run", "clang-format"]
        C.save(root, cfg)

        apply_run(root)

        assert C.c_format_command(C.load(root)) == [
            "uv",
            "run",
            "clang-format",
        ]

    def test_a_later_object_command_preserves_the_key(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = ["pinned-cf"]
        C.save(root, cfg)

        object_run(root, "widget", None, state_vars=[("g", "double", "1.0")])

        assert C.c_format_command(C.load(root)) == ["pinned-cf"]


@pytest.mark.skipif(not _HAS_CLANG_FORMAT, reason="clang-format not installed")
class TestAgainstTheRealFormatter:
    """End-to-end, with the binary actually present."""

    def test_apply_converges_and_status_is_clean(self, tmp_path):
        """The gh-635 property still holds with a pinned command declared.

        Note this does *not* prove the command reached the temp scaffold —
        both sides use the same binary here, so it would pass either way.
        `TestTempScaffoldGetsTheSameCommand` is what proves that.
        """
        from just_makeit import _status

        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        object_run(
            root, "widget", None, state_vars=[("gain", "double", "1.0")]
        )
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = [shutil.which("clang-format")]
        C.save(root, cfg)

        apply_run(root)
        assert _status.run(root, check=True) == 0

    def test_format_version_reports_the_binary(self, tmp_path):
        cfg = {
            "project": {
                "c_style": "clang-format",
                "c_format_command": ["clang-format"],
            }
        }
        assert "clang-format" in _cfmt.format_version(cfg).lower()

    def test_format_version_is_empty_without_c_style(self):
        assert _cfmt.format_version({"project": {}}) == ""

    def test_format_version_never_raises_on_a_missing_binary(self):
        cfg = {
            "project": {
                "c_style": "clang-format",
                "c_format_command": ["definitely-not-a-real-binary-xyz"],
            }
        }
        assert _cfmt.format_version(cfg) == ""


class TestTempScaffoldGetsTheSameCommand:
    """`apply` compares against a throwaway scaffold it formats itself.

    Formatting the real tree with the pinned command and the comparison tree
    with a PATH-resolved one would reproduce gh-635 exactly: two sides styled
    by different binaries, drift that no number of `apply` runs can clear.
    The plumbing is that `_apply` hands `format_project` the *real* cfg, and
    this is the test that says so.
    """

    def test_format_project_receives_the_pinned_command(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        object_run(
            root, "widget", None, state_vars=[("gain", "double", "1.0")]
        )
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = ["pinned-cf", "--flag"]
        C.save(root, cfg)

        seen = []
        real = _cfmt.format_project

        def spy(target, cfg_arg, **kw):
            seen.append((Path(target), C.c_format_command(cfg_arg)))
            return real(target, cfg_arg, **kw)

        monkeypatch.setattr(_cfmt, "format_project", spy)
        monkeypatch.setattr(_cfmt.shutil, "which", lambda n: None)  # soft-fail
        apply_run(root)

        assert seen, "apply never invoked the formatter hook"
        targets, commands = zip(*seen)
        # Every invocation — the temp scaffold included — sees the pinned
        # command, not the ["clang-format"] default.
        assert all(c == ["pinned-cf", "--flag"] for c in commands), commands
        # And at least one of them is the throwaway tree, not the real root.
        assert any(t != root for t in targets), targets
