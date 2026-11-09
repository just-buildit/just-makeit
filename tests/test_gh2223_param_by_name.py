"""A header ``@param`` documents a Python argument by NAME only (gh-2223).

`_docstring.render_numpy_method_doc` used to match the header's ``@param``
entries to the Python arguments by name, then zip the LEFTOVERS together by
position whenever their counts agreed. That is how ``@param in`` (jm's C
name for the block input) reached the Python ``x`` -- and also how,
documenting C-only parameters (``out1``, ``max_out``, ``status``, ``why``),
``x`` read "Output buffer." and ``L`` read "Second output." the moment the
counts happened to line up. Separately, `parse_doxygen_block` dropped
Doxygen's comma-grouped ``@param a, b, c  Desc.`` outright, which is what
left the real arguments as leftovers in doppler's header.

So the fix is two halves, each sabotaged separately:

* the match -- a Python argument takes the ``@param`` of its own name, or of
  the C name jm gives it (`_docstring.C_PARAM_NAMES`: ``x`` is ``in``), and
  nothing else; no positional fallback;
* the parse -- a comma group, with or without spaces and after an optional
  ``[in]``, documents each name it lists with the one description.

GATE: on both ``.pyi`` producers and the runtime ``__doc__``, every argument
reads its own prose or the ``Input.`` fallback, never another ``@param``'s.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jminc import INC_ROOT  # noqa: E402
from test_gh2028_out_beside_an_input import (  # noqa: E402
    _param,
    _runtime_doc,
    _stub_doc,
)

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    C_PARAM_NAMES,
    parse_doxygen_block,
    reconcile_param_docs,
    render_numpy_method_doc,
)
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


#: doppler's header as reported on the issue: the real arguments documented
#: on one comma-grouped line, five C-only parameters documented after it.
_FOLD = """/**
 * @brief Fold.
 * @param state, x, x_len, L, g_min, g_max, rates, rates_len  As fold().
 * @param out     Output buffer.
 * @param out1    Second output.
 * @param max_out Capacity.
 * @param status  Status code.
 * @param why     Reason.
 */"""

_FOLD_ARGS = ["x", "L", "g_min", "g_max", "rates"]


def _foreign(block, own: str) -> "set[str]":
    """Every ``@param`` description in *block* other than *own*."""
    return {d for _n, d in block.params if d != own}


@pytest.mark.parametrize(
    "sep", [", ", ",", " , "], ids=["comma-space", "comma", "spaced"]
)
@pytest.mark.parametrize("direction", ["", "[in]"], ids=["plain", "dir"])
def test_a_comma_group_documents_each_name(sep, direction):
    """Every name of the group reads the shared prose, and no other."""
    raw = _FOLD.replace(
        "@param state, x, x_len, L, g_min, g_max, rates, rates_len",
        f"@param{direction} "
        + sep.join("state x x_len L g_min g_max rates rates_len".split()),
    )
    block = parse_doxygen_block(raw)
    names = [n for n, _ in block.params]
    assert names[:8] == "state x x_len L g_min g_max rates rates_len".split()
    if direction:
        assert {block.param_dirs[n] for n in names[:8]} == {"in"}
    descs = render_numpy_method_doc(block, [(n, "t") for n in _FOLD_ARGS])[2]
    assert descs == dict.fromkeys(_FOLD_ARGS, "As fold()."), descs


def test_a_comma_in_prose_is_not_a_group():
    """``@param gain Scale, roughly.`` names one parameter."""
    block = parse_doxygen_block("/**\n * @param gain Scale, roughly.\n */")
    assert block.params == [("gain", "Scale, roughly.")]


def test_a_group_wraps_onto_a_continuation_line():
    """A continuation extends the one description every name shares."""
    block = parse_doxygen_block(
        "/**\n * @param a, b  First half\n *   second half.\n */"
    )
    assert block.params == [
        ("a", "First half second half."),
        ("b", "First half second half."),
    ]


#: #2223's own shape: the C block input `in`, C-only `state` / `n_in`, and
#: one real param, documented in every order.
_SCALE_PARAMS = [
    ("state", "The state."),
    ("in", "Samples to scale."),
    ("n_in", "Sample count."),
    ("gain", "Linear scale."),
]


@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(range(len(_SCALE_PARAMS)))),
    ids=lambda o: "".join(map(str, o)),
)
def test_in_reaches_x_by_name_in_any_order(order):
    lines = "".join(
        f" * @param {_SCALE_PARAMS[i][0]}  {_SCALE_PARAMS[i][1]}\n"
        for i in order
    )
    block = parse_doxygen_block(f"/**\n * @brief Scale.\n{lines} */")
    py = [("x", "ndarray"), ("gain", "float"), ("out", "ndarray")]
    descs = render_numpy_method_doc(block, py)[2]
    assert descs == {
        "x": "Samples to scale.",
        "gain": "Linear scale.",
        "out": "",
    }, descs


def test_the_bridge_is_jms_block_input_name():
    """`in` is the name jm's prototype gives the input `x`."""
    assert C_PARAM_NAMES == {"x": "in"}


@pytest.mark.parametrize("n_c_only", [1, 2, 3])
def test_a_c_only_param_never_documents_an_argument(n_c_only):
    """Undocumented arguments stay empty whatever the counts are.

    The old zip fired exactly when the leftover counts agreed, so this
    sweeps the C-only count across the number of undocumented arguments.
    """
    c_only = [
        ("state", "The state."),
        ("max_out", "Capacity."),
        ("why", "Reason."),
    ][:n_c_only]
    block = parse_doxygen_block(
        "/**\n * @brief Run.\n"
        + "".join(f" * @param {n}  {d}\n" for n, d in c_only)
        + " */"
    )
    for k in (1, 2, 3):
        py = [(f"a{i}", "float") for i in range(k)]
        descs = render_numpy_method_doc(block, py)[2]
        assert descs == {n: "" for n, _ in py}, (k, descs)


