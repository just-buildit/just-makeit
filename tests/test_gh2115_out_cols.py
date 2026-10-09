"""``out_cols``: a ``variable_output`` result that is a matrix (gh-2115).

A kernel that produces fixed-width rows -- a spectrogram's ``nfft`` bins, a
frame of ``n`` samples -- fills a flat buffer and returns an element count.
Every array jm allocated for it was 1-D, so the caller had to know the width
and reshape, and a declaration of the width had nowhere to live. ``out_cols``
is that declaration: a C expression over the object (or an integer), and the
binding hands back ``(count / cols, cols)``.

Nothing about the kernel changes. It still sees a flat buffer, its capacity
and its count in ELEMENTS, and ``<m>_max_out`` still answers in elements; the
binding only changes the SHAPE it returns and the shape it accepts for
``out=``. That is the contract this file proves, in four ways:

* **the build**: one compiled object whose kernels write known values, driven
  through the allocating path and through ``out=`` -- the latter into a view
  of a larger buffer, so a write past the rows asked for lands on guard rows
  the test reads back;
* **the refusals**: an ``out=`` of the wrong rank or width, and a kernel that
  returns a count that is not a whole number of rows;
* **the unchanged**: a method without ``out_cols`` on the same object still
  returns a flat array, so the key is opt-in;
* **the manifest**: ``out_cols`` on a method it cannot mean anything for is
  refused at load, with the reason.

GATE: a variable_output method that declares ``out_cols`` returns a 2-D array
of that width on every path, accepts only a 2-D ``out=`` of that width, and
leaves a method that does not declare it alone.
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

from _jmrun import run_cli, script_round_trip  # noqa: E402

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


# -- the build ----------------------------------------------------------------

#: Three kernels over one object with a `width` of 4.
#:
#: * ``run``    echoes ``x`` as whole rows, and declares its width as an
#:   expression over the object (``state->width``), which is the case that
#:   needs the `state` alias;
#: * ``lit``    declares a literal width, which must need no alias at all;
#: * ``ragged`` returns ``x_len`` elements whatever that is, which breaks the
#:   whole-rows contract the declaration made;
#: * ``flat``   declares nothing and must still return a flat array.
_FRAGMENT = '''\
[mat]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "true"

[[mat.state]]
name = "width"
type = "size_t"
default = "4"

[[mat.methods]]
name = "run"
arg_type = "void"
return_type = "float"
variable_output = true
pass_capacity = true
max_out = 12
out_cols = "state->width"
params = [{name = "x", type = "float[]"}]
impl = """size_t w = state->width;
    size_t rows = x_len / w;
    if (rows * w > max_out) rows = max_out / w;
    for (size_t i = 0; i < rows * w; i++) out[i] = x[i];
    return rows * w;"""

[[mat.methods]]
name = "lit"
arg_type = "void"
return_type = "float"
variable_output = true
pass_capacity = true
max_out = 12
out_cols = 3
params = [{name = "x", type = "float[]"}]
impl = """size_t rows = x_len / 3;
    if (rows * 3 > max_out) rows = max_out / 3;
    for (size_t i = 0; i < rows * 3; i++) out[i] = x[i];
    return rows * 3;"""

[[mat.methods]]
name = "ragged"
arg_type = "void"
return_type = "float"
variable_output = true
pass_capacity = true
max_out = 12
out_cols = "state->width"
params = [{name = "x", type = "float[]"}]
impl = """(void)state;
    for (size_t i = 0; i < x_len && i < max_out; i++) out[i] = x[i];
    return x_len < max_out ? x_len : max_out;"""

[[mat.methods]]
name = "flat"
arg_type = "void"
return_type = "float"
variable_output = true
pass_capacity = true
max_out = 12
params = [{name = "x", type = "float[]"}]
impl = """(void)state;
    for (size_t i = 0; i < x_len && i < max_out; i++) out[i] = x[i];
    return x_len < max_out ? x_len : max_out;"""
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The `mat` object above, applied from its fragment and compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh2115") / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    fragment = dest.parent / "mat.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    _quiet(apply_run, dest, fragment)

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
    """What *body* prints, or ``CRASH <rc>`` when the interpreter died."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import numpy as np\nfrom p.mat import Mat\n" + body,
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


def test_the_allocated_result_is_a_matrix_of_the_declared_width(built):
    """10 floats in, width 4: two whole rows, the last two samples dropped."""
    out = _outcome(
        built,
        "r = Mat().run(np.arange(10, dtype=np.float32))\n"
        "print(r.shape, r.dtype, r.tolist())",
    )
    assert out == (
        "(2, 4) float32 [[0.0, 1.0, 2.0, 3.0], [4.0, 5.0, 6.0, 7.0]]"
    ), out


def test_a_kernel_that_fills_the_whole_allocation_is_still_a_matrix(built):
    """12 floats in, width 4, capacity 12: the kernel fills EXACTLY what was
    allocated. The flat path hands that array back untouched as a fast path;
    for a matrix that would be a 1-D array, so the fast path must not apply."""
    out = _outcome(
        built,
        "r = Mat().run(np.arange(12, dtype=np.float32))\n"
        "print(r.shape, r[2].tolist())",
    )
    assert out == "(3, 4) [8.0, 9.0, 10.0, 11.0]", out


def test_a_literal_width_needs_no_object(built):
    out = _outcome(
        built,
        "r = Mat().lit(np.arange(7, dtype=np.float32))\n"
        "print(r.shape, r.tolist())",
    )
    assert out == "(2, 3) [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]]", out


