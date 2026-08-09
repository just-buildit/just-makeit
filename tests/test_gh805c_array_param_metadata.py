"""An array param can state its rank and its interleave (gh-805 §C).

An array param used to be declared by element type alone, so jm generated
exactly one length expression — ``PyArray_SIZE(arr)`` — and had no way to
express either of the two things that make that expression wrong.

**Rank.** ``PyArray_FROM_OTF`` with ``NPY_ARRAY_C_CONTIGUOUS`` accepts an array
of any rank, and ``PyArray_SIZE`` then returns the *total* element count. A 2-D
array handed to a 1-D contract therefore does not fail — it flattens, and the
kernel reads a buffer whose shape nobody told it about.

**Interleave.** ``PyArray_SIZE`` counts elements; a kernel taking an
interleaved buffer counts logical samples. A complex pair in an ``int16_t[]``
is two elements and one sample. This is the dangerous one because it compiles:
with `pass_capacity` the kernel receives an element count where it expects a
pair count and writes twice as far as the caller's buffer allows — an overrun,
not a wrong answer.

Both are **opt-in**. Flattening is today's behaviour and a caller handing a
C-contiguous 2-D block to a kernel that wants the flat run is doing something
legitimate, so an unconditional guard would break working trees. Declaring the
key is the author saying which case this parameter is.

The two builders that emit array acquisition — `_context/_parse` for a method
param and `_render` for a module-function param — are a documented peer pair.
They share the emitter here, because an interleave factor applied to one and
not the other is a buffer overrun in whichever face was missed.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _coerce
from just_makeit._apply import run as apply_run
from just_makeit._keys import FUNCTION_PARAM_KEYS, PARAM_KEYS
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _object_with_param(tmp_path, param):
    """Scaffold an object with one method taking *param*, and apply."""
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    cfg = C.load(root)
    cfg["w"]["methods"] = [
        {
            "name": "dec",
            "arg_type": "void",
            "return_type": "int",
            "params": [param],
        }
    ]
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    return root, (root / "native/src/w/w_ext.c").read_text()


def test_rank_emits_the_ndim_guard(tmp_path):
    """The guard that stops a 2-D array flattening into a 1-D contract."""
    _, ext = _object_with_param(
        tmp_path, {"name": "x", "type": "int16_t[]", "rank": 1}
    )
    assert "PyArray_NDIM(x_arr) != 1" in ext, (
        f"no rank guard emitted for a param declaring rank = 1:\n{ext[:400]}"
    )


def test_the_guard_runs_before_the_length_is_taken(tmp_path):
    """Order is the whole point.

    A guard after `PyArray_SIZE` would compute the flattened length first and
    reject afterwards, which is merely a slower way to be right — but the
    length is also what a `pass_capacity` kernel is handed, so anything reading
    it between the two would read the wrong number.
    """
    _, ext = _object_with_param(
        tmp_path, {"name": "x", "type": "int16_t[]", "rank": 1}
    )
    assert ext.index("PyArray_NDIM(x_arr)") < ext.index("size_t x_len"), (
        "the rank guard is emitted after the length it exists to protect"
    )


def test_elements_per_sample_divides_the_length(tmp_path):
    """Element count in, sample count out."""
    _, ext = _object_with_param(
        tmp_path,
        {"name": "x", "type": "int16_t[]", "elements_per_sample": 2},
    )
    assert "size_t x_len = (size_t)PyArray_SIZE(x_arr) / 2;" in ext, (
        f"the interleave factor never reached the length:\n{ext[:400]}"
    )


def test_neither_key_changes_an_ordinary_param(tmp_path):
    """No churn: every existing param renders the line it always did."""
    _, ext = _object_with_param(tmp_path, {"name": "x", "type": "int16_t[]"})
    assert "size_t x_len = (size_t)PyArray_SIZE(x_arr);" in ext
    assert "PyArray_NDIM" not in ext, (
        "an undeclared param grew a rank guard, which would reject the 2-D "
        "arrays callers legitimately flatten today"
    )


def test_both_keys_survive_the_manifest_round_trip(tmp_path):
    """gh-838's shape: a key the validator accepts and no writer persists.

    That registry once said `capsule` was valid while `_dump` had no branch
    writing it, so the declaration came back as something else entirely. A key
    that does not round-trip is worse than an unrecognised one — it works
    until the next `jm apply` and then silently stops.
    """
    root, _ = _object_with_param(
        tmp_path,
        {
            "name": "x",
            "type": "int16_t[]",
            "rank": 1,
            "elements_per_sample": 2,
        },
    )
    back = C.load(root)["w"]["methods"][0]["params"][0]
    assert back.get("rank") == 1, back
    assert back.get("elements_per_sample") == 2, back


def test_both_param_kinds_recognise_the_keys():
    """The peer pair, gated rather than remembered.

    `_context/_parse` (method params) and `_render` (module-function params)
    both emit array acquisition and now share one emitter. Recognising a key
    on only one side would accept it in the manifest and drop it in the C —
    which is how this pair has drifted before.
    """
    for key in ("rank", "elements_per_sample"):
        assert key in PARAM_KEYS, f"method params reject {key}"
        assert key in FUNCTION_PARAM_KEYS, f"function params reject {key}"


def test_the_guard_uses_the_initproc_return_where_it_must():
    """`return NULL` inside an `initproc` compiles and reports success.

    The same split `capsule_new_c` draws. This pins that the emitter takes the
    choice rather than hard-coding one, because the failure is silent: the
    constructor returns a non-zero value that `tp_init` reads as success.
    """
    assert "return -1;" in _coerce.array_rank_guard(
        "h", "h_arr", 1, fail="return -1;"
    )
    assert "return NULL;" in _coerce.array_rank_guard("h", "h_arr", 1)


def test_the_guard_releases_prior_arrays(tmp_path):
    """A bailout after two acquisitions must free both.

    The guard runs after its own array is acquired, so it releases that one on
    top of whatever the caller passes — get this wrong and a rejected call
    leaks an ndarray per invocation.
    """
    out = _coerce.array_rank_guard(
        "y", "y_arr", 1, decrefs="Py_DECREF(x_arr);"
    )
    assert "Py_DECREF(x_arr);" in out and "Py_DECREF(y_arr);" in out
