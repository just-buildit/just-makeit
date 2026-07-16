"""Integration tests for `just-makeit error` (gh-482).

`create()` returning NULL is the only failure channel C has, so it carries
every reason a component can refuse to construct. jm reported all of them as
`MemoryError` — false for anything but an allocation failure, and uncatchable
the way a caller would reach for it (`except ValueError`).

The load-bearing test is `TestCreateErrorEndToEnd`: a component whose
`create()` genuinely refuses must raise the declared exception in Python. Every
other test here is a detail by comparison.
"""

import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._apply import run as apply_run
from just_makeit._error import run as error_run
from just_makeit._property import run as property_run
from just_makeit._script import run as script_run
from just_makeit._config import load, create_error, create_error_message

_MSG = "invalid acquisition parameters: pd unreachable at this reps/cn0_dbhz"


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["acq"], [("reps", "int", "1")])
    return dest


def _ext_c(project, obj="acq"):
    return (project / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


def _init_body(project, obj="acq", Obj="Acq"):
    """Just the tp_init function.

    Scoped deliberately: `if (!self->handle)` and `PyExc_RuntimeError` also
    appear in every accessor's destroyed-guard, so a whole-file assertion
    about the create-failure block would be measuring unrelated code.
    """
    ext = _ext_c(project, obj)
    start = ext.index(f"{Obj}_init({Obj}Object *self")
    return ext[start : ext.index("\n}\n", start)]


class _CliResult:
    def __init__(self, returncode, out, err):
        self.returncode = returncode
        self.stdout = out
        self.stderr = err


def _cli(*args, cwd=None, capsys=None, monkeypatch=None) -> _CliResult:
    """Drive the real argv parser in-process (see test_warnings for why)."""
    from just_makeit._cli import main

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["just-makeit", *args])
    code = 0
    try:
        main()
    except SystemExit as e:
        code = e.code or 0
    out, err = capsys.readouterr()
    return _CliResult(code, out, err)


