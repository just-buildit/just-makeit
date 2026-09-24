"""gh-1486: every type jm emits names a module Python can import.

A static type's ``__module__`` is read from its ``tp_name``: everything before
the last dot. jm wrote ``"<component>.<Component>"`` for a standalone object
and ``"<module>.<Component>"`` for a flat module's, so ``Gain.__module__`` was
``'gain'`` -- a module that does not exist -- and pickle, which imports
``__module__`` to find a class again, failed on the class and on anything whose
reduction names it::

    PicklingError: Can't pickle <class 'gain.Gain'>: import of module 'gain'
    failed

The prefix is now one render slot, ``module_tp``, for every type a component's
binding emits (the class, a view, a stream iterator, a ``single`` record):

=========================  ===============================================
standalone object          ``<pkg>.<comp>`` -- its own ``.so``
module object              ``<pkg>.<module>``, dotted when nested
``[module.X] package``     ``<pkg>.<package>`` -- where it is re-exported
=========================  ===============================================

The nested case was already right, and is kept in the matrix so that the
flat fix cannot regress it. handle and composer modules already derived the
dotted path and are asserted by their own tests.

The text test pins every ``tp_name`` in a scaffolded tree. The compiled test is
the property itself: build the tree, import each class, pickle it, and pickle a
``serializable`` instance through a reducer that names its type.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402

_TP_NAME = re.compile(r'^\s*\.tp_name\s*=\s*"([^"]+)"', re.M)

#: Every type the matrix below emits, by its full dotted name. The stream
#: iterators are not exported, so only their module must import; the classes
#: must also be reachable at that path, which is what pickle needs.
EXPECTED = {
    "demo.gain.Gain",
    "demo.osc.Osc",
    "demo.src.Src",
    "demo.src.SrcStreamIter",
    "demo.dsp.Fir",
    "demo.dsp.FirView",
    "demo.dsp.Tick",
    "demo.dsp.TickStreamIter",
    "demo.dsp.filters.Biq",
    # `[module.rd] package = "dsp"`: the .so lands in `demo/dsp/` and
    # `demo.dsp` re-exports it, so that is the path, not `demo.rd`.
    "demo.dsp.Rdr",
}

#: The classes the compiled test imports and pickles: (module, name).
CLASSES = sorted(
    tuple(n.rsplit(".", 1)) for n in EXPECTED if not n.endswith("Iter")
)


def _cli(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _streamable(name: str, *extra: str) -> list[str]:
    return [
        "object",
        name,
        *extra,
        "--arg-type",
        "void",
        "--return-type",
        "float",
        "--mutable",
        "--streamable",
        "--state",
        "v:float:0.0f",
    ]


def _matrix(tmp: Path) -> Path:
    """One project holding every shape that emits a type."""
    _cli("new", "demo", "--object", "gain", cwd=tmp)
    root = tmp / "demo"
    _cli(
        "object",
        "osc",
        "--serializable",
        "--state",
        "phase:double:0.0",
        cwd=root,
    )
    _cli(*_streamable("src"), cwd=root)
    _cli("module", "dsp", cwd=root)
    _cli("object", "fir", "--module", "dsp", cwd=root)
    _cli(*_streamable("tick", "--module", "dsp"), cwd=root)
    _cli(
        "view",
        "fir",
        "FirView",
        "--module",
        "dsp",
        "--create-fn",
        "fir_create_view",
        cwd=root,
    )
    _cli("module", "dsp.filters", cwd=root)
    _cli("object", "biq", "--module", "dsp.filters", cwd=root)
    _cli("module", "rd", cwd=root)
    frag = root / "modules" / "rd.toml"
    frag.write_text(frag.read_text() + 'package = "dsp"\n')
    _cli("object", "rdr", "--module", "rd", cwd=root)
    return root


def _tp_names(root: Path) -> set[str]:
    return {
        m
        for c in (root / "native" / "src").rglob("*.c")
        for m in _TP_NAME.findall(c.read_text())
    }


@pytest.fixture(scope="module")
def matrix(tmp_path_factory) -> Path:
    return _matrix(tmp_path_factory.mktemp("gh1486"))


class TestEveryTpNameIsImportable:
    def test_the_tree_emits_exactly_these_names(self, matrix):
        assert _tp_names(matrix) == EXPECTED

    def test_the_scan_reaches_every_binding_file(self, matrix):
        """Armed: a scan that found no `.tp_name` at all would pass the set
        comparison only if EXPECTED were empty, so also check each binding
        file the matrix writes contributed one."""
        files = {
            c.name
            for c in (matrix / "native" / "src").rglob("*.c")
            if _TP_NAME.search(c.read_text())
        }
        for want in (
            "gain_ext.c",
            "osc_ext.c",
            "src_ext.c",
            "dsp_ext_fir.c",
            "dsp_ext_tick.c",
            "dsp_filters_ext_biq.c",
            "rd_ext_rdr.c",
        ):
            assert want in files, sorted(files)

    def test_a_second_apply_keeps_them(self, matrix):
        """The replay renders the same slots: `apply` goes through a ctx
        path of its own (the gh-1117 lesson), and must agree."""
        _cli("apply", cwd=matrix)
        assert _tp_names(matrix) == EXPECTED


class TestARecordIsQualifiedTheSameWay:
    """A `single` record's structseq name is `<module_tp>.<Name>` unless
    `record_module` overrides it -- the same prefix as the class beside it,
    and the path the module `__init__` re-exports it from."""

    @staticmethod
    def _record(root: Path, obj: str) -> None:
        method_run(
            root,
            obj,
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
        )

    def test_standalone_and_module(self, tmp_path):
        _cli("new", "demo", "--object", "gain", cwd=tmp_path)
        root = tmp_path / "demo"
        _cli("module", "dsp", cwd=root)
        _cli("object", "fir", "--module", "dsp", cwd=root)
        self._record(root, "gain")
        self._record(root, "fir")
        desc = re.compile(r"PyStructSequence_Desc \w+ = \{\s*\"([^\"]+)\"")
        gain = (root / "native/src/gain/gain_ext.c").read_text()
        fir = (root / "native/src/dsp/dsp_ext_fir.c").read_text()
        assert desc.findall(gain) == ["demo.gain.ToneMetrics"]
        assert desc.findall(fir) == ["demo.dsp.ToneMetrics"]


# ── compiled: the property itself ───────────────────────────────────────────


def _no_toolchain() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()

_PROBE = """
import copyreg
import importlib
import pickle

