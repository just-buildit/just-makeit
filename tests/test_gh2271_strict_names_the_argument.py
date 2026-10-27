"""gh-2271: a `strict` refusal names the argument the caller passed.

A strict input is refused rather than converted (gh-1426 B), and the
refusal named the binding's C local: a method's input is ``in_arr`` while
its keyword is ``x``, so ``prod(x[::2])`` raised "in must be a 1-D
C-contiguous array" -- naming no argument the caller can find. The
converting path already named ``x``; the strict path and a record input
derived the name from the local instead of taking the one passed.

Registration-free over shapes: every refusal of the form ``"<name> must
be ..."`` in a rendered binding names a keyword of the same function.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _coerce  # noqa: E402


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


#: Each strict-capable input shape: the flags after `jm method o <name>`.
SHAPES = {
    "batch": [
        "--arg-type",
        "float _Complex",
        "--return-type",
        "float _Complex",
        "--batch",
    ],
    "array_arg": ["--arg-type", "float _Complex[]", "--return-type", "bool"],
    "variable_output": [
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--variable-output",
    ],
}


@pytest.fixture(scope="module", params=["standalone", "module"])
def ext(request, tmp_path_factory) -> str:
    root = tmp_path_factory.mktemp(request.param)
    _ok("new", "p", "--no-c-prefix", cwd=root)
    proj = root / "p"
    where = []
    if request.param == "module":
        _ok("module", "dsp", cwd=proj)
        where = ["--module", "dsp"]
    _ok("object", "o", *where, "--state", "k:int:0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, "--strict", cwd=proj)
    rel = (
        "native/src/dsp/dsp_ext_o.c"
        if request.param == "module"
        else "native/src/o/o_ext.c"
    )
    return (proj / rel).read_text(encoding="utf-8")


def _functions(c: str) -> "dict[str, str]":
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r"^(\w+)\(.*?^}$", c, re.S | re.M)
    }


def test_every_strict_refusal_names_a_keyword(ext):
    seen = 0
    for name, body in _functions(ext).items():
        refused = re.findall(r'"(\w+) must be (?:an ndarray|a 1-D)', body)
        if not refused:
            continue
        kw = re.search(r"_kwlist\[\] = \{([^}]*)\}", body)
        assert kw, f"{name} refuses but declares no keywords"
        keywords = set(re.findall(r'"(\w+)"', kw.group(1)))
        for label in refused:
            seen += 1
            assert label in keywords, (name, label, keywords)
    # Every shape above renders both refusals (dtype, rank/contiguity).
    assert seen >= 2 * len(SHAPES), seen


def test_the_record_input_path_takes_the_name_too():
    acq = _coerce.input_array_acq(dtype_fn="o_rec", label="x", strict=True)
    assert '"x must be an array of the declared record' in acq, acq
    assert '"in must be' not in acq
