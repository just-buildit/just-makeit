"""gh-490: close three holes in the declarative surface.

All three are the same species — a feature that exists in one layer but is
unreachable or lossy in another, and says nothing about it:

1. `jm warning` / `jm error` shipped as authoring commands with no removal
   path, so the only undo was hand-editing the manifest. That cuts against the
   contract those features exist to uphold (manifest is SSOT, the CLI drives
   it).
2. `_script._property_flags` silently dropped doc/expr/buf_field/len_field/
   valid_field, so `jm script` emitted a script that rebuilt a *different*
   project — a buf-backed ndarray property came back as a plain scalar getter.
3. The argv parser never wired --buf-field/--len-field/--valid-field/--expr,
   though `_property.run` has always accepted them. They were reachable only
   through `jm apply`'s replay of a hand-written manifest, so a user could not
   author one at all — and (2) could not have round-tripped even if it tried.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._error import run as error_run
from just_makeit._property import run as property_run
from just_makeit._script import run as script_run, _property_flags
from just_makeit._remove import run as remove_run
from just_makeit._warning import run as warning_run
from just_makeit._config import (
    load,
    create_error,
    properties,
    warnings as cfg_warnings,
)


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run(
        "dsp",
        dest,
        ["acq"],
        [("underpowered", "int", "0"), ("n", "size_t", "0")],
    )
    return dest


def _ext_c(project):
    return (project / "native" / "src" / "acq" / "acq_ext.c").read_text(
        encoding="utf-8"
    )


def _init_body(project):
    ext = _ext_c(project)
    start = ext.index("Acq_init(AcqObject *self")
    return ext[start : ext.index("\n}\n", start)]


class _CliResult:
    def __init__(self, returncode, out, err):
        self.returncode, self.stdout, self.stderr = returncode, out, err


def _cli(*args, cwd=None, capsys=None, monkeypatch=None) -> _CliResult:
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


class TestRemoveWarning:
    def test_removes_from_manifest_and_glue(self, project):
        warning_run(project, "acq", "underpowered", "best effort")
        assert "PyErr_WarnEx" in _ext_c(project)
        remove_run(
            project, "warning", "underpowered", object_name="acq", force=True
        )
        assert cfg_warnings(load(project), "acq") == []
        assert "PyErr_WarnEx" not in _ext_c(project)

    def test_addressed_by_condition_not_name(self, project):
        # A warning has no name; its condition is what identifies it.
        warning_run(project, "acq", "underpowered", "a")
        warning_run(project, "acq", "n", "b")
        remove_run(
            project, "warning", "underpowered", object_name="acq", force=True
        )
        left = cfg_warnings(load(project), "acq")
        assert [w["condition"] for w in left] == ["n"]
        assert "self->handle->n" in _ext_c(project)
        assert "self->handle->underpowered" not in _ext_c(project)

    def test_leaves_the_state_field_alone(self, project):
        # The condition field is the component's own state and may have other
        # readers — removing the warning must not touch the struct.
        warning_run(project, "acq", "underpowered", "best effort")
        remove_run(
            project, "warning", "underpowered", object_name="acq", force=True
        )
        core_h = (project / "native" / "inc" / "acq" / "acq_core.h").read_text(
            encoding="utf-8"
        )
        assert "underpowered" in core_h

    def test_unknown_condition_errors_and_lists_declared(
        self, project, capsys
    ):
        warning_run(project, "acq", "underpowered", "best effort")
        capsys.readouterr()
        with pytest.raises(SystemExit):
            remove_run(
                project, "warning", "nosuch", object_name="acq", force=True
            )
        assert "underpowered" in capsys.readouterr().err

    def test_unknown_object_errors(self, project):
        with pytest.raises(SystemExit):
            remove_run(
                project,
                "warning",
                "underpowered",
                object_name="nosuch",
                force=True,
            )

    def test_declining_the_prompt_changes_nothing(
        self, project, monkeypatch, capsys
    ):
        # "Aborted." must mean aborted — a removal that mutates anyway would
        # be the worst kind of bug in a destructive command.
        warning_run(project, "acq", "underpowered", "best effort")
        monkeypatch.setattr("builtins.input", lambda *_: "n")
        remove_run(project, "warning", "underpowered", object_name="acq")
        assert "Aborted." in capsys.readouterr().out
        assert len(cfg_warnings(load(project), "acq")) == 1
        assert "PyErr_WarnEx" in _ext_c(project)

    def test_cli(self, project, capsys, monkeypatch):
        warning_run(project, "acq", "underpowered", "best effort")
        r = _cli(
            "remove",
            "warning",
            "underpowered",
            "--object",
            "acq",
            "--force",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        assert cfg_warnings(load(project), "acq") == []


class TestRemoveError:
    def test_reverts_glue_to_memoryerror(self, project):
        error_run(project, "acq", "ValueError", "bad params")
        assert "PyExc_ValueError" in _init_body(project)
        remove_run(project, "error", "acq", object_name="acq", force=True)
        assert create_error(load(project), "acq") == ""
        body = _init_body(project)
        assert "PyExc_MemoryError" in body
        assert "PyExc_ValueError" not in body

    def test_drops_both_keys(self, project):
        error_run(project, "acq", "ValueError", "bad params")
        remove_run(project, "error", "acq", object_name="acq", force=True)
        cfg = load(project)
        assert "create_error" not in cfg["acq"]
        assert "create_error_message" not in cfg["acq"]

    def test_undeclared_errors(self, project):
        with pytest.raises(SystemExit):
            remove_run(project, "error", "acq", object_name="acq", force=True)

    def test_unknown_object_errors(self, project):
        with pytest.raises(SystemExit):
            remove_run(
                project, "error", "acq", object_name="nosuch", force=True
            )

    def test_declining_the_prompt_changes_nothing(
        self, project, monkeypatch, capsys
    ):
        error_run(project, "acq", "ValueError", "bad params")
        monkeypatch.setattr("builtins.input", lambda *_: "n")
        remove_run(project, "error", "acq", object_name="acq")
        assert "Aborted." in capsys.readouterr().out
        assert create_error(load(project), "acq") == "ValueError"
        assert "PyExc_ValueError" in _init_body(project)

    def test_cli(self, project, capsys, monkeypatch):
        error_run(project, "acq", "ValueError", "bad params")
        r = _cli(
            "remove",
            "error",
            "acq",
            "--object",
            "acq",
            "--force",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        assert create_error(load(project), "acq") == ""


class TestPropertyFlagsRoundTrip:
    """`jm script` must reproduce the project, not a lookalike."""

    def test_flags_emit_every_manifest_key(self):
        p = {
            "name": "samples",
            "type": "float[]",
            "buf_field": "data",
            "len_field": "count",
            "valid_field": "ready",
            "doc": "The samples.",
        }
        flags = "".join(_property_flags(p, None))
        for expected in (
            "--type",
            "--buf-field data",
            "--len-field count",
            "--valid-field ready",
            "--doc",
        ):
            assert expected in flags, f"{expected} dropped by _property_flags"

    def test_expr_survives(self):
        flags = "".join(
            _property_flags(
                {"name": "x", "type": "double", "expr": "a*b"}, None
            )
        )
        assert "--expr" in flags

    def test_script_round_trips_a_buf_property(self, project, capsys):
        property_run(
            project,
            "acq",
            "samples",
            None,
            "float[]",
            False,
            buf_field="data",
            len_field="n",
        )
        capsys.readouterr()
        script_run(project)
        out = capsys.readouterr().out
        assert "--buf-field data" in out
        assert "--len-field n" in out
        # Without these the script would rebuild a scalar getter instead.


class TestPropertyCliFlags:
    """The flags _property.run always accepted, now reachable from argv."""

    def test_cli_authors_a_buf_property(self, project, capsys, monkeypatch):
        r = _cli(
            "property",
            "acq",
            "samples",
            "--type",
            "float[]",
            "--buf-field",
            "data",
            "--len-field",
            "n",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        entry = properties(load(project), "acq")[0]
        assert entry["buf_field"] == "data"
        assert entry["len_field"] == "n"
        assert entry["type"] == "float[]"

    def test_cli_authors_an_expr_property(self, project, capsys, monkeypatch):
        r = _cli(
            "property",
            "acq",
            "ratio",
            "--type",
            "double",
            "--expr",
            "state->n * 2.0",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode == 0, r.stderr
        assert properties(load(project), "acq")[0]["expr"] == "state->n * 2.0"

    def test_cli_still_rejects_a_bogus_scalar_type(
        self, project, capsys, monkeypatch
    ):
        # Dropping the CLI's duplicate check must not lose the validation —
        # _property.run still owns it.
        r = _cli(
            "property",
            "acq",
            "bad",
            "--type",
            "notatype",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "unsupported --type" in r.stderr

    def test_len_field_without_buf_field_is_rejected(
        self, project, capsys, monkeypatch
    ):
        r = _cli(
            "property",
            "acq",
            "x",
            "--type",
            "size_t",
            "--len-field",
            "count",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "only applies alongside --buf-field" in r.stderr

    def test_buf_field_and_expr_are_mutually_exclusive(
        self, project, capsys, monkeypatch
    ):
        r = _cli(
            "property",
            "acq",
            "x",
            "--type",
            "float[]",
            "--buf-field",
            "data",
            "--expr",
            "1+1",
            cwd=project,
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert r.returncode != 0
        assert "mutually exclusive" in r.stderr
