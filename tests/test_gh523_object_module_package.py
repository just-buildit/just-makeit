"""gh-523 — `package` works for a plain *object* module, not just capsules.

``[module.X] package = "wfm"`` puts a module's Python-side artifacts (the
``.so``'s ``LIBRARY_OUTPUT_DIRECTORY``, the ``.pyi``, the re-export
``__init__.py``, the generated tests and benchmarks) inside a *sibling*
package instead of one named after the module. That already worked for
``kind = "handle"`` / ``"capsule"`` / ``"composer"``; an object module
accepted the key and silently ignored it, materialising a stray top-level
``src/<pkg>/<module>/`` tree instead.

The tests below pin the four things the fix has to get right:

1. every Python path routes through ``C.module_package`` (dir layout, CMake
   output dir, ``.pyi`` beside the ``.so``, tests/benchmarks);
2. an already-existing package's ``__init__.py`` gains re-exports rather than
   being stamped over — the whole point of the feature;
3. two modules sharing one package converge (neither prunes the other's
   ``__all__`` on alternate applies), and ``jm apply`` stays idempotent;
4. a module *without* the key renders byte-identically to before.
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402
from just_makeit._object import (  # noqa: E402
    _merge_module_init,
    package_siblings,
)
from just_makeit import _config as C  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


PACKAGED_TOML = """
[module.wfm_reader]
objects = ["wfm_reader"]
package = "wfm"

[wfm_reader]
class_name = "Reader"
no_step = "true"
"""

PLAIN_TOML = """
[module.wfm_reader]
objects = ["wfm_reader"]

[wfm_reader]
class_name = "Reader"
no_step = "true"
"""

# A second module that owns the `wfm` package the reader lands in — the
# doppler shape: `wfm` already exports Synth, `wfm_reader` adds Reader.
SHARED_TOML = """
[module.wfm]
objects = ["synth"]

[synth]
class_name = "Synth"
no_step = "true"

[module.wfm_reader]
objects = ["wfm_reader"]
package = "wfm"

