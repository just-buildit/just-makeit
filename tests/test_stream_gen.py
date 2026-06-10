"""Stream generator (gh-201): ``stream()`` / ``__iter__`` codegen.

A ``--streamable`` object grows a generated C iterator type so callers write
``for blk in obj.stream(n): ...`` instead of the hand-rolled drain loop. The
producer is the object's ``variable_output`` method when one exists
(blockwise), else the built-in ``steps`` (source).

Two layers of coverage:
  * String-level assertions on the rendered ``_ext.c`` / ``.pyi`` / TOML — fast,
    always run, and cover the blockwise producer (whose array-return *core*
    scaffold is an unrelated pre-existing bug, so it is not built here).
  * An end-to-end build + import + behavior check on a source object, skipped
    when the C toolchain is unavailable (mirrors test_preset_build).
"""

import importlib
import shutil
import subprocess
import sys

import pytest

from just_makeit import _cli_object, _config as C
from just_makeit._new import run as new_run


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


def _scaffold(root, monkeypatch, *obj_args):
    """Create a fresh project and one object; return the project root."""
    new_run("proj", root)
    monkeypatch.chdir(root)
    _cli_object.run(list(obj_args))
    return root


class TestStreamCodegen:
    """The producer wiring renders without building anything."""

    def test_source_object_emits_iterator(self, tmp_path, monkeypatch):
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            "--streamable",
        )
        ext = (root / "native/src/wave/wave_ext.c").read_text()
        # The iterator type, its iternext, the type-ready guard, the method
        # table entry, and the tp_iter thunk all land.
        assert "} WaveStreamIter;" in ext
        assert "WaveStreamIter_next(WaveStreamIter *it)" in ext
        assert "if (PyType_Ready(&WaveStreamIterType) < 0)" in ext
        assert '{"stream", (PyCFunction)(void *)Wave_stream,' in ext
        assert ".tp_iter      = (getiterfunc)Wave_getiter," in ext
        # A source drives the built-in steps().
        assert 'PyObject_CallMethod(it->src, "steps", "n", it->block)' in ext

    def test_streamable_persists_in_manifest(self, tmp_path, monkeypatch):
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            "--stream-block",
            "256",
        )
        cfg = C.load(root)
        assert C.is_streamable(cfg, "wave") is True
        # --stream-block implies --streamable and records the default.
        assert C.stream_block_default(cfg, "wave") == 256
        ext = (root / "native/src/wave/wave_ext.c").read_text()
        assert "Py_ssize_t block = 256;" in ext

    def test_pyi_exposes_stream_and_iter(self, tmp_path, monkeypatch):
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            "--streamable",
        )
        pyi = (root / "src/proj/wave.pyi").read_text()
        assert "from typing import Any, Callable, Iterator" in pyi
        assert "def stream(" in pyi
        assert "on_block: Callable[[NDArray[np.float32]], None] | None" in pyi
        assert "def __iter__(self) -> Iterator[NDArray[np.float32]]:" in pyi

    def test_blockwise_producer_is_variable_output_method(
        self, tmp_path, monkeypatch
    ):
        # A variable_output method wins over the built-in steps; the iterator
        # calls it by name. (variable_output methods are count-driven
        # `run(n=1) -> array`, so the int-block producer call is correct.)
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "filt",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "float _Complex[]",
            "--variable-output",
            "--streamable",
        )
        ext = (root / "native/src/filt/filt_ext.c").read_text()
        assert 'PyObject_CallMethod(it->src, "run", "n", it->block)' in ext

    def test_non_streamable_emits_no_iterator(self, tmp_path, monkeypatch):
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
        )
        ext = (root / "native/src/wave/wave_ext.c").read_text()
        assert "StreamIter" not in ext
        assert "stream" not in ext
        pyi = (root / "src/proj/wave.pyi").read_text()
        assert "from typing import Any\n" in pyi
        assert "stream" not in pyi


class TestStreamSurvivesRegeneration:
    """Mutating commands re-render _ext.c; the stream code must persist."""

    def test_property_then_apply_keep_stream(self, tmp_path, monkeypatch):
        from just_makeit import _apply, _property

        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            "--streamable",
        )
        _property.run(root, "wave", "gain", None, "float", True, field=True)
        assert (
            "Wave_stream" in (root / "native/src/wave/wave_ext.c").read_text()
        )
        _apply.run(root)
        assert (
            "Wave_stream" in (root / "native/src/wave/wave_ext.c").read_text()
        )


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestStreamRuntime:
    """Build a source object and exercise the generated iterator."""

    def test_build_and_stream(self, tmp_path, monkeypatch):
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "wave",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            "--streamable",
        )
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

        np = pytest.importorskip("numpy")
        src = str(root / "src")
        sys.path.insert(0, src)
        for mod in ("proj", "proj.wave"):
            sys.modules.pop(mod, None)
        try:
            proj = importlib.import_module("proj")
            wave = proj.Wave(1.0)
            # count caps the number of yielded blocks; each has the requested
            # length and the producer's dtype.
            blocks = list(wave.stream(8, count=3))
            assert len(blocks) == 3
            assert all(b.shape == (8,) for b in blocks)
            assert blocks[0].dtype == np.float32

            # on_block fires once per consumed block (post-yield).
            seen = []
            for _ in wave.stream(
                4, count=2, on_block=lambda b: seen.append(len(b))
            ):
                pass
            assert seen == [4, 4]

            # __iter__ uses the default block; a source never drains, so just
            # pull the first block.
            first = next(iter(wave))
            assert first.shape == (1024,)
        finally:
            sys.path.remove(src)
            for mod in ("proj", "proj.wave"):
                sys.modules.pop(mod, None)

    def test_blockwise_streamable_builds_and_drains(
        self, tmp_path, monkeypatch
    ):
        # A blockwise (variable_output) streamable object compiles and its
        # producer drives the iterator. The placeholder `run` stub returns 0
        # output, so the stream drains on the first block — list() is empty,
        # exercising the drain (empty-block) path end to end.
        root = _scaffold(
            tmp_path / "p",
            monkeypatch,
            "det",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--variable-output",
            "--max-out",
            "16",
            "--streamable",
        )
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

        src = str(root / "src")
        sys.path.insert(0, src)
        for mod in ("proj", "proj.det"):
            sys.modules.pop(mod, None)
        try:
            proj = importlib.import_module("proj")
            det = proj.Det(0.0)
            # Placeholder producer returns 0 samples → first block is empty →
            # the iterator stops immediately.
            assert list(det.stream(8)) == []
        finally:
            sys.path.remove(src)
            for mod in ("proj", "proj.det"):
                sys.modules.pop(mod, None)
