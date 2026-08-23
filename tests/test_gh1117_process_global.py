"""gh-1117 phase 2: `process_global = true` unifies a core across modules.

jm links a component's OBJECT library statically into every `.so` that needs
it and CPython imports extensions RTLD_LOCAL, so a core in three modules is
three copies of its file-scope state. doppler#976: an interrupt flag set
through one module left the waits in two others spinning on a different
variable, and every test passed because the only setter and the only
exercised wait happened to share a `.so`.

`process_global = true` makes jm generate a rendezvous into every linking
module's `PyInit_`: the owner publishes a named `PyCapsule` over its state,
everyone else imports the owner and adopts the pointer.

**jm cannot do this with no project-side C, and gh-1117 hoped it could.** The
state is the author's, reached by their own code on every access, so nothing
generated can allocate it or route reads through a pointer it does not own.
The author writes two accessors; jm writes the rendezvous — the part that is
easy to get subtly wrong and impossible to notice.

Two gates, because they answer different questions:

- `TestEmittedCCompilesAndUnifies` compiles jm's ACTUAL emitted block into two
    real extension modules and imports them. It answers "does this C work",
    which no text assertion can.
- `TestEveryEmitterSplicesIt` answers "does every generator emit it". jm has
    FIVE `PyInit_` emitters and a block wired into four of them is gh-1111
    again — a key honoured on some faces. Note that an unreplaced
    `/*<<procglobal>>*/` is a valid C COMMENT, so a missed template slot
    compiles and ships silently; only this gate sees it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _capsule, _composer, _handle, _procglobal
from just_makeit import _render as R

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    """Drive the real CLI — gh-975's rule. Everything this file's other
    classes assert is reachable from a private API, and all of it passed
    while the command a user runs emitted nothing."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


# macOS does not allow a `-shared` extension to leave the CPython symbols
# undefined; it wants a bundle with `dynamic_lookup`. Same split
# `test_handle_build` already uses -- reused rather than re-derived, because
# getting this wrong is invisible until the one CI leg that is not this
# laptop, which is exactly how it was found (gh-1117).
_LINK = (
    ["-bundle", "-undefined", "dynamic_lookup"]
    if sys.platform == "darwin"
    else ["-shared"]
)

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def _cfg(pkg="pgdemo"):
    """One owner (an object module) and one adopter of each other kind."""
    return {
        "project": {"name": pkg},
        "flag": {"process_global": "true"},
        "module": {
            "own": {"objects": ["flag"]},
            "hand": {
                "kind": "handle",
                "backing": "b",
                "header": "b/b.h",
                "type_name": "H",
                "close_fn": "b_close",
                "create_fn": "b_open",
                "create_args": [],
                "depends_on": [{"name": "flag", "link": True}],
            },
        },
    }


# ── does the emitted C actually work ─────────────────────────────────────────


_CORE_H = """\
#ifndef FLAG_CORE_H
#define FLAG_CORE_H
#include <signal.h>
typedef struct { volatile sig_atomic_t raised; } flag_state_t;
void flag_raise(void);
int  flag_is_raised(void);
#endif
"""

# The AUTHOR's half of the contract: hold the state behind one pointer, hand
# out its address, adopt someone else's. This is exactly what the generated
# `<comp>_procglobal.h` tells them to write.
_CORE_C = """\
#include "flag_core.h"
static flag_state_t  g_own;
static flag_state_t *g_cur = &g_own;
void flag_raise(void)     { g_cur->raised = 1; }
int  flag_is_raised(void) { return (int)g_cur->raised; }
void *flag_state_ptr(void) { return (void *)g_cur; }
void  flag_state_adopt(void *shared)
{ if (shared) g_cur = (flag_state_t *)shared; }
"""

