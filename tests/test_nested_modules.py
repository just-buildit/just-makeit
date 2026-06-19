"""Nested module subpackages — `pkg.dsp.filters.Obj`.

A dotted module id (`jm module dsp.filters`) nests the extension under
`src/<pkg>/dsp/filters/` and imports as `pkg.dsp.filters`. The single name
plays three roles that nesting splits apart, derived once by
``_config.module_paths``:

- leaf  (``filters``) — ``PyInit_`` / ``.m_name`` / ``from .<leaf> import``
- cname (``dsp_filters``) — CMake target, flat ``native/src/<cname>/`` dir
- pypath (``dsp/filters``) — the Python output directory

For a flat (dotless) module every form collapses to the name, so existing
projects render byte-for-byte unchanged.
"""

import shutil
import subprocess
import sys

import pytest

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._apply import run as jm_apply
from just_makeit._module import run as jm_module
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object
from just_makeit._remove import run as jm_remove


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


def _nested(root):
    """A project with one nested module `dsp.filters` holding object `fir`."""
    jm_new("p", root)
    jm_module(root, "dsp.filters")
    jm_object(
        root, "fir", module="dsp.filters", state_vars=[("g", "double", "1.0")]
    )
    return root


# ── config helper ────────────────────────────────────────────────────────────


class TestModulePaths:
    def test_dotted(self):
        mp = C.module_paths("dsp.filters")
        assert mp.leaf == "filters"
        assert mp.cname == "dsp_filters"
        assert mp.pypath == "dsp/filters"
        assert mp.parents == ("dsp",)

    def test_flat_invariant(self):
        mp = C.module_paths("dsp")
        assert mp.leaf == mp.cname == mp.pypath == "dsp"
        assert mp.parents == ()

    def test_deep(self):
        mp = C.module_paths("a.b.mod")
        assert mp.cname == "a_b_mod"
        assert mp.pypath == "a/b/mod"
        assert mp.parents == ("a", "b")

    def test_validation(self):
        assert C.validate_module_id("dsp.filters") is None
        assert C.validate_module_id("a..b")  # double dot -> error
        assert C.validate_module_id("1x")  # leading digit
        assert C.validate_module_id("a.b-c")  # bad char
        assert C.validate_module_id("")  # empty

    def test_cnames(self):
        cfg = {"module": {"dsp.filters": {}, "flat": {}}}
        assert C.module_cnames(cfg) == {"dsp_filters", "flat"}


# ── render tier (no build) ───────────────────────────────────────────────────


class TestNestedScaffold:
    def test_module_layout(self, tmp_path):
        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "dsp.filters")
        # Flat native dir keyed by cname.
        ext = (root / "native/src/dsp_filters/dsp_filters_ext.c").read_text()
        assert "PyInit_filters(void)" in ext
        assert '.m_name    = "filters"' in ext
        # CMake: target=cname, OUTPUT_NAME=leaf, output dir=pypath.
        cm = (root / "native/src/dsp_filters/CMakeLists.txt").read_text()
        assert "Python3_add_library(dsp_filters MODULE" in cm
        assert "OUTPUT_NAME filters" in cm
        assert "${PYTHON_PACKAGE_DIR}/dsp/filters" in cm
        # Nested Python package + intermediate marker.
        assert (root / "src/p/dsp/__init__.py").exists()
        assert (root / "src/p/dsp/filters/__init__.py").exists()
        assert (root / "src/p/dsp/filters/filters.pyi").exists()
        # Top CMake wires the cname dir.
        top = (root / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/dsp_filters)" in top
        # Manifest carries the dotted key as a quoted TOML key (monolith
        # layout writes it into just-makeit.toml; the split layout into
        # modules/dsp_filters.toml — check both).
        frag = root / "modules/dsp_filters.toml"
        manifest = (root / "just-makeit.toml").read_text()
        toml_text = manifest + (frag.read_text() if frag.exists() else "")
        assert '[module."dsp.filters"]' in toml_text

    def test_object_in_nested_module(self, tmp_path):
        root = _nested(tmp_path / "p")
        # Fragment carries the fully-qualified tp_name.
        frag = (
            root / "native/src/dsp_filters/dsp_filters_ext_fir.c"
        ).read_text()
        assert '.tp_name      = "p.dsp.filters.Fir"' in frag
        # Leaf __init__ imports from the .so basename (leaf).
        init = (root / "src/p/dsp/filters/__init__.py").read_text()
        assert "from .filters import Fir" in init
        # Stub + tests land under the nested pypath.
        assert (root / "src/p/dsp/filters/filters.pyi").exists()
        test = (root / "src/p/dsp/filters/tests/test_fir.py").read_text()
        assert "from p.dsp.filters import Fir" in test

    def test_split_layout_fragment_filename(self, tmp_path):
        # In the per-component (fragment) layout the dotted module routes to a
        # cname-sanitized fragment file, with the dotted key inside.
        root = tmp_path / "p"
        jm_new("p", root, fragments=True)
        jm_module(root, "dsp.filters")
        frag = root / "modules/dsp_filters.toml"
        assert frag.exists()
        assert '[module."dsp.filters"]' in frag.read_text()
        assert _status.run(root, check=True) == 0

    def test_status_clean(self, tmp_path):
        root = _nested(tmp_path / "p")
        assert _status.run(root, check=True) == 0

    def test_apply_idempotent(self, tmp_path):
        root = _nested(tmp_path / "p")
        jm_apply(root)
        assert _status.run(root, check=True) == 0


