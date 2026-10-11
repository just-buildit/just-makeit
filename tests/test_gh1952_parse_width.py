"""gh-1952: no generated parse writes past the C variable it fills.

A PyArg format char decides how many bytes it writes through its address:
``i`` writes an ``int``, ``L`` a ``long long``, ``D`` a 16-byte
``Py_complex``. A binding that parsed a scalar straight into its declared
type wrote past the local: an ``int8_t`` or a ``bool`` through ``i`` / ``p``,
or a ``uint32_t`` through ``k`` on LP64. Every handle shape, the capsule
create and setters, a controllable ``step()`` / ``steps()`` override and a
codec method's fixed params all did this. They now parse through the primitive
every other face uses (``_context/_parse.scalar_arg_c`` /
``scalar_narrow_c``): into the row's ``parse_type`` local, then narrowed
with gh-2144's range check.

Two halves:

* **the width gate**, static. It renders every face -- the object project
  ``tests/test_gh2144_int_range_refused`` sweeps, controllable fields on
  every ``step()`` shape, a handle, a capsule and a composer -- each over
  every integer scalar. Every ``PyArg_Parse*`` call in that C is read, and
  each scalar format unit's target must be declared with exactly the C type
  its char writes. The char -> type map is derived from ``_CTYPE_META``
  (``fmt`` -> ``parse_type``, or the type itself), plus the two chars jm
  spells outside it (``n``, ``C``). A char the map does not know fails the
  gate, so a new spelling cannot pass unread.
* **the module kinds**, compiled. A handle has every integer scalar on a
  create arg, a factory param, method shapes (a), (b), (d), (e) and (f),
  and a writable property. A capsule has it on a create param and a writable
  property. Both are built ``-Werror`` and driven from Python: ``min`` and
  ``max`` round-trip, and one step outside raises ``OverflowError`` naming
  the arg -- against the same refusal and ``STILL_WRAPS`` ratchet as
  ``tests/test_gh2144_int_range_refused``.

An AddressSanitizer run cannot replace the width gate. The write past the
local is made by ``libpython``'s ``PyArg_Parse*``, which ASan does not
instrument, so a sanitized extension never sees it.

GATE: no generated PyArg parse writes into a variable of another C type than
its format char writes, on any face; every module-kind integer arg
round-trips its range and refuses one step outside it, naming the arg.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from test_gh2035_composer_typed_rows import SCALARS, _every_row
from test_gh2144_int_range_refused import (
    INTS,
    _scaffold,
    _slug,
    int_range,
    refusal_wrong,
)

from just_makeit import _capsule, _composer, _handle
from just_makeit import _config as C
from just_makeit import _types as T

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


# ── the width gate ───────────────────────────────────────────────────────────


def _fmt_writes() -> "dict[str, str]":
    """``{format char: the C type PyArg writes through its address}``.

    Derived from ``_CTYPE_META``: a row parses with ``fmt`` into its
    ``parse_type`` local, or into a local of its own type. Each char must
    name ONE type across the table, or a row could be parsed into a
    variable another row's char sizes differently. ``z`` is a nullable
    string's (``_types.param_fmt``). ``n`` and ``C`` are CPython's, for the
    two scalars jm parses outside the table: a count (``Py_ssize_t``) and a
    codec discriminant (``int``).
    """
    seen: "dict[str, set]" = {}
    for ct, meta in T._CTYPE_META.items():
        seen.setdefault(meta["fmt"], set()).add(meta.get("parse_type", ct))
    seen.setdefault(T.param_fmt("const char *", "NULL"), set()).add(
        "const char *"
    )
    split = {f: ts for f, ts in seen.items() if len(ts) != 1}
    assert not split, f"a format char names two C types: {split}"
    out = {f: next(iter(ts)) for f, ts in seen.items()}
    out.update({"n": "Py_ssize_t", "C": "int"})
    return out


#: Format units that fill no scalar: an object, a converter or a buffer.
#: Their targets are not read here.
_NOT_SCALAR = ("O", "O&", "O!", "y#", "s#", "z#", "y*", "s*", "w*")

_CALL = re.compile(r"\bPyArg_Parse(TupleAndKeywords|Tuple)?\s*\(")


def _args(text: str, start: int) -> "list[str]":
    """The top-level arguments of the call whose ``(`` ends at *start*."""
    out, cur, depth, i = [], "", 1, start
    while depth:
        c = text[i]
        if c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            cur += text[i : j + 1]
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if not depth:
                break
        if c == "," and depth == 1:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    out.append(cur.strip())
    return out


def _units(fmt: str) -> "list[str]":
    """*fmt*'s format units, each as spelled (``i``, ``O&``, ``y#``)."""
    units, i = [], 0
    while i < len(fmt):
        c = fmt[i]
        if c in "|$":
            i += 1
            continue
        if c in ":;":
            break
        nxt = fmt[i + 1] if i + 1 < len(fmt) else ""
        if nxt in "&!#*":
            units.append(c + nxt)
            i += 2
            continue
        units.append(c)
        i += 1
    return units


def _declared_type(text: str, at: int, name: str) -> "str | None":
    """The type *name* is declared with in the function holding *at*.

    The function starts at the last column-0 ``{`` line before *at*; the
    LAST declaration of *name* in it before *at* is the one in scope.
    """
    body = text[text.rfind("\n{\n", 0, at) : at]
    decl = re.compile(
        rf"^[ \t]+(?!return\b)([A-Za-z_][\w \t]*?[ \t*]+){re.escape(name)}"
        r"[ \t]*(?:=[^;]*)?;",
        re.M,
    )
    found = decl.findall(body)
    return found[-1] if found else None


def _norm(ctype: str) -> str:
    return re.sub(r"\s*\*\s*", " *", " ".join(ctype.split())).strip()


def parse_width_violations(text: str) -> "list[str]":
    """Every scalar a PyArg parse in *text* writes at another type's width.

    ``[]`` for code that parses every scalar into a local of exactly the
    type its char writes.
    """
    writes = _fmt_writes()
    out = []
    for m in _CALL.finditer(text):
        kind = m.group(1) or ""
        args = _args(text, m.end())
        fmt_i = 2 if kind == "TupleAndKeywords" else 1
        lits = re.findall(r'"((?:[^"\\]|\\.)*)"', args[fmt_i])
        line = text.count("\n", 0, m.start()) + 1
        if not lits or re.sub(r'"(?:[^"\\]|\\.)*"', "", args[fmt_i]).strip():
            out.append(f"line {line}: format is not a literal: {args[fmt_i]}")
            continue
        addrs = args[fmt_i + (2 if kind == "TupleAndKeywords" else 1) :]
        for unit in _units("".join(lits)):
            want = writes.get(unit)
            take = 2 if len(unit) == 2 else 1
            targets, addrs = addrs[:take], addrs[take:]
            if unit in _NOT_SCALAR:
                continue
            if want is None:
                out.append(f"line {line}: format unit {unit!r} is not known")
                continue
            target = targets[0] if targets else ""
            if not re.fullmatch(r"&\w+", target):
                out.append(f"line {line}: {unit!r} parses into {target!r}")
                continue
            got = _declared_type(text, m.start(), target[1:])
            if got is None or _norm(got) != _norm(want):
                out.append(
                    f"line {line}: {unit!r} writes a {want} into "
                    f"{target[1:]}, declared {got!r}"
                )
    return out


def test_the_width_reader_reads():
    """A reader that finds nothing passes every render: arm it."""
    bad = (
        "static PyObject *\nf(PyObject *self, PyObject *args)\n{\n"
        "    int8_t k = 0;\n    double g = 0.0;\n"
        '    if (!PyArg_ParseTuple(args, "id", &k, &g))\n'
        "        return NULL;\n}\n"
    )
    assert parse_width_violations(bad) == [
        "line 6: 'i' writes a int into k, declared 'int8_t '"
    ]
    good = bad.replace("int8_t k", "int k")
    assert parse_width_violations(good) == []
    assert parse_width_violations(good.replace('"id"', '"iq"'))


def _handle_module(types: "list[str]") -> dict:
    """A handle with each of *types* on every arg-bearing shape."""
    methods = []
    for t in types:
        s = _slug(t)
        methods += [
            {
                "name": f"a_{s}",
                "fn": f"hb_a_{s}",
                "returns": t,
                "args": [{"name": f"va_{s}", "type": t}],
            },
            {
                "name": f"b_{s}",
                "fn": f"hb_b_{s}",
                "returns": "size_t",
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": f"vb_{s}", "type": t},
                ],
            },
            {
                "name": f"d_{s}",
                "fn": f"hb_d_{s}",
                "returns": "float[]",
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": "out", "type": "float[]", "writable": True},
                    {"name": f"vd_{s}", "type": t},
                ],
            },
            {
                "name": f"e_{s}",
                "fn": f"hb_e_{s}",
                "returns": "float[]",
                "out_len_fn": "hb_len",
                "args": [{"name": f"ve_{s}", "type": t}],
            },
            {
                "name": f"f_{s}",
                "fn": f"hb_f_{s}",
                "returns": "bytes",
                "out_len_fn": "hb_len",
                "args": [{"name": f"vf_{s}", "type": t}],
            },
            {"name": f"last_{s}", "fn": f"hb_last_{s}", "returns": t},
            {"name": f"cget_{s}", "fn": f"hb_cget_{s}", "returns": t},
        ]
    return {
        "kind": "handle",
        "backing": "hb",
        "type_name": "Hb",
        "create_fn": "hb_open",
        "close_fn": "hb_close",
        "create_args": [
            {"name": f"c_{_slug(t)}", "type": t, "default": "0"} for t in types
        ],
        "factories": [
            {
                "name": f"from_{_slug(t)}",
                "create_fn": f"hbz_{_slug(t)}",
                "init_params": [{"name": f"z_{_slug(t)}", "type": t}],
            }
            for t in types
        ],
        "methods": methods,
        "getters": [
            {
                "fn": f"hb_p_{_slug(t)}",
                "out": t,
                "fields": [
                    {
                        "name": f"p_{_slug(t)}",
                        "type": t,
                        "writable_fn": f"hb_set_p_{_slug(t)}",
                    }
                ],
            }
            for t in types
        ],
        "extra_link_libs": ["backing_core"],
    }


