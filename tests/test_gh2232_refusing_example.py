"""gh-2232: a method that declares a way to refuse claims no result in its
runtime example.

jm synthesizes a doctest for every method's runtime ``__doc__``. For a
scalar return it printed the type's zero as the call's output, and for a
``variable_output`` result the array's dtype. Both are true of the scaffold's
stub and a guess about any real kernel -- and for a method that declares it
can refuse its input, the guess is the one thing the declaration says may
not happen. doppler's Spectrogram ``flush`` (``error_negative``) showed

    >>> obj.flush(np.zeros(4, dtype=np.float32))
    0

over a kernel that refuses a zero-filled row. gh-869 had already stopped the
same guess for ``status_return``: the call is shown, and no result. This
holds every way a method can refuse to that rule.

Registration-free in the axis that grows: which keys are a refusal is read
from `declared_raise`, the one reading the binding's raise and both doc faces
already share (gh-869), by probing it with every key a method may carry. The
table below gives each such key the shapes it can be declared on, and a key
`declared_raise` learns without a row here fails the first test.

The `.pyi` face synthesizes no method example (only a header's ``@code``
reaches it), so the two faces agree; the last test holds that.

GATE: in the runtime doc of every method that declares a refusal, the
      example's call to it, and anything after, expects no output; a method
      that cannot refuse keeps its result.
"""

from __future__ import annotations

import ast
import doctest
import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit._context._diagnostics import declared_raise
from just_makeit._keys import METHOD_KEYS

_ARR = ("--arg-type", "float[]")
_VO = (*_ARR, "--return-type", "float", "--variable-output")

#: ``{refusal key: [(method, *its jm method flags), ...]}`` -- one row per
#: result shape the key can be declared on (the CLI refuses the rest).
REFUSING = {
    "status_return": [
        ("st", *_ARR, "--return-type", "int", "--status-return"),
    ],
    "error_negative": [
        ("neg", *_ARR, "--return-type", "int64_t", "--error-negative"),
        ("negc", *_VO, "--count-type", "int64_t", "--error-negative"),
    ],
    "error_sentinel": [
        ("sen", *_VO, "--error-sentinel", "SIZE_MAX"),
    ],
    "error_on_empty": [
        ("emp", *_VO, "--error-on-empty"),
        (
            "recs",
            *_VO,
            "--record-dtype",
            "rec_t",
            "--result-field",
            "n:int",
            "--error-on-empty",
        ),
    ],
}

#: The same shapes with no refusal: their example still states a result.
PLAIN = [
    ("plain", *_ARR, "--return-type", "int64_t"),
    ("plainvo", *_VO),
]


def _refusal_keys() -> "set[str]":
    """Every method key whose presence alone makes `declared_raise` answer."""
    return {k for k in METHOD_KEYS if declared_raise({"name": "m", k: "1"})}


def test_the_table_covers_every_refusal_declared_raise_reads():
    assert set(REFUSING) == _refusal_keys()


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    where = tmp_path_factory.mktemp("gh2232")
    r = run_cli("new", "p", "--no-c-prefix", cwd=where)
    assert r.returncode == 0, r.stdout + r.stderr
    root = where / "p"
    r = run_cli("object", "ring", "--state", "cap:size_t:8", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for name, *flags in [*(s for v in REFUSING.values() for s in v), *PLAIN]:
        r = run_cli("method", "ring", name, *flags, cwd=root)
        assert r.returncode == 0, f"{name}: {r.stdout}{r.stderr}"
    return root


_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _runtime_examples(root: Path, method: str) -> "list[doctest.Example]":
    """The doctest examples in *method*'s ``PyMethodDef`` doc literal."""
    ext_c = (root / "native" / "src" / "ring" / "ring_ext.c").read_text(
        encoding="utf-8"
    )
    start = ext_c.index(f'{{"{method}",')
    lits = []
    for raw in ext_c[start:].splitlines()[1:]:
        m = _C_DOC_LINE.match(raw)
        if not m:
            break
        lits.append(m.group(1))
    doc = "".join(lits).encode().decode("unicode_escape")
    return doctest.DocTestParser().get_examples(doc)


def _from_the_call(root: Path, method: str) -> "list[doctest.Example]":
    """The example that calls *method*, and every one after it."""
    exs = _runtime_examples(root, method)
    at = [i for i, ex in enumerate(exs) if f"obj.{method}(" in ex.source]
    assert at, f"{method}: no example calls it"
    return exs[at[0] :]


@pytest.mark.parametrize(
    "key,method",
    [(k, s[0]) for k, v in sorted(REFUSING.items()) for s in v],
)
def test_a_refusing_method_claims_no_result(project, key, method):
    claimed = [
        f"{ex.source.strip()} -> {ex.want.strip()}"
        for ex in _from_the_call(project, method)
        if ex.want
    ]
    assert claimed == [], (
        f"{method} declares {key}, so its kernel may refuse the example's "
        f"input; the example still claims a result: {claimed}"
    )


@pytest.mark.parametrize("method", [s[0] for s in PLAIN])
def test_a_method_that_cannot_refuse_keeps_its_result(project, method):
    assert any(ex.want for ex in _from_the_call(project, method)), method


def test_the_stub_face_claims_no_result_either(project):
    pyi = (project / "src" / "p" / "ring.pyi").read_text(encoding="utf-8")
    cls = next(
        n
        for n in ast.parse(pyi).body
        if isinstance(n, ast.ClassDef) and n.name == "Ring"
    )
    docs = {
        fn.name: ast.get_docstring(fn) or ""
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef)
    }
    parser = doctest.DocTestParser()
    for rows in REFUSING.values():
        for name, *_ in rows:
            assert name in docs, name
            assert not [
                ex for ex in parser.get_examples(docs[name]) if ex.want
            ], name
