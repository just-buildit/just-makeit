"""A list-of-records method sizes its result from a declared capacity (gh-2184).

A list-of-records method -- ``result_fields`` with neither ``single`` nor
``record_dtype`` -- binds a kernel whose contract is::

    size_t <c>_<m>(state, ..., rec_t *result, size_t max_results);

It fills at most ``max_results`` records and has no way to say there were
more. The binding handed it a FIXED stack array, ``rec_t results[64]``, so a
call due 100 records returned 64 and the rest were gone without a word.
doppler's ``CorrDetector`` / ``CorrDetector2D`` / ``AcqEngine`` (module
objects) all have this shape (doppler#1992), and the only lever was a bigger
``max_results`` -- a bigger stack array on every call.

The fix reuses the capacity function a ``variable_output`` method already has
(gh-607, gh-761): when the sacred header declares ``<c>_<m>_max_out``, same
name and same arity rules, the binding asks it, heap-allocates that many
records, hands the kernel exactly that capacity, and returns them all.
Without one, the stack array is byte-identical to before.

GATE, built and called (cmake, ``-Wall -Wextra -Werror``):

* a module object (doppler's shape) and a standalone object, each with a
  capacity declared on every list-of-records shape, return every record due
  -- 100 and 1000, past any fixed buffer;
* the same two layouts WITHOUT a capacity still return ``max_results`` (64);
* the heap buffer is released on the way out (``tracemalloc`` sees
  ``PyMem_Malloc``);
* a module object's fragment rendered before the capacity was declared is
  sacred, so ``apply`` reports it -- for the records it drops, not as the
  ndarray "negative dimension" its size guard would otherwise read as -- and
  a regenerated fragment is quiet.

Which shapes must have a row is the source's answer:
`test_every_capacity_site_is_reached_by_a_row` reads every place
`make_methods_ctx` sizes a records buffer and refuses one no row reaches.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from _compilers import default_cc
from _jminc import INC_ROOT
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _csym as CSYM  # noqa: E402
from just_makeit._context import _methods as M  # noqa: E402
from just_makeit._object import _load_doc_blocks  # noqa: E402

_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: What a fixed buffer holds: the manifest's `max_results` default.
FIXED = 64

#: object -> (module, declares a capacity). Both layouts, both ways.
OBJECTS = {
    "hits": ("det", True),
    "miss": ("det", False),
    "solo": (None, True),
    "lone": (None, False),
}

#: `div_t` is the record: `{int quot; int rem;}`, from <stdlib.h>, so no
#: header has to declare a struct for it.
_DIV = (
    'return_type = "div_t"\n'
    'result_fields = [{name = "quot", type = "int"}, '
    '{name = "rem", type = "int"}]'
)


class Row(NamedTuple):
    """One list-of-records method and the capacity its header declares."""

    name: str
    keys: str
    #: the kernel: one record per unit due, never past `max_results`
    impl: str
    #: the capacity's parameters after the state; "" is state-only
    cap_params: str
    cap_body: str
    #: ``(python expression over `o` and `np`, records due)``
    calls: "tuple[tuple[str, int], ...]"


def _fill(bound: str, rem: str) -> str:
    return (
        "(void)state;\n"
        "    size_t i = 0;\n"
        f"    for (; i < {bound} && i < max_results; i++) {{\n"
        "        result[i].quot = (int)i;\n"
        f"        result[i].rem = {rem};\n"
        "    }\n"
        "    return i;"
    )


ROWS = [
    # an input, no params: doppler's push(x)
    Row(
        "push",
        f'arg_type = "float"\n{_DIV}',
        _fill("n_in", "(int)in[i]"),
        "size_t n_in",
        "return n_in;",
        (
            ("o.push(np.ones(100, np.float32))", 100),
            ("o.push(np.ones(1000, np.float32))", 1000),
        ),
    ),
    # an input and a param: the input is the block `x`, its count `x_len`
    Row(
        "scan",
        f'arg_type = "float"\n{_DIV}\nparams = [{{name = "k", type = "int"}}]',
        _fill("n_in", "k + (int)in[i]"),
        "size_t n_in",
        "return n_in;",
        (("o.scan(np.ones(1000, np.float32), 3)", 1000),),
    ),
    # no input, an array param: its length is the count
    Row(
        "pick",
        f'arg_type = "void"\n{_DIV}\n'
        'params = [{name = "w", type = "double[]"}]',
        _fill("w_len", "(int)w[i]"),
        "size_t w_len",
        "return w_len;",
        (("o.pick(np.full(1000, 2.0))", 1000),),
    ),
    # no input, scalar params only: nothing to count, so state-only
    Row(
        "tune",
        f'arg_type = "void"\n{_DIV}\nparams = [{{name = "k", type = "int"}}]',
        _fill("(size_t)k", "k"),
        "",
        "return 500;",
        (("o.tune(300)", 300),),
    ),
    # neither: state-only
    Row(
        "drain",
        f'arg_type = "void"\n{_DIV}',
        _fill("200", "0"),
        "",
        "return 200;",
        (("o.drain()", 200),),
    ),
]


def _title(obj: str) -> str:
    return obj.title()


def _fragment() -> str:
    """Every object, every row, and the module the first two live in."""
    out = []
    for obj in OBJECTS:
        out.append(
            f"[{obj}]\n"
            'arg_type = "void"\nreturn_type = "void"\n'
            'no_state = "true"\nno_step = "true"\n'
        )
        out += [
            f'[[{obj}.methods]]\nname = "{r.name}"\n{r.keys}\n'
            f'impl = """{r.impl}"""\n'
            for r in ROWS
        ]
    mods: dict = {}
    for obj, (mod, _cap) in OBJECTS.items():
        if mod:
            mods.setdefault(mod, []).append(obj)
    for mod, objs in mods.items():
        names = ", ".join(f'"{o}"' for o in objs)
        out.append(f"[module.{mod}]\nobjects = [{names}]\n")
    return "\n".join(out)


def _declare_capacities(root: Path, obj: str) -> None:
    """What the AUTHOR writes: each method's `_max_out` in the sacred
    header, and its body in `_core.c`."""
    cfg = C.load(root)
    stem = CSYM.stem(cfg, obj)
    decls, defs = [], []
    for m in C.methods(cfg, obj):
        row = next(r for r in ROWS if r.name == m["name"])
        sym = f"{C.method_c_symbol(stem, m)}_max_out"
        params = f"{stem}_state_t *state" + (
            f", {row.cap_params}" if row.cap_params else ""
        )
        decls.append(f"size_t {sym}({params});\n")
        defs.append(
            f"\nsize_t\n{sym}({params})\n{{\n    (void)state;\n"
            f"    {row.cap_body}\n}}\n"
        )
    h = root / INC_ROOT / obj / f"{obj}_core.h"
    text = h.read_text(encoding="utf-8")
    cut = text.rindex("#ifdef __cplusplus")
    h.write_text(text[:cut] + "".join(decls) + text[cut:], encoding="utf-8")
    c = root / "native" / "src" / obj / f"{obj}_core.c"
    c.write_text(
        c.read_text(encoding="utf-8") + "".join(defs), encoding="utf-8"
    )


def _fragment_path(root: Path, obj: str) -> Path:
    mod = OBJECTS[obj][0]
    return root / "native" / "src" / mod / f"{mod}_ext_{obj}.c"


class Project(NamedTuple):
    root: Path
    #: `apply` once the capacities are declared: a module object's fragment
    #: is sacred and still has the fixed buffer.
    stale: str
    #: `apply` after that fragment is deleted, the documented remedy.
    fresh: str


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Project:
    base = tmp_path_factory.mktemp("gh2184")
    root = base / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    frag = base / "frag.toml"
    frag.write_text(_fragment(), encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for obj, (_mod, cap) in OBJECTS.items():
        if cap:
            _declare_capacities(root, obj)
    stale = run_cli("apply", cwd=root)
    assert stale.returncode == 0, stale.stdout + stale.stderr
    for obj, (mod, cap) in OBJECTS.items():
        if mod and cap:
            _fragment_path(root, obj).unlink()
    fresh = run_cli("apply", cwd=root)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    return Project(
        root, stale.stdout + stale.stderr, fresh.stdout + fresh.stderr
    )


# -- which shapes must have a row: every capacity site ------------------------


def _capacity_sites() -> "list[tuple[int, int]]":
    """Each place `make_methods_ctx` sizes a records buffer, as the line
    span of its `_rf_buffer(...)` call -- read off the source."""
    path = SRC / "just_makeit" / "_context" / "_methods.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "make_methods_ctx"
    )
    return [
        (n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_rf_buffer"
    ]


def test_every_capacity_site_is_reached_by_a_row(project, monkeypatch):
    """A records shape added with its own buffer fails here until a row
    builds it, and so is held to every check below."""
    sites = _capacity_sites()
    # Armed: an input, params, and neither.
    assert len(sites) >= 3, sites
    reached: dict = {}
    real = M._records_buffer_c

    def spy(*a, **k):
        # spy <- _rf_buffer <- make_methods_ctx: the line of the call site.
        line = sys._getframe(2).f_lineno
        out = real(*a, **k)
        reached.setdefault(line, out[1])
        return out

    monkeypatch.setattr(M, "_records_buffer_c", spy)
    cfg = C.load(project.root)
    for m in C.methods(cfg, "solo"):
        M.make_methods_ctx(
            "solo",
            "Solo",
            [m],
            pkg="p",
            doc_blocks=_load_doc_blocks(project.root, "solo", cfg),
            records=C.records(cfg, "solo"),
            csym=CSYM.stem(cfg, "solo"),
        )
    missing = [
        s for s in sites if not any(s[0] <= ln <= s[1] for ln in reached)
    ]
    assert not missing, f"records buffer site(s) no row reaches: {missing}"
    # ...and every site reached took the declared capacity.
    assert set(reached.values()) == {"_cap"}, reached


# -- built and called ---------------------------------------------------------

_PROBE = r"""
import gc, json, sys, tracemalloc
import numpy as np
sys.path.insert(0, sys.argv[1])
from p.det import Hits, Miss
from p import Solo, Lone
classes = {"hits": Hits, "miss": Miss, "solo": Solo, "lone": Lone}
calls = json.loads(sys.argv[2])
out = {}
for obj, Cls in classes.items():
    o = Cls()
    got = {}
    for expr in calls:
        r = eval(expr, {"np": np, "o": o})
        got[expr] = [len(r), list(r[-1]) if r else None]
    out[obj] = got
