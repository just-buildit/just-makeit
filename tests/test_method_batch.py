"""A `--batch` method generates the 1:1-rate block signature (gh-179).

The batch binding calls `<comp>_<name>(state, const in *in, size_t n, out *out)`
(or `(state, size_t n, out *out)` for a void arg_type). The C prototype + stub
must match that, not the scalar `(state, T x)` fall-through — otherwise the
project fails to compile ("too many arguments to function").
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _decl(dest: Path, comp: str, name: str) -> str:
    text = (dest / f"native/inc/{comp}/{comp}_core.h").read_text("utf-8")
    return next(line for line in text.splitlines() if f"{name}(" in line)


def test_batch_method_array_input_signature(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest, fragments=True)
    _silent(module_run, dest, "dsp")
    _silent(
        object_run,
        dest,
        "gain",
        module="dsp",
        state_vars=[("g", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    _silent(
        method_run,
        dest,
        "gain",
        "process_batch",
        "dsp",
        "float",
        "float",
        False,  # variable_output
        [],  # multi_output
        batch=True,
    )
    _silent(apply_run, dest)
    decl = _decl(dest, "gain", "process_batch")
    assert (
        "void gain_process_batch(gain_state_t *state, const float *in,"
        " size_t n, float *out);" in decl
    ), decl
    # the stub matches (no scalar `float x`)
    stub = (dest / "native/src/gain/gain_core.c").read_text("utf-8")
    assert "const float *in, size_t n, float *out" in stub
    assert "process_batch(gain_state_t *state, float x)" not in stub


def test_batch_method_void_input_signature(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest, fragments=True)
    _silent(module_run, dest, "dsp")
    _silent(
        object_run,
        dest,
        "src",
        module="dsp",
        state_vars=[("k", "float", "1.0")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    _silent(
        method_run,
        dest,
        "src",
        "gen_batch",
        "dsp",
        "void",
        "float",
        False,
        [],
        batch=True,
    )
    _silent(apply_run, dest)
    decl = _decl(dest, "src", "gen_batch")
    assert (
        "void src_gen_batch(src_state_t *state, size_t n, float *out);" in decl
    ), decl