[wfm_reader]
class_name = "Reader"
no_step = "true"
"""


def _project(dest: Path, extra_toml: str) -> Path:
    """Scaffold an empty project at *dest* and apply *extra_toml*."""
    _silent(new_run, "probe", dest)
    manifest = dest / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + extra_toml, encoding="utf-8"
    )
    _silent(apply_run, dest)
    return dest


# ── 1. path routing ──────────────────────────────────────────────────────────


def test_python_artifacts_land_in_the_declared_package(tmp_path):
    root = _project(tmp_path / "p", PACKAGED_TOML)

    pkg_dir = root / "src" / "probe" / "wfm"
    assert pkg_dir.is_dir()
    assert (pkg_dir / "__init__.py").exists()
    # The .pyi sits beside the .so, not orphaned at the old location.
    assert (pkg_dir / "wfm_reader.pyi").exists()
    assert (pkg_dir / "tests" / "test_wfm_reader.py").exists()
    assert (pkg_dir / "benchmarks" / "bench_wfm_reader.py").exists()

    # No stray top-level package named after the module.
    assert not (root / "src" / "probe" / "wfm_reader").exists()


def test_generated_test_imports_from_the_package(tmp_path):
    root = _project(tmp_path / "p", PACKAGED_TOML)
    test_py = (
        root / "src" / "probe" / "wfm" / "tests" / "test_wfm_reader.py"
    ).read_text(encoding="utf-8")
    # A test importing `probe.wfm_reader` would ImportError — the extension
    # only exists as `probe.wfm.wfm_reader`.
    assert "from probe.wfm import Reader" in test_py
    assert "from probe.wfm_reader import" not in test_py


def test_cmake_output_directory_points_at_the_package(tmp_path):
    root = _project(tmp_path / "p", PACKAGED_TOML)
    cmake = (
        root / "native" / "src" / "wfm_reader" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert 'LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/wfm"' in cmake
    assert "PYTHON_PACKAGE_DIR}/wfm_reader" not in cmake
    # The C-side identity is untouched: the .so is still wfm_reader.so, built
    # from native/src/wfm_reader/, only the destination moved.
    assert "Python3_add_library(wfm_reader MODULE" in cmake


def test_package_round_trips_through_the_manifest(tmp_path):
    root = _project(tmp_path / "p", PACKAGED_TOML)
    # _dump must re-emit the key for an object module or the next save drops
    # it and the module silently migrates back out of the package.
    assert 'package = "wfm"' in (root / C.FILENAME).read_text(encoding="utf-8")
    assert C.module_package(C.load(root), "wfm_reader") == "wfm"


def test_module_package_is_the_single_implementation():
    # capsule_package / handle_package are aliases, not peer copies.
    cfg = {"module": {"m": {"package": "shared"}}}
    assert C.module_package(cfg, "m") == "shared"
    assert C.capsule_package(cfg, "m") == "shared"
    assert C.handle_package(cfg, "m") == "shared"
    assert C.module_package(cfg, "absent") == ""


# ── 2. a pre-existing package is added to, never overwritten ─────────────────


def test_existing_package_init_is_preserved(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "probe", dest)
    manifest = dest / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + PACKAGED_TOML, encoding="utf-8"
    )
    init = dest / "src" / "probe" / "wfm" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(
        '"""Hand-written wfm package."""\n'
        "\n"
        "from .other import Thing  # sentinel\n"
        "\n"
        '__all__ = ["Thing"]\n',
        encoding="utf-8",
    )
    _silent(apply_run, dest)

    after = init.read_text(encoding="utf-8")
    # The hand-written content survives: docstring and the sentinel import.
    assert '"""Hand-written wfm package."""' in after
    assert "from .other import Thing  # sentinel" in after
    # ...and the module's re-export is *added* alongside it.
    assert "from .wfm_reader import Reader" in after


def test_shared_package_exports_both_modules(tmp_path):
    root = _project(tmp_path / "p", SHARED_TOML)
    init = (root / "src" / "probe" / "wfm" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from .wfm import Synth" in init
    assert "from .wfm_reader import Reader" in init
    assert '__all__ = ["Synth", "Reader"]' in init


def test_package_siblings_lists_package_mates_only(tmp_path):
    cfg = {
        "module": {
            "wfm": {"objects": ["synth"]},
            "wfm_reader": {"objects": ["wfm_reader"], "package": "wfm"},
            "other": {"objects": ["x"]},
        }
    }
    assert package_siblings(cfg, "wfm") == ["wfm_reader"]
    assert package_siblings(cfg, "wfm_reader") == ["wfm"]
    assert package_siblings(cfg, "other") == []


def test_merge_protects_a_sibling_modules_exports():
    # Without protection the two modules take turns pruning each other out of
    # __all__ and the file never converges.
    src = (
        "from .wfm import Synth  # noqa: E402\n"
        "from .wfm_reader import Reader  # noqa: E402\n"
        '__all__ = ["Synth", "Reader"]\n'
    )
    out = _merge_module_init(src, "wfm", ["Synth"], {}, ["wfm_reader"])
    assert '__all__ = ["Synth", "Reader"]' in out
    out2 = _merge_module_init(out, "wfm_reader", ["Reader"], {}, ["wfm"])
    assert out2 == out  # already converged


def test_merge_still_prunes_the_modules_own_stale_exports():
    # gh-329 must keep working: a removed object's name goes, sibling or not.
    src = (
        "from .wfm import Synth, Gone  # noqa: E402\n"
        "from .wfm_reader import Reader  # noqa: E402\n"
        '__all__ = ["Synth", "Gone", "Reader"]\n'
    )
    out = _merge_module_init(src, "wfm", ["Synth"], {}, ["wfm_reader"])
    assert "from .wfm import Synth  # noqa: E402" in out
    assert '__all__ = ["Synth", "Reader"]' in out


# ── 3. idempotence ───────────────────────────────────────────────────────────


def test_apply_is_idempotent_and_status_is_clean(tmp_path):
    root = _project(tmp_path / "p", SHARED_TOML)
    _silent(apply_run, root)
    before = {
        p: p.read_bytes()
        for p in sorted((root / "src").rglob("*"))
        if p.is_file()
    }
    _silent(apply_run, root)
    after = {
        p: p.read_bytes()
        for p in sorted((root / "src").rglob("*"))
        if p.is_file()
    }
    assert before == after
    assert _silent(status_run, root, check=True) == 0


# ── 4. no churn when the key is absent ───────────────────────────────────────


def test_module_without_package_renders_unchanged(tmp_path):
    """A module with no `package` key must land exactly where it always did.

    The gh-523 plumbing threads an override through every Python path; this
    pins that the override collapses to the module's own pypath so existing
    projects see zero diff.
    """
    root = _project(tmp_path / "p", PLAIN_TOML)
    pkg_dir = root / "src" / "probe" / "wfm_reader"
    assert (pkg_dir / "__init__.py").exists()
    assert (pkg_dir / "wfm_reader.pyi").exists()
    assert (pkg_dir / "tests" / "test_wfm_reader.py").exists()
    assert not (root / "src" / "probe" / "wfm").exists()

    cmake = (
        root / "native" / "src" / "wfm_reader" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert (
        'LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/wfm_reader"' in cmake
    )
    assert 'package = "' not in (root / C.FILENAME).read_text(encoding="utf-8")
    assert "from probe.wfm_reader import Reader" in (
        pkg_dir / "tests" / "test_wfm_reader.py"
    ).read_text(encoding="utf-8")


def test_packaged_and_plain_differ_only_in_the_python_destination(tmp_path):
    """Byte-for-byte guard: the ONLY generated difference between a module
    with `package = "wfm"` and the same module without it is the Python
    destination — the C sources are identical."""
    packaged = _project(tmp_path / "a", PACKAGED_TOML)
    plain = _project(tmp_path / "b", PLAIN_TOML)

    def _c_files(root):
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / "native").rglob("*"))
            if p.is_file() and p.suffix in (".c", ".h")
        }

    assert _c_files(packaged) == _c_files(plain)
