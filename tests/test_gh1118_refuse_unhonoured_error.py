"""gh-1118: a handle method's `error` is honoured or refused — never ignored.

`error = "<category>"` was read by every shape and used by three of six. The
array-in, array-out and `bytes` bindings reach their own `return` in
`_emit_method` before the validation block, so on those shapes the key was
read, never validated and never used:

- an unrecognised exception name was accepted in silence on exactly those
  three, while being refused on the other three; and
- a recognised one declared an exception that can never be raised.

The second is the quieter defect. #1116 was the stub disagreeing with the
binding, which mypy or a reader catches; this is the binding disagreeing with
the *manifest*, invisible until the C returns a failure code in production and
the caller receives it as data.

The gate is the invariant, not a list: for every shape, an `error` declaration
either produces a binding that RAISES, or is refused outright. A seventh shape
must land in one of those two buckets to pass.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle


_BASE = {
    "kind": "handle",
    "backing": "sink",
    "header": "sink/sink.h",
    "type_name": "Sink",
    "close_fn": "sink_close",
    "create_fn": "sink_open",
    "create_args": [{"name": "path", "type": "path"}],
}

_SHAPES = {
    "zero_arg": {"returns": "int"},
    "scalar_arg": {
        "returns": "int",
        "args": [{"name": "timeout_ms", "type": "int", "default": "0"}],
    },
    "path_arg": {"returns": "int", "args": [{"name": "p", "type": "path"}]},
    "array_in": {"returns": "int", "args": [{"name": "x", "type": "float[]"}]},
    "array_out": {
        "returns": "float[]",
        "args": [{"name": "n", "type": "size_t"}],
    },
    "bytes_out": {"returns": "bytes", "out_len_fn": "sink_len"},
}


def _render(extra, error="OSError"):
    m = {"name": "op", "fn": "sink_op", **extra}
    if error is not None:
        m["error"] = error
    cfg = {
        "project": {"name": "demo"},
        "module": {"sink": {**_BASE, "methods": [m]}},
    }
    return _handle.render_ext(cfg, "sink")


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_error_is_honoured_or_refused_never_ignored(shape):
    """The invariant. No shape may accept `error` and do nothing with it."""
    try:
        ext = _render(_SHAPES[shape])
    except ValueError:
        return  # refused — an acceptable answer
    body = ext.split("Sink_op(")[1].split("\nstatic ")[0]
    assert "PyErr_Format(PyExc_OSError" in body, (
        f"{shape}: `error` was accepted but the binding never raises it — "
        "the declaration promises an exception that cannot happen"
    )


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_unrecognised_category_is_refused_on_every_shape(shape):
    """The check existed; it just could not be reached from three shapes."""
    with pytest.raises(ValueError, match="not a recognised exception"):
        _render(_SHAPES[shape], error="NotAThing")


@pytest.mark.parametrize("shape", ["array_in", "array_out", "bytes_out"])
def test_refusal_names_the_consumer_and_a_way_out(shape):
    """A refusal that cannot be acted on is worse than the silence.

    Each message must name what consumes the return on THIS shape, so the
    reader does not have to guess which of their keys is the problem.
    """
    with pytest.raises(ValueError) as excinfo:
        _render(_SHAPES[shape])
    msg = str(excinfo.value)
    assert "needs a status return" in msg
    assert "Drop `error`" in msg
    named = {
        "array_in": "the array argument 'x'",
        "array_out": 'returns = "float[]"',
        "bytes_out": 'returns = "bytes"',
    }[shape]
    assert named in msg


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_no_error_declared_is_untouched(shape):
    """The refusal must not fire on a method that declares nothing."""
    ext = _render(_SHAPES[shape], error=None)
    assert "Sink_op(" in ext


def test_error_without_a_return_still_refused():
    with pytest.raises(ValueError, match="requires an .int. status return"):
        _render({})
