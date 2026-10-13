"""Two more faces of a list of records size it from a declared capacity (gh-2205).

gh-2184 made an object method's list-of-records binding ask a header-declared
``<c>_<m>_max_out(state, n_in)`` for its capacity. Two other faces render the
same ``(..., rec_t *result, size_t max_results) -> count`` contract and still
handed the C a fixed ``max_results`` (64):

* a list-of-records MODULE FUNCTION (``jm function --result-field``). A
  function has no state and so no ``_max_out``; its capacity key is the one a
  ``variable_output`` function already declares, ``out_size`` -- a C
  expression over its params. Declared, it sizes the records and is what the
  C is told; undeclared, ``max_results`` stands.
* the generated BENCHMARK of a list-of-records method, which timed a kernel
  that stops at a full buffer over 64 records. It sizes the buffer from the
  method's declared ``_max_out`` when the header declares one at render
  time; the bench stays create-only.

GATE, built and run (cmake, ``-Wall -Wextra -Werror``):

* a function declaring ``out_size = "x_len"`` returns all 100 / 1000
  records, and one without it still 64;
* the benchmark of each list-of-records shape -- an input, an input beside a
  param, an array param, neither -- runs to completion against a kernel that
  ABORTS when handed less room than it is due, and one with no capacity
  keeps its fixed buffer;
* ``out_size`` beside ``max_results_param`` -- two capacities for one buffer
  -- is refused; ``jm function --out-size`` is accepted with
  ``--result-field`` (and still refused with neither it nor
  ``--variable-output``), and ``jm script`` re-emits it.
"""

from __future__ import annotations

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

_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: What a fixed buffer holds: the manifest's `max_results` default.
FIXED = 64

#: `div_t` is the record: `{int quot; int rem;}`, from <stdlib.h>, so no
#: header has to declare a struct for it.
_DIV = (
    'return_type = "div_t"\n'
    'result_fields = [{name = "quot", type = "int"}, '
    '{name = "rem", type = "int"}]'
)
_DIV_FLAGS = (
    "--return-type", "div_t", "--result-field", "quot:int",
    "--result-field", "rem:int",
)  # fmt: skip

# -- a module function ---------------------------------------------------------

#: function -> its flags beyond the records: with `out_size`, and without.
FUNCTIONS = {"every": ("--out-size", "x_len"), "fixed": ()}

#: One record per input element, never past the room it was handed.
_FN_BODY = """
    size_t j = 0;
    for (; j < x_len && j < max_results; j++) {
        result[j].quot = (int)j;
        result[j].rem = (int)x[j];
    }
    return j;"""

# -- the benchmark of a list-of-records method ---------------------------------


class Row(NamedTuple):
    """One list-of-records method shape, and the capacity its author
    declares."""

    name: str
    keys: str
    #: the params its kernel does not read
    unused: str
    #: the records the benchmark's call is due, as C over the kernel params
    due: str
    #: the capacity's parameters after the state; "" is state-only
    cap_params: str
    cap_body: str


ROWS = [
    # an input: the bench passes BENCH_N elements
    Row("push", f'arg_type = "float"\n{_DIV}', "(void)in;", "n_in",
        "size_t n_in", "return n_in;"),
    # an input beside a param
    Row("scan", f'arg_type = "float"\n{_DIV}\n'
        'params = [{name = "k", type = "int"}]', "(void)in; (void)k;",
        "n_in", "size_t n_in", "return n_in;"),
    # no input, an array param: the bench passes (NULL, 0)
    Row("pick", f'arg_type = "void"\n{_DIV}\n'
        'params = [{name = "w", type = "double[]"}]', "(void)w;", "w_len",
        "size_t w_len", "return w_len;"),
    # neither: the capacity takes only the state
    Row("drain", f'arg_type = "void"\n{_DIV}', "", "200", "", "return 200;"),
]  # fmt: skip