def test_reconcile_keeps_a_group_once():
    """`reconcile_param_docs` reads the same grammar (the create() peer)."""
    block = (
        "/**\n * @brief Make.\n * @param[in] a,b  Bounds.\n"
        " * @param gone  Old.\n */"
    )
    decl = "s_t *s_create(int a, int b, int c, int d, int e);"
    out = reconcile_param_docs(block, decl)
    assert out.count("@param[in] a,b  Bounds.") == 1, out
    assert "@param a\n" not in out and "@param b\n" not in out, out
    assert "@param gone" not in out, out
    # Every new name arrives bare, in signature order, each exactly once.
    bare = [ln for ln in out.splitlines() if ln.startswith(" * @param")]
    assert bare == [
        " * @param[in] a,b  Bounds.",
        " * @param c",
        " * @param d",
        " * @param e",
    ], out


# -- generated: both stubs and the runtime literal ---------------------------

#: Per method: its declared params and the authored block body. The input is
#: `float[]`, so the Python `x` is the C `in`.
_AUTHORED = {
    # The issue's report: the grouped line, then C-only lines whose count
    # matches the real arguments', with and without spaces.
    "fold": (
        [("L", "int"), ("g_min", "double"), ("g_max", "double")],
        " * @brief Fold.\n"
        " * @param state, x, x_len, L, g_min, g_max  As fold().\n"
        " * @param out1    Second output.\n"
        " * @param max_out Capacity.\n"
        " * @param status  Status code.\n"
        " * @param why     Reason.",
    ),
    "fold_tight": (
        [("L", "int"), ("g_min", "double"), ("g_max", "double")],
        " * @brief Fold.\n"
        " * @param state,in,n_in,L,g_min,g_max  As fold().\n"
        " * @param out1    Second output.\n"
        " * @param max_out Capacity.\n"
        " * @param status  Status code.\n"
        " * @param why     Reason.",
    ),
    # #2223: `in` documented among C-only params, out of order.
    "scale": (
        [("gain", "double")],
        " * @brief Scale.\n"
        " * @param gain  Linear scale.\n"
        " * @param n_in  Sample count.\n"
        " * @param in    Samples to scale.\n"
        " * @param state The state.",
    ),
    # The input undocumented, one C-only param beside it: one leftover each.
    "bare": (
        [("gain", "double")],
        " * @brief Bare.\n"
        " * @param state The state.\n"
        " * @param gain  Linear scale.",
    ),
}

#: What each argument must read on every face; `Input.` is the fallback.
_WANT = {
    "fold": {
        "x": "As fold().",
        "L": "As fold().",
        "g_min": "As fold().",
        "g_max": "As fold().",
    },
    "fold_tight": {
        "x": "As fold().",
        "L": "As fold().",
        "g_min": "As fold().",
        "g_max": "As fold().",
    },
    "scale": {"x": "Samples to scale.", "gain": "Linear scale."},
    "bare": {"x": "Input.", "gain": "Linear scale."},
}

#: (face, module, the .pyi, the binding's C file) -- the standalone stub is
#: `_context/_methods`'s, the module-aggregated one is `_stubs`'.
_FACES = [
    ("standalone", None, "src/demo/amp.pyi", "native/src/amp/amp_ext.c"),
    ("module", "dsp", "src/demo/dsp/dsp.pyi", "native/src/dsp/dsp_ext_amp.c"),
]


def _authored_project(tmp_path: Path, module: "str | None") -> Path:
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root, c_prefix=None)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "amp",
        module,
        state_vars=[("g", "double", "1.0")],
        arg_type="void",
        return_type="float",
    )
    for name, (params, _) in _AUTHORED.items():
        _quiet(
            method_run,
            root,
            "amp",
            name,
            module,
            "float[]",
            "float",
            True,
            [],
            params=params,
        )
    header = root / INC_ROOT / "amp" / "amp_core.h"
    text = header.read_text(encoding="utf-8")
    for name, (_, block) in _AUTHORED.items():
        seed = f" * @brief {name}."
        assert text.count(seed) == 1, f"the scaffold no longer seeds {seed}"
        text = text.replace(seed, block)
    header.write_text(text, encoding="utf-8")
    _quiet(apply_run, root)
    return root


@pytest.fixture(scope="module", params=_FACES, ids=[f[0] for f in _FACES])
def faces(request, tmp_path_factory):
    _, module, pyi, ext = request.param
    root = _authored_project(tmp_path_factory.mktemp("gh2223"), module)
    return {
        name: {
            "stub": _stub_doc((root / pyi).read_text("utf-8"), name),
            "runtime": _runtime_doc((root / ext).read_text("utf-8"), name),
        }
        for name in _AUTHORED
    }


@pytest.mark.parametrize("face", ["stub", "runtime"])
@pytest.mark.parametrize("method", sorted(_AUTHORED))
def test_each_argument_reads_its_own_param(faces, method, face):
    """Its own prose or the fallback -- never another `@param`'s."""
    doc = faces[method][face]
    got = {arg: _param(doc, arg) for arg in _WANT[method]}
    assert got == _WANT[method], doc