leaks = {}
for obj in ("hits", "solo"):
    o = classes[obj]()
    x = np.ones(1000, np.float32)
    for _ in range(5):
        o.push(x)
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    for _ in range(200):
        o.push(x)
    gc.collect()
    leaks[obj] = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
out["_leaks"] = leaks
print(json.dumps(out))
"""


class Built(NamedTuple):
    log: str
    probe: dict


@pytest.fixture(scope="module")
def built(project) -> Built:
    if not _TOOLCHAIN:
        pytest.skip("needs cmake + a C compiler")
    root = project.root
    build = root / "build"
    log = ""
    for args in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        # Everything: the extensions, and the C tests whose symbol table
        # must now link each capacity function too (gh-1361).
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(args, capture_output=True, text=True, timeout=900)
        log += r.stdout + r.stderr
        assert r.returncode == 0, log[-6000:]
    calls = [c for row in ROWS for c, _due in row.calls]
    p = subprocess.run(
        [sys.executable, "-c", _PROBE, str(root / "src"), json.dumps(calls)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert p.returncode == 0, p.stdout + p.stderr[-4000:]
    return Built(log, json.loads(p.stdout))


def _cases(with_capacity: bool):
    return [
        pytest.param(obj, expr, due, id=f"{obj}-{expr}")
        for obj, (_mod, cap) in OBJECTS.items()
        if cap == with_capacity
        for row in ROWS
        for expr, due in row.calls
    ]


@pytest.mark.parametrize("obj,expr,due", _cases(True))
def test_a_declared_capacity_returns_every_record(built, obj, expr, due):
    n, last = built.probe[obj][expr]
    assert n == due, f"{obj}: {expr} returned {n} of {due} records"
    assert last[0] == due - 1, last


@pytest.mark.parametrize("obj,expr,due", _cases(False))
def test_without_a_capacity_the_fixed_buffer_is_unchanged(
    built, obj, expr, due
):
    n, last = built.probe[obj][expr]
    assert n == min(due, FIXED), f"{obj}: {expr} returned {n}"
    assert last[0] == n - 1, last


@pytest.mark.parametrize("obj", ["hits", "solo"])
def test_the_result_buffer_is_released(built, obj):
    """200 calls of 1000 records each: 1.6 MB if the buffer stayed."""
    grown = built.probe["_leaks"][obj]
    assert grown < 200_000, f"{obj}: {grown} bytes still held"


# -- a module fragment rendered before the capacity ---------------------------

_WHY = "does not ask it, so a call returns at most the fixed max_results"


def test_a_stale_module_fragment_is_reported(project):
    stale = " ".join(project.stale.split())
    assert _WHY in stale, project.stale
    assert _fragment_path(project.root, "hits").name in stale
    # The size guard came with the capacity: no ndarray story about it.
    assert "negative dimension" not in stale, project.stale


def test_the_regenerated_fragment_is_quiet(project):
    fresh = " ".join(project.fresh.split())
    assert _WHY not in fresh, project.fresh
    text = _fragment_path(project.root, "hits").read_text(encoding="utf-8")
    assert "_push_max_out(self->handle, n_in)" in text
