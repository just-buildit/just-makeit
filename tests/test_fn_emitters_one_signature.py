"""Every C function emitter renders one signature per shape.

A module function's C signature is rendered by three emitters: the
prototype (`fn_c_decl`), the out-of-line stub (`fn_c_stub`) and the header
stub for `--inline` (`fn_c_inline_stub`). The binding calls the function the
same way whichever stub holds the body, so all three must agree on every
shape. The inline stub used to be a separate rendering that knew only the
scalar shape: `jm function --inline --out-type` scaffolded
`mag(const float _Complex *x, size_t x_len)` while the binding called
`mag(x, x_len, out)`, and the feature tour's Step 6 did not compile.

The emitters and their shape keywords are enumerated from `_render`, as in
test_gh1072_void_param_list, so a new emitter or shape is covered without an
edit here; a shape keyword with no value there fails that file by name.

GATE: fn_c_decl, fn_c_stub and fn_c_inline_stub agree on every shape.
"""

from __future__ import annotations

import re

import pytest

from just_makeit import _render
from test_gh1072_void_param_list import _combos, _emitters

#: Parameter lists that exercise each branch: none, scalar, array, both.
PARAMS = {
    "none": [],
    "scalar": [("n", "int")],
    "array": [("x", "float _Complex[]")],
    "array+scalar": [("x", "float[]"), ("k", "double")],
}

_SIG = re.compile(
    r"^(?:static inline )?(?P<ret>[^\n(]+?)\s*\n?\s*(?P<name>\w+)\((?P<args>[^)]*)\)",
    re.M,
)


def _signature(text: str) -> "tuple[str, str, str]":
    """``(return type, name, parameter list)`` of the one function in *text*."""
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("/*"))
    m = _SIG.search(body)
    assert m, f"no C signature in:\n{text}"
    return (" ".join(m["ret"].split()), m["name"], " ".join(m["args"].split()))


def test_the_three_emitters_are_the_ones_compared():
    assert {"fn_c_decl", "fn_c_stub", "fn_c_inline_stub"} <= set(_emitters())


@pytest.mark.parametrize("plist", sorted(PARAMS))
@pytest.mark.parametrize(
    "shape", list(_combos(_render.fn_c_decl)), ids=lambda kw: repr(kw)
)
def test_every_emitter_renders_the_prototypes_signature(plist, shape):
    args = ("f", PARAMS[plist], "int")
    want = _signature(_render.fn_c_decl(*args, **shape))
    for name in ("fn_c_stub", "fn_c_inline_stub"):
        got = _signature(getattr(_render, name)(*args, **shape))
        assert got == want, f"{name} {shape} {plist}: {got} != {want}"


def test_the_inline_stub_is_static_inline():
    text = _render.fn_c_inline_stub(
        "f", PARAMS["array"], "void", out_type="float"
    )
    assert "\nstatic inline void\nf(" in text
