"""A method's params reach its kernel, in every shape (gh-1960, gh-1961, gh-2001).

Three method shapes read a declared ``param`` differently from their siblings:

* **gh-1961** -- a list-of-records method (``result_fields``, neither
  ``single`` nor ``variable_output``). The prototype took the param; the
  binding never parsed it and called the kernel without it, and so did the
  generated benchmark. Neither compiled.
* **gh-1960** -- a ``variable_output`` method with an input AND a param. The
  prototype, the ``_core.c`` stub and the binding all dropped the param while
  both ``.pyi`` writers and the runtime doc advertised it: a positional one
  was refused, a keyword one accepted and discarded, ``kwds`` never read.
* **gh-2001** -- a ``variable_output`` method's runtime doc example passed one
  argument whatever the params: ``obj.delay(np.zeros(4))`` for
  ``delay(x, mu)``, a ``TypeError`` the moment it runs.

The single-record shape closed the same gap in gh-594 by parsing its params
through `_build_params_parse`. The two shapes above now take that route, and
both examples come from `_demo_call_args`, the one builder every other shape's
example already used. (The second shape is offered ``out=`` since gh-2028,
as its parse's trailing optional argument; tests/test_gh2028_*.py gates it.)

GATE. Every row below is a method whose kernel computes its result FROM its
params, compiled with ``-Wall -Wextra -Werror`` (binding, ``_core.c`` and
benchmark) and called:

* the ``_core.h`` prototype declares every param;
* the object builds warning-clean;
* each call returns what the kernel computed from the arguments passed;
* the ``.pyi`` and the runtime ``__doc__`` list the same arguments;
* the runtime doc's example, run up to its call, is accepted by the binding.

The rows are written out, but WHICH rows must exist is not a list kept here:
`test_every_kernel_call_is_reached_by_a_row` reads every kernel call that
`make_methods_ctx` and `_bench_method_block` spell off their source, marks
each, renders every row through the marked copy and refuses a call no row
reaches. A new shape's call is a new site, so it cannot land without a row,
and the row brings the checks above with it.

The ``batch`` shape drops its params the same way and is carried as a strict
xfail (gh-2021), so this file turns red the day it is fixed.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import sysconfig
import types
from pathlib import Path
from typing import NamedTuple

import pytest
from _compilers import default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _csym as CSYM  # noqa: E402
from just_makeit._context._methods import make_methods_ctx  # noqa: E402

_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None


class Row(NamedTuple):
    """One method: its manifest keys, a kernel that reads every param, and
    the calls that prove the kernel saw what the caller passed."""

    obj: str
    name: str
    keys: str
    impl: str
    #: ``(python expression over `o` and `np`, expected repr)``; a raise is
    #: expected as ``!<ExceptionType>``.
    calls: "tuple[tuple[str, str], ...]"


_K = 'params = [{name = "k", type = "int"}]'
_DIV = (
    'return_type = "div_t"\n'
    'result_fields = [{name = "quot", type = "int"}, '
    '{name = "rem", type = "int"}]'
)
#: `variable_output` with `max_out = 1`: every row's own length is at least
#: that, so the allocation is sized from the call itself.
_VO = 'variable_output = true\nmax_out = 1\nreturn_type = "float"'

ROWS = [
    # -- fixed output: the shapes `_build_params_parse` always served -------
    Row(
        "fixed",
        "plain",
        f'arg_type = "void"\nreturn_type = "int"\n{_K}',
        "(void)state;\n    return k + 1;",
        (("o.plain(41)", "42"), ("o.plain(k=41)", "42")),
    ),
    Row(
        "fixed",
        "scalar_in",
        f'arg_type = "float"\nreturn_type = "float"\n{_K}',
        "(void)state;\n    return x * (float)k;",
        (("o.scalar_in(1.5, 2)", "3.0"),),
    ),
    Row(
        "fixed",
        "array_in",
        f'arg_type = "float[]"\nreturn_type = "float"\n{_K}',
        "(void)state; (void)x;\n    return (float)x_len * (float)k;",
        (("o.array_in(np.zeros(3, np.float32), 2)", "6.0"),),
    ),
    Row(
        "fixed",
        "fills",
        'arg_type = "void"\nreturn_type = "void"\n'
        'params = [{name = "buf", type = "float[]", out = true}, '
        '{name = "k", type = "int"}]',
        "(void)state;\n"
        "    for (size_t i = 0; i < buf_len; i++) buf[i] = (float)k;",
        (
            (
                "[o.fills(b := np.zeros(2, np.float32), 5), b.tolist()][1]",
                "[5.0, 5.0]",
            ),
        ),
    ),
    Row(
        "fixed",
        "alloc",
        f'arg_type = "void"\nreturn_type = "void"\nout_type = "float"\n{_K}',
        "(void)state;\n    for (int i = 0; i < k; i++) out[i] = (float)k;",
        (("o.alloc(3).tolist()", "[3.0, 3.0, 3.0]"),),
    ),
    Row(
        "fixed",
        "pair",
        f'arg_type = "float"\nreturn_type = "float"\n'
        f'multi_output = ["int"]\n{_K}',
        "(void)state;\n    *out1 = k;\n    return x;",
        (("o.pair(1.5, 7)", "(1.5, 7)"),),
    ),
    Row(
        "fixed",
        "pair_void",
        f'arg_type = "void"\nreturn_type = "void"\n'
        f'multi_output = ["int"]\n{_K}',
        "(void)state;\n    *out1 = k;",
        (("o.pair_void(7)", "(None, 7)"),),
    ),
    Row(
        "fixed",
        "status",
        f'arg_type = "void"\nreturn_type = "int"\nstatus_return = true\n{_K}',
        "(void)state;\n    return k;",
        (("o.status(0)", "None"), ("o.status(3)", "!ValueError")),
    ),
    Row(
        "fixed",
        "negative",
        f'arg_type = "void"\nreturn_type = "int"\nerror_negative = true\n{_K}',
        "(void)state;\n    return k;",
        (("o.negative(5)", "5"), ("o.negative(-2)", "!ValueError")),
    ),
    Row(
        "fixed",
        "lend",
        'arg_type = "void"\nreturn_type = "float"\nborrow = true\n'
        'borrow_count = "n"\n'
        'params = [{name = "n", type = "size_t"}, {name = "k", type = "int"}]',
        "static float buf[8] = {1, 2, 3, 4, 5, 6, 7, 8};\n"
        "    (void)state;\n"
        "    return n + (size_t)k <= 8 ? buf + k : NULL;",
        (("o.lend(3, 1).tolist()", "[2.0, 3.0, 4.0]"),),
    ),
    # -- batch: drops its params too (gh-2021). The kernels do not read `k`
    # because the prototype has none; they add it when that is fixed.
    Row(
        "batch",
        "block",
        f'arg_type = "float"\nreturn_type = "float"\nbatch = true\n{_K}',
        "(void)state;\n    for (size_t i = 0; i < n; i++) out[i] = in[i];",
        (("o.block(np.ones(2, np.float32), 3).tolist()", "[4.0, 4.0]"),),
    ),
    Row(
        "batch",
        "count",
        f'arg_type = "void"\nreturn_type = "float"\nbatch = true\n{_K}',
        "(void)state;\n    for (size_t i = 0; i < n; i++) out[i] = 0;",
        (("o.count(2, 3).tolist()", "[3.0, 3.0]"),),
    ),
    # -- variable_output with an input: gh-1960 ------------------------------
    Row(
        "voin",
        "run",
        f'arg_type = "float"\n{_VO}\n{_K}',
        "(void)state;\n"
        "    for (size_t i = 0; i < n_in; i++) out[i] = in[i] + (float)k;\n"
        "    return n_in;",
        (
            ("o.run(np.ones(2, np.float32), 3).tolist()", "[4.0, 4.0]"),
            # The issue's other half: a keyword was accepted and dropped.
            ("o.run(np.ones(2, np.float32), k=3).tolist()", "[4.0, 4.0]"),
        ),
    ),
    Row(
        "voin",
        "kinds",
        f'arg_type = "float"\n{_VO}\n'
        'params = [{name = "c", type = "double[]"}, '
        '{name = "mu", type = "double", default = "0.5"}]',
        "(void)state; (void)c;\n"
        "    for (size_t i = 0; i < n_in; i++)\n"
        "        out[i] = in[i] + (float)c_len + (float)mu;\n"
        "    return n_in;",
        (
            (
                "o.kinds(np.ones(2, np.float32), np.zeros(3)).tolist()",
                "[4.5, 4.5]",
            ),
            (
                "o.kinds(np.ones(2, np.float32), c=np.zeros(3), mu=1.0)"
                ".tolist()",
                "[5.0, 5.0]",
            ),
        ),
    ),
    Row(
        "voin",
        "rows",
        f'arg_type = "float"\n{_VO}\nrecord_dtype = "div_t"\n'
        'result_fields = [{name = "quot", type = "int"}, '
        '{name = "rem", type = "int"}]\n' + _K,
        "(void)state;\n"
        "    for (size_t i = 0; i < n_in; i++) {\n"
        "        out[i].quot = (int)in[i];\n"
        "        out[i].rem = k;\n"
        "    }\n"
        "    return n_in;",
        (
            (
                "o.rows(np.ones(2, np.float32), 3).tolist()",
                "[(1, 3), (1, 3)]",
            ),
        ),
    ),
    Row(
        "voin",
        "two",
        f'arg_type = "float"\n{_VO}\nmulti_output = ["int"]\n{_K}',
        "(void)state;\n"
        "    for (size_t i = 0; i < n_in; i++) {\n"
        "        out[i] = in[i];\n"
        "        out1[i] = k;\n"
        "    }\n"
        "    return n_in;",
        (
            (
                "[a.tolist() for a in o.two(np.ones(2, np.float32), 3)]",
                "[[1.0, 1.0], [3, 3]]",
            ),
        ),
    ),
    # -- variable_output, params and no input: gh-2001's examples ------------
    Row(
        "vo",
        "scalar",
        f'arg_type = "void"\n{_VO}\n{_K}',
        "(void)state;\n    out[0] = (float)k;\n    return 1;",
        (("o.scalar(4).tolist()", "[4.0]"),),
    ),
    Row(
        "vo",
        "array",
        f'arg_type = "void"\n{_VO}\n'
        'params = [{name = "x", type = "float[]"}, {name = "k", type = "int"}]',
        "(void)state;\n"
        "    for (size_t i = 0; i < x_len; i++) out[i] = x[i] + (float)k;\n"
        "    return x_len;",
        (("o.array(np.ones(2, np.float32), 3).tolist()", "[4.0, 4.0]"),),
    ),
    # -- one record (gh-594's route), and its no-param kernels ---------------
    Row(
        "single",
        "one",
        f'arg_type = "void"\n{_DIV}\nsingle = true\nrecord_name = "One"\n{_K}',
        "(void)state;\n    div_t r = {.quot = k, .rem = 0};\n    return r;",
        (("tuple(o.one(4))", "(4, 0)"),),
    ),
    Row(
        "single",
        "one_in",
        f'arg_type = "float[]"\n{_DIV}\nsingle = true\n'
        f'record_name = "OneIn"\n{_K}',
        "(void)state; (void)in;\n"
        "    div_t r = {.quot = (int)n_in, .rem = k};\n    return r;",
        (("tuple(o.one_in(np.zeros(3, np.float32), 4))", "(3, 4)"),),
    ),
    Row(
        "single",
        "bare",
        f'arg_type = "void"\n{_DIV}\nsingle = true\nrecord_name = "Bare"',
        "(void)state;\n    div_t r = {.quot = 7, .rem = 0};\n    return r;",
        (("tuple(o.bare())", "(7, 0)"),),
    ),
    Row(
        "single",
        "bare_in",
        f'arg_type = "float[]"\n{_DIV}\nsingle = true\nrecord_name = "BareIn"',
        "(void)state; (void)in;\n"
        "    div_t r = {.quot = (int)n_in, .rem = 0};\n    return r;",
        (("tuple(o.bare_in(np.zeros(3, np.float32)))", "(3, 0)"),),
    ),
    # -- a list of records: gh-1961, and its no-param kernels ----------------
    Row(
        "rows",
        "found",
        f'arg_type = "void"\n{_DIV}\n{_K}',
        "(void)state; (void)max_results;\n"
        "    result[0].quot = k;\n    result[0].rem = 0;\n    return 1;",
        (("o.found(4)", "[(4, 0)]"), ("o.found(k=4)", "[(4, 0)]")),
    ),
    Row(
        "rows",
        "found_in",
        f'arg_type = "float"\n{_DIV}\n{_K}',
        "(void)state; (void)in; (void)max_results;\n"
        "    result[0].quot = (int)n_in;\n    result[0].rem = k;\n"
        "    return 1;",
        (("o.found_in(np.zeros(3, np.float32), 4)", "[(3, 4)]"),),
    ),
    Row(
        "rows",
        "kinds",
        f'arg_type = "void"\n{_DIV}\n'
        'params = [{name = "m", type = "int", enum = "mode"}, '
        '{name = "c", type = "double[]"}]',
        "(void)state; (void)c; (void)max_results;\n"
        "    result[0].quot = m;\n    result[0].rem = (int)c_len;\n"
        "    return 1;",
        (("o.kinds('b', np.zeros(2))", "[(1, 2)]"),),
    ),
    Row(
        "rows",
        "bare",
        f'arg_type = "void"\n{_DIV}',
        "(void)state; (void)max_results;\n"
        "    result[0].quot = 7;\n    result[0].rem = 0;\n    return 1;",
        (("o.bare()", "[(7, 0)]"),),
    ),
    Row(
        "rows",
        "bare_in",
        f'arg_type = "float"\n{_DIV}',
        "(void)state; (void)in; (void)max_results;\n"
        "    result[0].quot = (int)n_in;\n    result[0].rem = 0;\n"
        "    return 1;",
        (("o.bare_in(np.zeros(3, np.float32))", "[(3, 0)]"),),
    ),
]

#: The objects, in declaration order.
OBJECTS = list(dict.fromkeys(r.obj for r in ROWS))

#: `[[enum]]` tables a fragment declares, by object.
_ENUMS = {"rows": '[[enum]]\nname = "mode"\nvalues = ["a", "b"]\n\n'}

#: Known breakage, ratcheted: (object, method) -> {check: issue}. A strict
#: xfail, so a fix turns the check red until the entry is removed.
CARVED = {
    ("batch", name): {
        "prototype": "gh-2021: a batch method's prototype drops its params",
        "call": "gh-2021: a batch method's binding drops its params",
        "stub": "gh-1905: a standalone batch method has no .pyi member",
    }
    for name in ("block", "count")
}


def _title(obj: str) -> str:
    return "".join(p.title() for p in obj.split("_"))


def _fragment(obj: str) -> str:
    """The manifest fragment declaring *obj* and every row on it."""
    head = (
        f"{_ENUMS.get(obj, '')}[{obj}]\n"
        'arg_type = "void"\nreturn_type = "void"\n'
        'no_state = "true"\nno_step = "true"\n'
    )
    return head + "".join(
        f'\n[[{obj}.methods]]\nname = "{r.name}"\n{r.keys}\n'
        f'impl = """{r.impl}"""\n'
        for r in ROWS
        if r.obj == obj
    )


def _rows(check: str):
    """Every row as a parameter, each carve-out of *check* a strict xfail."""
    out = []
    for r in ROWS:
        why = CARVED.get((r.obj, r.name), {}).get(check)
        marks = [pytest.mark.xfail(strict=True, reason=why)] if why else []
        out.append(pytest.param(r, marks=marks, id=f"{r.obj}.{r.name}"))
    return out


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """Every object applied from its fragment, through the CLI."""
    base = tmp_path_factory.mktemp("gh1960")
    root = base / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stderr
    for obj in OBJECTS:
        frag = base / f"{obj}.toml"
        frag.write_text(_fragment(obj), encoding="utf-8")
        r = run_cli("apply", str(frag), cwd=root)
        assert r.returncode == 0, f"apply {obj}:\n{r.stdout}\n{r.stderr}"
    return root


def _method(root: Path, row: Row) -> dict:
    return next(
        m for m in C.methods(C.load(root), row.obj) if m["name"] == row.name
    )


# -- which rows must exist: every kernel call jm spells -----------------------

#: The functions that spell a call of the author's kernel: the binding and
#: the benchmark.
_KERNEL_CALLERS = ("make_methods_ctx", "_bench_method_block")
_SITE = re.compile(r"/\*@site:(\d+\.\d+)\*/")


def _is_declaration(node: ast.JoinedStr, parents: dict) -> bool:
    """A prototype jm DECLARES rather than a call it makes.

    The declarations are appended to ``decl_lines``; the one other spelling
    of ``{c_fn}(`` that is not a call is a varargs method's ``extern``.
    """
    if any(
        isinstance(v, ast.Constant) and "extern " in str(v.value)
        for v in node.values
    ):
        return True
    p = parents.get(node)
    while p is not None:
        if (
            isinstance(p, ast.Call)
            and isinstance(p.func, ast.Attribute)
            and p.func.attr == "append"
            and isinstance(p.func.value, ast.Name)
            and p.func.value.id == "decl_lines"
        ):
            return True
        p = parents.get(p)
    return False


def _marked_methods_module() -> "tuple[types.ModuleType, dict[str, str]]":
    """``_context/_methods`` with every kernel call it spells marked.

    A kernel call is an f-string that spells the method's C symbol
    (``{c_fn}``) followed by ``(`` and is not a declaration. Each one gets a
    ``/*@site:<line>.<n>*/`` comment after the symbol, in an AST-transformed
    copy of the module -- so a rendered binding or benchmark names the site
    that wrote each of its calls, and the site list is read off the source
    rather than kept here. Returns the copy and ``{site: source line}``.
    """
    path = SRC / "just_makeit" / "_context" / "_methods.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    sites: dict[str, str] = {}
    for fn in tree.body:
        if not (
            isinstance(fn, ast.FunctionDef) and fn.name in _KERNEL_CALLERS
        ):
            continue
        parents = {
            child: node
            for node in ast.walk(fn)
            for child in ast.iter_child_nodes(node)
        }
        for node in list(ast.walk(fn)):
            if not isinstance(node, ast.JoinedStr) or _is_declaration(
                node, parents
            ):
                continue
            marked: list = []
            n = 0
            for i, v in enumerate(node.values):
                marked.append(v)
                nxt = node.values[i + 1] if i + 1 < len(node.values) else None
                if (
                    isinstance(v, ast.FormattedValue)
                    and isinstance(v.value, ast.Name)
                    and v.value.id == "c_fn"
                    and isinstance(nxt, ast.Constant)
                    and str(nxt.value).startswith("(")
                ):
                    site = f"{node.lineno}.{n}"
                    n += 1
                    sites[site] = f"{fn.name}:{node.lineno}: " + (
                        lines[node.lineno - 1].strip()
                    )
                    marked.append(ast.Constant(f"/*@site:{site}*/"))
            node.values = marked
    ast.fix_missing_locations(tree)
    mod = types.ModuleType("just_makeit._context._methods_marked")
    mod.__package__ = "just_makeit._context"
    mod.__file__ = str(path)
    exec(compile(tree, str(path), "exec"), mod.__dict__)
    return mod, sites


def test_every_kernel_call_is_reached_by_a_row(project):
    """WHICH shapes the gate must build is the source's answer, not ours.

    Every row is rendered through the marked copy of the binding and the
    benchmark emitters, and every call they spell must be reached. A shape
    added with its own kernel call fails here until it has a row -- and so
    the prototype, build, call, stub and example checks.
    """
    mod, sites = _marked_methods_module()
    # Armed: the binding spells a kernel call in every result shape, and the
    # benchmark in five more.
    assert len(sites) >= 20, sites
    cfg = C.load(project)
    reached: dict[str, str] = {}
    for obj in OBJECTS:
        csym = CSYM.stem(cfg, obj)
        for m in C.methods(cfg, obj):
            ctx = mod.make_methods_ctx(
                obj,
                _title(obj),
                [m],
                pkg="p",
                enums=C.enums(cfg),
                records=C.records(cfg, obj),
                csym=csym,
            )
            text = "".join(v for v in ctx.values() if isinstance(v, str))
            for site in _SITE.findall(text):
                reached.setdefault(site, f"{obj}.{m['name']}")
    missing = [where for site, where in sites.items() if site not in reached]
    assert not missing, (
        "kernel call(s) no row reaches -- add a row for the shape that "
        "takes each:\n  " + "\n  ".join(missing)
    )


# -- the prototype ------------------------------------------------------------


def _prototype(text: str, sym: str) -> "str | None":
    """The parameter list *text* declares for *sym*, whitespace collapsed."""
    proto = re.search(rf"\b{sym}\s*\(([^;]*?)\)\s*;", text)
    return " ".join(proto.group(1).split()) if proto else None


@pytest.mark.parametrize("row", _rows("prototype"))
def test_the_prototype_declares_every_param(project, row):
    cfg = C.load(project)
    m = _method(project, row)
    sym = C.method_c_symbol(CSYM.stem(cfg, row.obj), m)
    header = next((project / "native" / "inc").rglob(f"{row.obj}_core.h"))
    proto = _prototype(header.read_text(encoding="utf-8"), sym)
    assert proto is not None, f"no prototype of {sym} in {header}"
    names = [re.findall(r"\w+", part)[-1] for part in proto.split(",")]
    for p in m.get("params") or []:
        want = [p["name"]] + (
            [f"{p['name']}_len"] if str(p["type"]).endswith("[]") else []
        )
        assert set(want) <= set(names), (
            f"{sym}({proto}) does not declare param {p['name']!r}"
        )


@pytest.mark.parametrize("row", _rows("peers"))
def test_the_two_prototype_builders_agree(project, row):
    """`make_methods_ctx`'s declaration is the peer of the header's.

    The header gets the method's prototype from
    `_method._build_method_prototype`; `make_methods_ctx` renders the same
    declaration for a ``_core.h`` written whole (gh-594: "must render the
    identical signature"). gh-1960 dropped the params from both, so they
    agreed with each other and with no stub -- and an end-to-end build sees
    only the one that reaches the header, so the other is held to it here.
    """
    cfg = C.load(project)
    m = _method(project, row)
    csym = CSYM.stem(cfg, row.obj)
    sym = C.method_c_symbol(csym, m)
    header = next((project / "native" / "inc").rglob(f"{row.obj}_core.h"))
    decls = make_methods_ctx(
        row.obj,
        _title(row.obj),
        [m],
        pkg="p",
        enums=C.enums(cfg),
        records=C.records(cfg, row.obj),
        csym=csym,
    )["method_decls"]
    assert _prototype(decls, sym) == _prototype(
        header.read_text(encoding="utf-8"), sym
    )


# -- built and called ---------------------------------------------------------

_PROBE = r"""
import importlib.util, json, re, sys
import numpy as np
so, modname, cls, rows = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
spec = importlib.util.spec_from_file_location(modname, so)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Cls = getattr(mod, cls)

def outcome(f):
    try:
        return repr(f())
    except Exception as e:
        return "!" + type(e).__name__ + ": " + str(e)

def example(name, doc):
    # The doc's example, run up to and including its call of the method.
    # The import of the class is the package's, whose other objects this
    # probe does not load; the class is handed to it instead.
    g = {"np": np, cls: Cls}
    ran = False
    for ln in doc.splitlines():
        ln = ln.strip()
        if not ln.startswith(">>> "):
            continue
        stmt = ln[4:]
        if re.match(r"from\s+\S+\s+import\s+" + cls + r"\b", stmt):
            continue
        exec(stmt, g)
        if re.search(r"\bobj\." + name + r"\(", stmt):
            ran = True
            break
    if not ran:
        raise AssertionError("no example call of " + name)
    return None

out = {}
for name, calls in json.loads(rows):
    doc = getattr(Cls, name).__doc__ or ""
    out[name] = {
        "calls": [outcome(lambda e=e: eval(e, {"np": np, "o": Cls()}))
                  for e in calls],
        "doc": doc,
        "example": outcome(lambda: example(name, doc)),
    }
print(json.dumps(out))
"""


class Built(NamedTuple):
    root: Path
    #: object -> (returncode, compiler output)
    build: "dict[str, tuple[int, str]]"
    #: object -> {method -> probe record}, or the probe's failure
    probe: "dict[str, dict | str]"


@pytest.fixture(scope="module")
def built(project) -> Built:
    """Configured with ``-Wall -Wextra -Werror``, each object built on its
    own -- so one broken shape fails its own rows -- and probed."""
    if not _TOOLCHAIN:
        pytest.skip("needs cmake + a C compiler")
    build = project / "build"
    r = subprocess.run(
        [
            "cmake",
            "-S",
            str(project),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert r.returncode == 0, f"configure:\n{r.stdout}\n{r.stderr}"
    results: dict[str, tuple[int, str]] = {}
    for obj in OBJECTS:
        r = subprocess.run(
            ["cmake", "--build", str(build), "--parallel", "4", "--target"]
            + [obj, f"test_{obj}_core", f"bench_{obj}_core"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        results[obj] = (r.returncode, r.stdout + r.stderr)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    probes: dict[str, "dict | str"] = {}
    for obj in OBJECTS:
        if results[obj][0]:
            probes[obj] = "did not build"
            continue
        rows = [
            [r.name, [c[0] for c in r.calls]] for r in ROWS if r.obj == obj
        ]
        p = subprocess.run(
            [
                sys.executable,
                "-c",
                _PROBE,
                str(project / "src" / "p" / f"{obj}{suffix}"),
                f"p.{obj}",
                _title(obj),
                json.dumps(rows),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        probes[obj] = (
            json.loads(p.stdout)
            if p.returncode == 0
            else f"probe exited {p.returncode}:\n{p.stderr[-2000:]}"
        )
    return Built(project, results, probes)


def _probed(built: Built, row: Row) -> dict:
    rec = built.probe[row.obj]
    assert isinstance(rec, dict), f"{row.obj}: {rec}"
    return rec[row.name]


@pytest.mark.parametrize("obj", OBJECTS)
def test_the_object_builds_warning_clean(built, obj):
    """The binding, the ``_core.c`` kernels and the benchmark, -Werror."""
    code, log = built.build[obj]
    diags = [
        ln for ln in log.splitlines() if re.search(r"\b(error|warning)\b", ln)
    ]
    assert code == 0, f"{obj} does not build:\n" + "\n".join(diags[:40])


@pytest.mark.parametrize("row", _rows("call"))
def test_the_call_reaches_the_kernel(built, row):
    """Each call returns what the kernel computed from what was passed."""
    got = _probed(built, row)["calls"]
    for (expr, want), result in zip(row.calls, got):
        if want.startswith("!"):
            assert result.startswith(want + ":"), f"{expr} -> {result}"
        else:
            assert result == want, f"{expr} -> {result}, want {want}"


def _stub_args(root: Path, row: Row) -> "list[str] | None":
    """The arguments the standalone ``.pyi`` declares for the method."""
    tree = ast.parse(
        (root / "src" / "p" / f"{row.obj}.pyi").read_text(encoding="utf-8")
    )
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == _title(row.obj):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == row.name:
                    a = fn.args
                    every = a.posonlyargs + a.args + a.kwonlyargs
                    return [x.arg for x in every][1:]
    return None


@pytest.mark.parametrize("row", _rows("stub"))
def test_the_stub_and_the_runtime_doc_name_the_same_arguments(built, row):
    """The ``.pyi`` signature against the built ``__doc__``'s first line."""
    doc = _probed(built, row)["doc"]
    sig = re.match(rf"{row.name}\(([^)]*)\)", doc)
    assert sig, f"the runtime doc opens with no signature:\n{doc[:200]}"
    runtime = [
        a.split("=")[0].strip() for a in sig.group(1).split(",") if a.strip()
    ]
    assert _stub_args(built.root, row) == runtime


@pytest.mark.parametrize("row", _rows("example"))
def test_the_doc_example_call_is_accepted(built, row):
    """The example is executable prose (gh-1021, gh-1208): run up to its
    call of the method, it must be a call the binding accepts."""
    got = _probed(built, row)["example"]
    assert got == "None", f"{row.obj}.{row.name}'s doc example: {got}"
