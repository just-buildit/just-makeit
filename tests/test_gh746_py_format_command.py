"""gh-746: run the project's pinned Python formatter over generated stubs.

The Python twin of gh-745. A generated ``.pyi`` is drift-gated, so a project
that runs its own formatter over one is *creating* drift: it formats the file,
jm regenerates it unformatted, and no number of ``apply`` runs converges. That
is gh-635, one language over — which is why the formatter has to be jm's to
run, and why the project excluding stubs from its own ``ruff format`` is not a
workaround but the only currently-safe option.

The load-bearing part is not running the formatter. It is running it on
**both** the real tree and the throwaway scaffold ``apply`` compares against.
Format one side only and every project is permanently stale; there is a
sabotage-style test below that pins exactly that, because it is the failure
this design exists to avoid and it is invisible in a happy-path test.

A consequence worth stating: jm's own emission does **not** need to be a fixed
point of the formatter (it isn't — ruff applies PEP 484 stub style and drops
the blank line jm puts between two simple stub defs). The *formatted* output
is the fixed point, because formatters are idempotent.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _pyfmt  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_HAS_RUFF = shutil.which("ruff") is not None
_RUFF = ["ruff", "format", "--line-length", "79"]


class TestConfigAccessor:
    def test_unset_is_off(self):
        assert C.py_format_command({}) == []
        assert C.py_format_command({"project": {}}) == []

    def test_a_declared_command_is_returned_verbatim(self):
        cfg = {"project": {"py_format_command": ["uv", "run", "ruff", "fmt"]}}
        assert C.py_format_command(cfg) == ["uv", "run", "ruff", "fmt"]

    def test_a_shell_string_is_rejected_rather_than_split(self):
        cfg = {"project": {"py_format_command": "uv run ruff format"}}
        with pytest.raises(ValueError, match="not a string"):
            C.py_format_command(cfg)

    def test_an_empty_list_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty list"):
            C.py_format_command({"project": {"py_format_command": []}})

    def test_a_non_string_argument_is_rejected(self):
        with pytest.raises(ValueError, match="only strings"):
            C.py_format_command({"project": {"py_format_command": ["x", 1]}})


def _project(tmp_path, command=None):
    root = tmp_path / "proj"
    new_run("proj", root)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "double", "1.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    if command is not None:
        cfg = C.load(root)
        cfg["project"]["py_format_command"] = command
        C.save(root, cfg)
    return root


class TestScope:
    """What may be reformatted, and what must not be."""

    def test_only_pyi_stubs_are_collected(self, tmp_path):
        root = _project(tmp_path)
        files = _pyfmt.generated_py_files(root)
        assert files, "no stubs found"
        assert all(f.suffix == ".pyi" for f in files), files

    def test_a_package_init_is_never_reformatted(self, tmp_path):
        """`apply` *merges* those, so they carry hand-written Python.

        gh-746 asks for the re-export shims, but a merged file is hybrid —
        reformatting one rewrites the author's own code, which the issue's
        own constraint forbids. Same reasoning keeps `_cfmt` off
        `native/inc/**`.
        """
        root = _project(tmp_path)
        inits = list((root / "src").rglob("__init__.py"))
        assert inits, "fixture has no package __init__.py to check"
        collected = set(_pyfmt.generated_py_files(root))
        assert not collected & set(inits)

    def test_no_src_tree_yields_nothing(self, tmp_path):
        assert _pyfmt.generated_py_files(tmp_path) == []


class TestInvocation:
    def test_the_declared_command_is_the_argv_prefix(
        self, tmp_path, monkeypatch, capsys
    ):
        root = _project(tmp_path)
        capsys.readouterr()
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return type("R", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(_pyfmt.subprocess, "run", fake_run)
        monkeypatch.setattr(_pyfmt.shutil, "which", lambda n: "/usr/bin/" + n)
        cfg = {"project": {"py_format_command": ["uv", "run", "ruff", "fmt"]}}
        _pyfmt.format_project(root, cfg, quiet=True)
        argv = seen["argv"]
        assert argv[:4] == ["uv", "run", "ruff", "fmt"]
        assert argv[4:], "no files passed to the formatter"
        assert all(a.endswith(".pyi") for a in argv[4:])

    def test_only_argv0_is_resolved_on_path(self, tmp_path, monkeypatch):
        """`uv run ruff format` resolves `uv`; `ruff` may not be on PATH."""
        root = _project(tmp_path)
        looked = []

        def fake_which(name):
            looked.append(name)
            return "/usr/bin/uv" if name == "uv" else None

        monkeypatch.setattr(_pyfmt.shutil, "which", fake_which)
        monkeypatch.setattr(
            _pyfmt.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})(),
        )
        cfg = {"project": {"py_format_command": ["uv", "run", "ruff"]}}
        _pyfmt.format_project(root, cfg, quiet=True)
        assert looked == ["uv"]

    def test_a_missing_command_is_a_soft_failure_naming_it(
        self, tmp_path, monkeypatch, capsys
    ):
        root = _project(tmp_path)
        capsys.readouterr()
        monkeypatch.setattr(_pyfmt.shutil, "which", lambda n: None)
        cfg = {"project": {"py_format_command": ["my-pinned-fmt"]}}
        _pyfmt.format_project(root, cfg, quiet=True)  # must not raise
        err = capsys.readouterr().err
        assert "my-pinned-fmt" in err
        assert "WARNING" in err

    def test_unset_never_runs_anything(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        monkeypatch.setattr(
            _pyfmt.subprocess,
            "run",
            lambda *a, **k: pytest.fail("formatter ran while unset"),
        )
        _pyfmt.format_project(root, {"project": {}}, quiet=True)


class TestBothTreesAreFormatted:
    """The failure this design exists to avoid, pinned directly.

    Formatting the real tree but not the throwaway scaffold `apply` compares
    against makes every project permanently stale — `apply` writes formatted
    bytes, `status` regenerates unformatted ones, and no run converges. It is
    gh-635 one language over, and a happy-path test cannot see it.
    """

    def test_format_project_receives_the_declared_command(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, ["pinned-fmt"])
        seen = []
        real = _pyfmt.format_project

        def spy(target, cfg_arg, **kw):
            seen.append((Path(target), C.py_format_command(cfg_arg)))
            return real(target, cfg_arg, **kw)

        monkeypatch.setattr(_pyfmt, "format_project", spy)
        monkeypatch.setattr(_pyfmt.shutil, "which", lambda n: None)
        apply_run(root)

        assert seen, "apply never invoked the Python formatter hook"
        # `_replay` rebuilds the scaffold through `_new.run`, whose own hook
        # fires with a freshly-built cfg that carries no command yet — a
        # harmless no-op, but it means "every call used the command" is the
        # wrong assertion. What must hold is narrower and is the actual
        # invariant: the throwaway tree is formatted, with the declared
        # command, and it is not the real tree.
        armed = [(t, c) for t, c in seen if c == ["pinned-fmt"]]
        assert armed, f"the declared command never reached a tree: {seen}"
        assert any(t != root for t, _c in armed), (
            "the throwaway scaffold was never formatted with the declared "
            "command — status will report drift no apply can clear"
        )


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff not on PATH")
class TestAcceptance:
    """The issue's three criteria, against the real formatter."""

    def test_the_stub_passes_the_projects_own_formatter_unmodified(
        self, tmp_path
    ):
        import subprocess

        root = _project(tmp_path, _RUFF)
        apply_run(root)
        for pyi in _pyfmt.generated_py_files(root):
            proc = subprocess.run(
                ["ruff", "format", "--check", "--line-length", "79", str(pyi)],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, f"{pyi.name}: {proc.stdout}"

    def test_status_check_is_clean_and_stays_clean(self, tmp_path, capsys):
        root = _project(tmp_path, _RUFF)
        apply_run(root)
        capsys.readouterr()
        assert _status.run(root, check=True) == 0
        apply_run(root)  # convergent: a second run changes nothing
        capsys.readouterr()
        assert _status.run(root, check=True) == 0

    def test_the_stub_is_still_valid_python_with_its_doctests(self, tmp_path):
        """A formatter must not break the stub-doctest gate."""
        import ast

        root = _project(tmp_path, _RUFF)
        apply_run(root)
        for pyi in _pyfmt.generated_py_files(root):
            text = pyi.read_text(encoding="utf-8")
            ast.parse(text)
            if ">>>" in text:
                assert ">>> " in text, "doctest prompts must survive intact"
