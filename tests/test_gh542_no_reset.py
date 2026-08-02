"""Integration tests for ``no_reset`` (gh-542).

jm's object shape always emitted ``reset()``. Some objects have nothing
coherent to reset: doppler's ``wfm.Writer`` has already written its samples to
disk, and its internal written-sample count drives the BLUE ``data_size`` patch
applied at close — so a "reset" would *corrupt* the header rather than return
the object to a clean state. The honest answer is "construct a new Writer".

The three workarounds all degrade silently or loudly-but-wrongly: a C no-op
returns ``None`` so the caller believes the reset happened; a hand-written
raise in the sacred fragment reverts to that no-op the moment a regeneration
drops it; omitting the C function turns the problem into a link error, which is
loud but is a build failure standing in for a manifest key.

So the acceptance bar tested here is *removal*, not stubbing: with
``no_reset = "true"`` there is no ``reset()`` on the type, no ``<obj>_reset()``
declared or defined in the core, no ``.pyi`` entry, and nothing in the
generated tests or benchmarks that calls it.

`TestByteIdenticalWhenUnset` is the guard rail: the reset text was cut out of
templates every existing project renders, so an object without the flag must
still produce exactly the previous bytes.
"""

import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._script import run as script_run
from just_makeit._status import run as status_run
from just_makeit._view import run as view_run

# Files whose generated text is allowed to mention reset at all. Nothing is
# exempt today; the tuple documents that the sweep below is exhaustive.
_TEXT_SUFFIXES = (".c", ".h", ".py", ".pyi", ".toml", ".txt")


def _read(path):
    return path.read_text(encoding="utf-8")


