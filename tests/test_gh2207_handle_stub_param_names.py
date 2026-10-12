"""gh-2207: a handle method's ``.pyi`` names its parameters as the binding does.

The stub built shape (b)'s signature as ``self, x: <array>, <scalars>``:
``x`` whatever the manifest declared. The binding's ``kwlist`` takes the
declared name, so for doppler's ``StreamSink.send(iq, fs, fc)`` the stub
said ``send(self, x, fs, fc)``: ``send(x=...)`` raised ``TypeError`` and a
type checker refused ``send(iq=...)``, the call that works. Shape (c) did the
same with ``n``.

The gate does not list shapes. It builds every method the keys
``_emit_method`` dispatches on can form -- each ordering of up to three
argument rows drawn from the row kinds, against each return kind, with and
without ``out_len_fn`` and ``status_return`` -- renders each one jm accepts,
and asks the two artefacts:

- where the binding takes keywords, the stub's parameter names ARE its
  ``kwlist``, in order;
- everywhere, the stub names only what the manifest declares, so no
  parameter can be called something the author never wrote.

A shape added along these keys is reached without an edit here, and the
armed check fails if the walk stops reaching the array shapes.
"""

from __future__ import annotations

import ast
import itertools
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle  # noqa: E402

#: Argument row kinds, by what `_emit_method` dispatches on: an array, the
#: caller's writable buffer, and a scalar with and without a default. Names
#: are assigned per position, and none is a name jm ever hard-coded.
_ROWS = {
    "array": {"type": "float[]"},
    "out": {"type": "float[]", "writable": True},
    "scalar": {"type": "double"},
    "default": {"type": "float", "default": "1.0f"},
}
_NAMES = ("zqa", "zqb", "zqc")
_RETURNS = (None, "int", "float[]", "bytes")

_BASE = {
    "kind": "handle",
    "backing": "h",
    "header": "h/h.h",
    "type_name": "H",
    "create_fn": "h_open",
    "close_fn": "h_close",
}


def _methods():
    """Every method the keys form, but a required scalar after a defaulted
    one, which no Python signature can spell."""
    for n in range(len(_NAMES) + 1):
        for kinds in itertools.product(_ROWS, repeat=n):
            if (
                "default" in kinds
                and "scalar" in kinds[kinds.index("default") :]
            ):
                continue
            args = [
                {"name": _NAMES[i], **_ROWS[k]} for i, k in enumerate(kinds)
            ]
            for returns, out_len, status in itertools.product(
                _RETURNS, (False, True), (False, True)
            ):
                m = {"name": "op", "fn": "h_op", "args": args}
                if returns:
                    m["returns"] = returns
                if out_len:
                    m["out_len_fn"] = "h_len"
                if status:
                    m["status_return"] = True
                ident = (
                    f"({', '.join(kinds)}) -> {returns}"
                    + (" +out_len_fn" if out_len else "")
                    + (" +status_return" if status else "")
                )
                yield ident, m


@pytest.fixture(scope="module")
def cases():
    """``(id, method, wrapper C, stub parameter names)`` for each method jm
    accepts. A fixture rather than parametrize, so a worker that runs none
    of these tests renders nothing."""
    out = []
    for ident, m in _methods():
        cfg = {
            "project": {"name": "p"},
            "module": {"h": {**_BASE, "methods": [m]}},
        }
        try:
            ext = _handle.render_ext(cfg, "h")
            pyi = _handle.render_pyi(cfg, "h")
        except (ValueError, NotImplementedError):
            continue  # refused: no binding, so nothing to disagree with
        body = ext.split("\nH_op(")[1].split("\nstatic ")[0]
        try:
            tree = ast.parse(pyi)
        except SyntaxError as exc:
            # A stub that does not parse names nothing; say so in the place
            # a name would go, so every check below fails on it by name.
            out.append((ident, m, body, [f"<the .pyi does not parse: {exc}>"]))
            continue
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "op"
        )
        names = [a.arg for a in fn.args.args[1:]]
        out.append((ident, m, body, names))
    return out


def _kwlist(body: str) -> "list[str] | None":
    """The binding's keyword names, in order; None for a positional parse."""
    found = re.search(r"static char \*kwlist\[\] = \{(.*?), NULL\};", body)
    if found is None:
        return None
    return re.findall(r'"(\w+)"', found.group(1))


def _has_array_arg(m: dict) -> bool:
    return any(a["type"].endswith("[]") for a in m["args"])


def test_the_walk_reaches_the_array_shapes(cases):
    """Armed: shapes (b) and (d), each taking keywords, and a case whose
    array is declared after a scalar, where the Python order is not the
    manifest's -- the case a comparison of names alone would miss."""
    kw = [
        (m, _kwlist(body))
        for _, m, body, _ in cases
        if _has_array_arg(m) and _kwlist(body)
    ]
    out = [m for m, _ in kw if any(a.get("writable") for a in m["args"])]
    plain = [m for m, _ in kw if m not in out]
    assert out and plain, (len(out), len(plain))
    reordered = [
        m for m, names in kw if names != [a["name"] for a in m["args"]]
    ]
    assert reordered, "no case puts an array after a scalar"


def test_a_keyword_binding_and_its_stub_name_the_same_parameters(cases):
    """The stub's parameters are the `kwlist`, name for name, in order."""
    wrong = [
        f"{ident}: the stub says {names}, the binding takes {_kwlist(body)}"
        for ident, _m, body, names in cases
        if _kwlist(body) is not None and names != _kwlist(body)
    ]
    assert not wrong, "\n".join(wrong)


def _misnamed(m: dict, names: "list[str]") -> bool:
    """Whether the stub names a parameter the manifest did not, or (with an
    array argument) leaves a declared one out."""
    declared = [a["name"] for a in m["args"]]
    if _has_array_arg(m):
        return sorted(names) != sorted(declared)
    # A count-in shape takes one count, `n` when none is declared.
    return bool(declared) and not set(names) <= set(declared)


def test_the_stub_names_only_what_the_manifest_declares(cases):
    """No parameter is called something the author never wrote: on a
    positional shape the names reach no keyword, but they still document
    the call, beside a runtime ``__doc__`` that uses the declared ones."""
    wrong = [
        f"{ident}: the stub says {names}, the manifest declares"
        f" {[a['name'] for a in m['args']]}"
        for ident, m, _body, names in cases
        if _misnamed(m, names)
    ]
    assert not wrong, "\n".join(wrong)