CLASSES = {classes!r}

for mod, name in CLASSES:
    cls = getattr(importlib.import_module(mod), name)
    assert cls.__module__ == mod, (name, cls.__module__, mod)
    assert cls.__qualname__ == name, (name, cls.__qualname__)
    assert pickle.loads(pickle.dumps(cls)) is cls, name

# A stream iterator is not exported, but its module must still import.
from demo import Src
from demo.dsp import Tick
for obj in (Src(), Tick()):
    it = iter(obj)
    importlib.import_module(type(it).__module__)

# An instance: a reducer that names the TYPE, the shape a serializable
# object's __reduce__ takes, so pickle has to find the class by __module__.
from demo import Osc


def _rebuild(cls, blob):
    o = cls()
    o.set_state(blob)
    return o


copyreg.pickle(Osc, lambda o: (_rebuild, (type(o), o.get_state())))
a = Osc(phase=1.25)
b = pickle.loads(pickle.dumps(a))
assert type(b) is Osc
assert b.get_state() == a.get_state()
print("OK")
"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_every_class_pickles(matrix):
    # The serializable triplet is scaffolded (gh-1509), so the tree builds
    # as written and the instance round trip carries real state.
    build = matrix / "build"
    for cmd in (
        ["cmake", "-S", str(matrix), "-B", str(build)],
        ["cmake", "--build", str(build), "-j", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    probe = matrix / "probe_gh1486.py"
    probe.write_text(_PROBE.format(classes=CLASSES))
    out = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(matrix),
        env={**os.environ, "PYTHONPATH": str(matrix / "src")},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "OK" in out.stdout, out.stdout