def _capsule_module(types: "list[str]") -> dict:
    """A capsule with each of *types* on a create param and a writable
    property."""
    return {
        "kind": "capsule",
        "backing": "cb",
        "init_params": [{"name": f"i_{_slug(t)}", "type": t} for t in types],
        "properties": [
            {"name": f"q_{_slug(t)}", "type": t, "writable": True}
            for t in types
        ],
        "extra_link_libs": ["backing_core"],
    }


def _renders(root: Path) -> "dict[str, str]":
    """``{where: C}`` for every face of every numeric scalar."""
    proj = _scaffold(root, SCALARS)
    _controllable_shapes(proj)
    out = {
        str(p.relative_to(proj)): p.read_text("utf-8")
        for p in sorted((proj / "native" / "src").rglob("*.c"))
    }
    cfg = {
        "project": {"name": "kq", "version": "0.1.0"},
        "module": {
            "hb": _handle_module(SCALARS),
            "cb": _capsule_module(SCALARS),
            "mix": _every_row(),
        },
    }
    out["handle"] = _handle.render_ext(cfg, "hb")
    out["capsule"] = _capsule.render_ext(cfg, "cb")
    out["composer"] = _composer.render_ext(cfg, "mix")
    return out


#: The `step()` shapes besides a scalar processor, each of which parses its
#: controllable overrides on its own (`_context/_step.py`).
_SHAPES = {
    "gen": ("void", "float"),
    "sink": ("float", "void"),
    "arr": ("float[]", "float"),
    "blk": ("float[]", "float[]"),
}