class TestCreateErrorCodegen:
    def test_replaces_the_memoryerror_block(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        body = _init_body(project)
        assert "PyErr_SetString(PyExc_ValueError," in body
        # The whole point: the MemoryError lie is gone, not merely joined.
        assert "MemoryError" not in body
        assert "acq_create returned NULL" not in body

    def test_message_survives_intact(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        body = _init_body(project)
        block = body[body.index("if (!self->handle)") :]
        block = block[: block.index("return -1;")]
        assert "".join(re.findall(r'"([^"]*)"', block)) == _MSG

    def test_still_guarded_by_a_null_check(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        assert "if (!self->handle) {" in _init_body(project)

    def test_redeclaring_replaces_rather_than_stacks(self, project):
        error_run(project, "acq", "ValueError", "first")
        error_run(project, "acq", "RuntimeError", "second")
        body = _init_body(project)
        assert body.count("if (!self->handle) {") == 1
        assert "PyExc_RuntimeError" in body
        assert "PyExc_ValueError" not in body
        assert "first" not in body
        assert create_error(load(project), "acq") == "RuntimeError"

    def test_emitted_block_stays_within_79_cols(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        block = _init_body(project)
        for ln in block.splitlines():
            assert len(ln) <= 79, f"{len(ln)} cols: {ln!r}"

    def test_no_stray_placeholders(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".c", ".h", ".py", ".pyi"):
                assert not _STRAY_PLACEHOLDER.search(
                    path.read_text(encoding="utf-8")
                ), path


class TestCreateErrorZeroChurn:
    """An undeclared component must be untouched by gh-482."""

    def test_undeclared_keeps_the_memoryerror_block(self, project):
        body = _init_body(project)
        assert "PyErr_SetString(PyExc_MemoryError," in body
        assert '"acq_create returned NULL");' in body

    def test_undeclared_block_is_byte_identical_to_the_old_template(
        self, project
    ):
        # Pins the equivalence that makes introducing the slot safe: this text
        # is what component_ext.c used to hardcode.
        expected = (
            "    if (!self->handle) {\n"
            "        PyErr_SetString(PyExc_MemoryError,\n"
            '                        "acq_create returned NULL");\n'
            "        return -1;\n"
            "    }\n"
        )
        assert expected in _ext_c(project)

    def test_explicitly_declaring_memoryerror_is_allowed(self, project):
        # Not a no-op: the message becomes the component's own.
        error_run(project, "acq", "MemoryError", "out of memory sizing grid")
        body = _init_body(project)
        assert "PyExc_MemoryError" in body
        assert "out of memory sizing grid" in body
        assert "acq_create returned NULL" not in body


class TestCreateErrorConfig:
    def test_persists_to_manifest(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        cfg = load(project)
        assert create_error(cfg, "acq") == "ValueError"
        assert create_error_message(cfg, "acq") == _MSG

    def test_message_with_quotes_round_trips(self, project):
        # The scalar_keys dump loop does no escaping; the message must go
        # through _str_assign or this produces broken TOML.
        msg = 'grid is "best effort" — pd target not met'
        error_run(project, "acq", "ValueError", msg)
        assert create_error_message(load(project), "acq") == msg

    def test_undeclared_reads_empty(self, project):
        cfg = load(project)
        assert create_error(cfg, "acq") == ""
        assert create_error_message(cfg, "acq") == ""


class TestCreateErrorSurvivesRegeneration:
    def test_survives_fragment_delete_and_apply(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        ext_path = project / "native" / "src" / "acq" / "acq_ext.c"
        ext_path.unlink()
        apply_run(project)
        assert "PyExc_ValueError" in ext_path.read_text(encoding="utf-8")

    def test_survives_a_later_property(self, project):
        error_run(project, "acq", "ValueError", _MSG)
        property_run(project, "acq", "dropped", None, "size_t", False)
        assert "PyExc_ValueError" in _init_body(project)


class TestCreateErrorScriptRoundTrip:
    def test_script_reconstructs_the_command(self, project, capsys):
        error_run(project, "acq", "ValueError", _MSG)
        capsys.readouterr()
        script_run(project)
        out = capsys.readouterr().out
        assert "just-makeit error acq" in out
        assert "--category ValueError" in out
        assert _MSG in out

    def test_script_omits_undeclared(self, project, capsys):
        capsys.readouterr()
        script_run(project)
        assert "just-makeit error" not in capsys.readouterr().out


class TestCreateErrorValidation:
    def test_rejects_unknown_category(self, project):
        with pytest.raises(SystemExit):
            error_run(project, "acq", "Nope", _MSG)

    def test_rejects_a_warning_category(self, project):
        # UserWarning is a valid *warning* category but not an exception to
        # raise from __init__; the two allowlists are deliberately distinct.
        with pytest.raises(SystemExit):
            error_run(project, "acq", "UserWarning", _MSG)

    def test_rejects_empty_message(self, project):
        with pytest.raises(SystemExit):
            error_run(project, "acq", "ValueError", "")

    def test_rejects_unknown_object(self, project):
        with pytest.raises(SystemExit):
            error_run(project, "nosuch", "ValueError", _MSG)


class TestCreateErrorCli:
    def test_cli_declares(self, project, capsys, monkeypatch):
        r = _cli(
            "error",
            "acq",
            "--category",
            "ValueError",
            "--message",
            _MSG,
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        assert create_error(load(project), "acq") == "ValueError"

    def test_cli_warns_about_the_blanket_translation(
        self, project, capsys, monkeypatch
    ):
        # The tradeoff is inherent, so the command must say it out loud.
        r = _cli(
            "error",
            "acq",
            "--category",
            "ValueError",
            "--message",
            _MSG,
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert "including a genuine allocation failure" in r.stdout

    def test_cli_requires_category(self, project, capsys, monkeypatch):
        r = _cli(
            "error",
            "acq",
            "--message",
            "m",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "--category is required" in r.stderr

    def test_cli_requires_object_name(self, project, capsys, monkeypatch):
        r = _cli("error", cwd=project, capsys=capsys, monkeypatch=monkeypatch)
        assert r.returncode != 0
        assert "requires an object name" in r.stderr

    def test_cli_rejects_unknown_flag(self, project, capsys, monkeypatch):
        r = _cli(
            "error",
            "acq",
            "--category",
            "ValueError",
            "--message",
            "m",
            "--bogus",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "unexpected argument '--bogus'" in r.stderr

    def test_cli_rejects_flag_without_value(
        self, project, capsys, monkeypatch
    ):
        r = _cli(
            "error",
            "acq",
            "--category",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "--category requires a value" in r.stderr

    def test_cli_passes_module_through(self, tmp_path, capsys, monkeypatch):
        from just_makeit._object import run as object_run

        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [], modules=["filt"])
        object_run(dest, "fir", module="filt", state_vars=[("n", "int", "1")])
        r = _cli(
            "error",
            "fir",
            "--module",
            "filt",
            "--category",
            "ValueError",
            "--message",
            "bad taps",
            cwd=dest,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        ext = (dest / "native" / "src" / "filt" / "filt_ext_fir.c").read_text(
            encoding="utf-8"
        )
        assert "PyErr_SetString(PyExc_ValueError," in ext

    def test_cli_errors_outside_a_project(self, tmp_path, capsys, monkeypatch):
        r = _cli(
            "error",
            "acq",
            "--category",
            "ValueError",
            "--message",
            "m",
            cwd=tmp_path,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "just-makeit.toml" in r.stderr


@pytest.mark.slow
class TestCreateErrorEndToEnd:
    """Compile a component whose create() genuinely refuses, and catch it."""

    def test_refusal_raises_the_declared_exception(self, project):
        error_run(project, "acq", "ValueError", _MSG)

        # The generated create() always succeeds, so there'd be nothing to
        # observe. Make it refuse the way a real validating ctor would.
        core_c = project / "native" / "src" / "acq" / "acq_core.c"
        text = core_c.read_text(encoding="utf-8")
        text = text.replace(
            "    acq_state_t *obj = calloc(1, sizeof(*obj));",
            "    if (reps < 1)\n"
            "        return NULL;\n"
            "    acq_state_t *obj = calloc(1, sizeof(*obj));",
            1,
        )
        core_c.write_text(text, encoding="utf-8")

        build = subprocess.run(
            ["make"], cwd=project, capture_output=True, text=True
        )
        assert build.returncode == 0, build.stderr[-3000:]

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
        so = list(project.rglob(f"acq{ext_suffix}"))
        assert so, "extension module was not built"

        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(so[0].parent.parent)!r})\n"
            "from dsp import Acq\n"
            "assert Acq(1) is not None, 'valid construction should succeed'\n"
            "try:\n"
            "    Acq(0)\n"
            "    raise AssertionError('refusal did not raise')\n"
            "except ValueError as e:\n"
            "    assert 'pd unreachable' in str(e), str(e)\n"
            "except MemoryError:\n"
            "    raise AssertionError('still reporting the MemoryError lie')\n"
            "print('ok')\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr[-3000:]
        assert "ok" in run.stdout
