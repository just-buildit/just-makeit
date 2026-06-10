"""Async stream generator (gh-206): opt-in ``--async-stream``.

On top of the synchronous ``stream()`` / ``__iter__`` (#202), ``--async-stream``
makes the generated iterator also implement ``__aiter__`` / ``__anext__`` (and
the object an async-iterable). ``__anext__`` offloads the GIL-holding producer
call to the running loop's default executor, so a ``nogil`` producer lets the
loop run while the kernel works; on drain the executor callable raises
``StopAsyncIteration``. All in C — no Python wrapper class.

Covers: the async C glue + ``tp_as_async`` slots are emitted; a plain
``--streamable`` object is byte-clean (no async glue); the manifest persists
``async_stream``; the ``.pyi`` grows ``__aiter__`` + the ``AsyncIterator``
import; and an end-to-end build + real ``async for`` (over ``stream(...)`` and
over the object), with sync iteration still working.
"""

import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from just_makeit import _config as C
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object


def _skip_reason():
    if sys.platform == "win32":
        return "raw cmake selects MSVC on Windows; project requires MinGW"
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()

_STEP_STUB = "    (void)state; /* TODO: implement */\n    return (float)0;"
_STEP_BODY = (
    "    const float out = state->v;\n    state->v += 1.0f;\n    return out;"
)


def _scaffold_async(root, monkeypatch, *, async_stream=True):
    jm_new("proj", root)
    monkeypatch.chdir(root)
    jm_object(
        root,
        "osc",
        module=None,
        arg_type="void",
        return_type="float",
        mutable=True,
        streamable=True,
        async_stream=async_stream,
        stream_block_default=64,
        state_vars=[("v", "float", "0.0f")],
    )
    return root


class TestAsyncCodegen:
    def test_async_glue_and_slots(self, tmp_path, monkeypatch):
        root = _scaffold_async(tmp_path / "p", monkeypatch)
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        assert "OscStreamIter_anext_blocking" in ext
        assert "run_in_executor" in ext
        assert "PyAsyncMethods OscStreamIter_as_async" in ext
        assert ".am_anext  = (unaryfunc)OscStreamIter_anext," in ext
        # Both the iterator type and the object type get tp_as_async.
        assert ".tp_as_async  = &OscStreamIter_as_async," in ext
        assert ".tp_as_async  = &Osc_as_async," in ext

    def test_manifest_and_pyi(self, tmp_path, monkeypatch):
        root = _scaffold_async(tmp_path / "p", monkeypatch)
        cfg = C.load(root)
        assert C.is_async_stream(cfg, "osc") is True
        assert C.is_streamable(cfg, "osc") is True  # implied
        pyi = (root / "src/proj/osc.pyi").read_text()
        assert (
            "from typing import Any, AsyncIterator, Callable, Iterator" in pyi
        )
        assert (
            "def __aiter__(self) -> AsyncIterator[NDArray[np.float32]]:" in pyi
        )

    def test_plain_streamable_has_no_async_glue(self, tmp_path, monkeypatch):
        # --streamable without --async-stream stays byte-clean of async code.
        root = _scaffold_async(tmp_path / "p", monkeypatch, async_stream=False)
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        assert "am_anext" not in ext
        assert "tp_as_async" not in ext
        assert "run_in_executor" not in ext
        pyi = (root / "src/proj/osc.pyi").read_text()
        assert "AsyncIterator" not in pyi
        assert "__aiter__" not in pyi


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestAsyncRuntime:
    def test_build_and_async_iterate(self, tmp_path, monkeypatch):
        root = _scaffold_async(tmp_path / "p", monkeypatch)
        core_h = root / "native/inc/osc/osc_core.h"
        core_h.write_text(core_h.read_text().replace(_STEP_STUB, _STEP_BODY))
        build = root / "build"
        cfg = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(build)],
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stderr
        bld = subprocess.run(
            ["cmake", "--build", str(build)],
            capture_output=True,
            text=True,
        )
        assert bld.returncode == 0, f"{bld.stdout}\n{bld.stderr}"

        driver = textwrap.dedent(
            """
            import asyncio, sys
            import numpy as np
            from proj import Osc

            async def main():
                # async for over stream(block, count) with on_block
                o = Osc(0.0)
                seen, blocks = [], []
                async for b in o.stream(
                    4, count=3, on_block=lambda x: seen.append(float(x.sum()))
                ):
                    blocks.append(b.copy())
                assert np.array_equal(
                    np.concatenate(blocks), np.arange(12, dtype=np.float32)
                ), blocks
                # post-yield hook fires once per consumed block (incl. last).
                assert seen == [6.0, 22.0, 38.0], seen

                # async for over the object itself uses the default block (64).
                o2, n = Osc(0.0), 0
                async for b in o2:
                    assert b.shape == (64,)
                    n += 1
                    if n == 2:
                        break
                assert n == 2

                # sync iteration still works on the same object.
                o3 = Osc(0.0)
                assert [b.shape for b in o3.stream(5, count=2)] == [(5,), (5,)]
                print("OK")

            asyncio.run(main())
            """
        )
        env = {**os.environ, "PYTHONPATH": str(root / "src")}
        r = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert "OK" in r.stdout
