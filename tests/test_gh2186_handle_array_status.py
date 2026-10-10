"""gh-2186: an array-argument handle method can say its ``int`` is a status.

doppler's ``StreamSink.send(iq, fs, fc)`` wraps ``int
dp_wfm_stream_sink_send(sink, const float _Complex *iq, size_t n, double fs,
double fc)``: 0 when the stream took the block, non-zero when it refused it.
jm reads the return of a method with an array argument as a count, so
``error = "OSError"`` there was refused (gh-1118, rightly: before it, the key
was ignored), and the manifest had no way to say the reading was wrong for
this call. The refusal told the author to drop the key.

``status_return = true`` is that way, and it is the OBJECT face's key with
the object face's meaning, not a new one: the ``int`` is a status, a non-zero
return raises ``error`` (``ValueError`` when none is named) and the method
returns ``None`` in the binding and in the ``.pyi``.

Each gate reads an artefact jm produced, never the manifest it was given:

- the compiled binding raises on a refused block and returns ``None`` on a
  taken one, with and without a named exception;
- the generated ``.pyi`` and the runtime ``__doc__`` say so;
- without the key, ``jm apply`` refuses with one ``error:`` line naming
  ``status_return = true``, and leaves the project as it was;
- for every shape ``_emit_method`` has, the key is honoured or refused,
  never accepted and ignored (gh-1118's invariant, for the second key).
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _handle  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402
from test_gh1118_refuse_unhonoured_error import (  # noqa: E402
    _BASE,
    _SHAPES,
)

_SINK_H = """\
#ifndef SINK_H
#define SINK_H
#include <stddef.h>
typedef struct sink sink_t;
sink_t *sink_open(size_t room);
void sink_close(sink_t *s);
int sink_send(sink_t *s, const float _Complex *iq, size_t n, double fs,
              double fc);
int sink_push(sink_t *s, const float *x, size_t n, int r);
size_t sink_taken(const sink_t *s);
#endif
"""

# A stream with room for a fixed number of samples: a block that does not fit
# is REFUSED whole (rc 7) and takes nothing, which is the reconnect case the
# issue describes. `push` refuses an empty block with the rc it is handed.
_SINK_C = """\
#include "sink/sink.h"
#include <stdlib.h>
struct sink { size_t room, taken; };
sink_t *sink_open(size_t room) {
    sink_t *s = calloc(1, sizeof *s);
    if (s) s->room = room;
    return s;
}
void sink_close(sink_t *s) { free(s); }
int sink_send(sink_t *s, const float _Complex *iq, size_t n, double fs,
              double fc) {
    (void)iq; (void)fs; (void)fc;
    if (n > s->room - s->taken) return 7;
    s->taken += n;
    return 0;
}
int sink_push(sink_t *s, const float *x, size_t n, int r) {
    (void)x;
    if (!n) return r;
    s->taken += n;
    return 0;
}
size_t sink_taken(const sink_t *s) { return s->taken; }
"""

#: The issue's method, and the key without `error`, which raises what the
#: object face raises: `ValueError`. `push`'s trailing scalar is named `r`
#: on purpose: that is the result local a count-reading array binding
#: declares, and a status binding keeps its rc in `_rc` instead, so the name
#: is free here and `jm apply` must not refuse it as a collision (gh-1525).
_SEND = {
    "name": "send",
    "fn": "sink_send",
    "returns": "int",
    "status_return": True,
    "error": "OSError",
    "error_message": "the stream refused the block",
    "args": [
        {"name": "iq", "type": "float _Complex[]"},
        {"name": "fs", "type": "double"},
        {"name": "fc", "type": "double", "default": "0.0"},
    ],
}
_PUSH = {
    "name": "push",
    "fn": "sink_push",
    "returns": "int",
    "status_return": True,
    "args": [
        {"name": "x", "type": "float[]"},
        {"name": "r", "type": "int", "default": "1"},
    ],
}


def _sink_module(*methods: dict) -> dict:
    return {
        "kind": "handle",
        "backing": "sink",
        "header": "sink/sink.h",
        "type_name": "Sink",
        "create_fn": "sink_open",
        "close_fn": "sink_close",
        "create_args": [{"name": "room", "type": "size_t"}],
        "methods": [
            *methods,
            {"name": "taken", "fn": "sink_taken", "returns": "size_t"},
        ],
    }


def _project(tmp: Path, module: dict) -> Path:
    """A fresh project carrying *module*, through the CLI where it has one.

    A handle module is manifest-only, so its table is written through
    `_config`, the manifest's one writer, as an author's edit would be.
    """
    from test_handle_build import _placed

    root = tmp / "proj"
    r = run_cli("new", "proj", "--object", "widget", cwd=tmp)
    assert r.returncode == 0, r.stderr
    cfg = C.load(root)
    cfg.setdefault("module", {})["sink"] = _placed(module, root)
    C.save(root, cfg)
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The issue's sink, applied by `jm apply`, compiled and imported.

    A failed apply or compile is returned rather than raised, so each test
    that needs the build FAILS on it by name instead of erroring in setup.
    """
    if default_cc() is None:
        pytest.skip("no C compiler available")
    from test_handle_build import _compile_import

    tmp = tmp_path_factory.mktemp("gh2186")
    root = _project(tmp, _sink_module(_SEND, _PUSH))
    applied = run_cli("apply", cwd=root)
    if applied.returncode != 0:
        return root, f"jm apply failed:\n{applied.stderr}"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return root, _compile_import(root, "sink", _SINK_H, _SINK_C)
    except subprocess.CalledProcessError as exc:
        return root, f"the binding did not compile:\n{exc.stderr}"