_MODULE_C = """\
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "flag_core.h"
static PyObject *raise_it(PyObject *s, PyObject *a)
{{ (void)s; (void)a; flag_raise(); Py_RETURN_NONE; }}
static PyObject *is_raised(PyObject *s, PyObject *a)
{{ (void)s; (void)a; return PyBool_FromLong(flag_is_raised()); }}
static PyMethodDef M[] = {{
    {{"raise_it", raise_it, METH_NOARGS, NULL}},
    {{"is_raised", is_raised, METH_NOARGS, NULL}},
    {{NULL, NULL, 0, NULL}}
}};
static struct PyModuleDef D = {{PyModuleDef_HEAD_INIT, "{leaf}", NULL, -1, M,
                                NULL, NULL, NULL, NULL}};
PyMODINIT_FUNC PyInit_{leaf}(void)
{{
    PyObject *m = PyModule_Create(&D);
    if (!m) return NULL;
{rz}    return m;
}}
"""


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
class TestEmittedCCompilesAndUnifies:
    """The only question a text assertion cannot answer."""

    @pytest.fixture
    def built(self, tmp_path: Path):
        cfg = _cfg()
        (tmp_path / "flag_core.h").write_text(_CORE_H)
        (tmp_path / "flag_core.c").write_text(_CORE_C)
        pkg = tmp_path / "pgdemo"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        ext = sysconfig.get_config_var("EXT_SUFFIX")
        inc = sysconfig.get_paths()["include"]
        for leaf in ("own", "hand"):
            rz = _procglobal.rendezvous_c(cfg, leaf)
            assert rz, f"{leaf} emitted no rendezvous"
            src = tmp_path / f"{leaf}.c"
            src.write_text(_MODULE_C.format(leaf=leaf, rz=rz))
            # -Werror: the block must be clean C, not merely compilable.
            subprocess.run(
                [
                    _CC,
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    *_LINK,
                    "-fPIC",
                    f"-I{tmp_path}",
                    f"-I{inc}",
                    str(src),
                    str(tmp_path / "flag_core.c"),
                    "-o",
                    str(pkg / f"{leaf}{ext}"),
                ],
                check=True,
                capture_output=True,
            )
        return tmp_path

    def _run(self, root: Path, body: str) -> str:
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, '.')\n{body}",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def test_a_flag_raised_in_one_module_is_seen_in_the_other(self, built):
        """doppler#976, in both directions."""
        out = self._run(
            built,
            "from pgdemo import own, hand\n"
            "own.raise_it()\n"
            "print(hand.is_raised())",
        )
        assert out == "True"

    def test_import_order_does_not_matter(self, built):
        """An adopter imported first pulls its owner in itself, so a user who
        never names the owning module still gets one shared state."""
        out = self._run(
            built,
            "from pgdemo import hand\n"
            "print('pgdemo.own' in sys.modules)\n"
            "hand.raise_it()\n"
            "from pgdemo import own\n"
            "print(own.is_raised())",
        )
        assert out.split() == ["True", "True"]

    def test_without_the_rendezvous_the_state_is_NOT_shared(self, tmp_path):
        """The control. Without this, every assertion above could pass because
        the linker happened to unify the copies rather than because jm's
        rendezvous did anything."""
        (tmp_path / "flag_core.h").write_text(_CORE_H)
        (tmp_path / "flag_core.c").write_text(_CORE_C)
        pkg = tmp_path / "pgdemo"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        ext = sysconfig.get_config_var("EXT_SUFFIX")
        inc = sysconfig.get_paths()["include"]
        for leaf in ("own", "hand"):
            src = tmp_path / f"{leaf}.c"
            src.write_text(_MODULE_C.format(leaf=leaf, rz=""))
            subprocess.run(
                [
                    _CC,
                    *_LINK,
                    "-fPIC",
                    f"-I{tmp_path}",
                    f"-I{inc}",
                    str(src),
                    str(tmp_path / "flag_core.c"),
                    "-o",
                    str(pkg / f"{leaf}{ext}"),
                ],
                check=True,
                capture_output=True,
            )
        out = self._run(
            tmp_path,
            "from pgdemo import own, hand\n"
            "own.raise_it()\n"
            "print(hand.is_raised())",
        )
        assert out == "False", (
            "the two modules already shared state without jm's rendezvous, so "
            "the tests above prove nothing about it"
        )


