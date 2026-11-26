"""gh-2588: a standalone method's definition drifting from its ``why`` /
``status_out`` is a gating finding, not a silent build failure.

Adding ``why = true`` to an existing STANDALONE object's method: `apply`
refreshes the header's prototype to the manifest (gh-632), but the
definition in ``_core.c`` is the author's and keeps the old signature. The
build then fails on conflicting types, while `jm status --check` exited 0:
gh-2574's WHY check compared only the header, which now agreed.

`_ctorsig.why_drift` now reads the author's definitions too -- inline in the
header and in every ``.c`` of the component's source directory -- and names
the file and the side (``defined:``) that disagrees. Every case drives jm
through ``run_cli``, as a user does.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

CORE_C = "native/src/cor/cor_core.c"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _project(tmp_path: Path, *method: str, prefix: bool = True) -> Path:
    _ok("new", "sq", *([] if prefix else ["--no-c-prefix"]), cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    _ok("method", "cor", *method, cwd=proj)
    assert run_cli("status", "--check", cwd=proj).returncode == 0
    return proj


_STAT = ["stat", "--param", "mode:int", "--return-type", "int"]
_STAT += ["--status-return"]


def _edit_manifest(proj: Path, old: str, new: str) -> None:
    path = proj / "objects" / "cor.toml"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _check(proj: Path) -> "tuple[int, str]":
    r = run_cli("status", "--check", cwd=proj)
    return r.returncode, r.stdout + r.stderr


def test_adding_why_reports_the_stale_definition(tmp_path: Path) -> None:
    """The issue's trigger: status --check fails, naming `_core.c`."""
    proj = _project(tmp_path, *_STAT)
    _edit_manifest(proj, 'name = "stat"\n', 'name = "stat"\nwhy = true\n')
    out = _ok("apply", cwd=proj)
    assert f"warning !: {CORE_C}: sq_cor_stat()" in out, out
    code, out = _check(proj)
    assert code != 0, out
    assert "WHY (1)" in out, out
    assert f"! {CORE_C}" in out, out
    assert "defined:  sq_cor_stat(sq_cor_state_t *state, int mode)" in out
    assert (
        "manifest: sq_cor_stat(sq_cor_state_t *state, int mode,"
        " const char **why)"
    ) in out, out


def test_a_matching_definition_is_not_a_finding(tmp_path: Path) -> None:
    """The over-fix guard: once the author updates the definition, clean."""
    proj = _project(tmp_path, *_STAT)
    _edit_manifest(proj, 'name = "stat"\n', 'name = "stat"\nwhy = true\n')
    _ok("apply", cwd=proj)
    core = proj / CORE_C
    text = core.read_text(encoding="utf-8")
    old = "sq_cor_stat(sq_cor_state_t *state, int mode)\n"
    assert text.count(old) == 1, text
    core.write_text(
        text.replace(
            old, "sq_cor_stat(sq_cor_state_t *state, int mode,"
            " const char **why)\n"
        ),
        encoding="utf-8",
    )  # fmt: skip
    code, out = _check(proj)
    assert code == 0, out
    assert "WHY (" not in out, out


def test_dropping_status_out_reports_the_stale_definition(
    tmp_path: Path,
) -> None:
    """The other direction, and gh-2584's parameter: the definition keeps an
    ``int *status`` the refreshed header no longer has."""
    proj = _project(
        tmp_path,
        "pmax", "--arg-type", "float[]", "--return-type", "float",
        "--variable-output", "--error-sentinel", "SIZE_MAX",
        "--status-out", "--status-error", "SQ_E_NOMEM:MemoryError",
    )  # fmt: skip
    path = proj / "objects" / "cor.toml"
    text = path.read_text(encoding="utf-8")
    assert text.count("status_out = true\n") == 1, text
    # status_errors rows need status_out (gh-2584), so they go too.
    head, sep, _rows = text.partition("[[cor.methods.status_errors]]")
    assert sep, text
    path.write_text(head.replace("status_out = true\n", ""), encoding="utf-8")
    _ok("apply", cwd=proj)
    code, out = _check(proj)
    assert code != 0, out
    assert f"! {CORE_C}" in out and "int *status" in out, out


def test_a_definition_in_a_sibling_c_file_is_read(tmp_path: Path) -> None:
    """gh-275: a component may define a method in another ``.c`` of its
    source directory. Moved there, the stale definition is still found.

    Without a ``c_prefix``: under one, a derived name defined outside its
    owning files is refused outright (gh-1657), so that layout cannot arise.
    """
    proj = _project(tmp_path, *_STAT, prefix=False)
    core = proj / CORE_C
    text = core.read_text(encoding="utf-8")
    start = text.index("int\ncor_stat(")
    end = text.index("\n}\n", start) + 3
    body = text[start:end]
    core.write_text(text[:start] + text[end:], encoding="utf-8")
    (core.parent / "stat_impl.c").write_text(
        '#include "sq/cor/cor_core.h"\n\n' + body, encoding="utf-8"
    )
    _edit_manifest(proj, 'name = "stat"\n', 'name = "stat"\nwhy = true\n')
    _ok("apply", cwd=proj)
    code, out = _check(proj)
    assert code != 0, out
    assert "! native/src/cor/stat_impl.c" in out, out
