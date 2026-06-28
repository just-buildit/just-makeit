"""gh-403: ``serializable = "true"`` on a ``kind = "handle"`` module.

A handle module wraps an opaque C resource (``self->h``) whose core provides the
standard state triplet (``<backing>_state_bytes/get_state/set_state``).  With the
flag set, jm must generate the Python triplet — ``state_bytes()`` /
``get_state() -> bytes`` / ``set_state(bytes)`` — over the handle, byte-identical
to the object binding (gh-400), plus the ``.pyi`` stubs.  Without the flag, the
triplet must be absent.  No C compiler needed — we assert on the generated tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


def _ring_module(*, serializable: bool) -> dict:
    """A toy ringbuf handle, optionally serializable."""
    mod = {
        "kind": "handle",
        "backing": "ringbuf",
        "type_name": "Ring",
        "context_manager": True,
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        "create_args": [
            {"name": "capacity", "type": "size_t", "default": "0"}
        ],
        "methods": [{"name": "clear", "fn": "ringbuf_clear"}],
    }
    if serializable:
        mod["serializable"] = "true"
    return mod


def _project(root: Path, *, serializable: bool) -> None:
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("module", {})["ringbuf"] = _ring_module(
        serializable=serializable
    )
    C.save(root, cfg)


def _ext(root: Path) -> str:
    return (root / "native/src/ringbuf/ringbuf_ext.c").read_text()


def _pyi(root: Path) -> str:
    return (root / "src/proj/ringbuf/ringbuf.pyi").read_text()


def test_serializable_handle_emits_triplet(tmp_path):
    _project(tmp_path, serializable=True)
    apply_run(tmp_path)
    ext = _ext(tmp_path)

    # C wrappers over the backing handle (self->h, <backing>_ prefix).
    assert "Ring_state_bytes(RingObject *self" in ext
    assert "ringbuf_state_bytes(self->h)" in ext
    assert "ringbuf_get_state(self->h, PyBytes_AS_STRING(_b))" in ext
    assert "ringbuf_set_state(self->h, PyBytes_AS_STRING(arg))" in ext
    # PyMethodDef rows.
    assert '{"state_bytes", (PyCFunction)Ring_state_bytes, METH_NOARGS' in ext
    assert '{"get_state", (PyCFunction)Ring_get_state, METH_NOARGS' in ext
    assert '{"set_state", (PyCFunction)Ring_set_state, METH_O' in ext
    # closed guard reused.
    assert '"Ring is closed"' in ext

    pyi = _pyi(tmp_path)
    assert "def state_bytes(self) -> int:" in pyi
    assert "def get_state(self) -> bytes:" in pyi
    assert "def set_state(self, blob: bytes) -> None:" in pyi


def test_non_serializable_handle_has_no_triplet(tmp_path):
    _project(tmp_path, serializable=False)
    apply_run(tmp_path)
    ext = _ext(tmp_path)
    assert "state_bytes" not in ext
    assert "get_state" not in ext
    assert "def set_state" not in _pyi(tmp_path)


def test_serializable_handle_apply_idempotent(tmp_path):
    _project(tmp_path, serializable=True)
    apply_run(tmp_path)
    before = _ext(tmp_path), _pyi(tmp_path)
    apply_run(tmp_path)
    after = _ext(tmp_path), _pyi(tmp_path)
    assert before == after


def test_serializable_flag_survives_manifest_roundtrip(tmp_path):
    _project(tmp_path, serializable=True)
    assert C.is_serializable(C.load(tmp_path), "ringbuf")
    assert (
        'serializable = "true"' in (tmp_path / "just-makeit.toml").read_text()
    )