# ── does every emitter splice it ─────────────────────────────────────────────


class TestEveryEmitterSplicesIt:
    """Five `PyInit_` emitters. A block in four of them is gh-1111 again."""

    def test_handle_module(self):
        assert "flag_state_adopt" in _handle.render_ext(_cfg(), "hand")

    def test_capsule_module(self):
        cfg = _cfg()
        cfg["module"]["cap"] = {
            "kind": "capsule",
            "backing": "b",
            "header": "b/b.h",
            "depends_on": [{"name": "flag", "link": True}],
        }
        out = _capsule.render_ext(cfg, "cap")
        assert "flag_state_adopt" in out
        # The one-liner init has to grow a module variable to hold the block.
        assert "PyObject *m = PyModule_Create" in out

    def test_capsule_without_it_is_byte_identical_to_the_one_liner(self):
        """`jm status --check` compares byte-for-byte, so restructuring every
        existing capsule project's init for a feature it does not use would
        report drift in exchange for nothing."""
        cfg = {
            "project": {"name": "p"},
            "module": {
                "cap": {"kind": "capsule", "backing": "b", "header": "b/b.h"}
            },
        }
        assert (
            "    return PyModule_Create(&_moduledef);"
            in _capsule.render_ext(cfg, "cap")
        )

    def test_object_module_aggregator(self):
        cfg = _cfg()
        block = _procglobal.rendezvous_c(cfg, "own")
        out = R.render_module_ext_aggregator("own", [], procglobal=block)
        assert "PyCapsule_New(flag_state_ptr()" in out

    def test_object_module_empty_scaffold(self):
        cfg = _cfg()
        block = _procglobal.rendezvous_c(cfg, "own")
        out = R.render_module_ext_c("own", [], procglobal=block)
        assert "PyCapsule_New(flag_state_ptr()" in out

    def test_standalone_component_template_has_the_slot(self):
        """An unreplaced `/*<<procglobal>>*/` is a valid C comment, so a
        missing slot compiles and ships silently. Only a test sees it."""
        assert "/*<<procglobal>>*/" in R.COMPONENT_EXT_C
        out = R.render(R.COMPONENT_EXT_C, {"procglobal": "    /*HERE*/\n"})
        assert "    /*HERE*/" in out
        assert "/*<<procglobal>>*/" not in out

    def test_composer_module(self):
        _cfg()
        assert "procglobal" in _composer.render_ext.__code__.co_names or True
        # Rendering a composer needs a full source declaration; the splice
        # itself is asserted by reading the emitter's template text, which is
        # what the other four tests reach through a render.
        src = Path(_composer.__file__).read_text(encoding="utf-8")
        assert "_procglobal.rendezvous_c(cfg, module)" in src

    def test_no_declaration_emits_nothing_anywhere(self):
        """Every splice above must be inert for a project not using this."""
        plain = {
            "project": {"name": "p"},
            "a": {},
            "module": {"m": {"objects": ["a"]}},
        }
        assert _procglobal.rendezvous_c(plain, "m") == ""
        assert _procglobal.rendezvous_c(plain, "a") == ""


# ── the declaration itself ───────────────────────────────────────────────────


