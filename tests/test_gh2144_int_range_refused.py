"""gh-2144: an integer outside its C type's range is refused, not wrapped.

A narrow integer parses into a wider ``parse_type`` local -- an ``int8_t``
through ``i`` into an ``int`` -- and the binding then cast it to the
declared type. A value the parse accepted but the type could not hold
wrapped silently: ``int8_t`` given 300 arrived as 44, on every face that
takes a scalar. Unsigned types were worse, parsed through PyArg's MASKING
chars (``I``, ``k``), so ``uint8_t`` given ``2**32 + 5`` arrived as 5.
numpy 2 refuses both (``np.int8(300)`` raises ``OverflowError``).

Every face now converts through ``_context/_parse.scalar_narrow_c``, which
range-checks a narrow row against its ``<stdint.h>`` bounds before the cast
and raises ``OverflowError`` naming the value's parameter, field or
property; an unsigned narrow row parses through a checked char instead.

The halves:

* **the sweep**, compiled: ONE project declares every integer
  ``_CTYPE_META`` scalar on every object, method and function face --
  constructor (state field and init param), ``set_<field>()``, writable
  property, method param, scalar method arg, variable-output param,
  ``step()``, module function, a controllable ``step()`` / ``steps()``
  override, a codec method's fixed param -- builds it ``-Werror`` and sends
  each its type's ``min``, ``max``, ``min - 1``, ``max + 1`` and
  ``max + 2**32`` (where a masking char lands back inside the range). The
  range is numpy's (``np.iinfo``), an oracle independent of the C macros,
  and the refusal's printed bounds are compared to it.
  ``tests/test_gh2035_composer_typed_rows`` sweeps the composer rows one
  step outside the range the same way, and
  ``tests/test_gh1952_parse_width`` the handle and capsule kinds.
* **the placement**: nothing but the primitive calls a row's ``to_c`` cast,
  so a face cannot convert around the check.
* **the advisory**: a sacred fragment rendered before the guard is told it
  lacks it, and not that it lacks gh-1710's output-size guard, which raises
  ``OverflowError`` the same way.

``STILL_WRAPS`` is a ratchet. ``uint64_t`` and ``size_t`` parse through
the masking ``K`` -- no checked unsigned 64-bit char exists -- so an
out-of-range value wraps before any check sees it (gh-2220). The sweep
asserts they STILL wrap, so the fix has to take them out.

GATE: every integer scalar round-trips its own range on every face, and
one step outside it raises ``OverflowError`` naming the argument and the
type's numpy bounds -- except the types ``STILL_WRAPS`` names.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _types as T
from just_makeit._context._parse import INT_RANGE_GUARD_RE, scalar_narrow_c

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

SRC = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: Every integer scalar jm converts, from the type table. ``bool`` is a
#: truth value (``p``), not a range.
INTS = sorted(
    t
    for t, meta in T._CTYPE_META.items()
    if meta["kind"] == "int" and t != "bool"
)

#: The ratchet: each type whose out-of-range value still wraps, and the
#: issue that will refuse it. It may only shrink.
STILL_WRAPS = {"uint64_t": "gh-2220", "size_t": "gh-2220"}

PKG = "rq"


def _slug(ctype: str) -> str:
    """A C and Python identifier fragment for *ctype*."""
    return re.sub(r"\W+", "_", ctype).strip("_")


def _np_name(ctype: str) -> str:
    """*ctype*'s numpy dtype, as ``np.<name>`` spells it."""
    return T._CTYPE_META[ctype]["py_type"].split(".", 1)[1]


def int_range(ctype: str) -> "tuple[int, int]":
    """``(min, max)`` of *ctype*, from numpy rather than from jm."""
    info = np.iinfo(getattr(np, _np_name(ctype)))
    return int(info.min), int(info.max)