def _controllable_shapes(proj: Path) -> None:
    """One object per `step()` shape, every real scalar a controllable
    field of it. Declared in the manifest and materialized from it, as
    `test_gh2144_int_range_refused` does for the processor shape."""
    real = [t for t in SCALARS if T._CTYPE_META[t]["kind"] in ("int", "float")]
    for o, (arg, ret) in _SHAPES.items():
        states = [a for t in real for a in ("--state", f"k_{_slug(t)}:{t}:0")]
        r = run_cli(
            "object", o, "--arg-type", arg, "--return-type", ret, *states,
            cwd=proj,
        )  # fmt: skip
        assert r.returncode == 0, r.stdout + r.stderr
    cfg = C.load(proj)
    for o in _SHAPES:
        for row in cfg[o]["state"]:
            row["controllable"] = True
    C.save(proj, cfg)
    pkg = cfg["project"]["name"]
    for o in _SHAPES:
        shutil.rmtree(proj / "native" / "inc" / pkg / o)
        shutil.rmtree(proj / "native" / "src" / o)
        for f in (
            proj / "native" / "tests" / f"test_{o}_core.c",
            proj / "native" / "benchmarks" / f"bench_{o}_core.c",
        ):
            f.unlink(missing_ok=True)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_parse_writes_past_its_target(tmp_path):
    renders = _renders(tmp_path)
    # Armed: the controllable overrides of every shape, the handle and the
    # capsule are each in the render, and parse something.
    joined = "\n".join(renders.values())
    for o in _SHAPES:
        assert (
            "k_int8_t_raw = self->handle->k_int8_t"
            in renders[f"native/src/{o}/{o}_ext.c"]
        ), o
    assert _CALL.search(renders["handle"])
    assert _CALL.search(renders["capsule"])
    assert len(_CALL.findall(joined)) > 100
    found = {
        where: v for where, text in renders.items()
        if (v := parse_width_violations(text))
    }  # fmt: skip
    assert not found, "\n".join(
        f"{where}: {v}" for where, vs in found.items() for v in vs
    )


