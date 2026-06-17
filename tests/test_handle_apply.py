"""Apply-integration tests for ``kind = "handle"`` modules (gh-306).

These exercise the wiring that turns ``_handle.render_*`` into a real
``jm apply`` materialization, and — crucially — that the new kind is recognized
at all FOUR ``_apply.py`` special-case sites (import, materialize dispatch, the
``_mods_need_update`` exclusion filter, and ``_sync_aggregates`` glue
reconciliation). Miss any one and ``jm apply`` / ``jm status --check`` break
silently, so the assertions below are deliberately end-to-end:

- ``test_apply_generates_handle_files`` — materialize dispatch (site b) emits the
  three glue files; the exclusion filter (site c) keeps the object-group path
  from choking on the handle module (apply would raise otherwise).
- ``test_apply_is_idempotent`` — a second apply runs ``_sync_aggregates`` (site
  d); a missing branch there would rewrite/drop the glue → non-identical tree.
- ``test_manifest_only_rebuild`` — the model ``jm status --check`` uses: wipe
  everything but the manifest and replay; every handle file must reappear byte
  identically.
- ``test_non_wfm_ring_*`` — the genericity gate, end-to-end on a non-wfm handle.

No C compiler is needed — we assert on the generated tree."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


# ── manifest fixtures (module dicts injected into a scaffolded project) ───────


def _writer_module(package: str | None = "wfm") -> dict:
    mod = {
        "kind": "handle",
        "backing": "wfm_writer",
        "header": "wfm/wfm_writer.h",
        "type_name": "Writer",
        "context_manager": True,
        "close_fn": "wfm_writer_close",
        "create_fn": "wfm_writer_open",
        "depends_on": [{"name": "wfm_writer", "link": True}],
        "extra_link_libs": ["m"],
        "create_args": [
            {"name": "path", "type": "path"},
            {
                "name": "file_type",
                "type": "int",
                "enum": "ftype",
                "default": "raw",
                "kwonly": True,
            },
            {
                "name": "sample_type",
                "type": "int",
                "enum": "stype",
                "default": "cf32",
                "kwonly": True,
            },
            {"name": "headroom", "type": "double", "default": "0.0"},
        ],
        "create_post": [
            {
                "fn": "wfm_writer_set_gain",
                "when": "headroom",
                "arg": "pow(10, -headroom/20)",
            }
        ],
        "methods": [
            {
                "name": "write",
                "fn": "wfm_writer_write",
                "returns": "size_t",
                "nogil": True,
                "args": [{"name": "iq", "type": "float _Complex[]"}],
            }
        ],
        "getters": [
            {
                "fn": "wfm_writer_stats",
                "out": "wfm_writer_stats_t",
                "cache": False,
                "fields": [
                    {
                        "name": "clip_fraction",
                        "from": "frac",
                        "type": "double",
                    },
                    {
                        "name": "peak_dbfs",
                        "type": "double",
                        "expr": "tmp.peak > 0 ? 20*log10(tmp.peak) : -INFINITY",
                    },
                    {
                        "name": "clipped",
                        "type": "bool",
                        "expr": "self->sample_type >= 2 && tmp.peak > 1.0",
                    },
                ],
            }
        ],
    }
    if package:
        mod["package"] = package
    return mod


def _ring_module() -> dict:
    """The non-wfm genericity gate — a toy ringbuf handle, zero wfm symbols."""
    return {
        "kind": "handle",
        "backing": "ringbuf",
        "type_name": "Ring",
        "context_manager": True,
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        "create_args": [{"name": "capacity", "type": "size_t"}],
        "methods": [
            {
                "name": "push",
                "fn": "ringbuf_push",
                "returns": "size_t",
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                "name": "pop",
                "fn": "ringbuf_pop",
                "returns": "float[]",
                "args": [{"name": "n", "type": "size_t"}],
            },
            {"name": "clear", "fn": "ringbuf_clear"},
        ],
        "getters": [
            {
                "fn": "ringbuf_stats",
                "out": "ringbuf_stats_t",
                "cache": True,
                "fields": [
                    {
                        "name": "fill_fraction",
                        "type": "double",
                        "expr": "self->capacity ? (double)tmp.used / "
                        "(double)self->capacity : 0.0",
                    }
                ],
            }
        ],
    }


_WRITER_ENUMS = [
    {"name": "ftype", "values": ["raw", "csv"]},
    {"name": "stype", "values": ["cf32", "cf64", "ci16"]},
]


def _project_with_handle(
    root: Path, *, writer_pkg: str | None = "wfm", ring: bool = False
) -> None:
    """Scaffold a project and inject one or two handle modules + the enums."""
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("enum", []).extend(_WRITER_ENUMS)
    cfg.setdefault("module", {})["wfm_writer"] = _writer_module(writer_pkg)
    if ring:
        cfg["module"]["ringbuf"] = _ring_module()
    C.save(root, cfg)


# ── the dispatch-exercising end-to-end tests ─────────────────────────────────


class TestApplyMaterialize:
    def test_apply_generates_handle_files(self, tmp_path):
        _project_with_handle(tmp_path)
        apply_run(tmp_path)

        d = tmp_path / "native" / "src" / "wfm_writer"
        ext = d / "wfm_writer_ext.c"
        cmake = d / "CMakeLists.txt"
        pyi = tmp_path / "src" / "proj" / "wfm" / "wfm_writer.pyi"
        assert ext.exists() and cmake.exists() and pyi.exists()
        src = ext.read_text()
        assert "PyInit_wfm_writer" in src
        assert 'include "wfm/wfm_writer.h"' in src
        # the typed-class face (not free functions)
        assert "PyTypeObject" in src and "WriterType" in src

    def test_top_cmake_wires_add_subdirectory(self, tmp_path):
        _project_with_handle(tmp_path)
        apply_run(tmp_path)
        top = (tmp_path / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/wfm_writer)" in top

    def test_package_override_places_pyi_in_sibling(self, tmp_path):
        # package = "wfm" → the .pyi lands in the sibling wfm package, not
        # src/proj/wfm_writer/.
        _project_with_handle(tmp_path, writer_pkg="wfm")
        apply_run(tmp_path)
        assert (tmp_path / "src" / "proj" / "wfm" / "wfm_writer.pyi").exists()
        assert not (
            tmp_path / "src" / "proj" / "wfm_writer" / "wfm_writer.pyi"
        ).exists()

    def test_apply_is_idempotent(self, tmp_path):
        """A second apply runs _sync_aggregates (site d) — the tree must not
        drift, proving the handle branch reconciles its glue identically."""
        _project_with_handle(tmp_path, ring=True)
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
        """The model ``jm status --check`` uses: rebuild from the manifest
        alone. Exercises the materialize dispatch for every handle file."""
        _project_with_handle(tmp_path, ring=True)
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
        for rel in (
            "native/src/wfm_writer/wfm_writer_ext.c",
            "native/src/wfm_writer/CMakeLists.txt",
            "src/proj/wfm/wfm_writer.pyi",
            "native/src/ringbuf/ringbuf_ext.c",
            "native/src/ringbuf/CMakeLists.txt",
            "src/proj/ringbuf/ringbuf.pyi",
        ):
            assert rel in rebuilt, f"{rel} missing after rebuild"
            assert rebuilt[rel] == full[rel], f"{rel} drifted"


class TestNonWfmGenericity:
    def test_non_wfm_ring_applies(self, tmp_path):
        """The genericity gate end-to-end: a non-wfm handle materializes and
        carries zero wfm symbols."""
        _project_with_handle(tmp_path, ring=True)
        apply_run(tmp_path)
        ext = (
            tmp_path / "native" / "src" / "ringbuf" / "ringbuf_ext.c"
        ).read_text()
        assert "PyInit_ringbuf" in ext
        assert "RingType" in ext
        assert "ringbuf_open" in ext and "ringbuf_stats" in ext
        # derived-expr getter rendered over the stashed capacity
        assert "fill_fraction" in ext
        assert "wfm" not in ext.lower()


class TestManifestRoundTrip:
    def test_handle_keys_survive_dump(self, tmp_path):
        _project_with_handle(tmp_path, ring=True)
        text = (tmp_path / "just-makeit.toml").read_text()
        assert 'kind = "handle"' in text
        assert 'type_name = "Writer"' in text
        # reloads cleanly with the handle readers intact
        cfg = C.load(tmp_path)
        assert C.is_handle_module(cfg, "wfm_writer")
        assert C.is_handle_module(cfg, "ringbuf")
        assert C.handle_create_fn(cfg, "wfm_writer") == "wfm_writer_open"
        # nested methods/getters survive the dump round-trip
        methods = C.handle_methods(cfg, "wfm_writer")
        assert methods and methods[0]["name"] == "write"
        getters = C.handle_getters(cfg, "ringbuf")
        assert getters and getters[0]["fields"][0]["name"] == "fill_fraction"
