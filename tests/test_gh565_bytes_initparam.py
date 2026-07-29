"""gh-565: `type = "bytes"` init-param — an opaque blob crossing into C as a
borrowed ``(const void *, size_t)`` pair via the ``y#`` PyArg format.

The input twin of a ``bytes``-returning method, and the first slice of
doppler's ``Plan`` save/restore (``PlanFromBlob(blob)``). These lock the C
constructor slots (``make_state_ctx``) and BOTH ``.pyi`` peers — the standalone
docstring/signature (``make_state_ctx``) and the module-aggregated stub
(``_stubs._obj_stub``) — which have drifted on every prior init-param type.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import contextlib
import io

import pytest

from just_makeit import _config as C
from just_makeit._context._state import make_state_ctx
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._stubs import _obj_stub


def _ctx():
    return make_state_ctx(
        "plan", "Plan", [], no_state=True, init_params=[("blob", "bytes", "")]
    )


def test_create_signature_is_void_ptr_size_pair():
    ctx = _ctx()
    # The blob expands to two C args, like a 1-D array (ptr + length).
    assert ctx["create_params"] == "const void *blob, size_t blob_len"
    # The C call forwards both, casting the borrowed buffer and its length.
    assert "(const void *)blob, (size_t)blob_len" in ctx["create_line"]


def test_parse_block_uses_y_hash_and_declares_locals():
    ctx = _ctx()
    assert ctx["init_parse_fmt"] == "y#"
    block = ctx["init_parse_block"]
    # Both locals declared, and the y# converter targets them — no release
    # step (y# borrows the buffer for the call's duration).
    assert "const char *blob = NULL;" in block
    assert "Py_ssize_t blob_len = 0;" in block
    assert '"y#"' in block
    assert "&blob, &blob_len" in block
    assert "Py_XDECREF(blob)" not in block


def test_kwlist_and_pyi_slot():
    ctx = _ctx()
    assert ctx["init_kwlist"] == '"blob", NULL'
    # Standalone signature slot: required (no `= ...`), typed `bytes`.
    assert ctx["init_params_pyi"] == "blob: bytes"
    # Standalone docstring documents the required blob param.
    assert "blob : bytes" in ctx["pyi_param_docs"]
    assert "(required)" in ctx["pyi_param_docs"]


def test_ctor_is_unseedable_no_example():
    # jm cannot invent a valid opaque blob, so the smoke/example is suppressed
    # (the `...` sentinel), exactly as for a path. gh-610: rendered as a
    # keyword arg like every other param, but still containing the `...`
    # sentinel the "..." in py_create_args" suppression check looks for.
    ctx = _ctx()
    assert ctx["py_create_args"] == "blob=..."
    assert "..." in ctx["py_create_args"]


def test_module_stub_peer_matches(tmp_path):
    # The module-aggregated stub (_stubs._obj_stub) is the other .pyi peer; it
    # must produce the same required `blob: bytes` signature and doc as the
    # standalone slot above (they have drifted on every prior init-param type).
    d = tmp_path / "proj"
    d.mkdir()
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", d, [], [])
        module_run(d, "wfm_plan_mod", ["plan"])
        object_run(
            d,
            "plan",
            module="wfm_plan_mod",
            state_vars=[],
            no_state=True,
            init_params=[("blob", "bytes", "")],
        )
    cfg = C.load(d)
    # Round-trip preserves the bytes type on the init-param.
    assert C.init_params(cfg, "plan")[0][:2] == ("blob", "bytes")
    stub = _obj_stub(cfg, "plan")
    assert "def __init__(self, blob: bytes) -> None: ..." in stub
    assert "blob : bytes" in stub
    assert "blob: Any" not in stub  # never the phantom-Any fallback


def test_bytes_rejects_array_dispatch_combo():
    # A bytes blob cannot combine with array-dispatch / optional-array
    # init-params: the create() is emitted in nested brace scopes where routing
    # the (ptr, len) pair is untested and unneeded (doppler's restore takes the
    # blob alone). Reject it with an actionable error.
    with pytest.raises(ValueError, match="'bytes' init-param cannot be"):
        make_state_ctx(
            "plan",
            "Plan",
            [],
            init_params=[
                ("blob", "bytes", ""),
                ("taps", "double[]", "", "", "", "", True, "alt_fn", False),
            ],
        )
