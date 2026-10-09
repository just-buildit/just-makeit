"""gh-2067 / gh-2068 / gh-2133: generated code that assumed an element dtype.

Three faces of one mistake: a generated test, contract or prototype read an
element's type from somewhere other than the declaration of THAT side.

- **gh-2133.** The blockwise scaffold's ``test_steps_runs`` asserted the
  output's dtype was the INPUT's. True only while the two element types
  matched, so `docs/templates/blockwise.md`'s own mixed example
  (``int16_t[]`` in, ``float[]`` out) failed `make test` out of the box.
- **gh-2067.** The element contract's refusal half handed the writer a
  "foreign" dtype chosen per C spelling -- ``int8`` for ``double``,
  ``float64`` for everything else -- and both safe-cast into some element
  (``int8`` into ``float64``, ``float64`` into ``complex128``), so the
  binding accepted it and a first declaration was red: ``DID NOT RAISE``.
- **gh-2068.** ``--record-dtype`` naming a SCALAR element was accepted, and
  the prototype it wrote spelled a type no C type carries.

The gate is one project, built ONCE, holding every element pair the type
vocabulary admits -- blockwise objects over mixed input/output elements and
a same-element one, and one object declaring a scalar element of every type
a pair can carry plus a struct element -- whose OWN generated tests must
pass. The pairs are derived from `_types`, so a type added there is swept
with no edit here. The cheap halves (numpy's ``can_cast`` over the foreign
choice, and the refusals with the tree byte-identical) need no build.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc
from _jminc import INC_ROOT
from _jmrun import run_cli

from just_makeit import _invariants
from just_makeit import _textio
from just_makeit import _types as T

#: Every element an array may carry, as `step_type_error` admits it: the
#: blockwise preset's vocabulary, and every scalar `jm record --type` can
#: declare with a numpy dtype.
ARRAY_ELEMENTS = sorted(T.STEP_TYPES)

#: Every scalar a writer/reader PAIR can carry today: an element's array
#: param indexes `_CTYPE_TO_NPY`, so a type outside it does not reach a
#: render (`int`, `bool` and `long double _Complex` raise a KeyError in
#: `jm method`: gh-2132, left open). The foreign choice is still held to
#: the whole of `ARRAY_ELEMENTS` below, which needs no render.
PAIR_ELEMENTS = sorted(T.SUPPORTED_ARRAY_CTYPES)

#: Blockwise (input, output) pairs: each element once on each side, every
#: pair MIXED (a cyclic shift). The sweep adds the preset's own default,
#: the same element on both sides, beside them.
MIXED = [
    (a, ARRAY_ELEMENTS[(i + 1) % len(ARRAY_ELEMENTS)])
    for i, a in enumerate(ARRAY_ELEMENTS)
]


def _np(expr: str):
    """``"np.float64"`` -> ``numpy.float64``: the generated spelling, read."""
    assert expr.startswith("np."), expr
    return getattr(np, expr[len("np.") :])


def test_the_vocabulary_is_not_empty():
    """Never vacuous: an empty parametrisation would report all-green."""
    assert len(ARRAY_ELEMENTS) >= 15, ARRAY_ELEMENTS
    assert "double" in PAIR_ELEMENTS and "double _Complex" in PAIR_ELEMENTS
    # At least one pair whose two dtypes differ, or gh-2133 is unswept.
    assert any(
        T._CTYPE_META[a]["py_type"] != T._CTYPE_META[b]["py_type"]
        for a, b in MIXED
    )


# -- gh-2067: the foreign dtype, asked of numpy itself -------------------------


@pytest.mark.parametrize("ctype", ARRAY_ELEMENTS)
def test_the_foreign_dtype_never_safe_casts_into_the_element(ctype):
    """The refusal half asserts a raise, so the dtype must be one the
    binding's safe-casting conversion refuses -- on this platform's numpy,
    for every scalar an element may declare."""
    rec = {"name": "e", "type": ctype}
    element = _np(_invariants._dtype_expr(rec))
    foreign = _np(_invariants._foreign_dtype(rec))
    assert not np.can_cast(foreign, element, "safe"), (ctype, foreign)


def test_a_struct_elements_foreign_dtype_is_not_its_own():
    rec = {
        "name": "iq_t",
        "fields": [
            {"name": "i", "type": "int16_t"},
            {"name": "q", "type": "int16_t"},
        ],
    }
    element = eval(_invariants.declared_dtype_expr(rec), {"np": np})
    foreign = _np(_invariants._foreign_dtype(rec))
    assert not np.can_cast(foreign, element, "safe")


# -- gh-2068: a struct's spelling on a scalar, refused before any write --------

_READ = ["--borrow", "--param", "n:size_t"]

#: A hand-written row naming the scalar element as a struct.
_ROW = """
[[o.methods]]
name = "read"
arg_type = "void"
return_type = "float _Complex"
borrow = true
record_dtype = "s"

