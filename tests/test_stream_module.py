"""Stream generator for module objects (gh-203).

#202 gave standalone objects a generated ``stream()`` / ``__iter__``; this
extends it to objects that live inside a shared-``.so`` module, rendered
through the per-object ``COMPONENT_TYPE_SECTION`` + the module aggregator
(``_object._regenerate_module`` → ``render_module_ext_aggregator``).

Covers: the aggregator readies each streamable object's iterator type; a
non-streamable sibling is byte-clean; the ``<Comp>StreamIter`` names don't
collide; the producer tracks a later ``variable_output`` method (the
body-preservation fix); and an end-to-end build + import + stream.
"""

import importlib
import shutil
import subprocess
import sys

import pytest

from just_makeit._method import run as jm_method
from just_makeit._module import run as jm_module
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


def _module_with_objects(root, monkeypatch):
    """A 'dsp' module: streamable source 'osc' + plain sibling 'gainer'."""
    jm_new("proj", root)
    monkeypatch.chdir(root)
    jm_module(root, "dsp")
    jm_object(
        root,
        "osc",
        module="dsp",
        arg_type="void",
        return_type="float",
        mutable=True,
        streamable=True,
        stream_block_default=128,
        state_vars=[("v", "float", "0.0f")],
    )
    jm_object(
        root,
        "gainer",
        module="dsp",
        arg_type="float",
        return_type="float",
        state_vars=[("g", "float", "1.0f")],
    )
    return root


class TestStreamModuleCodegen:
    def test_aggregator_readies_iterator_type(self, tmp_path, monkeypatch):
        root = _module_with_objects(tmp_path / "p", monkeypatch)
        agg = (root / "native/src/dsp/dsp_ext.c").read_text()
        # The streamable object's iterator type is readied right after its own
        # type, and the plain sibling's is not present at all.
        assert "if (PyType_Ready(&OscType) < 0) return NULL;" in agg
        assert "if (PyType_Ready(&OscStreamIterType) < 0) return NULL;" in agg
        assert "GainerStreamIter" not in agg
        # Type-prefixed names mean no cross-object symbol collision.
        assert "OscStreamIterType" in agg and "GainerType" in agg

    def test_streamable_object_fragment(self, tmp_path, monkeypatch):
        root = _module_with_objects(tmp_path / "p", monkeypatch)
        frag = (root / "native/src/dsp/dsp_ext_osc.c").read_text()
        assert "} OscStreamIter;" in frag
        assert "Osc_stream" in frag
        assert ".tp_iter      = (getiterfunc)Osc_getiter," in frag
        # A void-arg source drives the built-in steps().
        assert 'PyObject_CallMethod(it->src, "steps", "n"' in frag
        # Module objects share one <module>.pyi; the stream stubs land under
        # the streamable class.
        pyi = (root / "src/proj/dsp/dsp.pyi").read_text()
        assert "from typing import Callable, Iterator" in pyi
        assert "def stream(" in pyi
        assert "def __iter__(self) -> Iterator[NDArray[np.float32]]:" in pyi

    def test_plain_sibling_unchanged(self, tmp_path, monkeypatch):
        root = _module_with_objects(tmp_path / "p", monkeypatch)
        frag = (root / "native/src/dsp/dsp_ext_gainer.c").read_text()
        assert "StreamIter" not in frag
        assert "stream" not in frag
        # In the shared .pyi, the plain sibling's class carries no stream stub
        # (the streamable Osc's does, but that's a different class).
        pyi = (root / "src/proj/dsp/dsp.pyi").read_text()
        gainer_section = pyi[pyi.index("class Gainer:") :]
        assert "stream" not in gainer_section

    def test_producer_tracks_variable_output_method(
        self, tmp_path, monkeypatch
    ):
        # A streamable module object created as a source resolves to steps();
        # once a variable_output method is added, the regenerated fragment must
        # re-point the producer at it (the body-preservation fix — the stream
        # glue is never frozen).
        root = tmp_path / "p"
        jm_new("proj", root)
        monkeypatch.chdir(root)
        jm_module(root, "dsp")
        jm_object(
            root,
            "decim",
            module="dsp",
            arg_type="void",
            return_type="float _Complex",
            no_step=True,
            mutable=True,
            streamable=True,
            state_vars=[("total", "int32_t", "10"), ("pos", "int32_t", "0")],
        )
        frag = root / "native/src/dsp/dsp_ext_decim.c"
        jm_method(
            root,
            "decim",
            module="dsp",
            method_name="run",
            arg_type="void",
            return_type="float _Complex",
            variable_output=True,
            multi_output=[],
        )
        text = frag.read_text()
        assert 'PyObject_CallMethod(it->src, "run", "n"' in text
        assert 'PyObject_CallMethod(it->src, "steps"' not in text


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestStreamModuleRuntime:
    def test_build_and_stream(self, tmp_path, monkeypatch):
        root = _module_with_objects(tmp_path / "p", monkeypatch)
        build = root / "build"
        cfg = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(build)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert cfg.returncode == 0, cfg.stderr
        bld = subprocess.run(
            ["cmake", "--build", str(build)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert bld.returncode == 0, f"{bld.stdout}\n{bld.stderr}"

        np = pytest.importorskip("numpy")
        src = str(root / "src")
        sys.path.insert(0, src)
        for mod in ("proj", "proj.dsp"):
            sys.modules.pop(mod, None)
        try:
            dsp = importlib.import_module("proj.dsp")
            osc = dsp.Osc(0.0)
            blocks = list(osc.stream(8, count=3))
            assert len(blocks) == 3
            assert all(b.shape == (8,) for b in blocks)
            assert blocks[0].dtype == np.float32
            # __iter__ uses the per-object stream_block_default (128).
            assert next(iter(osc)).shape == (128,)
            # The plain sibling shares the .so but has no stream().
            assert not hasattr(dsp.Gainer, "stream")
        finally:
            sys.path.remove(src)
            for mod in ("proj", "proj.dsp"):
                sys.modules.pop(mod, None)
