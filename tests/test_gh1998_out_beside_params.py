"""``out=`` and ``<m>_max_out`` for an array beside other params (gh-1998).

gh-412 made a variable_output method with named params keyword-capable and,
in the same change, left it without the ``out=`` buffer the single-array and
all-scalar shapes have. gh-1079 kept that carve-out by name. So doppler's
``Farrow.delay(x, mu)`` (an array beside a scalar) and
``Resampler.execute_ctrl(x, ctrl)`` (two arrays) carried hand-written
bindings and ``manual_stub`` ``_max_out`` entries, and ``jm adopt`` refused
both fragments as "binding ahead": adopting would have removed a feature.

The sizing was never in doubt. The binding already hands ``<m>_max_out()``
the first array's length (gh-607) and allocates from it when ``max_out()``
answers 0 (gh-421), for these shapes, with or without ``out=``; doppler's
hand bindings size from ``x`` the same way. ``_outbuf.why_not`` now offers
``out=`` to them, and the caller's buffer is validated against exactly what
the binding would have allocated itself.

Built here from copies of doppler's two manifest rows, each kernel bounded by
the ``max_out`` it is handed:

* ``out=`` writes into the caller's buffer -- a view of a larger array whose
  tail is read back untouched -- positionally and by keyword;
* an undersized ``out=`` is refused before the kernel runs;
* ``<m>_max_out(len(x))`` sizes it;
* the allocating call is unchanged;
* both ``.pyi`` faces publish what the binding accepts.

GATE: a variable_output method with an array beside other params offers out=
and <m>_max_out, sized from its first array.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402


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


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


#: doppler's `objects/farrow.toml` and `objects/Resampler.toml` method rows,
#: verbatim but for `max_out` (a stand-in constant the fixture rewrites to
#: `return x_len;`) and `impl`: kernels that write what they are told they
#: may, so a capacity the binding gets wrong writes where it should not.
_FRAGMENTS = {
    "farrow": '''\
[farrow]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "true"
no_step = "true"

[[farrow.methods]]
name = "delay"
arg_type = "void"
return_type = "float _Complex"
variable_output = true
pass_capacity = true
nogil = true
max_out = 777
params = [{ name = "x", type = "float _Complex[]" }, { name = "mu", type = "double" }]
impl = """(void)state;
    size_t n = x_len < max_out ? x_len : max_out;
    for (size_t i = 0; i < n; i++)
        out[i] = x[i] + (float)mu;
    return n;"""
''',
    "resampler": '''\
[resampler]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "true"
no_step = "true"

[[resampler.methods]]
name = "execute_ctrl"
arg_type = "void"
return_type = "float _Complex"
variable_output = true
max_out = 777
params = [{name = "x", type = "float _Complex[]"}, {name = "ctrl", type = "double[]"}]
pass_capacity = true
impl = """(void)state;
    size_t n = x_len < max_out ? x_len : max_out;
    n = n < ctrl_len ? n : ctrl_len;
    for (size_t i = 0; i < n; i++)
        out[i] = x[i] * (float)ctrl[i];
    return n;"""
''',
}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Both objects applied from their fragments, `max_out` made per-call."""
    if _SKIP:
        pytest.skip(_SKIP)
    base = tmp_path_factory.mktemp("gh1998")
    dest = base / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    for comp, text in _FRAGMENTS.items():
        fragment = base / f"{comp}.toml"
        fragment.write_text(text, encoding="utf-8")
        _quiet(apply_run, dest, fragment)
        core = dest / f"native/src/{comp}/{comp}_core.c"
        src = core.read_text("utf-8")
        stub = re.search(
            r"_max_out\([^)]*size_t x_len\)\s*\{[^}]*?return 777;", src, re.S
        )
        assert stub, f"{comp}: max_out's constant stub is not where expected"
        src = (
            src[: stub.start()]
            + stub.group(0).replace("return 777;", "return x_len;")
            + src[stub.end() :]
        )
        core.write_text(src, encoding="utf-8")

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


