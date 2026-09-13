"""gh-1272: a pseudo-type must work on every face that can carry a param.

`path` and `bytes` are deliberately absent from `_CTYPE_META` -- they name a
Python-side *coercion*, not a C type. `_types.PSEUDO_TYPES` is the SSOT for
the pair, and its own comment already scoped it to

    component ``init_params`` and the ``params`` / ``arg_type`` of methods and
    module functions

so `_config._usable_ctype` has been answering *"yes, a binding exists for
this"* on all three faces. The renderers had not caught up:

======  ============  ==============  ================
type    init_params   method params   module functions
======  ============  ==============  ================
path    yes (gh-515)  **KeyError**    yes (gh-353)
bytes   yes (gh-565)  **KeyError**    **KeyError**
======  ============  ==============  ================

Validation said yes and rendering raised a bare ``KeyError: 'path'`` from
inside `_CTYPE_META`, in a stack frame deep in the renderer -- the same
traceback-where-a-diagnostic-belongs shape gh-1021 fixed for ``enum:``. Only
the `path`/method cell was reported; `bytes` was missing on two faces and
nobody had tried it.

**Why one cell became four.** `T.c_param_parts` is documented as *"the one
place that knows how a declared param becomes C"* -- and four sites in
`_method.py` re-implemented it inline. A pseudo-type reaching one of those
copies declared ``path meta;`` in the sacred ``_core.h``, which is not C and
does not compile, and the header is the half the author's ``_core.c`` is
written against. The copies are gone; the pseudo-types arrived on every site
at once as a result, which is the argument for routing rather than patching.

**And two `import os` deciders.** Both enumerated the surfaces that can carry
a path rather than reading the rendered annotation, so both missed the new
one -- the stub then wrote ``meta_path: str | os.PathLike`` with no import.
Both ask the rendered text now, where the annotation is the only thing that
can need the import. `_context/_state.py`'s comment had claimed the general
derivation (*"so a new path shape cannot forget the import"*) while seeing
only the constructor; gh-1272 is the new path shape that it forgot.

The parametrisation is over `PSEUDO_TYPES` itself, so a third pseudo-type is
covered on the day it is added rather than the day someone remembers.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _coerce  # noqa: E402
from just_makeit import _types as T  # noqa: E402

#: The Python annotation each pseudo-type must present. Keyed off the same
#: frozenset the product reads, so a new member fails here as a KeyError in
#: the test rather than silently going unchecked.
_PY_ANNOTATION = {
    "path": _coerce.PATH_PY_TYPE,
    "bytes": "bytes",
}

#: What the C prototype must NOT contain: the pseudo-type as a bare C token.
#: `int f(state_t *s, path meta)` does not compile.
_BAD_C = {t: f"{t} " for t in T.PSEUDO_TYPES}


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


def test_every_pseudo_type_has_an_expected_annotation():
    """The tables below are keyed off the product's own set.

    A pseudo-type added to `PSEUDO_TYPES` with no entry here would otherwise
    be parametrised over an empty intersection and prove nothing.
    """
    assert set(_PY_ANNOTATION) == set(T.PSEUDO_TYPES)


class TestTheCExpansionKnowsThem:
    """`c_param_parts` is "the one place that knows how a param becomes C"."""

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_no_pseudo_type_survives_into_c(self, ptype):
        parts = T.c_param_parts([("p", ptype)])
        assert parts
        for part in parts:
            assert not part.startswith(f"{ptype} "), parts

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_the_names_match_the_declarations(self, ptype):
        """`c_param_names` must expand in lockstep, or a stub body's
        `(void)n;` list stops matching its own signature and the scaffold
        fails to compile on an unused-parameter warning."""
        assert len(T.c_param_names([("p", ptype)])) == len(
            T.c_param_parts([("p", ptype)])
        )

    def test_bytes_expands_to_a_pair(self):
        """Like an array: the borrowed buffer and its length."""
        assert T.c_param_parts([("blob", "bytes")]) == [
            "const void * blob",
            "size_t blob_len",
        ]
        assert T.c_param_names([("blob", "bytes")]) == ["blob", "blob_len"]


class TestTheAnnotationKnowsThem:
    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_both_annotation_peers_agree(self, ptype):
        """`_stubs._py` knew both and `scalar_py_annotation` said `Any`.

        Two answers to one question, and the method face read the wrong one --
        so a parameter jm marshals with `PyUnicode_FSConverter` was documented
        as accepting anything at all.
        """
        from just_makeit._stubs import _py

        want = _PY_ANNOTATION[ptype]
        assert _py(ptype) == want
        assert T.scalar_py_annotation(ptype) == want


def _method_project(
    tmp: Path, ptype: str, extra: str = "", return_type: str = "void"
) -> Path:
    assert _cli("new", "z", cwd=tmp).returncode == 0
    root = tmp / "z"
    assert _cli("object", "o", "--state", "n:int:0", cwd=root).returncode == 0
    frag = root / "objects" / "o.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + f'\n[[o.methods]]\nname = "fin"\narg_type = "void"\n'
        f'return_type = "{return_type}"\n{extra}'
        f'params = [{{ name = "p", type = "{ptype}" }}]\n',
        encoding="utf-8",
    )
    r = _cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _function_project(tmp: Path, ptype: str) -> Path:
    assert _cli("new", "z", cwd=tmp).returncode == 0
    root = tmp / "z"
    assert _cli("module", "m", cwd=root).returncode == 0
    frag = root / "modules" / "m.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8")
        + f'\n[[module.m.functions]]\nname = "fin"\nreturn_type = "void"\n'
        f'params = [{{ name = "p", type = "{ptype}" }}]\n',
        encoding="utf-8",
    )
    r = _cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


class TestTheMethodFace:
    """The reported cell. `KeyError: 'path'` in `_build_params_parse`."""

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_it_renders_at_all(self, tmp_path, ptype):
        _method_project(tmp_path, ptype)

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_the_sacred_header_declares_real_c(self, tmp_path, ptype):
        """The header is what the author's `_core.c` is written against.

        `int o_fin(o_state_t *state, path p);` does not compile, and jm put
        it in the one file it must never get wrong.
        """
        root = _method_project(tmp_path, ptype)
        proto = [
            ln
            for ln in (root / "native" / "inc" / "o" / "o_core.h")
            .read_text(encoding="utf-8")
            .splitlines()
            if "o_fin(" in ln
        ]
        assert proto, "no prototype emitted"
        assert _BAD_C[ptype] not in proto[0], proto

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_the_stub_annotates_and_resolves(self, tmp_path, ptype):
        root = _method_project(tmp_path, ptype)
        pyi = (root / "src" / "z" / "o.pyi").read_text(encoding="utf-8")
        ast.parse(pyi)
        assert f"p: {_PY_ANNOTATION[ptype]}" in pyi, pyi
        # gh-1272: and the name it uses must be imported. `os.PathLike` with
        # no `import os` is an undefined name -- a working annotation on
        # paper and a broken stub in a type checker.
        if "os." in _PY_ANNOTATION[ptype]:
            assert "\nimport os" in pyi, pyi

    def test_the_path_borrow_is_released_after_the_call(self, tmp_path):
        """gh-219's rule, and the one thing that differs per face.

        The callee copies the string; the borrow is released after the call
        returns, not before it. Released too early and the C reads freed
        memory -- which is why this is asserted as an ORDER, not a presence.
        """
        root = _method_project(tmp_path, "path")
        ext = (root / "native" / "src" / "o" / "o_ext.c").read_text(
            encoding="utf-8"
        )
        body = ext[ext.index("O_fin(") if "O_fin(" in ext else 0 :]
        call = body.index("o_fin(self->handle")
        release = body.index("Py_XDECREF(p);")
        assert call < release, body[:600]

    def test_bytes_takes_no_release(self, tmp_path):
        """`y#` borrows the object's buffer; it creates no reference.

        A `Py_XDECREF` here would be a decref of something never increfed.
        """
        root = _method_project(tmp_path, "bytes")
        ext = (root / "native" / "src" / "o" / "o_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Py_XDECREF(p);" not in ext


class TestTheModuleFunctionFace:
    """`path` worked here and `bytes` did not — one arm, two pseudo-types."""

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_it_renders_at_all(self, tmp_path, ptype):
        _function_project(tmp_path, ptype)

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    def test_the_stub_annotates_and_resolves(self, tmp_path, ptype):
        root = _function_project(tmp_path, ptype)
        pyi = (root / "src" / "z" / "m" / "m.pyi").read_text(encoding="utf-8")
        ast.parse(pyi)
        assert f"p: {_PY_ANNOTATION[ptype]}" in pyi, pyi
        if "os." in _PY_ANNOTATION[ptype]:
            assert "\nimport os" in pyi, pyi


class TestEveryPrototypeBranch:
    """Four sites re-implemented the C expansion; four branches emit one.

    Parametrised over the method SHAPES that select different branches of
    `_build_method_prototype`, because a pseudo-type reaching the branch
    nobody exercised is exactly how this arrived: the cell that was reported
    was the plain shape, and the copies behind `variable_output` and
    `single` were no better.
    """

    _SHAPES = {
        "plain": "",
        "variable_output": "variable_output = true\n",
        "single": "single = true\n",
    }

    @pytest.mark.parametrize("ptype", sorted(T.PSEUDO_TYPES))
    @pytest.mark.parametrize("shape", sorted(_SHAPES))
    def test_no_pseudo_type_reaches_the_header(self, tmp_path, ptype, shape):
        extra, rt = self._SHAPES[shape], "void"
        if shape == "single":
            # A `single` record needs a struct return and its fields. The
            # point here is the PARAMS, not the record -- the struct is the
            # author's, named in the sacred header, so jm only has to spell
            # it back.
            extra = (
                "single = true\n"
                '[[o.methods.result_fields]]\nname = "a"\ntype = "uint64_t"\n'
            )
            rt = "o_rec_t"
        if shape == "variable_output":
            extra = 'variable_output = true\nout_type = "double"\n'
        root = _method_project(tmp_path, ptype, extra=extra, return_type=rt)
        header = (root / "native" / "inc" / "o" / "o_core.h").read_text(
            encoding="utf-8"
        )
        proto = [ln for ln in header.splitlines() if "o_fin(" in ln]
        assert proto, header
        assert _BAD_C[ptype] not in proto[0], proto


class TestTheStubBodySuppressesWhatItDeclares:
    """The fourth copy also derived the `(void)n;` list beside the signature.

    Two lists built by two loops from one declaration is the shape that
    drifts: a `bytes` param declares `blob` and `blob_len` and a suppression
    list built from the DECLARED params alone names only `blob`, leaving one
    parameter unsuppressed. Derived from the rendered signature here, so the
    check cannot agree with a second wrong copy.

    Reached through `jm method`, because the manifest route does not write a
    body at all -- that is gh-1294, filed separately, and it is why the
    pseudo-types cannot be exercised here: the CLI's `--param` accepts only
    `_CTYPE_META` scalars and arrays, consistently on every face.
    """

    def test_every_declared_parameter_is_suppressed(self, tmp_path):
        assert _cli("new", "z", cwd=tmp_path).returncode == 0
        root = tmp_path / "z"
        assert (
            _cli("object", "o", "--state", "n:int:0", cwd=root).returncode == 0
        )
        r = _cli(
            "method",
            "o",
            "fin",
            "--param",
            "taps:float[]",
            "--param",
            "q:size_t",
            "--return-type",
            "void",
            "--arg-type",
            "void",
            cwd=root,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        src = (root / "native" / "src" / "o" / "o_core.c").read_text(
            encoding="utf-8"
        )
        i = src.index("o_fin(")
        sig = src[i : src.index(")", i)]
        body = src[src.index("{", i) : src.index("}", i)]
        # Every identifier the signature declares, read off the signature
        # rather than off the manifest.
        names = [
            part.rsplit(None, 1)[-1].lstrip("*")
            for part in sig[sig.index("(") + 1 :].split(",")
        ]
        assert "taps_len" in names, sig
        missing = [n for n in names if f"(void){n};" not in body]
        assert not missing, f"unsuppressed: {missing}\nsig: {sig}\n{body}"