#: ``face: (Python expression sending {v}, the name its refusal leads
#: with)``, both formatted per type with its slug ``{s}``.
FACES = {
    "constructor (state field)": ("St(g_{s}={v}).get_g_{s}()", "g_{s}"),
    "set_<field>()": ("setget(St(), 'g_{s}', {v})", "g_{s}"),
    "property": ("attr(St(), 'g_{s}', {v})", "g_{s}"),
    "constructor (init param)": ("Ip(p_{s}={v}).get_s_{s}()", "p_{s}"),
    "method param": ("St().echo_{s}({v})", "e_{s}"),
    "method arg": ("St().sc_{s}({v})", "x"),
    "variable-output param": ("int(St().emit_{s}({v})[0])", "w_{s}"),
    "step()": ("cls(stp, 'x_{s}')().step({v})", "x"),
    "module function": ("fn.f_{s}({v})", "v_{s}"),
    # gh-1952: the controllable override, on both of its parses.
    "step() override": ("cls(stp, 'c_{s}')().step(0, {v})", "k"),
    "steps() override": (
        "int(cls(stp, 'c_{s}')().steps(np.zeros(1, np.{dt}), k={v})[0])",
        "k",
    ),
    # gh-1952: a codec method's fixed param, which its sink stores.
    "codec fixed param": ("pk(St(), '{s}', {v})", "q_{s}"),
}

#: The variant codec the `pk_*` methods pack through (gh-554).
CODEC = {
    "discriminant": "char",
    "scalar_collapse": True,
    "entries": [{"code": "D", "ctype": "double"}],
}


def _impl_c(types: "list[str]") -> str:
    """The bodies ``--impl`` lifts: each face hands its value back.

    ``create``'s is spliced between jm's allocation of ``obj`` and its
    ``return obj``, so it only stores each init param in its state field.
    """
    create = "".join(f"    obj->s_{_slug(t)} = p_{_slug(t)};\n" for t in types)
    out = [f"void\nip_create(void)\n{{\n{create}}}\n"]
    for t in types:
        s = _slug(t)
        out.append(
            f"{t}\necho_{s}(void *state, {t} e_{s})\n{{\n"
            f"    (void)state;\n    return e_{s};\n}}\n"
            f"{t}\nsc_{s}(void *state, {t} x)\n{{\n"
            f"    (void)state;\n    return x;\n}}\n"
            f"size_t\nemit_{s}(void *state, {t} w_{s}, {t} *out)\n{{\n"
            f"    (void)state;\n    out[0] = w_{s};\n    return 1;\n}}\n"
            f"{t}\nf_{s}({t} v_{s})\n{{\n    return v_{s};\n}}\n"
        )
    return "\n".join(out)


def _scaffold(root: Path, types: "list[str]" = INTS) -> Path:
    """Declare every face of each of *types*, through the CLI.

    ``tests/test_gh1952_parse_width`` renders it over every numeric scalar.
    """
    assert run_cli("new", PKG, cwd=root).returncode == 0
    proj = root / PKG
    impl = root / "impl_gh2144.c"
    impl.write_text(_impl_c(types), encoding="utf-8")

    def jm(*args: str) -> None:
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, (args, r.stdout + r.stderr)

    jm(
        "object",
        "st",
        "--no-step",
        *(a for t in types for a in ("--state", f"g_{_slug(t)}:{t}:0")),
    )
    jm(
        "object",
        "ip",
        "--no-step",
        *(a for t in types for a in ("--state", f"s_{_slug(t)}:{t}:0")),
        *(a for t in types for a in ("--init-param", f"p_{_slug(t)}:{t}:0")),
        "--impl",
        f"create::{impl}::ip_create",
    )
    jm("module", "stp")
    jm("module", "fn")
    for t in types:
        s = _slug(t)
        jm("property", "st", f"g_{s}", "--type", t, "--writable", "--field")
        jm(
            "method", "st", f"echo_{s}", "--param", f"e_{s}:{t}",
            "--return-type", t, "--impl", f"{impl}::echo_{s}",
        )  # fmt: skip
        jm(
            "method", "st", f"sc_{s}", "--arg-type", t,
            "--return-type", t, "--impl", f"{impl}::sc_{s}",
        )  # fmt: skip
        jm(
            "method", "st", f"emit_{s}", "--param", f"w_{s}:{t}",
            "--return-type", t, "--variable-output", "--max-out", "1",
            "--impl", f"{impl}::emit_{s}",
        )  # fmt: skip
        jm(
            "object", f"x_{s}", "--module", "stp",
            "--arg-type", t, "--return-type", t,
        )  # fmt: skip
        jm(
            "function", f"f_{s}", "--module", "fn", "--param", f"v_{s}:{t}",
            "--return-type", t, "--impl", f"{impl}::f_{s}",
        )  # fmt: skip
        jm(
            "object", f"c_{s}", "--module", "stp", "--state", f"k:{t}:0",
            "--arg-type", t, "--return-type", t,
        )  # fmt: skip
    _controllable_and_codec(proj, jm, types)
    return proj