def _kernel(r: Row) -> str:
    """Every record due -- or ABORT when handed less room than that: the
    benchmark would be timing a call that cannot finish its input, the
    gh-2205 symptom, made loud."""
    return (
        f"(void)state; {r.unused}\n"
        f"    size_t due = (size_t)({r.due});\n"
        "    if (max_results < due)\n"
        "        abort();\n"
        "    for (size_t i = 0; i < due; i++) {\n"
        "        result[i].quot = (int)i;\n"
        "        result[i].rem = 0;\n"
        "    }\n"
        "    return due;"
    )


def _fragment() -> str:
    head = (
        '[{o}]\narg_type = "void"\nreturn_type = "void"\n'
        'no_state = "true"\nno_step = "true"\n'
    )
    out = [head.format(o="hits")]
    out += [
        f'[[hits.methods]]\nname = "{r.name}"\n{r.keys}\n'
        f'impl = """{_kernel(r)}"""\n'
        for r in ROWS
    ]
    # No capacity declared: its benchmark keeps the fixed buffer.
    out.append(
        head.format(o="lone")
        + f'[[lone.methods]]\nname = "push"\narg_type = "float"\n{_DIV}\n'
    )
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


def _implement(path: Path, body: str) -> None:
    """The author's body in a scaffolded function stub."""
    text = path.read_text(encoding="utf-8")
    start = text.index("{\n", text.index("<<IMPLEMENT")) + 2
    end = text.rindex("}")
    path.write_text(
        text[:start] + body.lstrip("\n") + "\n" + text[end:], encoding="utf-8"
    )