# ── the module kinds, compiled ───────────────────────────────────────────────

KPKG = "kq"


def _backing_h() -> "dict[str, str]":
    """The C API both kinds wrap: every function the bindings call."""
    hb, cb = [], []
    for t in INTS:
        s = _slug(t)
        hb += [
            f"{t} hb_a_{s}(hb_t *h, {t} v);",
            f"size_t hb_b_{s}(hb_t *h, const float *x, size_t n, {t} v);",
            f"size_t hb_d_{s}(hb_t *h, const float *in, size_t n_in, {t} v,"
            " float *out, size_t max_out);",
            f"size_t hb_e_{s}(hb_t *h, {t} v, float *out);",
            f"size_t hb_f_{s}(hb_t *h, {t} v, void *out);",
            f"{t} hb_last_{s}(hb_t *h);",
            f"{t} hb_cget_{s}(hb_t *h);",
            f"hb_t *hbz_{s}({t} z);",
            f"{t} hb_p_{s}(hb_t *h);",
            f"void hb_set_p_{s}(hb_t *h, {t} v);",
        ]
        cb += [
            f"{t} cb_get_q_{s}(const cb_state_t *st);",
            f"void cb_set_q_{s}(cb_state_t *st, {t} v);",
        ]
    params = ", ".join(f"{t} c_{_slug(t)}" for t in INTS)
    iparams = ", ".join(f"{t} i_{_slug(t)}" for t in INTS)
    head = (
        "#include <stddef.h>\n#include <stdint.h>\n"
        "typedef struct hb hb_t;\ntypedef struct cb_state cb_state_t;\n"
    )
    return {
        "hb": f"#ifndef HB_CORE_H\n#define HB_CORE_H\n{head}"
        f"hb_t *hb_open({params});\nvoid hb_close(hb_t *h);\n"
        "size_t hb_len(hb_t *h);\n" + "\n".join(hb) + "\n#endif\n",
        "cb": f"#ifndef CB_CORE_H\n#define CB_CORE_H\n{head}"
        f"cb_state_t *cb_create({iparams});\n"
        "void cb_destroy(cb_state_t *st);\n" + "\n".join(cb) + "\n#endif\n",
    }