[[o.methods.params]]
name = "n"
type = "size_t"
"""


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


def _refusal_case(tmp_path: Path, face: str) -> "tuple[Path, list[str]]":
    """A project one command away from the declaration, and that command."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "o")
    if face == "record":
        # The other order: the member first, reading `s` as a struct...
        _ok(root, "method", "o", "read", *_READ, "--record-dtype", "s",
            "--result-field", "x:double")  # fmt: skip
        # ...then `s` declared a scalar under it.
        return root, ["record", "o", "s", "--type", "double"]
    _ok(root, "record", "o", "s", "--type", "double")
    if face == "method":
        return root, ["method", "o", "read", *_READ, "--record-dtype", "s"]
    frag = root / "objects" / "o.toml"
    _textio.write_text(frag, frag.read_text(encoding="utf-8") + _ROW)
    return root, ["apply"]


@pytest.mark.parametrize("face", ["method", "apply", "record"])
def test_a_scalar_record_dtype_is_refused_before_any_write(tmp_path, face):
    """`jm method`, `apply` on a hand-written manifest, and `jm record`
    under a member already reading the name as a struct: one refusal, which
    names the scalar's spelling, and the tree is left byte for byte."""
    root, argv = _refusal_case(tmp_path, face)
    before = _tree(root)
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 1, (r.stdout + r.stderr)[-3000:]
    assert "names a SCALAR element (double)" in r.stderr, r.stderr
    assert "--return-type s (out)" in r.stderr, r.stderr
    assert _tree(root) == before


# -- the sweep: one project, built once, its own tests green -------------------


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


def _declare_struct(root: Path, name: str, cols: "list[tuple[str, str]]"):
    """The author's half of a struct element: the struct, in the header."""
    header = root / INC_ROOT / "o" / "o_core.h"
    text = header.read_text(encoding="utf-8")
    body = "".join(f"    {t} {n};\n" for n, t in cols)
    block = f"typedef struct {{\n{body}}} {name};\n\n"
    _textio.write_text(
        header, text.replace("typedef struct", block + "typedef struct", 1)
    )


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_every_element_pair_scaffolds_green(tmp_path):
    """Every pair, one build: what jm generates passes its own tests.

    The blockwise objects carry gh-2133 (each output dtype asserted from
    its own side); object ``o`` carries gh-2067 (one input-face contract
    per scalar element, each refusing its foreign dtype for real, through
    the compiled binding) and the struct element beside them.
    """
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    # The preset's own default: the same element on both sides.
    _ok(root, "object", "bw", "--preset", "blockwise")
    for i, (a, b) in enumerate(MIXED):
        _ok(root, "object", f"bw{i}", "--preset", "blockwise",
            "--arg-type", f"{a}[]", "--return-type", f"{b}[]")  # fmt: skip

    _ok(root, "object", "o")
    for i, ctype in enumerate(PAIR_ELEMENTS):
        _ok(root, "record", "o", f"e{i}", "--type", ctype)
        _ok(root, "method", "o", f"w{i}", "--arg-type", f"e{i}[]",
            "--return-type", "bool")  # fmt: skip
        _ok(root, "method", "o", f"r{i}", "--arg-type", "void",
            "--return-type", f"e{i}", *_READ)  # fmt: skip
    _ok(root, "record", "o", "iq_t", "--field", "i:int16_t",
        "--field", "q:int16_t")  # fmt: skip
    _declare_struct(root, "iq_t", [("i", "int16_t"), ("q", "int16_t")])
    _ok(root, "method", "o", "wq", "--arg-type", "iq_t[]",
        "--return-type", "bool")  # fmt: skip
    _ok(root, "method", "o", "rq", *_READ, "--return-type", "float _Complex",
        "--record-dtype", "iq_t")  # fmt: skip

    r = run_cli("test", cwd=root)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-6000:]
    assert "100% tests passed" in out, out[-6000:]
    assert not re.search(r"\b\d+ failed\b", out), out[-6000:]

    # Armed, by the run's own report: every test named here RAN and passed,
    # so a pair that generated no test cannot pass by being absent.
    ran = [
        f"test_bw{i}.py::TestBw{i}::test_steps_runs" for i in range(len(MIXED))
    ]
    ran += [
        f"test_o_invariants.py::test_w{i}_speaks_e{i}"
        for i in range(len(PAIR_ELEMENTS))
    ]
    ran.append("test_o_invariants.py::test_wq_speaks_iq_t")
    missing = [t for t in ran if f"{t} PASSED" not in out]
    assert not missing, missing
    # ...and the blockwise assertion is about the OUTPUT side: deleting it
    # would also leave the run green.
    tests = root / "src" / "p" / "tests"
    for i, (_a, b) in enumerate(MIXED):
        text = (tests / f"test_bw{i}.py").read_text(encoding="utf-8")
        want = f"assert out.dtype == {T._CTYPE_META[b]['py_type']}\n"
        assert want in text, (i, b)
