"""Apply-integration tests for ``kind = "capsule"`` modules (gh-286, P1.3).

These exercise the wiring that turns the P1.2 ``render_ext`` generator into a
real ``jm apply`` materialization: the binding, the module ``CMakeLists.txt``,
the ``.pyi`` stub, the top-level ``add_subdirectory`` splice, idempotency, and
a manifest-only rebuild (the model ``jm status --check`` uses). No C compiler
is needed — we assert on the generated tree."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _capsule
from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


def _capsule_module(package: str | None = None) -> dict:
    mod = {
        "kind": "capsule",
        "backing": "ddcr",
        "capsule_name": "proj.ddc.ddcr_state",
        "header": "ddc/ddc_core.h",
        "depends_on": [
            {"name": "ddc", "link": True},
            {"name": "fir", "link": True},
        ],
        "extra_link_libs": ["m"],
        "init_params": [
            {"name": "norm_freq", "type": "double"},
            {"name": "rate", "type": "double"},
        ],
        "methods": [
            {
                "name": "execute",
                "arg_type": "float[]",
                "return_type": "float _Complex[]",
                "caller_out": True,
                "nogil": True,
            },
            {"name": "reset"},
        ],
        "properties": [
            {"name": "norm_freq", "type": "double", "writable": True},
            {"name": "rate", "type": "double"},
        ],
    }
    if package:
        mod["package"] = package
    return mod


def _project_with_capsule(root: Path, package: str | None = None) -> None:
    """Scaffold a project and inject a capsule module into the manifest."""
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("module", {})["ddc_fn"] = _capsule_module(package)
    C.save(root, cfg)


class TestRenderCMake:
    def test_links_dep_cores_and_extra_libs(self):
        cfg = {
            "project": {"name": "proj", "version": "0.1.0"},
            "module": {"ddc_fn": _capsule_module()},
        }
        s = _capsule.render_cmake(cfg, "ddc_fn")
        assert (
            "Python3_add_library(ddc_fn MODULE WITH_SOABI ddc_fn_ext.c)" in s
        )
        # link=true deps become <name>_core link targets
        assert "ddc_core" in s and "fir_core" in s
        assert "\n    m\n" in s  # extra_link_libs
        assert "Python3::NumPy)" in s
        assert "if(BUILD_PYTHON)" in s and s.rstrip().endswith("endif()")

    def test_default_output_dir_is_module_pypath(self):
        cfg = {
            "project": {"name": "proj", "version": "0.1.0"},
            "module": {"ddc_fn": _capsule_module()},
        }
        s = _capsule.render_cmake(cfg, "ddc_fn")
        assert '"${PYTHON_PACKAGE_DIR}/ddc_fn"' in s

    def test_package_override_redirects_output_dir(self):
        cfg = {
            "project": {"name": "proj", "version": "0.1.0"},
            "module": {"ddc_fn": _capsule_module(package="ddc")},
        }
        s = _capsule.render_cmake(cfg, "ddc_fn")
        assert '"${PYTHON_PACKAGE_DIR}/ddc"' in s
        assert "/ddc_fn/$<TARGET_FILE_NAME" not in s


class TestRenderPyi:
    def test_signatures(self):
        cfg = {
            "project": {"name": "proj", "version": "0.1.0"},
            "module": {"ddc_fn": _capsule_module()},
        }
        s = _capsule.render_pyi(cfg, "ddc_fn")
        assert "DDCRState = Any" in s
        assert (
            "def ddcr_create(norm_freq: float, rate: float) -> DDCRState:" in s
        )
        assert "def ddcr_execute(state: DDCRState, x: NDArray[Any], "
        assert "def ddcr_reset(state: DDCRState) -> None: ..." in s
        assert "def ddcr_destroy(state: DDCRState) -> None: ..." in s
        assert "def ddcr_get_norm_freq(state: DDCRState) -> float: ..." in s
        assert (
            "def ddcr_set_norm_freq(state: DDCRState, value: float) -> None:"
            in s
        )
        # read-only property has no setter
        assert "ddcr_set_rate" not in s


class TestApplyMaterialize:
    def test_apply_generates_capsule_files(self, tmp_path):
        _project_with_capsule(tmp_path)
        apply_run(tmp_path)

        ext = tmp_path / "native" / "src" / "ddc_fn" / "ddc_fn_ext.c"
        cmake = tmp_path / "native" / "src" / "ddc_fn" / "CMakeLists.txt"
        pyi = tmp_path / "src" / "proj" / "ddc_fn" / "ddc_fn.pyi"
        assert ext.exists() and cmake.exists() and pyi.exists()
        assert "PyInit_ddc_fn" in ext.read_text()
        assert 'include "ddc/ddc_core.h"' in ext.read_text()

    def test_top_cmake_wires_add_subdirectory(self, tmp_path):
        _project_with_capsule(tmp_path)
        apply_run(tmp_path)
        top = (tmp_path / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/ddc_fn)" in top

    def test_package_override_places_pyi_in_sibling(self, tmp_path):
        _project_with_capsule(tmp_path, package="ddc")
        apply_run(tmp_path)
        assert (tmp_path / "src" / "proj" / "ddc" / "ddc_fn.pyi").exists()

    def test_apply_is_idempotent(self, tmp_path):
        _project_with_capsule(tmp_path)
        apply_run(tmp_path)
        before = {
            p: p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        apply_run(tmp_path)
        after = {
            p: p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        assert before == after

    def test_manifest_only_rebuild(self, tmp_path):
        """The model ``jm status`` uses: rebuild from the manifest alone."""
        import shutil

        _project_with_capsule(tmp_path)
        apply_run(tmp_path)
        full = {
            str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file()
            and "build" not in p.parts
            and p.name != "compile_commands.json"
        }
        for p in tmp_path.iterdir():
            if p.name == "just-makeit.toml":
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        apply_run(tmp_path)
        rebuilt = {
            str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file()
            and "build" not in p.parts
            and p.name != "compile_commands.json"
        }
        # Every capsule file reappears identically.
        for rel in (
            "native/src/ddc_fn/ddc_fn_ext.c",
            "native/src/ddc_fn/CMakeLists.txt",
            "src/proj/ddc_fn/ddc_fn.pyi",
        ):
            assert rel in rebuilt, f"{rel} missing after rebuild"
            assert rebuilt[rel] == full[rel], f"{rel} drifted"


class TestManifestRoundTrip:
    def test_capsule_keys_survive_dump(self, tmp_path):
        _project_with_capsule(tmp_path, package="ddc")
        text = (tmp_path / "just-makeit.toml").read_text()
        assert 'kind = "capsule"' in text
        assert 'package = "ddc"' in text
        assert 'header = "ddc/ddc_core.h"' in text
        assert 'depends_on = [{ name = "ddc", link = true }' in text
        # reloads cleanly with the same capsule readers
        cfg = C.load(tmp_path)
        assert C.is_capsule_module(cfg, "ddc_fn")
        assert C.capsule_package(cfg, "ddc_fn") == "ddc"
        assert C.capsule_header(cfg, "ddc_fn") == "ddc/ddc_core.h"
        assert C.dep_link_libs(C.capsule_depends_on(cfg, "ddc_fn")) == [
            "ddc_core",
            "fir_core",
        ]
