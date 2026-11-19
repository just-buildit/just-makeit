"""A scalar ``out`` param on a module function is an output (gh-2683).

A scalar param with ``out = true`` was bound as an INPUT on every face: the
prototype took it by value, the binding parsed it, the stub asked the caller
for it, and nothing came back. ``out`` meant only "the caller's writable
array" (`_types.param_writable`), so a lookup that returns a pair --
doppler's ``rate_from_mbps(mbps) -> (n_bpsc, code)`` -- had no declarative
binding, and a method accepted the key the same silent way.

Now, on a module function, a scalar ``out`` is a value the C function
writes: the prototype takes ``T *name``, Python does not pass it, and the
binding returns it -- alone for one, a tuple in declared order for several.
It is accepted on a ``void`` function or with ``check_return = true``,
whose status raises before the outs are returned; every other shape, and
every method, is refused at load (gh-2685 tracks methods).

GATE. A compiled module whose kernels write their outs:

* a known rate returns the pair, an unknown one raises the declared
  exception, and the out is not an argument the caller can pass;
* one out comes back bare, a function of outs only takes no argument, an
  array out and a scalar out coexist, and a complex out converts;
* the stub, the header prototype, the generated contract test and the C
  smoke test agree with that -- and the contract test and smoke test run;
* ``jm function --out-param name:T`` records the out, and ``jm script``
  replays it;
* the load refuses a method scalar out, a non-void return without
  ``check_return``, an enum / defaulted / string out and an out beside an
  allocated output, each naming what to declare instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()

_FRAGMENT = """\
[module.ops]
objects = []

[[module.ops.functions]]
name = "rate_from_mbps"
return_type = "int"
check_return = true
status_errors = [{ status = "EXIT_FAILURE", error = "ValueError", message = "no rate" }]
impl = \"\"\"
if (mbps == 54) { *n_bpsc = 6; *code = 3; return 0; }
if (mbps == 6) { *n_bpsc = 1; *code = 0; return 0; }
return 1; /* EXIT_FAILURE */
\"\"\"
params = [
    { name = "mbps", type = "int" },
    { name = "n_bpsc", type = "int", out = true, doc = "Bits per subcarrier." },
    { name = "code", type = "int", out = true, doc = "Coding rate code." },
]

[[module.ops.functions]]
name = "half"
impl = "*h = x / 2.0;"
params = [
    { name = "x", type = "double" },
    { name = "h", type = "double", out = true },
]

[[module.ops.functions]]
name = "answer"
impl = "*a = 42;"
params = [{ name = "a", type = "int", out = true }]

[[module.ops.functions]]
name = "fill_count"
impl = "for (size_t i = 0; i < ys_len; i++) ys[i] = 7.0f; *n = ys_len;"
params = [
    { name = "ys", type = "float[]", out = true },
    { name = "n", type = "size_t", out = true },
]

[[module.ops.functions]]
name = "unit"
impl = "*z = 3.0;"
params = [{ name = "z", type = "double _Complex", out = true }]
"""


def _project(base: Path, fragment: str) -> "tuple[Path, str]":
    """A fresh project with *fragment* applied, and apply's output."""
    r = run_cli("new", "p", "--no-c-prefix", cwd=base)
    assert r.returncode == 0, r.stderr
    dest = base / "p"
    frag = base / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=dest)
    return dest, (r.stdout + r.stderr if r.returncode else "")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The module above, applied and compiled, its C tests built."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest, err = _project(tmp_path_factory.mktemp("gh2683"), _FRAGMENT)
    assert not err, err
    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return dest


def _py(dest: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, *args],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )


