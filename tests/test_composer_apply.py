"""Apply-integration tests for ``kind = "composer"`` modules (gh-287, C2.4).

Exercise the wiring that turns the codegen into a real ``jm apply``
materialization: the module ``_ext.c`` (all four OO types + factory table +
``PyInit``), the ``CMakeLists.txt``, the ``.pyi``, the top-level
``add_subdirectory`` splice, idempotency, and a manifest-only rebuild. No C
compiler is needed here — the compile + byte-exact behavior parity is exercised
against doppler's real ``wfm`` in the pilot."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


def _composer_module():
    return {
        "kind": "composer",
        "backing": "wfm_compose",
        "capsule_name": "proj.wfm.compose_state",
        "package": "wfm",
        "header": "wfm/wfm_compose.h",
        "composes": ["wfm_synth"],
        "sample_type": True,
        "depends_on": [
            {"name": "wfm_compose", "link": True},
            {"name": "wfm_synth", "link": True},
        ],
        "extra_link_libs": ["m"],
        "source": {
            "object": "wfm_synth",
            "struct": "wfm_source_t",
            "type_name": "Synth",
            "fields": [
                {
                    "name": "type",
                    "type": "int",
                    "enum": "wfm_type",
                    "default": "tone",
                },
                {"name": "freq", "type": "double", "default": "0.0"},
                {"name": "bits", "type": "uint8_t*", "bytes": True},
            ],
        },
        "segment": {
            "type_name": "Segment",
            "struct": "wfm_segment_t",
            "fields": [
                {"name": "fs", "type": "double", "default": "1e6"},
                {"name": "num_samples", "type": "size_t", "default": "1024"},
                {"name": "off_samples", "type": "size_t", "default": "0"},
            ],
            "sources": "multi",
        },
        "timeline": {
            "type_name": "Timeline",
            "loop": ["once", "repeat", "continuous"],
        },
        "oo": {
            "factories": ["tone", "noise"],
            "emit": "ctypes",
            "discriminant": "type",
            "composer_type_name": "Composer",
        },
        "json": {
            "enabled": True,
            "to_json_fn": "wfm_spec_to_json",
            "to_json_trailing": ["0.0"],
        },
    }


def _project(root: Path) -> None:
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("enum", []).extend(
        [
            {"name": "wfm_type", "values": ["tone", "noise", "pn"]},
        ]
    )
    cfg.setdefault("module", {})["wfm_compose"] = _composer_module()
    C.save(root, cfg)


_REL = (
    "native/src/wfm_compose/wfm_compose_ext.c",
    "native/src/wfm_compose/CMakeLists.txt",
    "src/proj/wfm/wfm_compose.pyi",
)


class TestApplyMaterialize:
    def test_generates_files(self, tmp_path):
        _project(tmp_path)
        apply_run(tmp_path)
        for rel in _REL:
            assert (tmp_path / rel).exists(), rel
        ext = (tmp_path / _REL[0]).read_text()
        # all four types + factories + PyInit, and the backing header include
        for sym in (
            "SynthType",
            "SegmentType",
            "TimelineType",
            "ComposerType",
            "PyInit_wfm_compose",
        ):
            assert sym in ext, sym
        assert 'include "wfm/wfm_compose.h"' in ext
        cm = (tmp_path / _REL[1]).read_text()
        assert "Python3_add_library(wfm_compose MODULE" in cm
        assert '"${PYTHON_PACKAGE_DIR}/wfm"' in cm
        pyi = (tmp_path / _REL[2]).read_text()
        assert "class Composer:" in pyi and "def tone(" in pyi

    def test_top_cmake_wired(self, tmp_path):
        _project(tmp_path)
        apply_run(tmp_path)
        assert (
            "add_subdirectory(native/src/wfm_compose)"
            in (tmp_path / "CMakeLists.txt").read_text()
        )

    def test_idempotent(self, tmp_path):
        _project(tmp_path)
        apply_run(tmp_path)
        snap = {
            p: p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        apply_run(tmp_path)
        again = {
            p: p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        assert snap == again

    def test_manifest_only_rebuild(self, tmp_path):
        import shutil

        _project(tmp_path)
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
        for rel in _REL:
            assert (tmp_path / rel).exists(), f"{rel} missing after rebuild"
            assert (tmp_path / rel).read_bytes() == full[rel], f"{rel} drifted"


class TestRoundTrip:
    def test_composer_manifest_round_trips(self, tmp_path):
        _project(tmp_path)
        text = (tmp_path / "just-makeit.toml").read_text()
        assert 'kind = "composer"' in text
        assert "[module.wfm_compose.source]" in text
        assert "[module.wfm_compose.json]" in text
        cfg2 = C.load(tmp_path)
        assert C.is_composer_module(cfg2, "wfm_compose")
        assert C.composer_json(cfg2, "wfm_compose") is True
        # json fn keys survive for the generator
        jt = cfg2["module"]["wfm_compose"]["json"]
        assert jt["to_json_fn"] == "wfm_spec_to_json"