def _generated_texts(project):
    """Every generated text file, minus build output and the manifest keys."""
    for path in sorted(project.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if "build" in path.parts or "__pycache__" in path.parts:
            continue
        yield path, _read(path)


def _mentions_reset(project, *, allow_manifest=True):
    """Paths whose generated text still refers to a reset.

    *allow_manifest* keeps the ``no_reset = "true"`` declaration itself out of
    the result — that key is the whole point, not a leak.
    """
    hits = []
    for path, text in _generated_texts(project):
        body = text.replace("no_reset", "") if allow_manifest else text
        if "reset" in body.lower():
            hits.append(path)
    return hits


@pytest.fixture()
def plain(tmp_path):
    """A project whose single object does NOT declare no_reset."""
    dest = tmp_path / "plain"
    new_run("plain", dest, ["keeper"], [("gain", "double", "1.0")])
    return dest


@pytest.fixture()
def flagged(tmp_path):
    """A project whose single standalone object declares no_reset."""
    dest = tmp_path / "flag"
    new_run("flag", dest, ["writerx"], [("gain", "double", "1.0")])
    object_run(
        dest,
        "writerx2",
        None,
        state_vars=[("gain", "double", "1.0")],
        no_reset=True,
    )
    return dest


# ── the manifest key ────────────────────────────────────────────────────────


class TestConfigKey:
    """`is_no_reset` follows `is_no_step`'s truthiness exactly."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            ("true", True),
            (False, False),
            ("false", False),
            (None, False),
        ],
    )
    def test_truthiness(self, value, expected):
        cfg = {"w": {} if value is None else {"no_reset": value}}
        assert C.is_no_reset(cfg, "w") is expected

    def test_absent_component_is_false(self):
        assert C.is_no_reset({}, "nope") is False

    def test_key_survives_dump_and_reload(self, flagged):
        # gh-519's second defect was a manifest key that `_dump` dropped; a
        # dropped `no_reset` regenerates the very method it removed.
        cfg = C.load(flagged)
        assert C.is_no_reset(cfg, "writerx2") is True
        C.save(flagged, cfg)
        assert C.is_no_reset(C.load(flagged), "writerx2") is True

    def test_only_persisted_when_set(self, plain):
        # Unset objects must not gain a key, or every existing manifest churns
        # the moment this feature lands.
        assert "no_reset" not in _read(plain / C.FILENAME)
        assert C.is_no_reset(C.load(plain), "keeper") is False


# ── byte-identical guard for objects WITHOUT the flag ───────────────────────


class TestByteIdenticalWhenUnset:
    """No flag -> the pre-gh-542 text, to the byte.

    The reset function, its declaration, its PyMethodDef row, its stub and
    both generated tests were hardcoded in templates until this feature needed
    to remove them. These assertions pin the previous bytes.
    """

    def test_core_c_reset_definition(self, plain):
        c = _read(plain / "native" / "src" / "keeper" / "keeper_core.c")
        assert (
            "}\n"
            "\n"
            "void\n"
            "keeper_reset(keeper_state_t *state)\n"
            "{\n"
            "    state->gain = 1.0;\n"
            "}\n"
            "\n"
        ) in c

    def test_core_h_declaration_and_lifecycle_line(self, plain):
        h = _read(plain / "native" / "inc" / "keeper" / "keeper_core.h")
        assert (
            " * Lifecycle: create -> [step / steps / reset]* -> destroy" in h
        )
        assert (
            "/**\n"
            " * @brief Reset Keeper to its post-create state.\n"
            " * @param state  Must be non-NULL.\n"
            " */\n"
            "void keeper_reset(keeper_state_t *state);\n"
        ) in h

    def test_ext_c_binding_and_pymethoddef(self, plain):
        ext = _read(plain / "native" / "src" / "keeper" / "keeper_ext.c")
        assert (
            "Keeper_reset(KeeperObject *self, PyObject *Py_UNUSED(ignored))\n"
            "{\n"
            "    if (!self->handle) {\n"
            '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            "        return NULL;\n"
            "    }\n"
            "    keeper_reset(self->handle);\n"
            "    Py_RETURN_NONE;\n"
            "}\n"
        ) in ext
        assert (
            '    {"reset",    (PyCFunction)Keeper_reset,    METH_NOARGS,\n'
            # gh-700: reset's runtime literal goes through `_build_ml_doc`
            # now (escaped, one C string per logical line), so it carries the
            # trailing newline every other generated doc literal has.
            '     "Reset state to post-create defaults.\\n"},\n'
        ) in ext

    def test_pyi_stub(self, plain):
        pyi = _read(plain / "src" / "plain" / "keeper.pyi")
        assert (
            "\n"
            "    def reset(self) -> None:\n"
            '        """Reset state to post-create defaults."""\n'
        ) in pyi
        # The class-docstring doctest is a *runnable* example, not prose.
        assert ">>> obj.reset()" in pyi

    def test_generated_tests(self, plain):
        py = _read(plain / "src" / "plain" / "tests" / "test_keeper.py")
        assert "\n    def test_reset(self):\n        obj = Keeper(" in py
        assert "obj.reset()" in py
        c = _read(plain / "native" / "tests" / "test_keeper_core.c")
        assert (
            "    /* reset restores defaults */\n"
            "    keeper_set_gain(obj, 2.0);\n"
            "    keeper_reset(obj);\n"
        ) in c


# ── removal, at the codegen level ───────────────────────────────────────────


class TestRemovedFromEveryArtifact:
    def test_nothing_generated_mentions_reset(self, tmp_path):
        dest = tmp_path / "gone"
        new_run(
            "gone",
            dest,
            ["writerx"],
            [("gain", "double", "1.0")],
            no_reset=True,
        )
        assert _mentions_reset(dest) == []

    def test_core_h_has_no_declaration(self, flagged):
        h = _read(flagged / "native" / "inc" / "writerx2" / "writerx2_core.h")
        assert "writerx2_reset" not in h
        # The lifecycle summary must not advertise a verb that is gone.
        assert "reset" not in h

    def test_core_c_has_no_definition(self, flagged):
        c = _read(flagged / "native" / "src" / "writerx2" / "writerx2_core.c")
        assert "writerx2_reset" not in c
        # The wrapper slots collapse cleanly: no stray empty function, and
        # the neighbouring definitions keep their single blank-line spacing.
        assert "\n\n\n" not in c

    def test_ext_c_has_no_binding(self, flagged):
        ext = _read(flagged / "native" / "src" / "writerx2" / "writerx2_ext.c")
        assert "reset" not in ext

    def test_pyi_has_no_stub_and_no_doctest(self, flagged):
        pyi = _read(flagged / "src" / "flag" / "writerx2.pyi")
        assert "def reset" not in pyi
        # A `>>> obj.reset()` example is a failing doctest under
        # `pytest --doctest-glob='*.pyi'`, not merely stale prose.
        assert "reset" not in pyi

    def test_generated_tests_do_not_call_it(self, flagged):
        py = _read(flagged / "src" / "flag" / "tests" / "test_writerx2.py")
        c = _read(flagged / "native" / "tests" / "test_writerx2_core.c")
        assert "reset" not in py
        assert "reset" not in c

    def test_no_slot_leaks(self, flagged):
        for path, text in _generated_texts(flagged):
            assert not re.search(r"<<[a-z_]*reset[a-z_]*>>", text), path

    def test_sibling_object_is_unaffected(self, flagged):
        # Two objects in one project: only the flagged one loses reset().
        assert "writerx_reset" in _read(
            flagged / "native" / "inc" / "writerx" / "writerx_core.h"
        )


class TestModuleObject:
    """The multi-object module is a separate codegen path (gh-541 had to fix
    both, and so does this)."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "mod"
        new_run("mod", dest, ["anchor"], [("gain", "double", "1.0")])
        module_run(dest, "filt")
        object_run(
            dest,
            "modw",
            "filt",
            state_vars=[("gain", "double", "1.0")],
            no_reset=True,
        )
        return dest

    def test_fragment_and_aggregator_have_no_binding(self, project):
        frag = project / "native" / "src" / "filt" / "filt_ext_modw.c"
        agg = project / "native" / "src" / "filt" / "filt_ext.c"
        text = _read(frag) if frag.exists() else ""
        assert "modw_reset" not in text
        assert "Modw_reset" not in text
        assert "modw_reset" not in _read(agg)

    def test_module_pyi_has_no_stub(self, project):
        pyi = _read(project / "src" / "mod" / "filt" / "filt.pyi")
        assert "def reset" not in pyi

    def test_module_core_files_and_tests(self, project):
        assert "modw_reset" not in _read(
            project / "native" / "inc" / "modw" / "modw_core.h"
        )
        assert "modw_reset" not in _read(
            project / "native" / "src" / "modw" / "modw_core.c"
        )
        assert "reset" not in _read(
            project / "src" / "mod" / "filt" / "tests" / "test_modw.py"
        )


class TestView:
    """A view (gh-504) is a second Python class over one C core, rendered by
    its own path — it inherits the parent's no_reset."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "vw"
        new_run("vw", dest, ["anchor"], [("gain", "double", "1.0")])
        module_run(dest, "vm")
        object_run(
            dest,
            "viewed",
            "vm",
            state_vars=[("gain", "double", "1.0")],
            no_reset=True,
        )
        view_run(dest, "viewed", "Alt", "vm", "viewed_create_alt")
        return dest

    def test_view_fragment_has_no_binding(self, project):
        frag = project / "native" / "src" / "vm" / "vm_ext_alt.c"
        assert "reset" not in _read(frag)

    def test_view_pyi_has_no_stub(self, project):
        pyi = _read(project / "src" / "vw" / "vm" / "vm.pyi")
        assert "def reset" not in pyi
        assert "Alt" in pyi  # the view really is in this stub


class TestNoResetWithNoState:
    """`no_state` and `no_reset` are independent keys; together they must still
    produce a coherent object. `no_state` alone keeps reset() — that is
    deliberate and is pinned here so it cannot regress into an implication."""

    def test_no_state_alone_still_has_reset(self, tmp_path):
        dest = tmp_path / "ns"
        new_run("ns", dest, ["sink"], None, no_state=True)
        h = _read(dest / "native" / "inc" / "sink" / "sink_core.h")
        assert "sink_reset(sink_state_t *state);" in h
        ext = _read(dest / "native" / "src" / "sink" / "sink_ext.c")
        assert "SinkObj_reset" in ext

    def test_both_flags_remove_it(self, tmp_path):
        dest = tmp_path / "both"
        new_run("both", dest, ["sink"], None, no_state=True, no_reset=True)
        assert _mentions_reset(dest) == []


# ── replay: apply / status / script ─────────────────────────────────────────


class TestReplay:
    def test_apply_materializes_without_reset(self, flagged):
        ext = flagged / "native" / "src" / "writerx2" / "writerx2_ext.c"
        pyi = flagged / "src" / "flag" / "writerx2.pyi"
        test_py = flagged / "src" / "flag" / "tests" / "test_writerx2.py"
        for p in (ext, pyi, test_py):
            p.unlink()
        apply_run(flagged)
        for p in (ext, pyi, test_py):
            assert p.exists()
            assert "reset" not in _read(p)

    def test_apply_is_idempotent(self, flagged):
        apply_run(flagged)
        first = {p: t for p, t in _generated_texts(flagged)}
        apply_run(flagged)
        second = {p: t for p, t in _generated_texts(flagged)}
        assert first == second

    def test_status_check_is_clean(self, flagged, capsys):
        capsys.readouterr()
        assert status_run(flagged, check=True) == 0
        assert "stale" not in capsys.readouterr().out

    def test_script_emits_the_flag(self, flagged, capsys):
        capsys.readouterr()
        script_run(flagged)
        out = capsys.readouterr().out
        assert "--no-reset" in out
        # ...only for the object that declared it.
        writerx2 = next(
            b for b in out.split("just-makeit object") if "writerx2" in b
        )
        assert "--no-reset" in writerx2


# ── the acceptance bar: it is gone at runtime ───────────────────────────────


@pytest.mark.slow
class TestNoResetEndToEnd:
    """One project, every codegen path, built and imported.

    This is the load-bearing test: `hasattr(obj, "reset")` must be False and
    the C symbol must not exist, because a *stub* would satisfy every text
    assertion above while still telling the caller a reset happened.
    """

    def test_builds_imports_and_has_no_reset(self, tmp_path):
        dest = tmp_path / "e2e"
        new_run("e2e", dest, ["writerx"], [("gain", "double", "1.0")])
        # Rebuild writerx with the flag (new_run's first object is the
        # unflagged control below).
        object_run(
            dest,
            "flagged_obj",
            None,
            state_vars=[("gain", "double", "1.0")],
            no_reset=True,
        )
        object_run(dest, "sinkx", None, no_state=True, no_reset=True)
        module_run(dest, "vm")
        object_run(
            dest,
            "viewed",
            "vm",
            state_vars=[("gain", "double", "1.0")],
            no_reset=True,
        )
        view_run(dest, "viewed", "Alt", "vm", "viewed_create_alt")

        build = subprocess.run(
            ["make"], cwd=dest, capture_output=True, text=True
        )
        assert build.returncode == 0, build.stderr[-3000:]

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
        so = list(dest.rglob(f"flagged_obj{ext_suffix}"))
        assert so, "extension module was not built"

        # No <obj>_reset symbol survives into a flagged artifact. `writerx`
        # is the unflagged control and keeps its symbol — checked below.
        for name in ("flagged_obj", "sinkx", "vm"):
            built = next(dest.rglob(f"{name}{ext_suffix}"))
            nm = subprocess.run(
                ["nm", "-D", "--defined-only", str(built)],
                capture_output=True,
                text=True,
            )
            assert "_reset" not in nm.stdout, built

        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(dest / 'src')!r})\n"
            "from e2e import FlaggedObj, Sinkx, Writerx\n"
            "from e2e.vm import Viewed, Alt\n"
            # Alt's create_fn is an IMPLEMENT stub returning NULL, so it
            # cannot be instantiated — the type object is the check there.
            "for cls in (FlaggedObj, Sinkx, Viewed, Alt):\n"
            "    assert not hasattr(cls, 'reset'), cls\n"
            "for cls in (FlaggedObj, Sinkx, Viewed):\n"
            "    assert not hasattr(cls(), 'reset'), cls\n"
            # the unflagged control still has it, so the removal is the flag's
            # doing and not a blanket regression
            "assert hasattr(Writerx(), 'reset')\n"
            "print('ok')\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=dest,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr[-3000:]
        assert "ok" in run.stdout

    def test_generated_test_suite_passes(self, tmp_path):
        """`make test` must pass — a generated test that calls the removed
        reset() would fail, and a CTest that links <obj>_reset would not even
        build."""
        dest = tmp_path / "mt"
        new_run(
            "mt", dest, ["writerx"], [("gain", "double", "1.0")], no_reset=True
        )
        object_run(dest, "sinkx", None, no_state=True, no_reset=True)
        build = subprocess.run(
            ["make"], cwd=dest, capture_output=True, text=True
        )
        assert build.returncode == 0, build.stderr[-3000:]
        test = subprocess.run(
            ["make", "test"], cwd=dest, capture_output=True, text=True
        )
        assert test.returncode == 0, (test.stdout + test.stderr)[-3000:]