def _outcome(dest: Path, body: str) -> str:
    """What *body* prints, ``ERR <type>: <msg>`` for a raise, or a crash."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import numpy as np\n"
            "from p.farrow import Farrow\n"
            "from p.resampler import Resampler\n"
            "x = np.arange(4, dtype=np.complex64)\n"
            "c = np.array([1, 2, 3, 4], np.float64)\n"
            "try:\n"
            + "".join(f"    {ln}\n" for ln in body.splitlines())
            + "except Exception as e:\n"
            "    print('ERR', type(e).__name__ + ':', e)\n",
        ],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        return f"CRASH {r.returncode}: {r.stderr.strip()[-300:]}"
    return r.stdout.strip()


#: (id, the call with `{out}` for the out= argument spelling, the expected
#: result as Python's repr of a list of complex).
_CALLS = [
    ("delay", "Farrow().delay(x, 0.5{out})", [0.5, 1.5, 2.5, 3.5]),
    ("delay-keyword", "Farrow().delay(x, mu=0.5{out})", [0.5, 1.5, 2.5, 3.5]),
    (
        "execute_ctrl",
        "Resampler().execute_ctrl(x, c{out})",
        [0.0, 2.0, 6.0, 12.0],
    ),
    (
        "execute_ctrl-keyword",
        "Resampler().execute_ctrl(x=x, ctrl=c{out})",
        [0.0, 2.0, 6.0, 12.0],
    ),
]


@pytest.mark.parametrize(
    "call, want", [c[1:] for c in _CALLS], ids=[c[0] for c in _CALLS]
)
def test_out_is_written_in_place_and_not_past(built, call, want):
    """The result IS the caller's buffer, and the guard tail is untouched."""
    got = _outcome(
        built,
        "buf = np.full(10, 9 + 9j, np.complex64)\n"
        f"y = {call.format(out=', out=buf[:4]')}\n"
        "print(y.real.tolist(), np.shares_memory(y, buf),"
        " (buf[4:] == 9 + 9j).all())",
    )
    assert got == f"{want} True True", got


@pytest.mark.parametrize(
    "call", [c[1] for c in _CALLS], ids=[c[0] for c in _CALLS]
)
def test_an_undersized_out_is_refused(built, call):
    got = _outcome(
        built,
        f"print({call.format(out=', out=np.zeros(3, np.complex64)')})",
    )
    assert got == "ERR ValueError: out has 3 elements, need >= 4", got


@pytest.mark.parametrize(
    "call, want", [c[1:] for c in _CALLS], ids=[c[0] for c in _CALLS]
)
def test_the_allocating_call_is_unchanged(built, call, want):
    got = _outcome(built, f"print({call.format(out='')}.real.tolist())")
    assert got == str(want), got


@pytest.mark.parametrize(
    "obj, member", [("Farrow", "delay"), ("Resampler", "execute_ctrl")]
)
def test_max_out_sizes_the_buffer(built, obj, member):
    """`<m>_max_out(len(x))`: the first array's length, as the call's."""
    assert _outcome(built, f"print({obj}().{member}_max_out(4))") == "4"


@pytest.mark.parametrize(
    "stub, sig",
    [
        (
            "farrow.pyi",
            r"def delay\(\s*self,\s*x: [^,]+,\s*mu: float,\s*"
            r"out: [^=]+\| None = None,?\s*\)",
        ),
        ("farrow.pyi", r"def delay_max_out\(self, x_len: int\) -> int:"),
        (
            "resampler.pyi",
            r"def execute_ctrl\(\s*self,\s*x: [^,]+,\s*ctrl: [^,]+,\s*"
            r"out: [^=]+\| None = None,?\s*\)",
        ),
        (
            "resampler.pyi",
            r"def execute_ctrl_max_out\(self, x_len: int\) -> int:",
        ),
    ],
    ids=["delay", "delay_max_out", "execute_ctrl", "execute_ctrl_max_out"],
)
def test_the_stub_publishes_what_the_binding_takes(built, stub, sig):
    text = (built / "src/p" / stub).read_text("utf-8")
    assert re.search(sig, text), text
