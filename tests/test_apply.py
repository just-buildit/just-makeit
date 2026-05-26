"""Integration tests for `just-makeit apply`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._module import run as module_run
from just_makeit._method import run as method_run
from just_makeit._property import run as property_run
from just_makeit._apply import run as apply_run

_IGNORE = {"compile_commands.json"}


def _scaffold(root: Path) -> None:
    """Build a project with a standalone object and a module object."""
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    object_run(root, "gadget", None, state_vars=[("g", "float", "1.0f")])
    module_run(root, "dsp")
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    method_run(
        root,
        "nco",
        "tune",
        "dsp",
        "void",
        "float _Complex",
        False,
        [],
        params=[("freq", "double")],
    )
    property_run(root, "nco", "locked", "dsp", "uint8_t", True, field=True)


def _tree(root: Path) -> dict[str, bytes]:
    """Map every file (relative path -> bytes), skipping build artifacts."""
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if "build" in rel.parts or rel.name in _IGNORE:
            continue
        out[str(rel)] = p.read_bytes()
    return out


def _strip_to_manifest(root: Path) -> None:
    """Delete everything except just-makeit.toml."""
    import shutil

    for p in root.iterdir():
        if p.name == "just-makeit.toml":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()


class TestApplyMaterialize:
    def test_recreates_every_file(self, tmp_path):
        """apply on a manifest-only directory rebuilds the whole project."""
        ref = tmp_path / "ref"
        _scaffold(ref)
        expected = _tree(ref)

        proj = tmp_path / "proj"
        _scaffold(proj)
        _strip_to_manifest(proj)
        assert list(proj.iterdir()) == [proj / "just-makeit.toml"]

        apply_run(proj)
        assert _tree(proj) == expected

    def test_manifest_untouched(self, tmp_path):
        proj = tmp_path / "proj"
        _scaffold(proj)
        before = (proj / "just-makeit.toml").read_bytes()
        _strip_to_manifest(proj)
        apply_run(proj)
        assert (proj / "just-makeit.toml").read_bytes() == before

    def test_idempotent(self, tmp_path):
        """A second apply on a complete project changes nothing."""
        proj = tmp_path / "proj"
        _scaffold(proj)
        before = _tree(proj)
        apply_run(proj)
        assert _tree(proj) == before


class TestApplyReconcilesAggregates:
    """A project authored as manifest + per-object fragments must have its
    aggregate wiring (top CMakeLists, umbrella, package __init__.py)
    reconciled by apply so the project actually builds."""

    def test_top_cmakelists_gets_component_wiring(self, tmp_path):
        from just_makeit._apply import run as apply_run
        from just_makeit._new import run as new_run

        proj = tmp_path / "proj"
        new_run("proj", proj)
        # Hand-author a fragment (the "compose" or "fresh-checkout" case).
        (proj / "objects").mkdir()
        (proj / "objects" / "agc.toml").write_text(
            '[agc]\narg_type = "float _Complex"\n'
            'return_type = "float _Complex"\nmutable = "false"\n'
            'no_state = "false"\nno_step = "false"\n\n'
            '[[agc.state]]\nname = "gain"\ntype = "float"\n'
            'default = "1.0f"\n'
        )
        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )

        apply_run(proj)

        cmake_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
        # add_subdirectory must be inside the Components sentinel section.
        comp_block = cmake_text.split("# ── Components", 1)[1].split(
            "# ── Modules", 1
        )[0]
        assert "add_subdirectory(native/src/agc)" in comp_block
        assert (
            "target_sources(proj_lib PRIVATE $<TARGET_OBJECTS:agc_core>)"
            in comp_block
        )

    def test_umbrella_header_gets_include(self, tmp_path):
        from just_makeit._apply import run as apply_run
        from just_makeit._new import run as new_run

        proj = tmp_path / "proj"
        new_run("proj", proj)
        (proj / "objects").mkdir()
        (proj / "objects" / "agc.toml").write_text(
            '[agc]\narg_type = "float _Complex"\n'
            'return_type = "float _Complex"\nmutable = "false"\n'
            'no_state = "false"\nno_step = "false"\n'
        )
        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )

        apply_run(proj)

        umbrella = (proj / "native" / "inc" / "proj.h").read_text(
            encoding="utf-8"
        )
        assert '#include "agc/agc_core.h"' in umbrella

    def test_package_init_py_gets_import(self, tmp_path):
        from just_makeit._apply import run as apply_run
        from just_makeit._new import run as new_run

        proj = tmp_path / "proj"
        new_run("proj", proj)
        (proj / "objects").mkdir()
        (proj / "objects" / "agc.toml").write_text(
            '[agc]\narg_type = "float _Complex"\n'
            'return_type = "float _Complex"\nmutable = "false"\n'
            'no_state = "false"\nno_step = "false"\n'
        )
        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )

        apply_run(proj)

        init = (proj / "src" / "proj" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "from .agc import Agc" in init
        assert '"Agc"' in init  # __all__

    def test_user_cmake_content_outside_sentinels_preserved(self, tmp_path):
        """The doppler case: hand-written CMake blocks outside the
        Components / Modules sentinels survive a reconcile."""
        from just_makeit._apply import run as apply_run
        from just_makeit._new import run as new_run

        proj = tmp_path / "proj"
        new_run("proj", proj)
        # Inject a user-written vendored-libfoo block ABOVE the Install
        # section (i.e., between Modules sentinel and # ── Install).
        cmake = proj / "CMakeLists.txt"
        text = cmake.read_text(encoding="utf-8")
        marker = "# ── Vendored libfoo (user content) ───\nadd_library(foo INTERFACE)\n\n"
        text = text.replace("# ── Install", marker + "# ── Install")
        cmake.write_text(text, encoding="utf-8")

        (proj / "objects").mkdir()
        (proj / "objects" / "agc.toml").write_text(
            '[agc]\narg_type = "float _Complex"\n'
            'return_type = "float _Complex"\nmutable = "false"\n'
            'no_state = "false"\nno_step = "false"\n'
        )
        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )

        apply_run(proj)

        after = cmake.read_text(encoding="utf-8")
        assert "add_library(foo INTERFACE)" in after
        assert "Vendored libfoo (user content)" in after


class TestApplyAddOnly:
    def test_existing_files_not_overwritten(self, tmp_path):
        """apply never clobbers a file that already exists."""
        proj = tmp_path / "proj"
        _scaffold(proj)
        core_c = proj / "native" / "src" / "widget" / "widget_core.c"
        marker = core_c.read_text(encoding="utf-8") + "\n/* HAND EDIT */\n"
        core_c.write_text(marker, encoding="utf-8")

        apply_run(proj)
        assert core_c.read_text(encoding="utf-8") == marker


class TestApplyErrors:
    def test_no_manifest_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            apply_run(tmp_path)

    def test_empty_manifest_exits(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj)
        with pytest.raises(SystemExit):
            apply_run(proj)


_COUNTER_FRAGMENT = """\
[counter]
module = "dsp"
arg_type = "void"
return_type = "uint64_t"
mutable = "true"
no_state = "false"
no_step = "false"

