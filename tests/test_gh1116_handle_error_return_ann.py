"""gh-1116: the handle stub's return annotation must match what the binding
returns.

A `kind = "handle"` method declaring `error = "..."` got `-> None` when it
took arguments and `-> <returns>` when it took none, from declarations that
differed only in the argument. Both bindings raise and `Py_RETURN_NONE`, so
the zero-arg stub was wrong about the function beside it: a caller trusting
it writes ``if sink.send_eos() != 0:`` and takes the error branch on every
success.

Cause: `_emit_method` and `render_pyi` classify a method's shape
independently, and only the second ever asked about `error` — in ONE of its
six branches.

The gate below is deliberately not a list of expected annotations. It renders
the binding, reads out of the generated C what that binding actually does,
and asserts `raises_instead_of_returning` and the `.pyi` both agree with it.
A seventh shape that consumes `error` some new way fails this test rather
than quietly joining the drift.
"""

import re
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

# One method per shape `_emit_method` distinguishes, each declaring the SAME
# `error`. The shapes are the axis this bug lives on, so they are enumerated
# here and nowhere else in the file.
_SHAPES = {
    "zero_arg": {"returns": "int"},
    "scalar_arg": {
        "returns": "int",
        "args": [{"name": "timeout_ms", "type": "int", "default": "0"}],
    },
    "path_arg": {"returns": "int", "args": [{"name": "p", "type": "path"}]},
    "array_in": {
        "returns": "int",
        "args": [{"name": "x", "type": "float[]"}],
    },
    "array_out": {
        "returns": "float[]",
        "args": [{"name": "n", "type": "size_t"}],
    },
    "bytes_out": {"returns": "bytes", "out_len_fn": "sink_len"},
}


def _method(extra):
    return {"name": "op", "fn": "sink_op", "error": "OSError", **extra}


def _cfg(extra):
    return {
        "project": {"name": "demo"},
        "module": {"sink": {**_BASE, "methods": [_method(extra)]}},
    }


def _binding_returns_none(cfg):
    """Read out of the GENERATED C whether `op` raises and returns None.

    Deliberately not "does the manifest say error" — that is the claim under
    test. This asks the artefact.
    """
    ext = _handle.render_ext(cfg, "sink")
    body = ext.split("Sink_op(")[1].split("\nstatic ")[0]
    return "PyErr_Format(PyExc_OSError" in body and "Py_RETURN_NONE" in body


def _stub_ann(cfg):
    pyi = _handle.render_pyi(cfg, "sink")
    line = next(
        ln for ln in pyi.splitlines() if ln.strip().startswith("def op(")
    )
    return re.search(r"->\s*(.+?):\s*$", line).group(1)


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_stub_annotation_matches_the_binding(shape):
    cfg = _cfg(_SHAPES[shape])
    returns_none = _binding_returns_none(cfg)
    ann = _stub_ann(cfg)
    assert (ann == "None") == returns_none, (
        f"{shape}: binding returns "
        f"{'None' if returns_none else 'its value'} but the stub says {ann}"
    )


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_predicate_matches_the_binding(shape):
    """The predicate is the SSOT, so it must describe the real artefact."""
    cfg = _cfg(_SHAPES[shape])
    assert _handle.raises_instead_of_returning(
        _method(_SHAPES[shape])
    ) is _binding_returns_none(cfg)


def test_the_two_declarations_from_the_report_agree():
    """The reported pair: identical apart from the argument (gh-1116)."""
    cfg = {
        "project": {"name": "demo"},
        "module": {
            "sink": {
                **_BASE,
                "methods": [
                    {
                        "name": "drain",
                        "fn": "sink_drain",
                        "returns": "int",
                        "error": "OSError",
                        "args": [
                            {
                                "name": "timeout_ms",
                                "type": "int",
                                "default": "0",
                            }
                        ],
                    },
                    {
                        "name": "send_eos",
                        "fn": "sink_send_eos",
                        "returns": "int",
                        "error": "OSError",
                    },
                ],
            }
        },
    }
    pyi = _handle.render_pyi(cfg, "sink")
    assert "def drain(self, timeout_ms: int = ...) -> None:" in pyi
    assert "def send_eos(self) -> None:" in pyi
    assert "-> int:" not in pyi


def test_a_method_without_error_still_returns_its_value():
    """The predicate must not swallow an ordinary return."""
    cfg = _cfg({"returns": "int"})
    cfg["module"]["sink"]["methods"][0].pop("error")
    assert _stub_ann(cfg) == "int"
