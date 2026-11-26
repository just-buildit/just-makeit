"""gh-2788: adding ``why`` to an existing method converges in one `apply`,
and the method's docstring stays the name fallback.

`apply` refreshes the method's declaration and its Doxygen block (gh-632),
writing jm's own ``@param why  <WHY_PARAM_DOC>`` (gh-1722). The scaffold
test counted that description as authored, so the untouched skeleton's
placeholder ``@brief stat.`` became the docstring -- but only on the SECOND
`apply`, since the first rendered the glue from the block before it was
refreshed. `status --check` reported the binding and stub STALE after one.

Driven through ``run_cli``, as a user does.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def test_one_apply_converges_and_keeps_the_docstring(tmp_path: Path) -> None:
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    _ok(
        "method", "cor", "stat", "--param", "mode:int", "--return-type",
        "int", "--status-return", cwd=proj,
    )  # fmt: skip
    pyi = proj / "src" / "sq" / "cor.pyi"
    assert '"""Stat.' in pyi.read_text(encoding="utf-8")
    manifest = proj / "objects" / "cor.toml"
    text = manifest.read_text(encoding="utf-8")
    assert text.count('name = "stat"\n') == 1, text
    manifest.write_text(
        text.replace('name = "stat"\n', 'name = "stat"\nwhy = true\n'),
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    header = proj / "native" / "inc" / "sq" / "cor" / "cor_core.h"
    # The refresh this is about did happen: jm's documented `why` line.
    assert "@param why  Set to a static reason" in header.read_text("utf-8")
    assert '"""Stat.' in pyi.read_text(encoding="utf-8")
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    # The definition's drift is gh-2588's finding, and expected here; what
    # must be absent is a stale binding or stub after one `apply`.
    assert "STALE" not in out, out
    again = _ok("apply", cwd=proj)
    assert "nothing to do" in again, again