impl = '''
uint64_t counter_step(counter_state_t *state) {
    return state->count++;
}
'''

[[counter.state]]
name = "count"
type = "uint64_t"
default = "0"
"""


class TestApplyModuleDirective:
    def test_module_objects_manifest_updated(self, tmp_path):
        """Composing a fragment with module='dsp' wires it into [module.dsp]."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        module_run(proj, "dsp")
        frag = tmp_path / "counter.toml"
        frag.write_text(_COUNTER_FRAGMENT)

        apply_run(proj, fragment=frag)

        import just_makeit._config as C

        cfg = C.load(proj)
        assert "counter" in C.module_objects(cfg, "dsp")

    def test_module_objects_no_standalone_ext(self, tmp_path):
        """Module objects get no standalone _ext.c; the module's ext.c is updated."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        module_run(proj, "dsp")
        frag = tmp_path / "counter.toml"
        frag.write_text(_COUNTER_FRAGMENT)

        apply_run(proj, fragment=frag)

        # Module object: core files exist in own subdir (normal)
        assert (proj / "native" / "src" / "counter" / "counter_core.c").exists()
        # Module object: no standalone ext.c (would exist for standalone objects)
        assert not (
            proj / "native" / "src" / "counter" / "counter_ext.c"
        ).exists()
        # Module's ext.c was updated to include counter
        dsp_ext = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert "counter" in dsp_ext

    def test_module_directive_preserved_through_mutation(self, tmp_path):
        """The module = 'dsp' field survives a subsequent C.save() call."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        module_run(proj, "dsp")
        frag = tmp_path / "counter.toml"
        frag.write_text(_COUNTER_FRAGMENT)
        apply_run(proj, fragment=frag)

        import just_makeit._config as C

        cfg = C.load(proj)
        assert cfg.get("counter", {}).get("module") == "dsp"
        # Simulate a mutation that calls C.save().
        C.save(proj, cfg)
        cfg2 = C.load(proj)
        assert cfg2.get("counter", {}).get("module") == "dsp"

    def test_module_directive_unknown_module_errors(self, tmp_path):
        """Referencing a module that doesn't exist raises ValueError."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        # No module created — "dsp" doesn't exist in manifest.
        frag = tmp_path / "counter.toml"
        frag.write_text(_COUNTER_FRAGMENT)

        with pytest.raises(SystemExit):
            apply_run(proj, fragment=frag)

    def test_fragment_already_in_objects_dir_succeeds(self, tmp_path):
        """jm apply objects/Foo.toml works when the fragment is already on
        disk under the include glob (issue #42).

        The "conflict" detected by _merge_fragment is the glob loading the
        fragment itself, which is the expected state — apply should proceed
        directly to materialization rather than raising an error."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        module_run(proj, "dsp")

        # Simulate the user manually copying the fragment into objects/.
        objects_dir = proj / "objects"
        objects_dir.mkdir()
        dest = objects_dir / "counter.toml"
        dest.write_text(_COUNTER_FRAGMENT)

        # Wire in the include glob and module membership by hand (as a user
        # who skipped jm apply the first time would do).
        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )
        from just_makeit._apply import _wire_module_object
        _wire_module_object(manifest, "dsp", "counter")

        # Must not raise — proceeds straight to materialization.
        apply_run(proj, fragment=dest)

        # Materialization actually ran.
        assert (proj / "native" / "src" / "counter" / "counter_core.c").exists()

    def test_fragment_already_in_objects_dir_no_duplicate_copy(self, tmp_path):
        """When the fragment is already in objects/, no second copy is made."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        module_run(proj, "dsp")

        objects_dir = proj / "objects"
        objects_dir.mkdir()
        dest = objects_dir / "counter.toml"
        dest.write_text(_COUNTER_FRAGMENT)

        manifest = proj / "just-makeit.toml"
        manifest.write_text(
            'include = ["objects/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8")
        )
        from just_makeit._apply import _wire_module_object
        _wire_module_object(manifest, "dsp", "counter")

        original_mtime = dest.stat().st_mtime
        apply_run(proj, fragment=dest)
        # The fragment itself must not be overwritten.
        assert dest.stat().st_mtime == original_mtime


class TestApplySelectiveOnly:
    def test_only_module_skips_other_module(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        module_run(proj, "util")
        object_run(
            proj, "helper", "util", state_vars=[("x", "float", "0.0f")]
        )
        apply_run(proj)

        util_ext = proj / "native" / "src" / "util" / "util_ext.c"
        sentinel = "/* SENTINEL_ONLY_TEST */"
        util_ext.write_text(
            util_ext.read_text(encoding="utf-8") + f"\n{sentinel}\n",
            encoding="utf-8",
        )

        apply_run(proj, only="dsp")

        assert sentinel in util_ext.read_text(encoding="utf-8")

    def test_only_comp_updates_owning_module(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        apply_run(proj)

        object_run(proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")])

        apply_run(proj, only="fir")

        fir_core = proj / "native" / "src" / "fir" / "fir_core.c"
        assert fir_core.exists()
        dsp_ext = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert "fir" in dsp_ext

    def test_only_comp_skips_unrelated_module(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        module_run(proj, "util")
        object_run(
            proj, "helper", "util", state_vars=[("x", "float", "0.0f")]
        )
        apply_run(proj)

        util_ext = proj / "native" / "src" / "util" / "util_ext.c"
        sentinel = "/* SENTINEL_ONLY_COMP */"
        util_ext.write_text(
            util_ext.read_text(encoding="utf-8") + f"\n{sentinel}\n",
            encoding="utf-8",
        )

        object_run(proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")])
        apply_run(proj, only="fir")

        assert sentinel in util_ext.read_text(encoding="utf-8")

    def test_only_unknown_exits(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        apply_run(proj)

        with pytest.raises(SystemExit):
            apply_run(proj, only="nonexistent")


class TestApplyExtraC:
    """apply preserves hand-written *_extra.c files through re-materialisation
    and keeps them wired into the module aggregator (gh-28)."""

    def test_extra_c_preserved_through_apply(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        extra = proj / "native" / "src" / "dsp" / "dsp_ext_extra.c"
        extra.write_text("/* hand-written extra */\n", encoding="utf-8")

        apply_run(proj)

        assert extra.exists(), "extra.c deleted by apply"
        assert extra.read_text(encoding="utf-8") == "/* hand-written extra */\n"

    def test_extra_c_included_in_aggregator_after_apply(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        object_run(
            proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")]
        )

        extra = proj / "native" / "src" / "dsp" / "dsp_ext_extra.c"
        extra.write_text("/* extra */\n", encoding="utf-8")

        apply_run(proj)

        ext_c = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert "dsp_ext_extra.c" in ext_c


class TestApplyExtraTypes:
    """extra_types in just-makeit.toml registers hand-written CPython types
    in PyInit_ and survives jm apply re-materialisation (gh-28 full fix)."""

    def _proj_with_extra_types(self, root):
        new_run("proj", root, [], [])
        module_run(root, "dsp")
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        manifest = root / "just-makeit.toml"
        toml_text = manifest.read_text(encoding="utf-8")
        toml_text = toml_text.replace(
            "[module.dsp]",
            '[module.dsp]\nextra_types = ["HalfbandDp", "HalfbandR2C"]',
        )
        manifest.write_text(toml_text, encoding="utf-8")
        return root

    def test_extra_types_in_pyinit_after_object(self, tmp_path):
        """Adding an object after extra_types are declared keeps them in PyInit_."""
        proj = tmp_path / "proj"
        self._proj_with_extra_types(proj)
        object_run(
            proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")]
        )
        ext_c = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert "PyType_Ready(&HalfbandDpType)" in ext_c
        assert "PyType_Ready(&HalfbandR2CType)" in ext_c
        assert 'PyModule_AddObject(m, "HalfbandDp"' in ext_c
        assert 'PyModule_AddObject(m, "HalfbandR2C"' in ext_c

    def test_extra_types_survive_apply(self, tmp_path):
        """jm apply preserves extra_types registrations in PyInit_."""
        proj = tmp_path / "proj"
        self._proj_with_extra_types(proj)
        apply_run(proj)
        ext_c = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert "PyType_Ready(&HalfbandDpType)" in ext_c
        assert 'PyModule_AddObject(m, "HalfbandDp"' in ext_c

    def test_extra_types_after_jm_owned_types(self, tmp_path):
        """Extra type registrations appear after jm-owned types in PyInit_."""
        proj = tmp_path / "proj"
        self._proj_with_extra_types(proj)
        apply_run(proj)
        ext_c = (
            proj / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        nco_pos = ext_c.find("NcoType")
        hb_pos = ext_c.find("HalfbandDpType")
        assert nco_pos != -1
        assert hb_pos != -1
        assert nco_pos < hb_pos, "extra types must follow jm-owned types"