def _backing_c() -> str:
    """Each call stores its value where a readback returns it."""
    fields = "".join(
        f"    {t} c_{_slug(t)}, last_{_slug(t)}, p_{_slug(t)};\n" for t in INTS
    )
    qs = "".join(f"    {t} q_{_slug(t)};\n" for t in INTS)
    params = ", ".join(f"{t} c_{_slug(t)}" for t in INTS)
    iparams = ", ".join(f"{t} i_{_slug(t)}" for t in INTS)
    c = [
        f'#include "{KPKG}/hb/hb_core.h"',
        f'#include "{KPKG}/cb/cb_core.h"',
        "#include <stdlib.h>",
        f"struct hb {{\n{fields}}};",
        f"struct cb_state {{\n{qs}}};",
        f"hb_t *hb_open({params})\n{{\n"
        "    hb_t *h = calloc(1, sizeof *h);\n    if (!h) return NULL;\n"
        + "".join(f"    h->c_{_slug(t)} = c_{_slug(t)};\n" for t in INTS)
        + "    return h;\n}",
        "void hb_close(hb_t *h) { free(h); }",
        "size_t hb_len(hb_t *h) { (void)h; return 1; }",
        f"cb_state_t *cb_create({iparams})\n{{\n"
        "    cb_state_t *st = calloc(1, sizeof *st);\n"
        "    if (!st) return NULL;\n"
        + "".join(f"    st->q_{_slug(t)} = i_{_slug(t)};\n" for t in INTS)
        + "    return st;\n}",
        "void cb_destroy(cb_state_t *st) { free(st); }",
    ]
    for t in INTS:
        s = _slug(t)
        c += [
            f"{t} hb_a_{s}(hb_t *h, {t} v) {{ (void)h; return v; }}",
            f"size_t hb_b_{s}(hb_t *h, const float *x, size_t n, {t} v)"
            f" {{ (void)x; h->last_{s} = v; return n; }}",
            f"size_t hb_d_{s}(hb_t *h, const float *in, size_t n_in, {t} v,"
            " float *out, size_t max_out)"
            " { (void)in; (void)n_in; (void)out; (void)max_out;"
            f" h->last_{s} = v; return 0; }}",
            f"size_t hb_e_{s}(hb_t *h, {t} v, float *out)"
            f" {{ out[0] = 0.0f; h->last_{s} = v; return 1; }}",
            f"size_t hb_f_{s}(hb_t *h, {t} v, void *out)"
            f" {{ ((char *)out)[0] = 0; h->last_{s} = v; return 1; }}",
            f"{t} hb_last_{s}(hb_t *h) {{ return h->last_{s}; }}",
            f"{t} hb_cget_{s}(hb_t *h) {{ return h->c_{s}; }}",
            f"hb_t *hbz_{s}({t} z)\n{{\n"
            "    hb_t *h = calloc(1, sizeof *h);\n"
            f"    if (h) h->c_{s} = z;\n    return h;\n}}",
            f"{t} hb_p_{s}(hb_t *h) {{ return h->p_{s}; }}",
            f"void hb_set_p_{s}(hb_t *h, {t} v) {{ h->p_{s} = v; }}",
            f"{t} cb_get_q_{s}(const cb_state_t *st) {{ return st->q_{s}; }}",
            f"void cb_set_q_{s}(cb_state_t *st, {t} v) {{ st->q_{s} = v; }}",
        ]
    return "\n".join(c) + "\n"


