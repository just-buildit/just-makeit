"""`jm apply` resolves the collocated OBJECT-core link block (gh-160).

A module whose object shares its name (collocated, e.g. module "ddc" + object
"ddc") renders its OBJECT-core CMake inside the module CMakeLists. gh-160 added
a ``<<extra_link_on_object_core>>`` slot that PUBLIC-links the module's
``extra_link_libs`` onto that OBJECT lib. The ``jm object`` path filled it, but
``jm apply`` (which rebuilds the collocated CMakeLists) did not — so the literal
placeholder leaked into the generated file and broke the build. This guards
that apply resolves it.
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_apply_resolves_collocated_object_core_link(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    # Collocated object: object name == module name.
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    # Declare module-level extra_link_libs, then regenerate via apply.
    cfg = C.load(dest)
    cfg["module"]["ddc"]["extra_link_libs"] = ["foo_core", "m"]
    C.save(dest, cfg)
    _silent(apply_run, dest)

    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    # No unresolved template placeholder of any kind leaked.
    assert "<<" not in cmake and ">>" not in cmake
    # The OBJECT lib is PUBLIC-linked against the declared libs.
    assert "target_link_libraries(ddc_core PUBLIC" in cmake
    assert "foo_core" in cmake


def test_apply_no_extra_libs_leaves_no_object_core_link(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _silent(apply_run, dest)
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "<<" not in cmake  # placeholder resolved to empty
    assert "target_link_libraries(ddc_core PUBLIC" not in cmake