def _controllable_and_codec(proj: Path, jm, types: "list[str]") -> None:
    """Make each ``c_*`` object's ``k`` a controllable override and give
    ``st`` a codec method per type (gh-1952).

    Both are manifest-only declarations. A controllable field changes the
    sacred ``step()`` signature, so each ``c_*`` object's files are removed
    and materialized again from the manifest. Each ``step()`` then hands its
    override back, and each codec sink stores its fixed param in the field
    ``get_g_*()`` reads. Only a real scalar may be controllable.
    """
    ctrl = [t for t in types if T._CTYPE_META[t]["kind"] in ("int", "float")]
    cfg = C.load(proj)
    cfg["codec"] = {"kw": CODEC}
    for t in ctrl:
        for row in cfg[f"c_{_slug(t)}"]["state"]:
            row["controllable"] = True
    for t in types:
        s = _slug(t)
        cfg["st"].setdefault("methods", []).append(
            {
                "name": f"pk_{s}",
                "codec": "kw",
                "sink_fn": f"st_pk_{s}",
                "params": [
                    {"name": f"q_{s}", "type": t},
                    {"name": "type", "type": "char", "role": "discriminant"},
                    {"name": "value", "role": "variant"},
                ],
            }
        )
    C.save(proj, cfg)
    inc = proj / "native" / "inc" / PKG
    src = proj / "native"
    for t in ctrl:
        o = f"c_{_slug(t)}"
        shutil.rmtree(inc / o)
        shutil.rmtree(src / "src" / o)
        for f in (
            src / "src" / "stp" / f"stp_ext_{o}.c",
            src / "tests" / f"test_{o}_core.c",
            src / "benchmarks" / f"bench_{o}_core.c",
        ):
            f.unlink()
    jm("apply")
    for t in ctrl:
        h = inc / f"c_{_slug(t)}" / f"c_{_slug(t)}_core.h"
        text, n = re.subn(
            rf"return \({re.escape(t)}\)x;",
            "(void)x;\n    return k;",
            h.read_text("utf-8"),
        )
        assert n == 1, f"{h.name}: the step() stub changed shape"
        h.write_text(text, encoding="utf-8")
    sig = "int st_pk_{s}({p}_st_state_t *s, {t} q_{s}, char type,"
    sig += " const void *val, size_t count)"
    sigs = [sig.format(s=_slug(t), p=PKG, t=t) for t in types]
    h = inc / "st" / "st_core.h"
    text = h.read_text("utf-8")
    at = text.rindex("#endif")
    decls = "".join(f"{x};\n" for x in sigs)
    h.write_text(text[:at] + decls + text[at:], encoding="utf-8")
    core = src / "src" / "st" / "st_core.c"
    core.write_text(
        core.read_text("utf-8")
        + "".join(
            f"\n{x}\n{{\n    (void)type; (void)val; (void)count;\n"
            f"    s->g_{_slug(t)} = q_{_slug(t)};\n    return 0;\n}}\n"
            for t, x in zip(types, sigs)
        ),
        encoding="utf-8",
    )


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