def _bench_source(root: Path, obj: str) -> Path:
    return root / "native" / "benchmarks" / f"bench_{obj}_core.c"


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh2205")
    root = base / "p"
    r = run_cli("new", "p", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    # The functions, through the CLI: `--out-size` beside `--result-field`.
    r = run_cli("module", "m", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for fn, extra in FUNCTIONS.items():
        r = run_cli(
            "function", fn, "--module", "m", "--param", "x:float[]",
            *_DIV_FLAGS, *extra, cwd=root,
        )  # fmt: skip
        assert r.returncode == 0, r.stdout + r.stderr
        _implement(root / "native" / "src" / "m" / f"{fn}.c", _FN_BODY)
    frag = base / "frag.toml"
    frag.write_text(_fragment(), encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    _declare_capacities(root, "hits")
    # The bench is create-only, so a capacity declared after it was rendered
    # reaches it when it is rendered again: delete it and re-apply.
    _bench_source(root, "hits").unlink()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


_PROBE = r"""
import json, sys
import numpy as np
sys.path.insert(0, sys.argv[1])
from p import m
out = {}
for fn in ("every", "fixed"):
    for n in (100, 1000):
        r = getattr(m, fn)(np.full(n, 3.0, np.float32))
        out[f"{fn}{n}"] = [len(r), list(r[-1])]
print(json.dumps(out))
"""


class Built(NamedTuple):
    probe: dict
    #: the benchmark's (returncode, output) for the object with capacities
    bench: "tuple[int, str]"


@pytest.fixture(scope="module")
def built(project, tmp_path_factory) -> Built:
    if not _TOOLCHAIN:
        pytest.skip("needs cmake + a C compiler")
    build = project / "build"
    log = ""
    for args in (
        [
            "cmake",
            "-S",
            str(project),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        # Everything, the benchmarks included.
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(args, capture_output=True, text=True, timeout=900)
        log += r.stdout + r.stderr
        assert r.returncode == 0, log[-6000:]
    p = subprocess.run(
        [sys.executable, "-c", _PROBE, str(project / "src")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert p.returncode == 0, p.stdout + p.stderr[-4000:]
    exe = next(
        f for f in build.rglob("bench_hits_core*") if f.is_file()
        and not f.suffix
    )  # fmt: skip
    run_in = tmp_path_factory.mktemp("bench")
    b = subprocess.run(
        [str(exe)], cwd=run_in, capture_output=True, text=True, timeout=600
    )
    return Built(json.loads(p.stdout), (b.returncode, b.stdout + b.stderr))


# -- the module function -------------------------------------------------------


@pytest.mark.parametrize("n", [100, 1000])
def test_out_size_returns_every_record(built, n):
    count, last = built.probe[f"every{n}"]
    assert count == n, f"every({n}) returned {count} of {n} records"
    assert last == [n - 1, 3]


@pytest.mark.parametrize("n", [100, 1000])
def test_without_out_size_the_default_stands(built, n):
    count, last = built.probe[f"fixed{n}"]
    assert count == FIXED, f"fixed({n}) returned {count}"
    assert last == [FIXED - 1, 3]


def test_jm_script_re_emits_out_size(project):
    r = run_cli("script", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    line = next(ln for ln in r.stdout.split("just-makeit ") if " every " in ln)
    assert "--out-size x_len" in line, line


def test_jm_function_takes_out_size_with_records(tmp_path):
    root = tmp_path / "p"
    assert run_cli("new", "p", str(root)).returncode == 0
    assert run_cli("module", "m", cwd=root).returncode == 0
    r = run_cli(
        "function", "f", "--module", "m", "--param", "x:float[]",
        *_DIV_FLAGS, "--out-size", "x_len * 2", cwd=root,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    (fn,) = C.module_functions(C.load(root), "m")
    assert fn["out_size"] == "x_len * 2"
    text = (root / "native" / "src" / "m" / "m_ext.c").read_text("utf-8")
    assert "(size_t)(x_len * 2)" in text, "the binding sizes from it"


def test_out_size_still_needs_an_output(tmp_path):
    """`--out-size` sizes an allocated output: a variable_output array, or
    (gh-2205) a list of records. With neither there is nothing to size."""
    root = tmp_path / "p"
    assert run_cli("new", "p", str(root)).returncode == 0
    assert run_cli("module", "m", cwd=root).returncode == 0
    r = run_cli(
        "function", "f", "--module", "m", "--param", "x:float[]",
        "--return-type", "float", "--out-size", "x_len", cwd=root,
    )  # fmt: skip
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode != 0, out
    assert "--out-size requires --variable-output or --result-field" in out


def test_two_capacities_are_refused(tmp_path):
    """`max_results_param` already names the capacity the C reads; an
    `out_size` beside it would size the buffer one way and tell the C
    another."""
    root = tmp_path / "p"
    assert run_cli("new", "p", str(root)).returncode == 0
    assert run_cli("module", "m", cwd=root).returncode == 0
    frag = tmp_path / "frag.toml"
    frag.write_text(
        "[[module.m.functions]]\n"
        'name = "both"\n'
        f"{_DIV}\n"
        'params = [{name = "x", type = "float[]"}, '
        '{name = "n", type = "size_t"}]\n'
        'max_results_param = "n"\n'
        'out_size = "x_len"\n',
        encoding="utf-8",
    )
    r = run_cli("apply", str(frag), cwd=root)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode != 0, out
    assert "declares both out_size and max_results_param" in out, out


# -- the benchmark ------------------------------------------------------------


def test_the_benchmark_sizes_each_records_call_from_its_capacity(
    project, built
):
    """Every list-of-records shape, timed over its whole input: the kernel
    aborts when the bench hands it less room than it is due."""
    code, out = built.bench
    assert code == 0, f"bench_hits_core exited {code}:\n{out[-3000:]}"
    text = _bench_source(project, "hits").read_text(encoding="utf-8")
    stem = CSYM.stem(C.load(project), "hits")
    for r in ROWS:
        assert f"{stem}_{r.name}_max_out(obj" in text, r.name
    assert f"results[{FIXED}]" not in text


def test_without_a_capacity_the_benchmark_is_unchanged(project):
    text = _bench_source(project, "lone").read_text(encoding="utf-8")
    assert f"push_results[{FIXED}];" in text
    assert "_max_out(" not in text