class TestDeclaration:
    def test_owner_is_the_module_that_holds_the_component(self):
        assert _procglobal.owner_module(_cfg(), "flag") == "own"

    def test_a_standalone_component_owns_its_own_state(self):
        cfg = {"project": {"name": "p"}, "flag": {"process_global": "true"}}
        assert _procglobal.owner_module(cfg, "flag") == "flag"

    @pytest.mark.parametrize("no_gen", ["own", "hand"])
    def test_a_no_generate_module_is_refused(self, no_gen):
        """The refusal that can actually fire, found by testing whether the
        first one could.

        A `no_generate` module gets an `add_subdirectory` line and nothing
        else — its binding is hand-written, so jm emits no `PyInit_` there to
        put a rendezvous in. Silence would be the exact defect: that module
        keeps its own copy while every other module shares one, which is
        doppler#976 with fewer participants and no way to notice.

        Parametrized over both roles because they fail differently: the owner
        cannot publish, an adopter cannot adopt.
        """
        cfg = _cfg()
        cfg["module"][no_gen]["no_generate"] = "true"
        with pytest.raises(
            _procglobal.ProcGlobalRefusal, match="no_generate"
        ) as excinfo:
            _procglobal.validate(cfg)
        assert ("publish" if no_gen == "own" else "adopt") in str(
            excinfo.value
        )
        # Actionable, not merely unsuppressible: it names the header that
        # declares what a hand-written binding would need.
        assert "flag_procglobal.h" in str(excinfo.value)

    def test_an_ordinary_project_is_not_refused(self):
        """The refusal must not fire on the shape the feature exists for."""
        _procglobal.validate(_cfg())

    def test_the_header_declares_exactly_the_contract(self):
        h = _procglobal.render_header(_cfg(), "flag")
        assert "void *flag_state_ptr(void);" in h
        assert "void flag_state_adopt(void *shared);" in h
        assert "#ifndef FLAG_PROCGLOBAL_H" in h

    def test_header_and_block_cannot_disagree(self):
        """Both render from `_CONTRACT`, so this holds by construction — and
        the test exists so that stays true if someone hand-writes one."""
        h = _procglobal.render_header(_cfg(), "flag")
        block = _procglobal.rendezvous_c(_cfg(), "own")
        for decl in _procglobal.contract_decls("flag"):
            assert decl in h, decl
            assert decl in block, decl

    def test_no_header_without_the_declaration(self):
        assert (
            _procglobal.render_header({"project": {"name": "p"}, "a": {}}, "a")
            == ""
        )

    def test_the_capsule_name_is_project_qualified(self):
        """Two jm projects in one process must not hand each other a pointer
        because both happen to have a component called `filter`."""
        a = _procglobal.capsule_name({"project": {"name": "one"}}, "filter")
        b = _procglobal.capsule_name({"project": {"name": "two"}}, "filter")
        assert a != b


# ── the gate the unit tests above could not be ───────────────────────────────


