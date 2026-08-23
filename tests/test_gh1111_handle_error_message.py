"""gh-1111: `error_message` and the `Raises` doc on a handle-module method.

Two declaration sites accept ``error`` / ``error_message``, and only one
honoured them. An object method generated the author's message into the
binding *and* a numpy ``Raises`` section in the stub; the same keys on a
``kind = "handle"`` module produced the right exception *type* with a canned
message and a stub with no ``Raises`` section at all — so
``drain(self, timeout_ms: int = ...) -> None`` read as a call that cannot
fail.

That is backwards from where the documentation is needed: a handle module is
the shape that wraps files, sockets and devices, so a drain / flush / close
whose failure a caller must handle mostly lives here.

Both faces now read one pair, ``_diagnostics.handle_declared_raise``, so the
class the stub advertises is by construction the class the binding raises.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle
from just_makeit._context import _diagnostics


MESSAGE = "the drain budget ran out with data still pending"


def _sink_cfg(message=MESSAGE, error="OSError"):
    """A `StreamSink` handle whose `drain` is a status method (gh-1111)."""
    m = {
        "name": "drain",
        "fn": "wfm_stream_sink_drain",
        "returns": "int",
        "args": [{"name": "timeout_ms", "type": "int", "default": "0"}],
    }
    if error is not None:
        m["error"] = error
    if message is not None:
        m["error_message"] = message
    return {
        "project": {"name": "demo"},
        "module": {
            "sink": {
                "kind": "handle",
                "backing": "wfm_sink",
                "header": "wfm/wfm_sink.h",
                "type_name": "StreamSink",
                "close_fn": "wfm_stream_sink_close",
                "create_fn": "wfm_stream_sink_open",
                "create_args": [{"name": "path", "type": "path"}],
                "methods": [m],
            }
        },
    }


class TestBinding:
    """The `PyErr_Format` the generated wrapper raises with."""

    def test_declared_message_reaches_the_binding(self):
        s = _handle.render_ext(_sink_cfg(), "sink")
        assert f'"{MESSAGE}",' in s
        # ...and the canned text it replaced is gone, not merely joined.
        assert "wfm_stream_sink_drain failed" not in s

    def test_message_is_an_argument_not_the_format(self):
        """A `%` in ordinary prose must not become a live conversion.

        Spliced in as the format string — which is what the hand-written copy
        did with the C `fn` — ``PyErr_Format`` walks off the end of its
        varargs, on the error path only.
        """
        s = _handle.render_ext(_sink_cfg(message="100% of the budget"), "sink")
        assert 'PyErr_Format(PyExc_OSError, "%s (rc=%lld)",' in s
        assert '"100% of the budget",' in s

    def test_undeclared_message_still_names_the_c_function(self):
        s = _handle.render_ext(_sink_cfg(message=None), "sink")
        assert '"wfm_stream_sink_drain failed",' in s


class TestStub:
    """The numpy `Raises` section in the generated `.pyi`."""

    def test_raises_section_names_class_and_message(self):
        pyi = _handle.render_pyi(_sink_cfg(), "sink")
        assert "Raises" in pyi
        assert "OSError" in pyi
        assert MESSAGE in pyi

    def test_documented_class_follows_the_declaration(self):
        pyi = _handle.render_pyi(_sink_cfg(error="RuntimeError"), "sink")
        assert "RuntimeError" in pyi
        assert "OSError" not in pyi

    def test_method_without_a_declared_raise_is_unchanged(self):
        """The one-line stub must stay byte-identical for everything else.

        `jm status --check` compares generated files byte-for-byte, so
        upgrading a member that declares nothing would report drift in every
        existing handle project in exchange for no information.
        """
        pyi = _handle.render_pyi(_sink_cfg(error=None, message=None), "sink")
        # No `error`, so the int return crosses as an int (not a raise).
        assert '"""drain(timeout_ms=0) -> int."""' in pyi
        assert "Raises" not in pyi


class TestSharedReading:
    """One pair behind both faces."""

    def test_handle_spelling_is_read(self):
        m = {"name": "drain", "fn": "sink_drain", "returns": "int"}
        assert _diagnostics.handle_declared_raise(m) is None
        m["error"] = "OSError"
        assert _diagnostics.handle_declared_raise(m) == (
            "OSError",
            "sink_drain failed",
        )

    def test_object_face_ignores_a_bare_error(self):
        """`declared_raise` must NOT fire on `error` alone.

        The object side rejects that combination at declaration time, so a
        hand-written manifest carrying it has a binding that does not raise —
        and a doc face that advertised an exception anyway would be gh-869 in
        reverse.
        """
        assert (
            _diagnostics.declared_raise({"name": "x", "error": "OSError"})
            is None
        )
