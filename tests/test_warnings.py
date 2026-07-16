"""Integration tests for `just-makeit warning` (gh-481).

The feature exists because a hand-patched ``PyErr_WarnEx`` in the ``_ext.c``
glue was silently lost whenever anything regenerated that file — and
delete-the-fragment-then-``jm apply`` is jm's own documented way to pick up a
new declarative field on an existing object. So the load-bearing test here is
not "does it emit the call" but `TestWarningSurvivesRegeneration`: the warning
has to come back from the manifest alone, with no human in the loop.
"""

import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

# Generated stubs intentionally embed <<IMPLEMENT:...>> guidance comments.
# Only flag tokens that are NOT the IMPLEMENT marker.
_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._property import run as property_run
from just_makeit._warning import run as warning_run
from just_makeit._script import run as script_run
from just_makeit._config import load, warnings as cfg_warnings

_MSG = (
    "Acquisition is under-powered: pd_predicted < pd at this reps/cn0_dbhz. "
    "Raise reps or cn0_dbhz, set max_noncoh>1, or narrow doppler_uncertainty."
)


@pytest.fixture()
def project(tmp_path):
    """A standalone object carrying the bool flag a warning keys off."""
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["acq"], [("underpowered", "int", "0")])
    return dest


class _CliResult:
    def __init__(self, returncode, out, err):
        self.returncode = returncode
        self.stdout = out
        self.stderr = err


def _cli(*args, cwd=None, capsys=None, monkeypatch=None) -> _CliResult:
    """Drive the real argv parser in-process.

    Deliberately not a subprocess: `main()` is what parses these flags, and a
    subprocess would run it where coverage can't see it (the CLI dispatch layer
    sits at ~53% for exactly that reason). In-process also runs ~100x faster.
    """
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


