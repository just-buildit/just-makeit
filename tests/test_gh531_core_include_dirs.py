"""gh-531: a collocated object's core target can see the module's include dirs.

For a collocated module-object jm emitted only

    target_include_directories(<obj>_core PUBLIC
        ${CMAKE_SOURCE_DIR}/native/inc
        ${CMAKE_SOURCE_DIR}/native/inc/<obj>)

so a core whose ``_core.c`` / ``_core.h`` includes a vendored header could not
compile. The module's ``extra_include_dirs`` reached the module's *own* core and
the ``.so``, but never a collocated object's core — and the only way out was
reshaping the project rather than the manifest. doppler moved a private
``wfm_names.h`` into ``native/inc/`` for exactly this reason: "arguably tidier,
but forced rather than chosen".

They are emitted PUBLIC, so the object's test and bench executables inherit them
transitively — a core that needs a vendored header to compile needs it to link
its own C test too.

The standalone (non-collocated) path already did this via
``extra_include_dirs_on_core``; this closes the gap between the two shapes.
"""

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

VENDOR_INC = "${CJSON_INCLUDE_DIR}"


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@pytest.fixture()
def proj(tmp_path):
    d = tmp_path / "p"
    _q(new_run, "p", d, [], [], modules=["wfm"])
    _q(object_run, d, "rdr", "wfm")
    return d


def _declare_module_incs(proj, dirs=(VENDOR_INC,)):
    cfg = C.load(proj)
    cfg["module"]["wfm"]["extra_include_dirs"] = list(dirs)
    C.save(proj, cfg)


def _obj_cmake(proj):
    """Regenerate the collocated CMakeLists and return it.

    Deleted first because `jm apply` is additive — it materializes what is
    missing, and with the manifest otherwise unchanged it would report
    "already matches" and rewrite nothing.
    """
    p = proj / "native" / "src" / "rdr" / "CMakeLists.txt"
    p.unlink()
    _q(apply_run, proj)
    return p.read_text(encoding="utf-8")


def _core_include_block(text):
    """Just the include-dirs lines that target <obj>_core."""
    return "\n".join(
        ln
        for ln in text.splitlines()
        if "rdr_core" in ln or ln.startswith("    $")
    )


class TestModuleIncludesReachTheCore:
    def test_vendor_dir_is_emitted_on_the_core(self, proj):
        _declare_module_incs(proj)
        assert VENDOR_INC in _obj_cmake(proj)

    def test_emitted_public_so_test_and_bench_inherit(self, proj):
        _declare_module_incs(proj)
        text = _obj_cmake(proj)
        assert "target_include_directories(rdr_core PUBLIC" in text
        # ...and it is a real target_include_directories, not a stray comment
        idx = text.index("target_include_directories(rdr_core PUBLIC")
        assert VENDOR_INC in text[idx : idx + 200]

    def test_defaults_are_kept_not_replaced(self, proj):
        """The vendored dir is added; the two built-in ones must survive."""
        _declare_module_incs(proj)
        text = _obj_cmake(proj)
        assert "${CMAKE_SOURCE_DIR}/native/inc" in text
        assert "${CMAKE_SOURCE_DIR}/native/inc/rdr" in text

    def test_several_dirs_all_land(self, proj):
        _declare_module_incs(proj, ("${A_INC}", "${B_INC}"))
        text = _obj_cmake(proj)
        assert "${A_INC}" in text and "${B_INC}" in text


class TestNoChurnWithoutTheKey:
    def test_undeclared_module_emits_no_extra_block(self, proj):
        """Opt-in: a module that declares nothing generates what it always
        did, so no existing project sees a CMakeLists diff."""
        text = _obj_cmake(proj)
        assert text.count("target_include_directories(rdr_core") == 0
        # the built-in block uses the wrapped `target_include_directories(`
        # form, which must still be the only one present
        assert text.count("target_include_directories(") == 3

    def test_placeholder_never_leaks(self, proj):
        """A missing ctx key would leave the raw token in generated CMake."""
        assert "extra_include_dirs_on_object_core" not in _obj_cmake(proj)
        _declare_module_incs(proj)
        assert "extra_include_dirs_on_object_core" not in _obj_cmake(proj)
