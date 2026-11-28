"""gh-2848: a warning whose CONDITION changed is reported as such.

After gh-2555 a kept warning block whose ``if (...)`` head the current
manifest does not render is reported, not re-rendered (a condition is the
author's C as often as a field, gh-601). But when the only difference was a
manifest condition change, the report read ``warns UserWarning "x", the
manifest UserWarning "x"`` -- identical sides -- and said the constructor
was not jm's own render, which was false: nothing was hand-edited.

Now both sides name the condition they warn under, and the reason is the
condition. Reported rather than re-rendered stays the decision: jm cannot
tell a manifest edit from an author's edit of a condition by reading the
text, and file content may refuse, never infer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


@pytest.fixture()
def edited(tmp_path: Path) -> Path:
    """``acc`` warns under ``hot``; the manifest then says ``cold``."""
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "dsp", cwd=proj)
    _ok(
        "object", "acc", "--module", "dsp", "--init-param", "mode:int:0",
        "--state", "hot:bool:false", "--state", "cold:bool:false",
        cwd=proj,
    )  # fmt: skip
    _ok(
        "warning", "acc", "--module", "dsp", "--condition", "hot",
        "--message", "old warn", cwd=proj,
    )  # fmt: skip
    path = proj / "objects" / "acc.toml"
    text = path.read_text("utf-8")
    assert text.count('condition = "hot"') == 1, text
    path.write_text(text.replace('condition = "hot"', 'condition = "cold"'))
    return proj


def test_status_names_both_conditions_and_the_reason(edited: Path) -> None:
    r = run_cli("status", "--check", cwd=edited)
    assert r.returncode != 0, r.stdout
    assert "under self->handle->hot" in r.stdout, r.stdout
    assert "under self->handle->cold" in r.stdout, r.stdout
    assert "condition is not one the manifest renders" in r.stdout, r.stdout


def test_apply_names_both_conditions_and_the_reason(edited: Path) -> None:
    out = _ok("apply", cwd=edited)
    assert "under self->handle->hot" in out, out
    assert "under self->handle->cold" in out, out
    assert "condition is not one the manifest renders" in out, out
    assert "This one is not." in out, out
    assert "otherwise its own render" not in out, out


def test_a_message_only_change_names_no_false_condition_reason(
    tmp_path: Path,
) -> None:
    """The reason is the condition only when the condition differs."""
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "dsp", cwd=proj)
    _ok(
        "object", "acc", "--module", "dsp", "--state", "hot:bool:false",
        "--init-param", "mode:int:0", cwd=proj,
    )  # fmt: skip
    _ok(
        "warning", "acc", "--module", "dsp", "--condition", "hot",
        "--message", "old warn", cwd=proj,
    )  # fmt: skip
    path = proj / "objects" / "acc.toml"
    text = path.read_text("utf-8")
    path.write_text(text.replace("old warn", "new warn"), "utf-8")
    r = run_cli("status", "--check", cwd=proj)
    assert "condition is not one the manifest renders" not in r.stdout
    assert 'UserWarning "old warn" under self->handle->hot' in r.stdout
    assert 'UserWarning "new warn" under self->handle->hot' in r.stdout