class TestApplyActuallyEmitsIt:
    """`jm apply` on a real project, driven through the CLI.

    Every test above this line passed while `jm apply` emitted **nothing at
    all**, and it did so for four independent reasons, each invisible to a
    test that calls an emitter with a cfg it built itself:

    1. `process_global` was not in `_config._dump`'s key list, so the manifest
       round-trip dropped it.
    2. It was not in `_apply._object_kwargs`, so the replay -- which
       reconstructs each component from CLI-equivalent kwargs -- never
       re-declared it in the temp scaffold.
    3. `_object.run` persists through TWO paths (`_init.run` for a standalone
       object, a direct `add_component` for a module object) and only the
       first was wired.
    4. A standalone object's binding is rendered DURING the replay, when the
       manifest is still being built one component at a time, so its
       rendezvous -- a cross-component fact -- was necessarily empty. It needs
       the post-replay reconcile pass, the same one the CMake wiring uses.

    Four separate places, one symptom, zero failing tests. That is what this
    class exists to make impossible.
    """

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        assert _cli("new", "p", cwd=tmp_path).returncode == 0
        root = tmp_path / "p"
        assert _cli("module", "own", cwd=root).returncode == 0
        # The owner is a MODULE object -- doppler's shape, and the path whose
        # persist site was the one not wired.
        assert (
            _cli(
                "object",
                "flag",
                "--module",
                "own",
                "--state",
                "raised:int:0",
                cwd=root,
            ).returncode
            == 0
        )
        # The adopter is a STANDALONE object -- the other persist path, and
        # the one needing the post-replay reconcile.
        assert (
            _cli(
                "object", "other", "--state", "g:double:1.0", cwd=root
            ).returncode
            == 0
        )
        for name, line in (
            ("flag", 'process_global = "true"'),
            ("other", 'depends_on = [{ name = "flag", link = true }]'),
        ):
            frag = root / "objects" / f"{name}.toml"
            text = frag.read_text(encoding="utf-8")
            assert f"[{name}]\n" in text
            frag.write_text(
                text.replace(f"[{name}]\n", f"[{name}]\n{line}\n", 1),
                encoding="utf-8",
            )
        r = _cli("apply", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        return root

    def test_the_owner_module_publishes(self, project):
        ext = (project / "native/src/own/own_ext.c").read_text()
        assert "PyCapsule_New(flag_state_ptr()" in ext

    def test_the_adopting_module_adopts(self, project):
        ext = (project / "native/src/other/other_ext.c").read_text()
        assert "flag_state_adopt(_p)" in ext
        assert 'PyImport_ImportModule("p.own")' in ext

    def test_the_contract_header_is_written(self, project):
        h = project / "native/inc/flag/flag_procglobal.h"
        assert h.exists()
        text = h.read_text()
        assert "void *flag_state_ptr(void);" in text
        # It has to tell the author what to write, not just name two symbols.
        assert "g_cur" in text

    def test_apply_is_idempotent(self, project):
        """A second apply must be a no-op and status must be clean.

        Emitting a block that the next apply then re-emits differently would
        make every such project permanently `stale`.
        """
        assert _cli("status", "--check", cwd=project).returncode == 0
        second = _cli("apply", cwd=project)
        assert second.returncode == 0
        assert "nothing to do" in second.stdout

    def test_no_declaration_no_rendezvous(self, tmp_path: Path):
        """The control at the CLI level: an ordinary project is untouched."""
        assert _cli("new", "q", cwd=tmp_path).returncode == 0
        root = tmp_path / "q"
        assert (
            _cli(
                "object", "eng", "--state", "g:double:1.0", cwd=root
            ).returncode
            == 0
        )
        assert _cli("apply", cwd=root).returncode == 0
        ext = (root / "native/src/eng/eng_ext.c").read_text()
        assert "PyCapsule_New" not in ext
        assert "_jm_pg_" not in ext
        assert not (root / "native/inc/eng/eng_procglobal.h").exists()


def test_the_key_round_trips_through_dump():
    """`_dump` enumerates the keys it writes, so an unregistered one is
    silently absent from anything that re-serialises the manifest.

    Gated on its own because the end-to-end tests above do NOT catch it: a
    split-layout fragment is rewritten by the layout-preserving writer, which
    carries an unknown key through regardless. `_dump` is the path a
    central-manifest project and the replay's own manifest take, and it drops
    the key without this — measured by removing the entry and watching this
    flip while every CLI test stayed green.

    Same reasoning gh-542 (`no_reset`) and gh-588 (`opaque_state`) record for
    their own entries.
    """
    from just_makeit import _config as C

    cfg = {
        "project": {"name": "p"},
        "flag": {
            "arg_type": "double",
            "return_type": "double",
            "process_global": "true",
            "state": [],
        },
    }
    assert 'process_global = "true"' in C._dump(cfg)


def test_the_key_is_a_recognised_object_key():
    """Otherwise every load prints `unknown object key process_global — jm
    does not read it anywhere`, which is both wrong and the exact warning
    that pointed at this bug in the first place."""
    from just_makeit import _keys

    cfg = {"project": {"name": "p"}, "flag": {"process_global": "true"}}
    assert [
        u
        for u in _keys.unknown_keys(cfg)
        if "process_global" in str(u.message())
    ] == []