class TestZeroChurn:
    def test_flat_module_unchanged(self, tmp_path):
        """A dotless module renders with no nesting / no OUTPUT_NAME line."""
        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "dsp")
        cm = (root / "native/src/dsp/CMakeLists.txt").read_text()
        assert "OUTPUT_NAME" not in cm
        assert "${PYTHON_PACKAGE_DIR}/dsp\n" in cm or (
            '"${PYTHON_PACKAGE_DIR}/dsp"' in cm
        )
        ext = (root / "native/src/dsp/dsp_ext.c").read_text()
        assert "PyInit_dsp(void)" in ext
        # No intermediate package dir for a flat module.
        assert not (root / "src/p/dsp/dsp").exists()


class TestCollisions:
    def test_cname_collision(self, tmp_path, capsys):
        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "a.b")
        with pytest.raises(SystemExit):
            jm_module(root, "a_b")  # same cname as a.b

    def test_siblings_share_parent(self, tmp_path):
        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "dsp.filters")
        jm_module(root, "dsp.windows")
        # One shared intermediate package, two leaf packages.
        assert (root / "src/p/dsp/__init__.py").exists()
        assert (root / "src/p/dsp/filters/__init__.py").exists()
        assert (root / "src/p/dsp/windows/__init__.py").exists()
        assert _status.run(root, check=True) == 0


class TestRemoval:
    def test_remove_prunes_parent(self, tmp_path):
        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "dsp.filters")
        jm_module(root, "dsp.windows")
        # Removing one sibling keeps the shared parent.
        jm_remove(root, "module", "dsp.filters", force=True)
        assert (root / "src/p/dsp/__init__.py").exists()
        assert not (root / "src/p/dsp/filters").exists()
        # Removing the last child prunes the parent.
        jm_remove(root, "module", "dsp.windows", force=True)
        assert not (root / "src/p/dsp").exists()
        assert not (root / "native/src/dsp_filters").exists()


# ── build / import tier (needs a compiler) ───────────────────────────────────


@pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
class TestBuildImport:
    def _build(self, root):
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

    def test_build_and_import(self, tmp_path):
        import importlib

        root = _nested(tmp_path / "p")
        self._build(root)
        # The .so lands beside the leaf __init__ as <leaf>.cpython-*.so.
        assert list((root / "src/p/dsp/filters").glob("filters.*.so"))
        src = str(root / "src")
        sys.path.insert(0, src)
        for m in ("p", "p.dsp", "p.dsp.filters"):
            sys.modules.pop(m, None)
        try:
            mod = importlib.import_module("p.dsp.filters")
            obj = mod.Fir()
            assert obj is not None
            assert type(obj).__module__ == "p.dsp.filters"
        finally:
            sys.path.remove(src)
            for m in ("p", "p.dsp", "p.dsp.filters"):
                sys.modules.pop(m, None)

    def test_sibling_leaf_no_symbol_clash(self, tmp_path):
        """Two modules `a.mod` and `b.mod` each export PyInit_mod in separate
        .so files — importing both must not clash."""
        import importlib

        root = tmp_path / "p"
        jm_new("p", root)
        jm_module(root, "a.mod")
        jm_object(
            root, "fir", module="a.mod", state_vars=[("g", "double", "1.0")]
        )
        jm_module(root, "b.mod")
        jm_object(
            root, "biq", module="b.mod", state_vars=[("g", "double", "1.0")]
        )
        self._build(root)
        src = str(root / "src")
        sys.path.insert(0, src)
        names = ("p", "p.a", "p.a.mod", "p.b", "p.b.mod")
        for m in names:
            sys.modules.pop(m, None)
        try:
            a = importlib.import_module("p.a.mod")
            b = importlib.import_module("p.b.mod")
            assert a.Fir().__class__.__module__ == "p.a.mod"
            assert b.Biq().__class__.__module__ == "p.b.mod"
        finally:
            sys.path.remove(src)
            for m in names:
                sys.modules.pop(m, None)