#: ``face: (Python expression sending {v}, the name its refusal leads
#: with)``, formatted per type with its slug ``{s}``.
KIND_FACES = {
    "handle create arg": ("Hb(c_{s}={v}).cget_{s}()", "c_{s}"),
    "handle factory param": ("hbm.from_{s}({v}).cget_{s}()", "z_{s}"),
    "handle (a) arg": ("Hb().a_{s}({v})", "va_{s}"),
    "handle (b) arg": ("sent(Hb(), 'b', '{s}', F, {v})", "vb_{s}"),
    "handle (d) arg": ("sent(Hb(), 'd', '{s}', F, F.copy(), {v})", "vd_{s}"),
    "handle (e) arg": ("sent(Hb(), 'e', '{s}', {v})", "ve_{s}"),
    "handle (f) arg": ("sent(Hb(), 'f', '{s}', {v})", "vf_{s}"),
    "handle property": ("attr(Hb(), 'p_{s}', {v})", "p_{s}"),
    "capsule create param": ("capnew('{s}', {v})", "i_{s}"),
    "capsule property": ("capset('{s}', {v})", "q_{s}"),
}

#: Run in the built project's interpreter, as the object sweep's driver is.
_KIND_DRIVER = """
import json, sys
import numpy as np
sys.path.insert(0, "src")
hbm = __import__(PKG + ".hb.hb", fromlist=["Hb"])
cb = __import__(PKG + ".cb.cb", fromlist=["cb"])
Hb = hbm.Hb
F = np.zeros(1, np.float32)


def sent(h, meth, s, *a):
    getattr(h, meth + "_" + s)(*a)
    return getattr(h, "last_" + s)()


def attr(o, f, v):
    setattr(o, f, v)
    return getattr(o, f)


def capnew(s, v):
    args = [v if o == s else 0 for o in SLUGS]
    return getattr(cb, "cb_get_q_" + s)(cb.cb_create(*args))


def capset(s, v):
    st = cb.cb_create(*[0] * len(SLUGS))
    getattr(cb, "cb_set_q_" + s)(st, v)
    return getattr(cb, "cb_get_q_" + s)(st)


out = {}
for case, expr in CASES.items():
    try:
        v = eval(expr)
        out[case] = ["value", repr(v), type(v).__name__]
    except Exception as e:  # every case is reported, not the first
        out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_module_kind_refuses_a_value_its_type_cannot_hold(tmp_path):
    """Build a handle and a capsule over every integer scalar, -Werror,
    and send each arg its type's range and one step outside it."""
    assert run_cli("new", KPKG, cwd=tmp_path).returncode == 0
    proj = tmp_path / KPKG
    for name, text in _backing_h().items():
        inc = proj / "native" / "inc" / KPKG / name
        inc.mkdir(parents=True, exist_ok=True)
        (inc / f"{name}_core.h").write_text(text, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "backing.c").write_text(_backing_c(), encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(
        "add_library(backing_core OBJECT backing.c)\n"
        "target_include_directories(backing_core PUBLIC"
        " ${CMAKE_SOURCE_DIR}/native/inc)\n",
        encoding="utf-8",
    )
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {}).update(
        {"hb": _handle_module(INTS), "cb": _capsule_module(INTS)}
    )
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)

    cases = {}
    for t in INTS:
        lo, hi = int_range(t)
        for face, (expr, label) in KIND_FACES.items():
            for v in (lo, hi, lo - 1, hi + 1):
                cases[f"{face} {t} {v}"] = (
                    expr.format(s=_slug(t), v=v),
                    t,
                    label.format(s=_slug(t)),
                    v,
                )
    exprs = {c: e for c, (e, *_rest) in cases.items()}
    driver = proj / "drive_gh1952.py"
    driver.write_text(
        f"PKG = {KPKG!r}\nSLUGS = {[_slug(t) for t in INTS]!r}\n"
        f"CASES = {exprs!r}\n{_KIND_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    assert set(got) == set(cases), sorted(set(got) ^ set(cases))
    wrong = []
    for case, (_e, ct, label, v) in sorted(cases.items()):
        lo, hi = int_range(ct)
        if not lo <= v <= hi:
            wrong.append(refusal_wrong(case, ct, label, v, got[case]))
        elif got[case] != ["value", repr(v), "int"]:
            wrong.append(f"{case}: sent {v}, got {got[case]}")
    wrong = [w for w in wrong if w]
    assert not wrong, f"{len(wrong)} wrong:\n" + "\n".join(wrong)