def _ext_c(project, obj="acq"):
    return (project / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


class TestWarningCodegen:
    def test_emits_pyerr_warnex_guarded_by_condition(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        ext = _ext_c(project)
        assert "if (self->handle->underpowered) {" in ext
        assert "PyErr_WarnEx(PyExc_UserWarning," in ext

    def test_message_survives_intact(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        # The message is split across adjacent C literals to fit 79 cols;
        # concatenating them back must reproduce it exactly.
        ext = _ext_c(project)
        block = ext[ext.index("PyErr_WarnEx") : ext.index("< 0)")]
        joined = "".join(re.findall(r'"([^"]*)"', block))
        assert joined == _MSG

    def test_propagates_escalated_warning_as_init_failure(self, project):
        # Under -W error PyErr_WarnEx returns -1; __init__ must fail rather
        # than swallow it, because construction genuinely did not succeed.
        warning_run(project, "acq", "underpowered", _MSG)
        ext = _ext_c(project)
        assert ") < 0)\n            return -1;" in ext

    def test_custom_category(self, project):
        warning_run(
            project, "acq", "underpowered", _MSG, category="RuntimeWarning"
        )
        assert "PyErr_WarnEx(PyExc_RuntimeWarning," in _ext_c(project)

    def test_custom_stacklevel(self, project):
        warning_run(project, "acq", "underpowered", _MSG, stacklevel=2)
        assert "2) < 0)" in _ext_c(project)

    def test_emitted_block_stays_within_79_cols(self, project):
        # Scoped to the gh-481 block: the generated PyMethodDef table already
        # runs to 81 cols on main, so a whole-file assertion would be testing
        # someone else's code.
        warning_run(project, "acq", "underpowered", _MSG)
        ext = _ext_c(project)
        block = ext[ext.index("if (self->handle->underpowered)") :]
        block = block[: block.index("return 0;")]
        for ln in block.splitlines():
            assert len(ln) <= 79, f"{len(ln)} cols: {ln!r}"

    def test_no_stray_placeholders(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".c", ".h", ".py", ".pyi"):
                text = path.read_text(encoding="utf-8")
                assert not _STRAY_PLACEHOLDER.search(text), path


class TestWarningZeroChurn:
    """A component that declares no warnings must be untouched by gh-481."""

    def test_no_warning_means_no_warnex(self, project):
        assert "PyErr_WarnEx" not in _ext_c(project)

    def test_memoryerror_fallback_is_unchanged(self, project):
        # gh-482 will turn this into a slot; until then it stays hardcoded and
        # a warnings-only project must still render it byte-for-byte.
        assert "PyErr_SetString(PyExc_MemoryError," in _ext_c(project)
        assert '"acq_create returned NULL");' in _ext_c(project)


class TestWarningConfig:
    def test_persists_to_manifest(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        entries = cfg_warnings(load(project), "acq")
        assert entries == [
            {
                "after": "__init__",
                "condition": "underpowered",
                "category": "UserWarning",
                "message": _MSG,
            }
        ]

    def test_message_with_quotes_round_trips(self, project):
        msg = 'grid is "best effort" — pd target not met'
        warning_run(project, "acq", "underpowered", msg)
        assert cfg_warnings(load(project), "acq")[0]["message"] == msg

    def test_redeclaring_same_condition_updates_in_place(self, project):
        warning_run(project, "acq", "underpowered", "first")
        warning_run(project, "acq", "underpowered", "second")
        entries = cfg_warnings(load(project), "acq")
        assert len(entries) == 1, "must not duplicate the guard"
        assert entries[0]["message"] == "second"
        assert _ext_c(project).count("PyErr_WarnEx") == 1


class TestWarningSurvivesRegeneration:
    """The actual bug from gh-481 — everything else is detail."""

    def test_survives_fragment_delete_and_apply(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        ext_path = project / "native" / "src" / "acq" / "acq_ext.c"
        assert "PyErr_WarnEx" in ext_path.read_text(encoding="utf-8")

        # doppler's documented migration mechanic: drop the fragment, let the
        # manifest rebuild it. This is what silently dropped the hand-patch.
        ext_path.unlink()
        apply_run(project)

        assert "PyErr_WarnEx" in ext_path.read_text(encoding="utf-8"), (
            "warning did not survive delete+apply — this is the gh-481 bug"
        )

    def test_survives_a_later_property(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        property_run(project, "acq", "dropped", None, "size_t", False)
        assert "PyErr_WarnEx" in _ext_c(project)

    def test_survives_a_later_method(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        method_run(
            project,
            "acq",
            "execute_ctrl",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
        )
        assert "PyErr_WarnEx" in _ext_c(project)


class TestWarningScriptRoundTrip:
    def test_script_reconstructs_the_command(self, project, capsys):
        warning_run(project, "acq", "underpowered", _MSG)
        capsys.readouterr()
        script_run(project)
        out = capsys.readouterr().out
        assert "just-makeit warning acq" in out
        assert "--condition underpowered" in out
        # The message is the whole point — a script that drops it recreates
        # the original bug one layer up.
        assert _MSG in out

    def test_script_omits_defaults(self, project, capsys):
        warning_run(project, "acq", "underpowered", _MSG)
        capsys.readouterr()
        script_run(project)
        out = capsys.readouterr().out
        assert "--category" not in out
        assert "--after" not in out
        assert "--stacklevel" not in out

    def test_script_keeps_non_defaults(self, project, capsys):
        warning_run(
            project,
            "acq",
            "underpowered",
            _MSG,
            category="RuntimeWarning",
            stacklevel=3,
        )
        capsys.readouterr()
        script_run(project)
        out = capsys.readouterr().out
        assert "--category RuntimeWarning" in out
        assert "--stacklevel 3" in out


class TestWarningValidation:
    def test_rejects_unknown_category(self, project):
        with pytest.raises(SystemExit):
            warning_run(project, "acq", "underpowered", _MSG, category="Nope")

    def test_rejects_non_identifier_condition(self, project):
        with pytest.raises(SystemExit):
            warning_run(project, "acq", "a || b", _MSG)

    def test_rejects_unsupported_after(self, project):
        # Method-site warnings are not wired; half-generating would be worse
        # than refusing.
        with pytest.raises(SystemExit):
            warning_run(project, "acq", "underpowered", _MSG, after="execute")

    def test_rejects_empty_message(self, project):
        with pytest.raises(SystemExit):
            warning_run(project, "acq", "underpowered", "")

    def test_rejects_unknown_object(self, project):
        with pytest.raises(SystemExit):
            warning_run(project, "nosuch", "underpowered", _MSG)

    def test_warns_on_undeclared_condition(self, project, capsys):
        # jm cannot see a field hand-added to the sacred struct, so an unknown
        # condition is a warning, not an error.
        warning_run(project, "acq", "mystery_flag", _MSG)
        assert "not a declared state field" in capsys.readouterr().err


class TestWarningModuleObject:
    def test_module_object_gets_the_warning(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [], modules=["filt"])
        from just_makeit._object import run as object_run

        object_run(
            dest, "fir", module="filt", state_vars=[("clipped", "int", "0")]
        )
        warning_run(dest, "fir", "clipped", "output clipped", module="filt")
        ext = (dest / "native" / "src" / "filt" / "filt_ext_fir.c").read_text(
            encoding="utf-8"
        )
        assert "PyErr_WarnEx(PyExc_UserWarning," in ext


@pytest.mark.slow
class TestWarningEndToEnd:
    """Compile a real project and observe the warning from Python."""

    def test_warning_actually_fires(self, project):
        warning_run(project, "acq", "underpowered", _MSG)
        build = subprocess.run(
            ["make"], cwd=project, capture_output=True, text=True
        )
        assert build.returncode == 0, build.stderr[-3000:]

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
        so = list(project.rglob(f"acq{ext_suffix}"))
        assert so, "extension module was not built"

        script = (
            "import sys, warnings\n"
            f"sys.path.insert(0, {str(so[0].parent.parent)!r})\n"
            "from dsp import Acq\n"
            "with warnings.catch_warnings(record=True) as rec:\n"
            "    warnings.simplefilter('always')\n"
            "    Acq(0)\n"
            "    assert not rec, 'warned when flag was clear'\n"
            "    Acq(1)\n"
            "    assert len(rec) == 1, rec\n"
            "    assert issubclass(rec[0].category, UserWarning)\n"
            "with warnings.catch_warnings():\n"
            "    warnings.simplefilter('error')\n"
            "    try:\n"
            "        Acq(1)\n"
            "        raise AssertionError('-W error did not propagate')\n"
            "    except UserWarning:\n"
            "        pass\n"
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


class TestWarningCli:
    """The argv layer, which `warning_run` tests bypass entirely."""

    def test_cli_declares_a_warning(self, project, capsys, monkeypatch):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
            "--message",
            _MSG,
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        assert "PyErr_WarnEx(PyExc_UserWarning," in _ext_c(project)
        assert cfg_warnings(load(project), "acq")[0]["message"] == _MSG

    def test_cli_passes_category_and_stacklevel(
        self, project, capsys, monkeypatch
    ):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
            "--message",
            "m",
            "--category",
            "RuntimeWarning",
            "--stacklevel",
            "2",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        entry = cfg_warnings(load(project), "acq")[0]
        assert entry["category"] == "RuntimeWarning"
        assert entry["stacklevel"] == 2
        assert "PyErr_WarnEx(PyExc_RuntimeWarning," in _ext_c(project)

    def test_cli_requires_condition(self, project, capsys, monkeypatch):
        r = _cli(
            "warning",
            "acq",
            "--message",
            "m",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "--condition is required" in r.stderr

    def test_cli_requires_object_name(self, project, capsys, monkeypatch):
        r = _cli(
            "warning", cwd=project, capsys=capsys, monkeypatch=monkeypatch
        )
        assert r.returncode != 0
        assert "requires an object name" in r.stderr

    def test_cli_rejects_unknown_flag(self, project, capsys, monkeypatch):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
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
            "warning",
            "acq",
            "--condition",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "--condition requires a value" in r.stderr

    def test_cli_rejects_non_numeric_stacklevel(
        self, project, capsys, monkeypatch
    ):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
            "--message",
            "m",
            "--stacklevel",
            "high",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "--stacklevel requires a positive integer" in r.stderr

    def test_cli_rejects_bad_category(self, project, capsys, monkeypatch):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
            "--message",
            "m",
            "--category",
            "Nope",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "unsupported --category" in r.stderr

    def test_cli_passes_module_through(self, tmp_path, capsys, monkeypatch):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [], modules=["filt"])
        from just_makeit._object import run as object_run

        object_run(
            dest, "fir", module="filt", state_vars=[("clipped", "int", "0")]
        )
        r = _cli(
            "warning",
            "fir",
            "--module",
            "filt",
            "--condition",
            "clipped",
            "--message",
            "output clipped",
            cwd=dest,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        ext = (dest / "native" / "src" / "filt" / "filt_ext_fir.c").read_text(
            encoding="utf-8"
        )
        assert "PyErr_WarnEx(PyExc_UserWarning," in ext

    def test_cli_passes_after_through(self, project, capsys, monkeypatch):
        # --after must reach _warning.run, which rejects anything but
        # __init__; a silently-swallowed flag would look like it worked.
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "underpowered",
            "--message",
            "m",
            "--after",
            "execute",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "not supported yet" in r.stderr

    def test_cli_errors_outside_a_project(self, tmp_path, capsys, monkeypatch):
        r = _cli(
            "warning",
            "acq",
            "--condition",
            "x",
            "--message",
            "m",
            cwd=tmp_path,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "just-makeit.toml" in r.stderr


class TestWarningModuleObjectErrors:
    def test_unknown_object_in_module(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [], modules=["filt"])
        with pytest.raises(SystemExit):
            warning_run(dest, "nosuch", "flag", "m", module="filt")