#: Run in the built project's interpreter. Each case is reported on its own
#: -- a value or the exception -- so one face that raises names that case
#: and no other. `PKG` and `CASES` (`{case: expression}`) are prepended.
_DRIVER = """
import json, sys
import numpy as np
sys.path.insert(0, "src")
pkg = __import__(PKG, fromlist=["St", "Ip"])
St, Ip = pkg.St, pkg.Ip
stp = __import__(PKG + ".stp", fromlist=["stp"])
fn = __import__(PKG + ".fn", fromlist=["fn"])


def setget(o, f, v):
    getattr(o, "set_" + f)(v)
    return getattr(o, "get_" + f)()


def attr(o, f, v):
    setattr(o, f, v)
    return getattr(o, f)


def pk(o, s, v):
    getattr(o, "pk_" + s)(v, "D", 1.0)
    return getattr(o, "get_g_" + s)()


def cls(mod, obj):
    name = obj.replace("_", "").lower()
    return next(getattr(mod, c) for c in dir(mod) if c.lower() == name)


out = {}
for case, expr in CASES.items():
    try:
        v = eval(expr)
        out[case] = ["value", repr(v), type(v).__name__]
    except Exception as e:  # every case is reported, not the first
        out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


#: Past the type's range by 2**32: where a MASKING format char (``I``,
#: ``k``) lands back inside it -- ``uint8_t`` given ``2**32 + 255`` read
#: ``255`` -- so only a checked parse refuses it.
MASKED = 2**32


def _cases() -> "dict[str, tuple[str, str, str, int]]":
    """``{case: (expression, ctype, label, value)}`` for every face."""
    cases = {}
    for t in INTS:
        lo, hi = int_range(t)
        for face, (expr, label) in FACES.items():
            for v in (lo, hi, lo - 1, hi + 1, hi + MASKED):
                cases[f"{face} {t} {v}"] = (
                    expr.format(s=_slug(t), v=v, dt=_np_name(t)),
                    t,
                    label.format(s=_slug(t)),
                    v,
                )
    return cases


def refusal_wrong(
    case: str, ctype: str, label: str, v: int, got
) -> "str | None":
    """Why *got* is the wrong outcome for sending *v* outside *ctype*.

    *got* is a driver's record: a list for a value that came back, the
    ``"<Exception>: <message>"`` string for a raise. One step outside a
    narrow type is jm's refusal, naming *label* and numpy's bounds; further
    out, past what the parse type holds, it may be CPython's own. Shared
    with ``tests/test_gh2035_composer_typed_rows``, so the composer rows
    answer to the same refusal and the same ratchet.
    """
    lo, hi = int_range(ctype)
    assert not lo <= v <= hi, (case, v)
    if ctype in STILL_WRAPS:
        if isinstance(got, list):
            return None
        return (
            f"{case}: {ctype} is refused now ({got}); take it out of "
            f"STILL_WRAPS ({STILL_WRAPS[ctype]})"
        )
    if not isinstance(got, str) or not got.startswith("OverflowError: "):
        return f"{case}: want OverflowError, got {got}"
    if "bounds" not in T._CTYPE_META[ctype] or v not in (lo - 1, hi + 1):
        return None
    want = f"OverflowError: {label}: {v} is out of range for {ctype}"
    want += f" [{lo}, {hi}]"
    return None if got == want else f"{case}: want {want!r}, got {got!r}"


def test_the_sweep_is_not_vacuous():
    """An empty derivation would build nothing and pass."""
    assert {"int8_t", "uint8_t", "int32_t", "uint32_t"} <= set(INTS), INTS
    narrow = {t for t in INTS if "bounds" in T._CTYPE_META[t]}
    assert {"int8_t", "int16_t", "int32_t", "uint8_t"} <= narrow, narrow
    assert set(STILL_WRAPS) <= set(INTS), STILL_WRAPS


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_face_refuses_a_value_its_type_cannot_hold(tmp_path):
    """Build ONE project holding every face of every integer type.

    Warning-clean under ``-Wall -Wextra -Werror``, as a downstream building
    with ``-Werror`` needs: the guard compares a signed parse local with a
    ``<stdint.h>`` bound, which ``-Wsign-compare`` watches.
    """
    proj = _scaffold(tmp_path)
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

    cases = _cases()
    driver = proj / "drive_gh2144.py"
    exprs = {c: e for c, (e, *_rest) in cases.items()}
    driver.write_text(
        f"PKG = {PKG!r}\nCASES = {exprs!r}\n{_DRIVER}", encoding="utf-8"
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


# ── the placement ────────────────────────────────────────────────────────────


def _to_c_reads(tree: ast.AST) -> "list[tuple[str, int]]":
    """``(enclosing function, line)`` of each read of a row's ``to_c``."""
    found = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else fn
            )
            is_key = isinstance(child, ast.Constant) and child.value == "to_c"
            if is_key and not isinstance(node, ast.Dict):
                found.append((fn, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_only_the_primitive_casts_a_parse_local():
    """A face that casts ``<name>_raw`` itself skips the range check.

    Registration-free over every module: any ``"to_c"`` that is not a
    ``_CTYPE_META`` row's own key is a read of the cast, and the one
    allowed reader is ``scalar_narrow_c``.
    """
    readers = {}
    for path in sorted(SRC.rglob("*.py")):
        # A template or an example is generated output, not a generator.
        if {"examples", "templates"} & set(path.relative_to(SRC).parts):
            continue
        for fn, line in _to_c_reads(ast.parse(path.read_text("utf-8"))):
            readers.setdefault(path.relative_to(SRC).as_posix(), []).append(
                f"{fn}:{line}"
            )
    primitive = readers.pop("_context/_parse.py", [])
    assert primitive and all(
        r.startswith("scalar_narrow_c:") for r in primitive
    ), primitive
    # gh-1952: `_handle.py` was the last, held here as a ratchet until the
    # handle shapes routed through the primitive.
    assert not readers, (
        "casts a parse local outside _context/_parse.scalar_narrow_c "
        f"(route it through the primitive): {readers}"
    )


def test_a_row_with_no_bounds_is_the_bare_cast():
    """``int``, ``int64_t``, ``double`` and the rest: byte for byte."""
    for t, meta in T._CTYPE_META.items():
        if "to_c" not in meta or "bounds" in meta:
            continue
        got = scalar_narrow_c(t, "v", "return NULL;", label="v")
        assert got == f"    {t} v = {meta['to_c']('v')};\n", t


def test_a_narrow_row_parses_signed():
    """The floor test ``v_raw < 0`` means something only on a signed local,
    and a masking (unsigned) char wraps before any check sees the value."""
    for t, meta in T._CTYPE_META.items():
        if "bounds" in meta:
            assert not meta["parse_type"].startswith("unsigned"), t
            assert meta["fmt"] in "ilL", (t, meta["fmt"])
            guard = scalar_narrow_c(t, "v", "return NULL;", label="v")
            assert INT_RANGE_GUARD_RE.search(guard), t


# ── the advisory ─────────────────────────────────────────────────────────────

FRAG = ("native", "src", "m", "m_ext_r.c")


def _project(tmp_path) -> Path:
    """A module object with one method taking an ``int8_t``."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    r = run_cli(
        "object", "r", "--module", "m", "--no-state", "--no-step", cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method", "r", "scale", "--module", "m", "--param", "k:int8_t",
        "--return-type", "float", cwd=proj,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _apply(proj) -> str:
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def test_a_fragment_predating_the_guard_is_told_what_it_lacks(tmp_path):
    """An upgrading project's sacred fragment casts with no range test.

    It is named for the consequence, and nothing else is blamed: the guard
    raises ``OverflowError`` as gh-1710's output-size guard does, and this
    member has no output size.
    """
    proj = _project(tmp_path)
    frag = proj.joinpath(*FRAG)
    old, n = re.subn(
        r"\n[ \t]*if \(k_raw < [^\n]*\{\n(?:[^\n]*\n)*?[ \t]*\}(?=\n)",
        "",
        frag.read_text("utf-8"),
    )
    assert n == 1, "fixture no longer renders the range guard"
    frag.write_text(old, encoding="utf-8")

    out = _apply(proj)
    assert "m_ext_r.c" in out, out
    assert "an int8_t given 300 arrives as 44" in out, out
    assert "output-size guard" not in out, out
    assert "result shape" not in out, out


def test_a_current_fragment_is_silent(tmp_path):
    out = _apply(_project(tmp_path))
    assert "no longer matches" not in out, out
