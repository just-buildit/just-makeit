"""gh-2222: a method's docs offer ``out=`` exactly where it takes one.

A ``variable_output`` method may take an optional ``out=`` buffer, and
`_outbuf.enabled` is the one predicate deciding which do -- the binding's
``_kwlist`` and both ``.pyi`` writers read it (gh-1079). The prose about
``out=`` did not:

* the runtime ``__doc__`` summary ended "Pass out= to write into your own
  buffer instead." on every ``variable_output`` shape, ``multi_output``
  included, which binds as ``METH_VARARGS`` with no ``out=``;
* the synthesized ``count`` argument's description told the reader to
  "size an `out=` buffer with the matching `_max_out()`" on a
  ``multi_output`` generator, which has neither, on all three faces;
* a ``record_dtype`` method takes ``out=`` (gh-805 section E) but its
  summary was written when it did not, and a comment beside it still said
  so;
* a count-driven generator's runtime signature line read ``run(count=1)``
  above a binding that takes ``out=``.

Now every one of those reads the same predicate the binding does.

The shapes are the cross product of what `_outbuf.why_not` reads -- an
input or not, each kind of params, one output or several -- plus a
structured result, so a shape is a case without being listed. Each is
rendered on all three faces (the module-aggregated ``.pyi``, the
standalone ``.pyi``, the binding's runtime ``__doc__``).

GATE: on every face, a method's doc mentions ``out=`` exactly when the
binding takes ``out=``, and the runtime signature line names ``out``
exactly then too.
"""

from __future__ import annotations

import ast
import contextlib
import io
import itertools
import re
from pathlib import Path

import pytest

from just_makeit import _outbuf
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

#: What `_outbuf.why_not` reads besides `variable_output`, each value it
#: distinguishes: the input, the params, the extra outputs.
INPUTS = ["void", "float"]
PARAMS = [
    [],
    [("n", "size_t")],
    [("x", "float[]")],
    [("x", "float[]"), ("mu", "double")],
]
MULTI = [[], ["float"]]

REC = "rr_rec_t"
FIELDS = [{"name": "n", "type": "uint64_t"}, {"name": "v", "type": "float"}]


def _shapes() -> "list[tuple[str, dict]]":
    out = []
    for arg, params, multi in itertools.product(INPUTS, PARAMS, MULTI):
        label = f"{arg}-{'+'.join(t for _, t in params) or 'none'}-" + (
            "multi" if multi else "single"
        )
        out.append(
            (label, dict(arg_type=arg, params=params, multi_output=multi))
        )
    # A structured result: `out=` since gh-805 section E.
    out.append(
        (
            "record-generator",
            dict(
                arg_type="void",
                params=[],
                multi_output=[],
                result_fields=[dict(f) for f in FIELDS],
                record_dtype=REC,
            ),
        )
    )
    return out


SHAPES = _shapes()


def _takes_out(kw: dict) -> bool:
    return _outbuf.enabled(
        variable_output=True,
        multi_output=bool(kw["multi_output"]),
        has_arg=kw["arg_type"] != "void",
        params=[{"name": n, "type": t} for n, t in kw["params"]],
    )


def test_the_shapes_cover_both_answers():
    """Armed: a matrix that never answers False would pass on a constant."""
    assert {_takes_out(kw) for _, kw in SHAPES} == {True, False}


def _project(root: Path, module: "str | None", kw: dict) -> Path:
    kw = dict(kw)
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("rr", root, c_prefix=None)
        if module:
            from just_makeit._module import run as module_run

            module_run(root, module)
        object_run(
            root,
            "amp",
            module=module,
            arg_type="float",
            return_type="float",
            state_vars=[("g", "double", "1.0")],
        )
        method_run(
            root,
            "amp",
            "run",
            module,
            kw.pop("arg_type"),
            "float",
            True,
            kw.pop("multi_output"),
            params=list(kw.pop("params")),
            **kw,
        )
    return root


def _runtime_docs(ext: str) -> "dict[str, str]":
    """``{method: __doc__}`` for each PyMethodDef row in *ext*."""
    docs = {}
    row = re.compile(
        r'\{\s*"(\w+)",\s*\(PyCFunction\)[^,]+,\s*[A-Z_| ]+,'
        r'((?:\s*"(?:[^"\\]|\\.)*")+)'
    )
    for m in row.finditer(ext):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        docs[m.group(1)] = ast.literal_eval('"' + "".join(parts) + '"')
    return docs


def _binding_takes_out(ext: str) -> bool:
    i = ext.index("\nAmp_run(")
    return '"out"' in ext[i : ext.index("\n}\n", i)]


def _pyi_methods(pyi: str) -> "dict[str, tuple[str, str]]":
    """``{method: (signature, docstring)}`` for the ``Amp`` class."""
    tree = ast.parse(pyi)
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Amp"
    )
    out = {}
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            sig = ", ".join(a.arg for a in fn.args.args + fn.args.kwonlyargs)
            out[fn.name] = (sig, ast.get_docstring(fn) or "")
    return out


def _mentions_out(doc: str) -> bool:
    """Prose offering the ``out=`` buffer, in any spelling jm uses."""
    return re.search(r"\bout=|^out : ", doc, re.M) is not None


@pytest.mark.parametrize("label,kw", SHAPES, ids=[s[0] for s in SHAPES])
def test_every_face_offers_out_exactly_when_it_is_taken(tmp_path, label, kw):
    want = _takes_out(kw)
    problems = []
    for module in ("dsp", None):
        root = _project(tmp_path / (module or "standalone"), module, kw)
        if module:
            ext = (root / "native/src/dsp/dsp_ext_amp.c").read_text(
                encoding="utf-8"
            )
            pyi = (root / "src/rr/dsp/dsp.pyi").read_text(encoding="utf-8")
        else:
            ext = (root / "native/src/amp/amp_ext.c").read_text(
                encoding="utf-8"
            )
            pyi = (root / "src/rr/amp.pyi").read_text(encoding="utf-8")
        face = module or "standalone"
        if _binding_takes_out(ext) != want:
            problems.append(f"{face}: binding takes out={not want}")
        docs = _runtime_docs(ext)
        sig_line = docs["run"].split("\n", 1)[0]
        if (re.search(r"\bout\b", sig_line) is not None) != want:
            problems.append(f"{face}: runtime signature {sig_line!r}")
        methods = _pyi_methods(pyi)
        if ("out" in methods["run"][0].split(", ")) != want:
            problems.append(f"{face}: .pyi signature {methods['run'][0]!r}")
        # Every doc of the method's family: the method itself and its
        # `_max_out` sibling, on the runtime face and the stub.
        for name in ("run", "run_max_out"):
            for where, doc in (
                ("runtime", docs.get(name)),
                (".pyi", methods.get(name, (None, None))[1]),
            ):
                if doc is not None and _mentions_out(doc) != want:
                    problems.append(
                        f"{face} {where} {name}: mentions out= is "
                        f"{not want}:\n{doc}"
                    )
    assert not problems, f"{label}:\n" + "\n".join(problems)
