"""Integration tests for `just-makeit apply`."""

from __future__ import annotations

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


class TestApplyImplInjection:
    """gh-121: apply must patch impl into existing headers, not just new ones."""

    def _add_impl_to_toml(
        self, toml_path: "Path", comp: str, body: str
    ) -> None:
        """Append an impl block to the component section in the TOML."""
        text = toml_path.read_text(encoding="utf-8")
        # Inject impl = '''...''' right after the [comp] header line.
        marker = f"[{comp}]\n"
        assert marker in text, f"[{comp}] section not found"
        impl_block = f"impl = '''\n{body}\n'''\n"
        text = text.replace(marker, marker + impl_block, 1)
        toml_path.write_text(text, encoding="utf-8")

    def test_impl_injected_into_pre_existing_header(self, tmp_path):
        """impl added to TOML after jm object must appear in the header."""
        proj = tmp_path / "proj"
        new_run("proj", proj, ["widget"], [("gain", "float", "0.0f")])

        # Simulate user manually adding impl to the TOML after initial scaffold.
        toml = proj / "just-makeit.toml"
        self._add_impl_to_toml(
            toml, "widget", "state->gain *= 2.0f;\nreturn state->gain;"
        )

        apply_run(proj)

        h_path = proj / "native" / "inc" / "widget" / "widget_core.h"
        text = h_path.read_text(encoding="utf-8")
        assert "state->gain *= 2.0f;" in text

    def test_impl_injected_into_newly_created_header(self, tmp_path):
        """impl set in TOML before any files exist must appear in the header."""
        proj = tmp_path / "proj"
        new_run("proj", proj, ["widget"], [("gain", "float", "0.0f")])

        toml = proj / "just-makeit.toml"
        self._add_impl_to_toml(toml, "widget", "return state->gain + 1.0f;")

        # Delete sacred files so apply creates them from scratch.
        import shutil

        shutil.rmtree(proj / "native" / "inc" / "widget")
        apply_run(proj)

        h_path = proj / "native" / "inc" / "widget" / "widget_core.h"
        text = h_path.read_text(encoding="utf-8")
        assert "return state->gain + 1.0f;" in text


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
        assert (
            proj / "native" / "src" / "counter" / "counter_core.c"
        ).exists()
        # Module object: no standalone ext.c (would exist for standalone objects)
        assert not (
            proj / "native" / "src" / "counter" / "counter_ext.c"
        ).exists()
        # Module's ext.c was updated to include counter
        dsp_ext = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
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
        assert (
            proj / "native" / "src" / "counter" / "counter_core.c"
        ).exists()

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
        object_run(proj, "helper", "util", state_vars=[("x", "float", "0.0f")])
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
        dsp_ext = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "fir" in dsp_ext

    def test_only_comp_skips_unrelated_module(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        module_run(proj, "util")
        object_run(proj, "helper", "util", state_vars=[("x", "float", "0.0f")])
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
        assert (
            extra.read_text(encoding="utf-8") == "/* hand-written extra */\n"
        )

    def test_extra_c_included_in_aggregator_after_apply(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, [], [])
        module_run(proj, "dsp")
        object_run(proj, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        object_run(proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")])

        extra = proj / "native" / "src" / "dsp" / "dsp_ext_extra.c"
        extra.write_text("/* extra */\n", encoding="utf-8")

        apply_run(proj)

        ext_c = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
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
        object_run(proj, "fir", "dsp", state_vars=[("taps", "float", "0.0f")])
        ext_c = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyType_Ready(&HalfbandDpType)" in ext_c
        assert "PyType_Ready(&HalfbandR2CType)" in ext_c
        assert 'PyModule_AddObject(m, "HalfbandDp"' in ext_c
        assert 'PyModule_AddObject(m, "HalfbandR2C"' in ext_c

    def test_extra_types_survive_apply(self, tmp_path):
        """jm apply preserves extra_types registrations in PyInit_."""
        proj = tmp_path / "proj"
        self._proj_with_extra_types(proj)
        apply_run(proj)
        ext_c = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyType_Ready(&HalfbandDpType)" in ext_c
        assert 'PyModule_AddObject(m, "HalfbandDp"' in ext_c

    def test_extra_types_after_jm_owned_types(self, tmp_path):
        """Extra type registrations appear after jm-owned types in PyInit_."""
        proj = tmp_path / "proj"
        self._proj_with_extra_types(proj)
        apply_run(proj)
        ext_c = (proj / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        nco_pos = ext_c.find("NcoType")
        hb_pos = ext_c.find("HalfbandDpType")
        assert nco_pos != -1
        assert hb_pos != -1
        assert nco_pos < hb_pos, "extra types must follow jm-owned types"


_VOID_ARG_METHODS_FRAGMENT = """\
[drain_obj]
arg_type = "void"
return_type = "uint64_t"
mutable = "true"

[[drain_obj.state]]
name = "pos"
type = "uint64_t"
default = "0"

[[drain_obj.methods]]
name = "drain"
params = [{name = "n", type = "uint32_t"}]
return_type = "uint64_t"
variable_output = true

[[drain_obj.methods]]
name = "reset"
params = [{name = "start", type = "uint32_t"}]
return_type = "void"
"""

# Same as above but uses out_type instead of return_type to specify the buffer
# element type — tests that out_type is honoured for variable_output methods.
_OUT_TYPE_METHODS_FRAGMENT = """\
[lfsr_obj]
arg_type = "void"
return_type = "uint8_t"
mutable = "true"

[[lfsr_obj.state]]
name = "state"
type = "uint64_t"
default = "0x3FF"

[[lfsr_obj.methods]]
name = "steps"
params = [{name = "n", type = "uint32_t"}]
out_type = "uint8_t"
variable_output = true
"""


class TestMethodReplayArgType:
    """Verify that apply replay uses 'void' as the default arg_type for methods
    (not 'float _Complex'), fixing gh#49."""

    def _apply_fragment(self, proj, frag_text):
        new_run("proj", proj)
        frag = proj.parent / "frag.toml"
        frag.write_text(frag_text)
        apply_run(proj, fragment=frag)
        return (
            proj / "native" / "inc" / "drain_obj" / "drain_obj_core.h"
        ).read_text(encoding="utf-8")

    def test_variable_output_params_used_not_float_complex(self, tmp_path):
        """drain() should use uint32_t n, not const float complex *in."""
        header = self._apply_fragment(
            tmp_path / "proj", _VOID_ARG_METHODS_FRAGMENT
        )
        assert "uint32_t n" in header
        assert "float complex" not in header

    def test_variable_output_correct_return_type(self, tmp_path):
        """drain() output array is uint64_t *, not float complex *."""
        header = self._apply_fragment(
            tmp_path / "proj", _VOID_ARG_METHODS_FRAGMENT
        )
        assert "uint64_t *out" in header

    def test_reset_with_params_no_duplicate(self, tmp_path):
        """User reset(start) suppresses the builtin no-arg reset declaration."""
        header = self._apply_fragment(
            tmp_path / "proj", _VOID_ARG_METHODS_FRAGMENT
        )
        # User's parameterised reset should be present
        assert "uint32_t start" in header
        # Builtin no-arg reset declaration should be absent (suppressed)
        reset_decls = [
            line
            for line in header.splitlines()
            if "drain_obj_reset" in line and line.strip().startswith("void")
        ]
        assert len(reset_decls) == 1, (
            f"Expected exactly one reset declaration, got: {reset_decls}"
        )
        assert "uint32_t start" in reset_decls[0]

    def test_standard_object_reset_decl_present(self, tmp_path):
        """A normal object (no user reset method) still emits the builtin reset."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        object_run(proj, "osc", None, state_vars=[("phase", "float", "0.0f")])
        header = (proj / "native" / "inc" / "osc" / "osc_core.h").read_text(
            encoding="utf-8"
        )
        assert "void osc_reset(osc_state_t *state);" in header


class TestVariableOutputOutType:
    """Verify that out_type is honoured as the buffer element type for
    variable_output methods (gh#49 follow-up)."""

    def _apply(self, proj):
        new_run("proj", proj)
        frag = proj.parent / "frag.toml"
        frag.write_text(_OUT_TYPE_METHODS_FRAGMENT)
        apply_run(proj, fragment=frag)
        header = (
            proj / "native" / "inc" / "lfsr_obj" / "lfsr_obj_core.h"
        ).read_text(encoding="utf-8")
        core_c = (
            proj / "native" / "src" / "lfsr_obj" / "lfsr_obj_core.c"
        ).read_text(encoding="utf-8")
        return header, core_c

    def test_header_uses_out_type_not_return_type(self, tmp_path):
        """Header buffer param should be uint8_t *, not float complex *."""
        header, _ = self._apply(tmp_path / "proj")
        assert "uint8_t *out" in header
        assert "float complex" not in header

    def test_header_params_used(self, tmp_path):
        """Header should carry the declared uint32_t n param."""
        header, _ = self._apply(tmp_path / "proj")
        assert "uint32_t n" in header

    def test_core_c_uses_out_type(self, tmp_path):
        """_core.c stub signature should use uint8_t *out."""
        _, core_c = self._apply(tmp_path / "proj")
        assert "uint8_t *out" in core_c
        assert "float complex" not in core_c


_CREATE_RESET_IMPL_FRAGMENT = """\
[lfsr]
arg_type = "void"
return_type = "uint8_t"
mutable = "true"
create_impl = \"\"\"
if (initial_state == 0) return NULL;
obj->initial_state = initial_state;
obj->state = initial_state;
\"\"\"
reset_impl = \"\"\"
state->state = state->initial_state;
\"\"\"

[[lfsr.state]]
name = "initial_state"
type = "uint64_t"
default = "0"

[[lfsr.state]]
name = "state"
type = "uint64_t"
default = "0"
"""


class TestCreateResetImpl:
    """Verify that create_impl and reset_impl override the generated
    field-assignment blocks in component_core.c (gh#51)."""

    def _apply(self, proj):
        new_run("proj", proj)
        frag = proj.parent / "lfsr.toml"
        frag.write_text(_CREATE_RESET_IMPL_FRAGMENT)
        apply_run(proj, fragment=frag)
        return (proj / "native" / "src" / "lfsr" / "lfsr_core.c").read_text(
            encoding="utf-8"
        )

    def test_create_impl_body_present(self, tmp_path):
        """create_impl body should appear inside lfsr_create()."""
        core_c = self._apply(tmp_path / "proj")
        assert "if (initial_state == 0) return NULL;" in core_c
        assert "obj->initial_state = initial_state;" in core_c

    def test_reset_impl_body_present(self, tmp_path):
        """reset_impl body should appear inside lfsr_reset()."""
        core_c = self._apply(tmp_path / "proj")
        assert "state->state = state->initial_state;" in core_c

    def test_generated_assignments_replaced(self, tmp_path):
        """Custom create_impl replaces the generated obj->field = value block."""
        core_c = self._apply(tmp_path / "proj")
        # Generated assignments would be "obj->initial_state = initial_state;"
        # followed by "obj->state = state;" — the latter collides with the
        # local parameter named 'state'. Our create_impl replaces the block.
        assert "obj->state = state;" not in core_c

    def test_create_impl_indented(self, tmp_path):
        """create_impl lines must be 4-space indented in the output."""
        core_c = self._apply(tmp_path / "proj")
        assert "    if (initial_state == 0) return NULL;" in core_c


_DESTROY_IMPL_FRAGMENT = """\
[buf]
arg_type = "void"
return_type = "void"
mutable = "true"
destroy_impl = \"\"\"
if (state->log) fclose(state->log);
\"\"\"

[[buf.state]]
name = "n"
type = "uint32_t"
default = "0"
"""


class TestDestroyImpl:
    """Verify destroy_impl TOML key splices a custom body into
    component_destroy() before the trailing free(state) (gh#51)."""

    def _apply(self, proj):
        new_run("proj", proj)
        frag = proj.parent / "buf.toml"
        frag.write_text(_DESTROY_IMPL_FRAGMENT)
        apply_run(proj, fragment=frag)
        return (proj / "native" / "src" / "buf" / "buf_core.c").read_text(
            encoding="utf-8"
        )

    def test_destroy_impl_body_present(self, tmp_path):
        """destroy_impl body should appear inside buf_destroy()."""
        core_c = self._apply(tmp_path / "proj")
        assert "if (state->log) fclose(state->log);" in core_c

    def test_destroy_impl_before_free(self, tmp_path):
        """Custom destroy_impl must run before the trailing free(state)."""
        core_c = self._apply(tmp_path / "proj")
        body_pos = core_c.index("fclose(state->log)")
        free_pos = core_c.index("free(state);")
        assert body_pos < free_pos

    def test_destroy_impl_indented(self, tmp_path):
        """destroy_impl lines must be 4-space indented."""
        core_c = self._apply(tmp_path / "proj")
        assert "    if (state->log) fclose(state->log);" in core_c

    def test_free_state_still_emitted(self, tmp_path):
        """free(state) must still be generated — destroy_impl prepends, not replaces."""
        core_c = self._apply(tmp_path / "proj")
        assert "    free(state);" in core_c


class TestDestroyImplMutex:
    """destroy_impl and destroy_impl_file are mutually exclusive."""

    def test_both_set_raises(self, tmp_path):
        from just_makeit._apply import _validate_fragment_impl_keys

        fragment = {
            "buf": {
                "destroy_impl": "free(state->x);",
                "destroy_impl_file": "legacy.c::buf_destroy",
            },
        }
        with pytest.raises(ValueError, match="mutually exclusive"):
            _validate_fragment_impl_keys(fragment, "test.toml")


_OPAQUE_FIELD_FRAGMENT = """\
[fft]
arg_type    = "void"
return_type = "void"
no_state    = "true"
create_impl = \"\"\"
obj->scratch = malloc(sizeof(float) * 8);
if (!obj->scratch) { free(obj); return NULL; }
\"\"\"

[[fft.state]]
name   = "scratch"
type   = "float *"
opaque = true
"""


class TestOpaqueState:
    """Opaque state fields are emitted verbatim into the struct with no
    auto-getter/setter, no constructor parameter, and no reset logic.
    Lifecycle is the user's responsibility via create_impl/destroy_impl."""

    def _apply(self, proj):
        new_run("proj", proj)
        frag = proj.parent / "fft.toml"
        frag.write_text(_OPAQUE_FIELD_FRAGMENT)
        apply_run(proj, fragment=frag)
        return (
            (proj / "native" / "inc" / "fft" / "fft_core.h").read_text(),
            (proj / "native" / "src" / "fft" / "fft_core.c").read_text(),
            (proj / "native" / "src" / "fft" / "fft_ext.c").read_text(),
        )

    def test_opaque_field_in_struct(self, tmp_path):
        """Opaque field's verbatim type appears as a struct member."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "float * scratch;" in header

    def test_no_getter_setter_decl(self, tmp_path):
        """Opaque fields must not generate fft_get_scratch / fft_set_scratch."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "fft_get_scratch" not in header
        assert "fft_set_scratch" not in header

    def test_no_getter_setter_impl(self, tmp_path):
        """Opaque fields must not generate getter/setter bodies in core.c."""
        _, core_c, _ = self._apply(tmp_path / "proj")
        assert "fft_get_scratch" not in core_c
        assert "fft_set_scratch" not in core_c

    def test_not_in_constructor_params(self, tmp_path):
        """Opaque fields must not appear in the C create() signature."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "fft_create(void)" in header

    def test_not_in_python_init_kwlist(self, tmp_path):
        """Opaque fields must not appear in the Python __init__ kwlist."""
        _, _, ext_c = self._apply(tmp_path / "proj")
        assert '"scratch"' not in ext_c

    def test_create_impl_initializes_opaque(self, tmp_path):
        """create_impl body should run and reference obj->scratch."""
        _, core_c, _ = self._apply(tmp_path / "proj")
        assert "obj->scratch = malloc" in core_c


class TestOpaqueRequiresCreateImpl:
    """An opaque field without create_impl would leave a wild pointer in
    the struct — validate up front and fail fast."""

    def test_missing_create_impl_raises(self):
        from just_makeit._apply import _validate_fragment_impl_keys

        fragment = {
            "fft": {
                "no_state": "true",
                "state": [
                    {"name": "scratch", "type": "float *", "opaque": True}
                ],
            },
        }
        with pytest.raises(ValueError, match="opaque state field"):
            _validate_fragment_impl_keys(fragment, "test.toml")

    def test_create_impl_file_satisfies(self):
        from just_makeit._apply import _validate_fragment_impl_keys

        fragment = {
            "fft": {
                "create_impl_file": "legacy.c::fft_create",
                "state": [
                    {"name": "scratch", "type": "float *", "opaque": True}
                ],
            },
        }
        _validate_fragment_impl_keys(fragment, "test.toml")


# Fragment: one ctor param (size), two no_ctor fields (idx, sum), one opaque (buf).
_NO_CTOR_FRAGMENT = """\
[ring]
arg_type    = "float"
return_type = "float"
mutable     = "true"
create_impl = \"\"\"
obj->size = size;
obj->buf  = calloc(size, sizeof(float));
if (!obj->buf) { free(obj); return NULL; }
obj->idx  = 0;
obj->sum  = 0.0f;
\"\"\"
destroy_impl = \"\"\"
free(state->buf);
\"\"\"

[[ring.state]]
name   = "buf"
type   = "float *"
opaque = true

[[ring.state]]
name    = "size"
type    = "size_t"
default = "16"

[[ring.state]]
name    = "idx"
type    = "size_t"
default = "0"
no_ctor = true

[[ring.state]]
name    = "sum"
type    = "float"
default = "0.0f"
no_ctor = true
"""


class TestNoCtorState:
    """no_ctor = true fields stay in the struct with getters/setters and reset
    logic, but are excluded from the C create() signature and Python kwlist.
    They are silently initialised to their TOML default in create_assignments."""

    def _apply(self, proj):
        new_run("proj", proj)
        frag = proj.parent / "ring.toml"
        frag.write_text(_NO_CTOR_FRAGMENT)
        apply_run(proj, fragment=frag)
        return (
            (proj / "native" / "inc" / "ring" / "ring_core.h").read_text(),
            (proj / "native" / "src" / "ring" / "ring_core.c").read_text(),
            (proj / "native" / "src" / "ring" / "ring_ext.c").read_text(),
        )

    def test_no_ctor_fields_in_struct(self, tmp_path):
        """no_ctor fields must still appear in the state struct."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "size_t idx;" in header
        assert "float sum;" in header

    def test_no_ctor_excluded_from_c_signature(self, tmp_path):
        """no_ctor fields must NOT appear in the C create() signature."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "ring_create(size_t size)" in header
        assert "idx" not in header.split("ring_create")[1].split(")")[0]
        assert "sum" not in header.split("ring_create")[1].split(")")[0]

    def test_no_ctor_excluded_from_python_kwlist(self, tmp_path):
        """no_ctor fields must NOT appear in the Python __init__ kwlist."""
        _, _, ext_c = self._apply(tmp_path / "proj")
        kwlist_line = next(
            line
            for line in ext_c.splitlines()
            if "kwlist" in line and "char" in line
        )
        assert '"idx"' not in kwlist_line
        assert '"sum"' not in kwlist_line
        assert '"size"' in kwlist_line

    def test_no_ctor_fields_have_getters_setters(self, tmp_path):
        """no_ctor fields must still have auto-generated getters/setters."""
        header, _, _ = self._apply(tmp_path / "proj")
        assert "ring_get_idx" in header
        assert "ring_set_idx" in header
        assert "ring_get_sum" in header
        assert "ring_set_sum" in header

    def test_no_ctor_initialised_to_default_in_create(self, tmp_path):
        """When create_impl is absent, no_ctor fields are set to their default
        in create_assignments.  With create_impl, the user handles it."""
        _, core_c, _ = self._apply(tmp_path / "proj")
        # create_impl overrides create_assignments here, so the defaults are
        # set by the user's create_impl body — just verify the signature.
        assert "ring_create(size_t size)" in core_c

    def test_no_ctor_no_impl_auto_initialised(self, tmp_path):
        """Without create_impl the no_ctor fields must be auto-assigned their
        TOML default inside create_assignments."""
        proj = tmp_path / "proj"
        new_run("proj", proj)
        frag = proj.parent / "ring2.toml"
        # Same but without create_impl so auto-assignments kick in.
        frag.write_text("""\
[ring2]
arg_type    = "float"
return_type = "float"

[[ring2.state]]
name    = "gain"
type    = "float"
default = "1.0f"

[[ring2.state]]
name    = "phase"
type    = "float"
default = "0.0f"
no_ctor = true
""")
        apply_run(proj, fragment=frag)
        core_c = (
            proj / "native" / "src" / "ring2" / "ring2_core.c"
        ).read_text()
        # gain is a ctor param — assigned from param
        assert "obj->gain = gain;" in core_c
        # phase is no_ctor — assigned from TOML default, not a param
        assert "obj->phase = 0.0f;" in core_c
        assert "ring2_create(float gain)" in core_c


# ---------------------------------------------------------------------------
# Module-path integration: opaque and no_ctor fields in module objects
# ---------------------------------------------------------------------------

_OPAQUE_MODULE_FRAGMENT = """\
[fft]
module      = "dsp"
arg_type    = "void"
return_type = "void"
no_state    = "true"
create_impl = \"\"\"
obj->scratch = malloc(sizeof(float) * 8);
if (!obj->scratch) { free(obj); return NULL; }
\"\"\"
destroy_impl = \"\"\"
free(state->scratch);
\"\"\"

[[fft.state]]
name   = "scratch"
type   = "float *"
opaque = true
"""

_NO_CTOR_MODULE_FRAGMENT = """\
[ticker]
module      = "dsp"
arg_type    = "float"
return_type = "float"
mutable     = "true"

[[ticker.state]]
name    = "gain"
type    = "float"
default = "1.0f"

[[ticker.state]]
name    = "count"
type    = "size_t"
default = "0"
no_ctor = true
"""


class TestOpaqueInModule:
    """Opaque fields on a module object must propagate correctly through
    _regenerate_module: present in the struct, absent from the ext fragment
    kwlist and binding wrappers, no getter/setter generated."""

    def _apply(self, proj):
        new_run("proj", proj)
        module_run(proj, "dsp")
        frag = proj.parent / "fft.toml"
        frag.write_text(_OPAQUE_MODULE_FRAGMENT)
        apply_run(proj, fragment=frag)
        ext_dir = proj / "native" / "src" / "dsp"
        return (
            (proj / "native" / "inc" / "fft" / "fft_core.h").read_text(),
            (ext_dir / "dsp_ext_fft.c").read_text(),
        )

    def test_opaque_field_in_struct(self, tmp_path):
        """Opaque field appears verbatim in the module object's struct."""
        header, _ = self._apply(tmp_path / "proj")
        assert "float * scratch;" in header

    def test_no_getter_setter_in_ext_fragment(self, tmp_path):
        """Opaque fields must not generate binding wrappers in the module ext fragment."""
        _, ext_frag = self._apply(tmp_path / "proj")
        assert "fft_get_scratch" not in ext_frag
        assert "fft_set_scratch" not in ext_frag

    def test_not_in_module_ext_kwlist(self, tmp_path):
        """Opaque fields must not appear in the Python kwlist in the module ext fragment."""
        _, ext_frag = self._apply(tmp_path / "proj")
        assert '"scratch"' not in ext_frag


class TestNoCtorInModule:
    """no_ctor fields on a module object must propagate correctly through
    _regenerate_module: present in the struct with getters/setters, absent
    from the C create() signature and the Python kwlist in the ext fragment."""

    def _apply(self, proj):
        new_run("proj", proj)
        module_run(proj, "dsp")
        frag = proj.parent / "ticker.toml"
        frag.write_text(_NO_CTOR_MODULE_FRAGMENT)
        apply_run(proj, fragment=frag)
        ext_dir = proj / "native" / "src" / "dsp"
        return (
            (proj / "native" / "inc" / "ticker" / "ticker_core.h").read_text(),
            (ext_dir / "dsp_ext_ticker.c").read_text(),
        )

    def test_no_ctor_field_in_struct(self, tmp_path):
        """no_ctor field must still appear in the module object's state struct."""
        header, _ = self._apply(tmp_path / "proj")
        assert "size_t count;" in header

    def test_no_ctor_excluded_from_c_signature(self, tmp_path):
        """no_ctor field must NOT appear in the C create() signature."""
        header, _ = self._apply(tmp_path / "proj")
        assert "ticker_create(float gain)" in header

    def test_no_ctor_excluded_from_module_ext_kwlist(self, tmp_path):
        """no_ctor field must NOT appear in the Python kwlist in the module ext fragment."""
        _, ext_frag = self._apply(tmp_path / "proj")
        assert '"count"' not in ext_frag
        assert '"gain"' in ext_frag

    def test_no_ctor_getters_setters_in_ext_fragment(self, tmp_path):
        """no_ctor fields must still have getter/setter bindings in the module ext fragment."""
        _, ext_frag = self._apply(tmp_path / "proj")
        assert "ticker_get_count" in ext_frag
        assert "ticker_set_count" in ext_frag


_FN_IMPL_FRAGMENT = """\
[module.io]

[[module.io.functions]]
name = "q15_to_float"
params = [
    {name = "input", type = "int16_t[]"},
    {name = "output", type = "float[]"},
]
impl = '''
for (size_t i = 0; i < input_len; i++) {
    output[i] = (float)input[i] / 32768.0f;
}
'''
"""


class TestApplyModuleFunctionImpl:
    """Regression test for gh-68: module-level functions declared with `impl`
    in the manifest must materialise the body into the function's own sacred
    ``native/src/<mod>/<fn>.c`` and the declaration into ``<mod>_core.h`` on
    ``jm apply``. The bug was that ``_sync_missing`` skipped these files
    (already created empty by ``jm module``) so the impls were silently
    dropped."""

    def _apply(self, proj_root):
        new_run("proj", proj_root)
        module_run(proj_root, "io")
        frag = proj_root.parent / "fns.toml"
        frag.write_text(_FN_IMPL_FRAGMENT)
        apply_run(proj_root, fragment=frag)
        fn_c = (
            proj_root / "native" / "src" / "io" / "q15_to_float.c"
        ).read_text()
        core_h = (
            proj_root / "native" / "inc" / "io" / "io_core.h"
        ).read_text()
        return fn_c, core_h

    def test_impl_body_in_fn_c(self, tmp_path):
        fn_c, _ = self._apply(tmp_path / "proj")
        assert "q15_to_float" in fn_c
        assert "(float)input[i] / 32768.0f" in fn_c

    def test_decl_in_core_h(self, tmp_path):
        _, core_h = self._apply(tmp_path / "proj")
        assert "q15_to_float" in core_h

    def test_idempotent(self, tmp_path):
        proj = tmp_path / "proj"
        self._apply(proj)
        fn_c_path = proj / "native" / "src" / "io" / "q15_to_float.c"
        before_c = fn_c_path.read_text()
        before_h = (proj / "native" / "inc" / "io" / "io_core.h").read_text()
        apply_run(proj)
        assert fn_c_path.read_text() == before_c
        assert (
            proj / "native" / "inc" / "io" / "io_core.h"
        ).read_text() == before_h


class TestApplySacredGlueSplit:
    """jm apply's sacred/glue contract for standalone objects:

    - Glue (binding, stub, header declarations) regenerates from the
      manifest on every apply, so a TOML edit reaches the public API.
    - Sacred _core.c (steps()/lifecycle bodies) is never touched once it
      exists; the inline step() body and struct fields in _core.h survive
      the declaration refresh. User algorithm code is never clobbered.
    """

    def _project(self, root: Path) -> None:
        new_run("proj", root, ["eng"], [("gain", "double", "1.0")])

    def test_core_c_is_sacred_byte_identical(self, tmp_path):
        root = tmp_path / "proj"
        self._project(root)
        core_c = root / "native" / "src" / "eng" / "eng_core.c"
        # Plant a user sentinel inside the steps() body.
        text = core_c.read_text(encoding="utf-8")
        text = text.replace(
            "eng_steps(", "/* USER_SENTINEL */\nvoid eng_steps(", 1
        )
        core_c.write_text(text, encoding="utf-8")
        before = core_c.read_bytes()
        # Edit the manifest (add a method) and re-apply.
        manifest = root / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[[eng.methods]]\nname = "reset_gain"\n'
            'arg_type = "void"\nreturn_type = "void"\n',
            encoding="utf-8",
        )
        apply_run(root)
        # Sacred file is byte-for-byte unchanged.
        assert core_c.read_bytes() == before
        assert "USER_SENTINEL" in core_c.read_text(encoding="utf-8")

    def test_glue_refreshes_from_manifest(self, tmp_path):
        root = tmp_path / "proj"
        self._project(root)
        manifest = root / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[[eng.methods]]\nname = "reset_gain"\n'
            'arg_type = "void"\nreturn_type = "void"\n',
            encoding="utf-8",
        )
        apply_run(root)
        # Header declares the method; binding exposes it; stub lists it.
        core_h = (root / "native" / "inc" / "eng" / "eng_core.h").read_text(
            encoding="utf-8"
        )
        ext_c = (root / "native" / "src" / "eng" / "eng_ext.c").read_text(
            encoding="utf-8"
        )
        pyi = (root / "src" / "proj" / "eng.pyi").read_text(encoding="utf-8")
        assert "eng_reset_gain" in core_h
        assert "reset_gain" in ext_c
        assert "reset_gain" in pyi

    def test_inline_step_body_preserved_on_header_refresh(self, tmp_path):
        root = tmp_path / "proj"
        self._project(root)
        core_h = root / "native" / "inc" / "eng" / "eng_core.h"
        # Plant a sentinel inside the inline step() body.
        text = core_h.read_text(encoding="utf-8")
        text = text.replace("/* TODO", "/* USER_STEP_SENTINEL */ /* TODO", 1)
        core_h.write_text(text, encoding="utf-8")
        # Force a header refresh by adding a method to the manifest.
        manifest = root / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[[eng.methods]]\nname = "reset_gain"\n'
            'arg_type = "void"\nreturn_type = "void"\n',
            encoding="utf-8",
        )
        apply_run(root)
        refreshed = core_h.read_text(encoding="utf-8")
        assert "eng_reset_gain" in refreshed  # declarations refreshed
        assert "USER_STEP_SENTINEL" in refreshed  # step() body preserved