def test_no_whole_row_is_an_empty_matrix_not_a_vector(built):
    """The width survives an empty result: (0, 4), so a caller can stack it."""
    out = _outcome(
        built,
        "r = Mat().run(np.arange(3, dtype=np.float32))\n"
        "print(r.shape, r.ndim, r.size)",
    )
    assert out == "(0, 4) 2 0", out


def test_out_is_filled_in_place_and_returned_as_a_matrix_view(built):
    """`out=` is a (5, 4) buffer; the 2 rows produced are a view of it, and
    nothing past them is touched (the rest holds a sentinel)."""
    out = _outcome(
        built,
        "buf = np.full((5, 4), -1.0, dtype=np.float32)\n"
        "r = Mat().run(np.arange(10, dtype=np.float32), out=buf)\n"
        "print(r.shape, r.base is buf or np.shares_memory(r, buf))\n"
        "print(buf[:2].tolist() == r.tolist(), bool((buf[2:] == -1.0).all()))",
    )
    assert out == "(2, 4) True\nTrue True", out


def test_out_of_the_wrong_rank_is_refused_with_the_expected_shape(built):
    out = _outcome(
        built,
        "try:\n"
        "    Mat().run(np.arange(8, dtype=np.float32),\n"
        "              out=np.zeros(20, dtype=np.float32))\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n",
    )
    assert out.startswith("ValueError"), out
    assert "2-D" in out and "4 columns" in out, out


def test_out_of_the_wrong_width_is_refused(built):
    out = _outcome(
        built,
        "try:\n"
        "    Mat().run(np.arange(8, dtype=np.float32),\n"
        "              out=np.zeros((5, 3), dtype=np.float32))\n"
        "except ValueError as e:\n"
        "    print('ValueError', e)\n",
    )
    assert out.startswith("ValueError") and "4 columns" in out, out


def test_a_count_that_is_not_whole_rows_is_a_kernel_error(built):
    """The kernel broke the contract the declaration made: 7 elements into a
    width of 4 would be a ragged last row, so it is refused, not reshaped."""
    out = _outcome(
        built,
        "try:\n"
        "    Mat().ragged(np.arange(7, dtype=np.float32))\n"
        "except RuntimeError as e:\n"
        "    print('RuntimeError', e)\n",
    )
    assert out.startswith("RuntimeError"), out
    assert "7 elements" in out and "4-wide" in out, out


def test_max_out_still_answers_in_elements(built):
    """Declaring a width does not move the unit `_max_out` speaks in."""
    out = _outcome(built, "print(Mat().run_max_out(10))")
    assert out == "12", out


def test_a_method_without_out_cols_is_unchanged(built):
    out = _outcome(
        built,
        "r = Mat().flat(np.arange(10, dtype=np.float32))\n"
        "print(r.shape, r.tolist())",
    )
    assert out == "(10,) [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]", (
        out
    )


# -- the stub -----------------------------------------------------------------


def test_the_stub_documents_the_matrix_and_leaves_flat_alone(built):
    pyi = (built / "src/p/mat.pyi").read_text("utf-8")
    run = pyi[pyi.index("def run(") : pyi.index("def lit(")]
    flat = pyi[pyi.index("def flat(") :]
    assert "2-D" in run and "(rows, cols)" in run, run
    assert "2-D" not in flat, flat


# -- the manifest -------------------------------------------------------------


def _scaffold(tmp_path: Path) -> Path:
    """A project made the way a user makes one: `jm new`, default layout. The
    round trip below compares manifests, and the fragment layout is `jm new`'s
    (`script_round_trip` says why)."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    return tmp_path / "p"


def test_the_cli_flag_writes_the_key_and_apply_renders_it(tmp_path):
    root = _scaffold(tmp_path)
    r = run_cli(
        "object",
        "mat",
        "--state",
        "width:size_t:4",
        "--no-step",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method",
        "mat",
        "run",
        "--param",
        "x:float[]",
        "--return-type",
        "float",
        "--variable-output",
        "--pass-capacity",
        "--out-cols",
        "state->width",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # The key is vocabulary: jm does not warn that it reads nothing.
    assert "unknown method key" not in r.stdout + r.stderr, r.stdout + r.stderr
    manifest = "\n".join(
        p.read_text("utf-8") for p in sorted(root.rglob("*.toml"))
    )
    assert re.search(r'out_cols\s*=\s*"state->width"', manifest), manifest
    ext = (root / "native/src/mat/mat_ext.c").read_text("utf-8")
    assert "size_t _cols = (size_t)(state->width);" in ext
    assert "PyArray_DIM(out_arr, 1)" in ext
    # `jm script` replays it, and the replay renders the same project.
    assert "--out-cols" in run_cli("script", cwd=root).stdout
    orig, replay = script_round_trip(root, tmp_path / "replay")
    assert orig == replay


@pytest.mark.parametrize(
    "extra, needle",
    [
        ([], "variable_output"),
        (["--variable-output", "--multi-output", "int"], "multi_output"),
    ],
)
def test_out_cols_where_it_cannot_apply_is_refused_at_load(
    tmp_path, extra, needle
):
    root = _scaffold(tmp_path)
    run_cli("object", "mat", "--state", "width:size_t:4", cwd=root)
    r = run_cli(
        "method",
        "mat",
        "run",
        "--param",
        "x:float[]",
        "--return-type",
        "float",
        *extra,
        "--out-cols",
        "4",
        cwd=root,
    )
    text = r.stdout + r.stderr
    assert "out_cols" in text and needle in text, text