def _built(built):
    """``(root, module)``, once the sink is known to have built."""
    root, mod = built
    assert not isinstance(mod, str), mod
    return root, mod


def test_a_refused_block_raises_the_declared_exception(built):
    """The issue's ask: a send the stream refuses raises; one it takes is
    ``None``."""
    import numpy as np

    _, mod = _built(built)
    s = mod.Sink(4)
    assert s.send(np.zeros(3, np.complex64), 1e6) is None
    assert s.taken() == 3
    refused = r"the stream refused the block \(rc=7\)"
    with pytest.raises(OSError, match=refused):
        s.send(np.zeros(2, np.complex64), 1e6, fc=2.5e9)
    assert s.taken() == 3, "a refused block must not count as taken"


def test_status_return_alone_raises_value_error(built):
    """The object face's meaning, unchanged: no `error` is `ValueError`.
    The rc in the message is the one the C call returned, through the
    trailing scalar."""
    import numpy as np

    _, mod = _built(built)
    s = mod.Sink(4)
    with pytest.raises(ValueError, match=r"sink_push failed \(rc=1\)"):
        s.push(np.zeros(0, np.float32))
    with pytest.raises(ValueError, match=r"sink_push failed \(rc=3\)"):
        s.push(np.zeros(0, np.float32), r=3)
    assert s.push(np.ones(2, np.float32)) is None
    assert s.taken() == 2


def test_both_doc_faces_say_none_and_name_the_raise(built):
    """The `.pyi` and `help()` describe the binding beside them."""
    root, mod = _built(built)
    pyi = next(root.rglob("sink.pyi")).read_text()
    for name, exc in (("send", "OSError"), ("push", "ValueError")):
        sig = re.search(rf"def {name}\((.*?)\) -> (.+?):\n", pyi, re.S)
        assert sig is not None, pyi
        assert sig.group(2) == "None", (name, sig.group(0))
        body = pyi[sig.end() :].split("    def ")[0]
        assert re.search(rf"Raises\n\s*-+\n\s*{exc}\n", body), (name, body)
        assert exc in getattr(mod.Sink, name).__doc__


def test_apply_keeps_the_key(built):
    """`jm apply` writes the manifest back; the key must survive it."""
    root, _ = _built(built)
    methods = C.load(root)["module"]["sink"]["methods"]
    assert [m.get("status_return") for m in methods] == [True, True, None]


def test_without_the_key_apply_refuses_and_names_it(tmp_path):
    """The refusal stays, and now names the spelling nobody could guess."""
    send = {k: v for k, v in _SEND.items() if k != "status_return"}
    root = _project(tmp_path, _sink_module(send))
    before = {
        p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    assert "the array argument 'iq'" in errors[0]
    assert "`status_return = true`" in errors[0]
    assert "Traceback" not in r.stderr
    after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert after == before, "a refused apply must leave the project alone"


def _render(m: dict) -> str:
    cfg = {
        "project": {"name": "demo"},
        "module": {"sink": {**_BASE, "methods": [m]}},
    }
    return _handle.render_ext(cfg, "sink")


@pytest.mark.parametrize("error", [None, "OSError"])
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_status_return_is_honoured_or_refused_never_ignored(shape, error):
    """gh-1118's invariant for the second key, over every shape it lists.

    Honoured means all three faces agree: the binding raises the declared
    class and returns ``None``, and the stub says ``-> None``.
    """
    m = {"name": "op", "fn": "sink_op", **_SHAPES[shape]}
    m["status_return"] = True
    if error:
        m["error"] = error
    try:
        ext = _render(m)
    except Refusal:
        assert shape in ("array_out", "bytes_out"), (
            f"{shape}: an int return there can be a status, and was refused"
        )
        return
    body = ext.split("Sink_op(")[1].split("\nstatic ")[0]
    assert f"PyErr_Format(PyExc_{error or 'ValueError'}" in body, shape
    assert "Py_RETURN_NONE" in body, shape
    assert _handle.raises_instead_of_returning(m), shape
    assert _handle.py_face(m).ann == "None", shape


@pytest.mark.parametrize(
    "extra, said",
    [
        ({}, "status_return requires an `int` status return"),
        ({"returns": "double"}, 'returns = "double" is not an integer'),
        (
            {"returns": "float[]", "args": [{"name": "n", "type": "size_t"}]},
            'with returns = "float[]" the C return is the output length',
        ),
    ],
    ids=["no-returns", "non-integer", "array-result"],
)
def test_status_return_where_it_means_nothing_is_refused(extra, said):
    """Each refusal names what is wrong with THIS declaration."""
    m = {
        "name": "op",
        "fn": "sink_op",
        "status_return": True,
        "args": [{"name": "x", "type": "float[]"}],
        **extra,
    }
    with pytest.raises(Refusal) as excinfo:
        _render(m)
    assert said in str(excinfo.value)
