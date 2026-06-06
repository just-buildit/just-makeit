"""gh-183: warn on jm-version skew + monotonically stamp [project].jm_version.

A stale CLI on $PATH silently emits old-format glue; jm records the generating
version and warns when the running CLI differs.
"""

from __future__ import annotations

import io
import contextlib
from pathlib import Path

from just_makeit import _config as C
from just_makeit._new import run as jm_new
from just_makeit._apply import run as jm_apply


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_new_stamps_jm_version(tmp_path: Path):
    proj = tmp_path / "p"
    _quiet(jm_new, "p", proj)
    assert C.jm_version(C.load(proj)), (
        "jm new should stamp [project].jm_version"
    )


def test_warn_on_skew(tmp_path, capsys, monkeypatch):
    proj = tmp_path / "p"
    _quiet(jm_new, "p", proj)
    monkeypatch.setattr(C, "jm_cli_version", lambda: "9.9.9")
    cfg = C.load(proj)
    cfg.setdefault("project", {})["jm_version"] = "0.15.9"
    from just_makeit import _cli

    _cli._warn_version_skew(cfg)
    err = capsys.readouterr().err
    assert "0.15.9" in err and "9.9.9" in err and "warning" in err


def test_no_warn_when_matched(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(C, "jm_cli_version", lambda: "0.16.0")
    from just_makeit import _cli

    _cli._warn_version_skew({"project": {"jm_version": "0.16.0"}})
    _cli._warn_version_skew({"project": {}})  # no record → silent
    assert capsys.readouterr().err == ""


def test_stamp_is_monotonic(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    _quiet(jm_new, "p", proj)
    # older recorded → stamp moves forward
    monkeypatch.setattr(C, "jm_cli_version", lambda: "0.16.0")
    cfg = C.load(proj)
    cfg["project"]["jm_version"] = "0.15.0"
    assert C.stamp_jm_version(proj, cfg) == "0.16.0"
    assert C.jm_version(C.load(proj)) == "0.16.0"
    # newer recorded + older CLI → NO downgrade (stale CLI keeps warning)
    monkeypatch.setattr(C, "jm_cli_version", lambda: "0.14.12")
    cfg2 = C.load(proj)  # recorded is now 0.16.0
    assert C.stamp_jm_version(proj, cfg2) is None
    assert C.jm_version(C.load(proj)) == "0.16.0"


def test_apply_stamps(tmp_path, monkeypatch):
    from just_makeit._object import run as jm_object

    proj = tmp_path / "p"
    _quiet(jm_new, "p", proj)
    _quiet(
        jm_object,
        proj,
        "g",
        None,
        state_vars=[("g", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    # wipe the record, then apply with a known running version
    cfg = C.load(proj)
    (proj / "just-makeit.toml").write_text(
        (proj / "just-makeit.toml")
        .read_text()
        .replace(f'jm_version = "{cfg["project"]["jm_version"]}"\n', "")
    )
    monkeypatch.setattr(C, "jm_cli_version", lambda: "0.16.0")
    _quiet(jm_apply, proj)
    assert C.jm_version(C.load(proj)) == "0.16.0"
