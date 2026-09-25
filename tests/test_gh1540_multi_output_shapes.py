"""`multi_output` is honoured, or refused -- on every method shape (gh-1540).

GATE: a method shape that cannot carry `multi_output` refuses it, on the CLI and on apply alike.

gh-600 made each extra ``--multi-output`` value a trailing ``<T> *outN``
parameter. Measured over the result shapes, three never render them: a
``--batch`` method, a list of records (``--result-field``) and a
``--single`` record. On those, ``jm method ... --multi-output int`` exited 0,
stored the key, and generated a prototype, binding and stub with no trace of
it -- the declared output silently vanished.

They are refused now, in ``_method.run``, which the CLI and ``apply``'s
replay share, so a hand-written manifest cannot get past what the CLI
refuses (the disagreement gh-1408 is about). The shapes that DO render the
extra outputs are held to still accepting them, so the refusal cannot
quietly widen.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

_REFUSED = {
    "batch": ["--arg-type", "double", "--return-type", "double", "--batch"],
    "records": [
        "--arg-type", "void", "--return-type", "row_t",
        "--result-field", "a:int",
    ],
    "single": [
        "--arg-type", "void", "--return-type", "rec_t", "--single",
        "--result-field", "a:int",
    ],
}  # fmt: skip

_HONOURED = {
    "plain": ["--arg-type", "double", "--return-type", "double"],
    "void": ["--arg-type", "double", "--return-type", "void"],
    "variable_output": [
        "--arg-type", "double[]", "--return-type", "double",
        "--variable-output",
    ],
    "out_type": [
        "--arg-type", "double[]", "--return-type", "double",
        "--out-type", "double",
    ],
}  # fmt: skip


@pytest.fixture
def proj(tmp_path):
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    p = tmp_path / "p"
    assert run_cli("object", "w", "--no-step", cwd=p).returncode == 0
    return p


def _header(proj: Path) -> str:
    return (proj / INC_ROOT / "w/w_core.h").read_text(encoding="utf-8")


@pytest.mark.parametrize("shape", sorted(_REFUSED))
def test_the_cli_refuses_a_shape_with_no_slot(proj, shape):
    before = (proj / "objects/w.toml").read_text(encoding="utf-8")
    r = run_cli(
        "method", "w", "m", *_REFUSED[shape], "--multi-output", "int",
        cwd=proj,
    )  # fmt: skip
    assert r.returncode != 0, f"{shape}: accepted --multi-output"
    assert "--multi-output has no effect" in r.stderr, r.stderr
    assert "Drop" in r.stderr, "the refusal must name the fix"
    assert "w_m(" not in _header(proj), f"{shape}: wrote C before refusing"
    assert (proj / "objects/w.toml").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("shape", sorted(_HONOURED))
def test_a_shape_with_a_slot_still_carries_it(proj, shape):
    r = run_cli(
        "method", "w", "m", *_HONOURED[shape], "--multi-output", "int",
        cwd=proj,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    proto = next(ln for ln in _header(proj).splitlines() if " w_m(" in ln)
    assert "int *out1" in proto, f"{shape}: {proto}"


def test_apply_refuses_what_the_cli_refuses(proj):
    """A hand-written manifest entry takes the same road as the CLI."""
    assert (
        run_cli("method", "w", "m", *_REFUSED["batch"], cwd=proj).returncode
        == 0
    )
    f = proj / "objects/w.toml"
    text = f.read_text(encoding="utf-8")
    assert "batch = true" in text
    f.write_text(
        text.replace(
            "batch = true", 'batch = true\nmulti_output = ["int"]', 1
        ),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, "apply accepted what the CLI refuses"
    assert "--multi-output has no effect" in r.stderr, r.stderr
