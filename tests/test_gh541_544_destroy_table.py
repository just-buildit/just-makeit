"""Integration tests for the ``[<obj>.destroy]`` table (gh-541 + gh-544).

gh-541 is the data-integrity half: a destructor that is part of the *work* —
a writer patching a header field and appending trailing metadata after the last
sample — had no way to report failure, and the generated ``__exit__`` ended in
an unconditional ``Py_RETURN_NONE``. A ``with`` block therefore produced a
corrupt artifact in silence.

gh-544 is the naming half: the Python method was hardcoded ``destroy()``, so a
type whose established API is ``close()`` hand-wrote it.

The load-bearing test is `TestDestroyEndToEnd`: it builds a component whose C
destructor genuinely fails and asserts the full propagation matrix at runtime.
`TestByteIdenticalWhenUndeclared` is its guard rail — a component with no table
must render exactly as it did before the feature existed, because these slots
were cut into templates every project already uses.
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
from just_makeit._context._destroy import (
    destroy_py_names,
    make_destroy_ctx,
    validate_destroy_spec,
)
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._script import run as script_run

_MSG = (
    "failed to finalise the capture: the trailing header patch or "
    "extended header was not written"
)

_TABLE = {
    "name": "close",
    "aliases": ["destroy"],
    "returns": "int",
    "error": "OSError",
    "error_message": _MSG,
}


def _ext_c(project, obj):
    return (project / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


def _fn(text, name):
    """The full text of the C function *name* defined in *text*."""
    start = text.index(f"\n{name}(")
    return text[start : text.index("\n}\n", start) + 3]


def _declare(project, obj, table):
    """Write a ``[<obj>.destroy]`` table into whichever file owns *obj*.

    A scaffolded project uses the split layout, so the object's section lives
    in ``objects/<obj>.toml``, not the manifest — appending to the wrong one
    is a duplicate-section error rather than a declaration.
    """
    cfg = C.load(project)
    C.set_destroy_spec(cfg, obj, table)
    C.save(project, cfg)


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "cap"
    new_run("cap", dest, ["wfm_writer"], [("fail", "int", "0")])
    return dest


# ── validation (gh-514's shape, applied to the destructor) ──────────────────


class TestValidation:
    """Every inert or mistyped key fails loudly, at generation time."""

    def test_empty_is_valid(self):
        validate_destroy_spec("w", {})
        validate_destroy_spec("w", None)

    def test_unknown_key_names_the_component(self):
        with pytest.raises(ValueError) as e:
            validate_destroy_spec("w", {"nmae": "close"})
        assert "object 'w'" in str(e.value)
        assert "nmae" in str(e.value)

    def test_bad_returns_is_rejected(self):
        with pytest.raises(ValueError, match="returns 'bool'"):
            validate_destroy_spec("w", {"returns": "bool"})

    def test_unknown_error_lists_the_supported_names(self):
        with pytest.raises(ValueError) as e:
            validate_destroy_spec("w", {"returns": "int", "error": "IOErrror"})
        assert "IOErrror" in str(e.value)
        assert "OSError" in str(e.value)  # the supported list is shown

    def test_error_without_int_returns_is_rejected(self):
        # This repo has shipped four bugs of exactly this shape: a key that is
        # silently inert. An error with nothing to test is one of them.
        with pytest.raises(ValueError, match="require returns"):
            validate_destroy_spec("w", {"error": "OSError"})
        with pytest.raises(ValueError, match="require returns"):
            validate_destroy_spec("w", {"error_message": "boom"})

    def test_non_identifier_name_or_alias_is_rejected(self):
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            validate_destroy_spec("w", {"name": "close it"})
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            validate_destroy_spec("w", {"aliases": ["2close"]})

    def test_generation_refuses_an_invalid_table(self, project, capsys):
        # apply turns the ValueError into a clean CLI diagnostic — the point
        # is that it never reaches the user's compiler as an undeclared
        # PyExc_Nope identifier.
        _declare(project, "wfm_writer", {"returns": "int", "error": "Nope"})
        with pytest.raises(SystemExit):
            apply_run(project)
        assert "Nope" in capsys.readouterr().err


# ── naming and aliases (gh-544) ─────────────────────────────────────────────


class TestNamesAndAliases:
    def test_default_is_destroy(self):
        assert destroy_py_names({}) == ["destroy"]

    def test_alias_equal_to_name_is_not_emitted_twice(self):
        # A duplicate key in a PyMethodDef table is a real bug: the second
        # entry is unreachable, and CPython does not diagnose it.
        assert destroy_py_names(
            {"name": "close", "aliases": ["close", "destroy"]}
        ) == ["close", "destroy"]

    def test_pymethoddef_has_one_row_per_unique_name(self):
        pmd = make_destroy_ctx(
            "w", "WObj", {"name": "close", "aliases": ["destroy", "close"]}, []
        )["destroy_pymethoddef"]
        assert pmd.count('{"close",') == 1
        assert pmd.count('{"destroy",') == 1
        # Both rows point at the SAME C function — an alias is a binding, not
        # a second implementation.
        assert pmd.count("(PyCFunction)WObj_destroy") == 2

    def test_generated_ext_c_binds_both_names(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        ext = _ext_c(project, "wfm_writer")
        assert '{"close",  (PyCFunction)WfmWriter_destroy' in ext
        assert '{"destroy",  (PyCFunction)WfmWriter_destroy' in ext
        # Exactly one definition of the wrapper, however many names bind it.
        assert ext.count("\nWfmWriter_destroy(WfmWriterObject *self") == 1


# ── the propagation matrix (gh-541) ─────────────────────────────────────────


class TestPropagationMatrix:
    """close() and __exit__ report; tp_dealloc must swallow."""

    @pytest.fixture()
    def ext(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        return _ext_c(project, "wfm_writer")

    def test_destroy_method_raises(self, ext):
        body = _fn(ext, "WfmWriter_destroy")
        assert "int rc = wfm_writer_destroy(self->handle);" in body
        assert "PyErr_SetString(PyExc_OSError," in body
        assert "return NULL;" in body

    def test_exit_raises_the_same_way(self, ext):
        # gh-541's severe case: a fix that only makes close() fallible leaves
        # the with-block silently corrupting data.
        body = _fn(ext, "WfmWriter_exit")
        assert "PyErr_SetString(PyExc_OSError," in body
        assert "return NULL;" in body

    def test_dealloc_swallows_and_says_why(self, ext):
        body = _fn(ext, "WfmWriter_dealloc")
        assert "(void)wfm_writer_destroy(self->handle);" in body
        assert "PyErr_" not in body
        # The reporter asked for this to be explicit rather than folklore.
        assert "gh-541" in body and "no exception context" in body

    def test_handle_is_cleared_before_reporting(self, ext):
        # Ordering is the whole of the idempotence guarantee: clear first, so
        # a second call sees NULL and no-ops instead of double-freeing.
        body = _fn(ext, "WfmWriter_destroy")
        assert body.index("self->handle = NULL;") < body.index("if (rc != 0)")


# ── the sacred signature ────────────────────────────────────────────────────


class TestSacredSignature:
    def test_fresh_scaffold_declares_int(self, tmp_path):
        # The scaffold path: the templates carry the signature slot, so a
        # component generated with the table already in hand needs no patch.
        dest = tmp_path / "cap"
        new_run("cap", dest, [], [])
        object_run(
            dest,
            "wfm_writer",
            None,
            state_vars=[("fail", "int", "0")],
            destroy=_TABLE,
        )
        h = (
            dest / "native" / "inc" / "wfm_writer" / "wfm_writer_core.h"
        ).read_text(encoding="utf-8")
        c = (
            dest / "native" / "src" / "wfm_writer" / "wfm_writer_core.c"
        ).read_text(encoding="utf-8")
        assert "int wfm_writer_destroy(wfm_writer_state_t *state);" in h
        assert "@return 0 on success, non-zero on failure." in h
        assert "int\nwfm_writer_destroy(wfm_writer_state_t *state)" in c
        assert "return 0;" in _fn(c, "wfm_writer_destroy")
        # ...and the table is persisted, so a later regeneration keeps it.
        assert C.destroy_spec(C.load(dest), "wfm_writer") == _TABLE

    def test_already_scaffolded_component_is_patched_in_place(self, project):
        # _core.c is sacred and _core.h only ever gains missing declarations,
        # so without an explicit patch the first build after declaring the
        # table fails with conflicting types.
        apply_run(project)
        h_path = (
            project / "native" / "inc" / "wfm_writer" / "wfm_writer_core.h"
        )
        assert "void wfm_writer_destroy" in h_path.read_text(encoding="utf-8")

        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)

        h = h_path.read_text(encoding="utf-8")
        c = (
            project / "native" / "src" / "wfm_writer" / "wfm_writer_core.c"
        ).read_text(encoding="utf-8")
        assert "int wfm_writer_destroy" in h
        assert "void wfm_writer_destroy" not in h
        assert "int\nwfm_writer_destroy" in c
        assert "return 0;" in _fn(c, "wfm_writer_destroy")
        # Known limit: the retrofit is a signature patch, not a re-render, so
        # the sacred header's Doxygen block keeps its original @param-only
        # text. A fresh scaffold gets the @return line.

    def test_patch_does_not_touch_a_body_that_already_returns(self, project):
        apply_run(project)
        c_path = (
            project / "native" / "src" / "wfm_writer" / "wfm_writer_core.c"
        )
        text = c_path.read_text(encoding="utf-8")
        text = text.replace(
            "wfm_writer_destroy(wfm_writer_state_t *state)\n{\n"
            "    free(state);\n}",
            "wfm_writer_destroy(wfm_writer_state_t *state)\n{\n"
            "    int rc = state->fail;\n    free(state);\n    return rc;\n}",
        )
        c_path.write_text(text, encoding="utf-8")

        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)

        body = _fn(c_path.read_text(encoding="utf-8"), "wfm_writer_destroy")
        assert "return rc;" in body
        assert "return 0;" not in body


# ── the type stub, from BOTH generators ─────────────────────────────────────


class TestStubs:
    def test_standalone_stub_declares_name_and_aliases(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        pyi = (project / "src" / "cap" / "wfm_writer.pyi").read_text(
            encoding="utf-8"
        )
        import ast

        ast.parse(pyi)  # a stub that does not parse helps nobody
        assert "def close(self) -> None:" in pyi
        assert "def destroy(self) -> None:" in pyi
        assert "OSError" in pyi

    def test_module_aggregated_stub_agrees(self, tmp_path):
        # There are two stub generators and this repo has repeatedly fixed
        # only one of them. Assert they agree.
        from just_makeit._module import run as module_run
        from just_makeit._stubs import make_module_pyi

        dest = tmp_path / "modp"
        new_run("modp", dest, [], [])
        module_run(dest, "io")
        object_run(dest, "rdr", "io", state_vars=[("fail", "int", "0")])
        _declare(dest, "rdr", _TABLE)
        cfg = C.load(dest)
        pyi = make_module_pyi(cfg, "io", root=dest)
        assert "def close(self) -> None:" in pyi
        assert "def destroy(self) -> None:" in pyi

    def test_undeclared_stub_keeps_the_default_shape(self, project):
        # gh-647 replaced the one-line literal with the shared _gluedoc prose,
        # so the text this once pinned is gone by design. What gh-541 actually
        # guards is the *shape* an undeclared table renders: the method is
        # named `destroy`, takes no arguments, returns None, and no alias
        # appears. That is asserted here; the prose is _gluedoc's to own.
        apply_run(project)
        pyi = (project / "src" / "cap" / "wfm_writer.pyi").read_text(
            encoding="utf-8"
        )
        assert "    def destroy(self) -> None:\n" in pyi
        assert "Release the underlying C resources immediately." in pyi
        assert "def close" not in pyi


# ── manifest round-trip and replay ──────────────────────────────────────────


class TestRoundTrip:
    def test_table_survives_save_and_load(self, project):
        _declare(project, "wfm_writer", _TABLE)
        assert C.destroy_spec(C.load(project), "wfm_writer") == _TABLE

    def test_accessors(self, project):
        _declare(project, "wfm_writer", _TABLE)
        cfg = C.load(project)
        assert C.destroy_name(cfg, "wfm_writer") == "close"
        assert C.destroy_aliases(cfg, "wfm_writer") == ["destroy"]
        assert C.destroy_returns_int(cfg, "wfm_writer") is True
        assert C.destroy_name({"x": {}}, "x") == "destroy"
        assert C.destroy_returns_int({"x": {}}, "x") is False

    def test_apply_is_idempotent(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        ext = _ext_c(project, "wfm_writer")
        apply_run(project)
        assert _ext_c(project, "wfm_writer") == ext
        # And the manifest still round-trips unchanged.
        assert C.destroy_spec(C.load(project), "wfm_writer") == _TABLE

    def test_apply_rebuilds_the_glue_from_a_deleted_fragment(self, project):
        # jm's sanctioned migration mechanic. A manifest key apply silently
        # drops was gh-519's second defect, so prove the replay carries it.
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        ext_path = (
            project / "native" / "src" / "wfm_writer" / "wfm_writer_ext.c"
        )
        expected = ext_path.read_text(encoding="utf-8")
        ext_path.unlink()
        apply_run(project)
        assert ext_path.read_text(encoding="utf-8") == expected

    def test_script_flags_the_manifest_only_table(self, project, capsys):
        # No CLI flag exists (see _config's note: five interacting keys is not
        # a CLI shape, and `package` set the manifest-only precedent), so the
        # reconstructed script must tell the reader rather than emit a command
        # that would silently rebuild a void destroy().
        _declare(project, "wfm_writer", _TABLE)
        script_run(project)
        out = capsys.readouterr().out
        assert "[wfm_writer.destroy] has no CLI flag" in out


# ── the guard rail ──────────────────────────────────────────────────────────


class TestByteIdenticalWhenUndeclared:
    """No table -> the pre-gh-541 text, to the byte.

    The four slots were cut into templates every existing project renders, so
    this is the test that makes the change safe rather than merely correct.
    """

    def test_ext_c_text(self, project):
        apply_run(project)
        ext = _ext_c(project, "wfm_writer")
        assert (
            "WfmWriter_dealloc(WfmWriterObject *self)\n"
            "{\n"
            "    if (self->handle)\n"
            "        wfm_writer_destroy(self->handle);\n"
        ) in ext
        assert (
            "WfmWriter_destroy(WfmWriterObject *self, "
            "PyObject *Py_UNUSED(ignored))\n"
            "{\n"
            "    if (self->handle) {\n"
            "        wfm_writer_destroy(self->handle);\n"
            "        self->handle = NULL;\n"
            "    }\n"
            "    Py_RETURN_NONE;\n"
            "}\n"
        ) in ext
        assert (
            "    (void)args;\n"
            "    if (self->handle) {\n"
            "        wfm_writer_destroy(self->handle);\n"
            "        self->handle = NULL;\n"
            "    }\n"
            "    Py_RETURN_NONE;\n"
            "}\n"
        ) in ext
        # The method-table entry: name, dispatch and flags are what gh-541
        # must not disturb. The doc string that followed them moved to
        # _gluedoc in gh-647 (and gained the full numpy body on this face),
        # so it is no longer pinned here -- only that the entry still leads
        # straight into __enter__, i.e. nothing was inserted or dropped.
        assert (
            '    {"destroy",  (PyCFunction)WfmWriter_destroy,  METH_NOARGS,\n'
        ) in ext
        assert "Release the underlying C resources immediately." in ext, (
            "teardown entry lost its docstring"
        )
        _tail = ext.split('{"destroy",  (PyCFunction)WfmWriter_destroy,')[1]
        assert _tail.lstrip().startswith("METH_NOARGS,")
        assert '{"__enter__",' in _tail.split("},")[1]

    def test_core_files_stay_void(self, project):
        apply_run(project)
        h = (
            project / "native" / "inc" / "wfm_writer" / "wfm_writer_core.h"
        ).read_text(encoding="utf-8")
        c = (
            project / "native" / "src" / "wfm_writer" / "wfm_writer_core.c"
        ).read_text(encoding="utf-8")
        assert "void wfm_writer_destroy(wfm_writer_state_t *state);" in h
        assert "@return 0 on success" not in h
        assert (
            "void\nwfm_writer_destroy(wfm_writer_state_t *state)\n{\n"
            "    free(state);\n}\n"
        ) in c

    def test_no_slot_leaks_into_generated_output(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)
        for path in project.rglob("*"):
            if path.suffix not in (".c", ".h", ".pyi", ".py", ".txt"):
                continue
            assert "destroy_" not in re.sub(
                r"[a-z_]*destroy_(impl|fn|stmt|silent)",
                "",
                "".join(m for m in re.findall(r"<<[^>]*>>", path.read_text())),
            ), path


# ── the acceptance test ─────────────────────────────────────────────────────


@pytest.mark.slow
class TestDestroyEndToEnd:
    """Build a component whose destructor genuinely fails, and catch it."""

    def test_full_propagation_matrix_at_runtime(self, project):
        _declare(project, "wfm_writer", _TABLE)
        apply_run(project)

        # The generated destructor always succeeds, so there'd be nothing to
        # observe. Make it fail on demand, the way a disk-full close would.
        core_c = (
            project / "native" / "src" / "wfm_writer" / "wfm_writer_core.c"
        )
        text = core_c.read_text(encoding="utf-8")
        text = text.replace(
            "    free(state);\n    return 0;",
            "    int rc = state ? state->fail : 0;\n"
            "    free(state);\n"
            "    return rc;",
            1,
        )
        core_c.write_text(text, encoding="utf-8")

        build = subprocess.run(
            ["make"], cwd=project, capture_output=True, text=True
        )
        assert build.returncode == 0, build.stderr[-3000:]

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
        so = list(project.rglob(f"wfm_writer{ext_suffix}"))
        assert so, "extension module was not built"

        script = (
            "import gc, sys\n"
            f"sys.path.insert(0, {str(so[0].parent.parent)!r})\n"
            "from cap import WfmWriter\n"
            "MSG = 'failed to finalise the capture'\n"
            # 1. the renamed method raises, with the declared message
            "try:\n"
            "    WfmWriter(1).close()\n"
            "    raise AssertionError('close() did not raise')\n"
            "except OSError as e:\n"
            "    assert MSG in str(e), str(e)\n"
            # 2. gh-541's acceptance case: it must escape a with block
            "ran = []\n"
            "try:\n"
            "    with WfmWriter(1) as w:\n"
            "        ran.append(w.step(1.0))\n"
            "    raise AssertionError('with block swallowed the failure')\n"
            "except OSError as e:\n"
            "    assert ran, 'body did not run'\n"
            "    assert MSG in str(e), str(e)\n"
            # 3. the alias behaves identically
            "try:\n"
            "    WfmWriter(1).destroy()\n"
            "    raise AssertionError('alias did not raise')\n"
            "except OSError:\n"
            "    pass\n"
            # 4. idempotent: a second close is a no-op, not a re-close
            "w = WfmWriter(0)\n"
            "assert w.close() is None\n"
            "assert w.close() is None\n"
            "w = WfmWriter(1)\n"
            "try:\n"
            "    w.close()\n"
            "    raise AssertionError('expected a raise')\n"
            "except OSError:\n"
            "    pass\n"
            "assert w.close() is None, 'second close after a failure raised'\n"
            # 5. tp_dealloc swallows: no crash, no exception, not even an
            #    unraisable one
            "unraisable = []\n"
            "sys.unraisablehook = lambda a: unraisable.append(a)\n"
            "w = WfmWriter(1)\n"
            "del w\n"
            "gc.collect()\n"
            "assert not unraisable, unraisable\n"
            "print('ok')\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr[-3000:]
        assert "ok" in run.stdout