def _outcome(dest: Path, body: str) -> str:
    """What *body* prints, or the exception it raised, by class and text."""
    r = _py(
        dest,
        "-c",
        "import numpy as np\nfrom p.ops import *\n"
        "try:\n"
        + "".join(f"    {ln}\n" for ln in body.splitlines())
        + "except Exception as e:\n"
        "    print(type(e).__name__, e)\n",
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


#: (call, what it prints).
CALLS = [
    ("print(rate_from_mbps(54))", "(6, 3)"),
    ("print(rate_from_mbps(mbps=6))", "(1, 0)"),
    ("print(rate_from_mbps(7))", "ValueError no rate"),
    # The out is not an argument: a caller passing it is refused.
    ("rate_from_mbps(54, 0)", "TypeError"),
    ("print(half(3.0))", "1.5"),
    ("print(answer())", "42"),
    ("answer(1)", "TypeError"),
    (
        "o = np.zeros(3, np.float32)\nprint(fill_count(o), o.tolist())",
        "3 [7.0, 7.0, 7.0]",
    ),
    ("print(repr(unit()))", "(3+0j)"),
]


@pytest.mark.parametrize(("call", "want"), CALLS, ids=[c for c, _ in CALLS])
def test_the_outs_come_back(built, call, want):
    got = _outcome(built, call)
    assert got.startswith(want), got


def test_the_stub_says_what_comes_back(built):
    pyi = (built / "src/p/ops/ops.pyi").read_text("utf-8")
    for sig in (
        "def rate_from_mbps(mbps: int) -> tuple[int, int]:",
        "def half(x: float) -> float:",
        "def answer() -> int:",
        "def unit() -> complex:",
    ):
        assert sig in pyi, pyi
    # The Returns section names each out by its doc, on both faces.
    assert "n_bpsc : int\n            Bits per subcarrier." in pyi, pyi
    doc = _py(
        built, "-c", "from p.ops import *; print(rate_from_mbps.__doc__)"
    )
    assert "n_bpsc : int\n        Bits per subcarrier." in doc.stdout, doc


def test_the_prototype_takes_the_address(built):
    hdr = next(built.glob("native/inc/**/ops_core.h")).read_text("utf-8")
    assert "int rate_from_mbps(int mbps, int *n_bpsc, int *code);" in hdr
    assert "void answer(int *a);" in hdr, hdr


def test_the_contract_test_calls_without_the_outs(built):
    test = built / "src/p/ops/tests/test_ops_functions.py"
    text = test.read_text("utf-8")
    assert "result = module.rate_from_mbps(mbps=1)" in text, text
    assert "result = module.answer()" in text, text
    assert "self.assertEqual(len(result), 2)" in text, text
    r = _py(built, "-m", "unittest", "-v", "p.ops.tests.test_ops_functions")
    assert r.returncode == 0, r.stderr
    # Each wrapper the kernel does not refuse runs its contract.
    for fn in ("half", "answer", "fill_count", "unit"):
        assert f"test_{fn}_contract" in r.stderr, r.stderr
        line = next(
            ln for ln in r.stderr.splitlines() if f"test_{fn}_contract" in ln
        )
        assert line.endswith("ok"), line


def test_the_c_smoke_test_runs(built):
    smoke = next(built.glob("native/tests/**/test_ops_core.c"))
    text = smoke.read_text("utf-8")
    assert "(void)rate_from_mbps(0, &n_bpsc, &code);" in text, text
    r = subprocess.run(
        ["ctest", "--test-dir", str(built / "build"), "-R", "ops"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "100% tests passed" in r.stdout, r.stdout


def test_the_cli_spells_it_and_script_replays_it(tmp_path):
    assert run_cli("new", "p", "--no-c-prefix", cwd=tmp_path).returncode == 0
    dest = tmp_path / "p"
    assert run_cli("module", "ops", cwd=dest).returncode == 0
    r = run_cli(
        "function",
        "pair",
        "--module",
        "ops",
        "--param",
        "k:int",
        "--out-param",
        "lo:int",
        "--out-param",
        "hi:double",
        cwd=dest,
    )
    assert r.returncode == 0, r.stderr
    hdr = next(dest.glob("native/inc/**/ops_core.h")).read_text("utf-8")
    assert "void pair(int k, int *lo, double *hi);" in hdr, hdr
    pyi = (dest / "src/p/ops/ops.pyi").read_text("utf-8")
    assert "def pair(k: int) -> tuple[int, float]:" in pyi, pyi
    r = run_cli("script", cwd=dest)
    assert r.returncode == 0, r.stderr
    assert "--out-param lo:int" in r.stdout, r.stdout
    assert "--out-param hi:double" in r.stdout, r.stdout
    # A non-void return without --check-return has nowhere to put it.
    r = run_cli(
        "function",
        "bad",
        "--module",
        "ops",
        "--out-param",
        "lo:int",
        "--return-type",
        "int",
        cwd=dest,
    )
    assert r.returncode != 0
    assert "Declare check_return = true" in r.stderr, r.stderr


# -- the refusals -------------------------------------------------------------

_METHOD = """\
[thing]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"

[[thing.methods]]
name = "peek"
arg_type = "void"
return_type = "void"
params = [{ name = "n", type = "int", out = true }]
"""

_FUNCTION = """\
[module.ops]
objects = []

[[module.ops.functions]]
name = "f"
"""

REFUSED = [
    ("method", _METHOD, "'peek' param 'n': out = true on a scalar"),
    ("method-gh2685", _METHOD, "(gh-2685)"),
    (
        "non-void",
        _FUNCTION + 'return_type = "int"\n'
        'params = [{ name = "n", type = "int", out = true }]\n',
        "Declare check_return = true",
    ),
    (
        "enum",
        '[[enum]]\nname = "mode"\nvalues = ["a", "b"]\n\n'
        + _FUNCTION
        + 'params = [{ name = "n", type = "int", enum = "mode", '
        "out = true }]\n",
        "out = true on an enum ('n')",
    ),
    (
        "default",
        _FUNCTION + 'params = [{ name = "n", type = "int", default = "2", '
        "out = true }]\n",
        "out = true on a param with a default ('n')",
    ),
    (
        "string",
        _FUNCTION + 'params = [{ name = "s", type = "const char *", '
        "out = true }]\n",
        "out = true on a 'const char *' param ('s')",
    ),
    (
        "out_type",
        _FUNCTION + 'out_type = "float[n]"\n'
        'params = [{ name = "n", type = "size_t" }, '
        '{ name = "m", type = "int", out = true }]\n',
        "declares out param 'm' and out_type",
    ),
]


@pytest.mark.parametrize(
    ("fragment", "says"),
    [(f, s) for _, f, s in REFUSED],
    ids=[i for i, _, _ in REFUSED],
)
def test_a_scalar_out_with_no_binding_is_refused(tmp_path, fragment, says):
    _dest, err = _project(tmp_path, fragment)
    assert says in err, err
