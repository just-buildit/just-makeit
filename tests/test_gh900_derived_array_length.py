"""An array can name its length parameter and put it first (gh-900).

jm passes an array init-param's length **after** the data pointer, always:

    hbd_state_t *hbd_create(const float *h, size_t h_len);

An adopted C API often takes it the other way round, and first:

    hbdecim_state_t *hbdecim_create(size_t num_taps, const float *h);

That was inexpressible, so the object was hand-owned in full — and by #805's
thesis a hand-owned fragment stops receiving every future codegen fix,
silently. `derived = "num_taps"` declares it.

Two things this is deliberately NOT:

**Not a Python-facing parameter.** The value is the array's own length, so it
must not reach `kwlist`, the `PyArg` format, the `.pyi` `__init__`, or the
class docstring's `Parameters`. A caller who had to pass it could pass a wrong
one.

**Not a rename of the C local.** `array_args_parse_block` still produces
`<name>_len`; only the *declared* parameter's name and position change. That
keeps the change to the two places that describe the prototype, rather than
threading a new variable through the acquisition code.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._cli_parse import parse_init_param_flag
from just_makeit._keys import INIT_PARAM_KEYS
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._script import run as script_run

_DERIVED = (
    "h",
    "float[]",
    "",
    "",
    "",
    "",
    False,
    "",
    False,
    "",
    "",
    "",
    "num_taps",
)
_PLAIN = ("h", "float[]", "")


def _project(tmp_path, param):
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(
            root,
            "hbdecim",
            None,
            arg_type="float",
            return_type="float",
            init_params=[param],
        )
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    return root


def _header(root):
    return (root / "native/inc/hbdecim/hbdecim_core.h").read_text()


def _ext(root):
    return (root / "native/src/hbdecim/hbdecim_ext.c").read_text()


def test_the_length_is_named_and_comes_first(tmp_path):
    """The prototype the issue asks for, in the sacred header."""
    header = _header(_project(tmp_path, _DERIVED))
    assert (
        "hbdecim_state_t *hbdecim_create(size_t num_taps, const float *h);"
        in header
    ), [ln for ln in header.splitlines() if "hbdecim_create" in ln]


def test_the_call_matches_the_prototype(tmp_path):
    """Order in the call must follow order in the declaration.

    They are written by two different loops over the same params; a mismatch
    compiles only by luck (both are pointer-sized here) and then passes a
    length where a pointer is expected.
    """
    ext = _ext(_project(tmp_path, _DERIVED))
    assert (
        "hbdecim_create(h_len, (const float *)PyArray_DATA(h_arr))" in ext
    ), [ln for ln in ext.splitlines() if "hbdecim_create(" in ln]


def test_it_is_not_a_python_argument(tmp_path):
    """A derived value the caller could get wrong is not an argument."""
    root = _project(tmp_path, _DERIVED)
    ext, pyi = _ext(root), (root / "src/p/hbdecim.pyi").read_text()
    assert '{"h", NULL}' in ext, "num_taps leaked into kwlist"
    assert "num_taps" not in pyi, (
        f"the stub asks the caller for a length jm derives:\n{pyi[:600]}"
    )
    assert "def __init__(self, h: npt.ArrayLike) -> None: ..." in pyi


def test_without_it_the_default_shape_is_unchanged(tmp_path):
    """Opt-in: every existing array param keeps the trailing length."""
    root = _project(tmp_path, _PLAIN)
    assert (
        "hbdecim_state_t *hbdecim_create(const float *h, size_t h_len);"
        in _header(root)
    )
    assert "hbdecim_create((const float *)PyArray_DATA(h_arr), h_len)" in _ext(
        root
    )


def test_the_doxygen_documents_both_parameters(tmp_path):
    """The header is the author's reference; a silent parameter is worse
    there than anywhere else, because it is the only place the C caller
    looks."""
    header = _header(_project(tmp_path, _DERIVED))
    assert "@param num_taps" in header
    assert "@param h" in header


def test_the_cli_form_parses(tmp_path):
    """`name:type:derived:<c-param>`, a slot-3 keyword like `capsule`."""
    tok, nxt = parse_init_param_flag(
        ["--init-param", "h:float[]:derived:num_taps"], 0
    )
    assert nxt == 2
    assert tok == _DERIVED


@pytest.mark.parametrize(
    "spec",
    [
        "h:float[]:derived",  # no name given
        "n:int:derived:num_taps",  # not an array
        "b:float[][]:derived:rows",  # 2-D already passes its shape
    ],
)
def test_the_cli_rejects_nonsense(spec):
    """Each of these would otherwise emit C that does not compile."""
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            parse_init_param_flag(["--init-param", spec], 0)


def test_it_round_trips_and_replays(tmp_path):
    """Manifest, `jm apply` and `jm script` all carry it.

    Dropping it anywhere replays the array with jm's trailing `<name>_len` —
    a project rebuilt with a *different* `create()` prototype, against C the
    author did not change. `jm script` did exactly that until this was wired.
    """
    root = _project(tmp_path, _DERIVED)
    assert C.init_params(C.load(root), "hbdecim")[0] == _DERIVED
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        script_run(root)
    assert "h:float[]:derived:num_taps" in buf.getvalue(), (
        f"`jm script` loses the declaration:\n{buf.getvalue()}"
    )


def test_it_is_a_recognised_init_param_key():
    """Otherwise `jm status` warns about jm's own key (gh-805 §G)."""
    assert "derived" in INIT_PARAM_KEYS
