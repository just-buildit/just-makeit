"""gh-2175: an ``extra_methods`` row meets the link-check table (gh-1361).

Two defects with one cause, that the table is read from the binding as text:

1. jm renders an ``[[<obj>.extra_methods]]`` row as
   ``(PyCFunction)(void (*) (void))<fn>``, and the scanner read ``void (`` as
   a call. A header with any callback member -- ``void (*cb)(void *)`` --
   supplied the other half, so ``(jm_any_fn)void,`` reached the table and the
   component's C test did not compile (measured on doppler's ``wfm_synth``:
   ``error: expected expression before 'void'``). A C keyword is never a
   function, so the scanner now drops every one in the standard.
2. The row's function lives in the object's ``_extra.c`` hook, which the
   scanner did not read, so a core call moved there left the table and was
   no longer link-checked. The hook is read beside the binding now.

GATE: an object whose header has a callback type and whose extra method's
      hook calls the core gets a table with that call and without ``void``,
      and -- with a toolchain -- its C test builds.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402
from just_makeit import _linkcheck  # noqa: E402

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: The standard's keywords, C99 through C23 (C23 6.4.1), written out here as
#: the oracle rather than read from the scanner: a keyword the scanner's own
#: list forgot would otherwise pass against itself.
STANDARD_KEYWORDS = (
    "auto break case char const continue default do double else enum extern"
    " float for goto if inline int long register restrict return short"
    " signed sizeof static struct switch typedef union unsigned void"
    " volatile while _Bool _Complex _Imaginary"
    " _Alignas _Alignof _Atomic _Generic _Noreturn _Static_assert"
    " _Thread_local"
    " alignas alignof bool constexpr false nullptr static_assert"
    " thread_local true typeof typeof_unqual _BitInt _Decimal32 _Decimal64"
    " _Decimal128"
).split()


class TestAKeywordIsNotAFunction:
    def test_the_issue_repro(self):
        """jm's own row cast, beside a header's callback member."""
        binding = (
            '  { "f", (PyCFunction)(void (*) (void))O_f, METH_NOARGS, NULL },\n'
            "static PyObject *O_g (O *s) { o_step (s->h); Py_RETURN_NONE; }\n"
        )
        header = (
            "typedef struct { void (*free_user)(void *); } o_state_t;\n"
            "void o_step (o_state_t *state);\n"
        )
        assert _linkcheck.bound_symbols(binding, header) == ["o_step"]

    @pytest.mark.parametrize("kw", STANDARD_KEYWORDS)
    def test_no_keyword_reaches_the_table(self, kw):
        """Each keyword before a ``(`` on BOTH sides, which is what it takes
        to reach the table: a cast or condition in the binding, a declarator
        or assertion in the header."""
        binding = f"{kw} (x); o_step (s);"
        header = f"{kw} (y);\nvoid o_step (o_state_t *state);\n"
        assert _linkcheck.bound_symbols(binding, header) == ["o_step"]


def _jm(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}:\n{r.stdout}{r.stderr}"


#: A core function only the hook calls, beside a callback type: both halves
#: of the defect in one header.
_DECLS = (
    "typedef void (*xm_o_cb_t)(void *);\n"
    "double xm_o_poke(const xm_o_state_t *state);\n\n"
)
_DEFN = (
    "\ndouble\nxm_o_poke(const xm_o_state_t *state)\n{\n"
    "    return state->g;\n}\n"
)
_HOOK = """\
static PyObject *
O_poke(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    return PyFloat_FromDouble(xm_o_poke(((OObject *)self)->handle));
}
"""
_ROW = {
    "name": "poke",
    "fn": "O_poke",
    "flags": "METH_NOARGS",
    "returns": "float",
    "doc": "The gain, through a core call only the hook makes.",
}


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A module object ``o`` with a callback type in its header and an
    extra method whose hook calls ``xm_o_poke``, applied."""
    root = tmp_path_factory.mktemp("gh2175")
    _jm("new", "xm", cwd=root)
    p = root / "xm"
    _jm("module", "m", cwd=p)
    _jm("object", "o", "--module", "m", "--state", "g:double:1.5", cwd=p)

    header = INC.core_h(p, "o")
    text = header.read_text(encoding="utf-8")
    anchor = "#ifdef __cplusplus\n}\n#endif"
    assert text.count(anchor) == 1, "the header's closing block moved"
    header.write_text(text.replace(anchor, _DECLS + anchor), encoding="utf-8")
    core = p / "native" / "src" / "o" / "o_core.c"
    core.write_text(core.read_text(encoding="utf-8") + _DEFN, encoding="utf-8")
    (p / "native" / "src" / "m" / "m_ext_o_extra.c").write_text(
        _HOOK, encoding="utf-8"
    )
    cfg = C.load(p)
    C.set_extra_methods(cfg, "o", [_ROW])
    C.save(p, cfg)
    _jm("apply", cwd=p)
    return p


def _table(root: Path) -> "list[str]":
    text = _linkcheck.symbols_file(root, "o").read_text(encoding="utf-8")
    return re.findall(r"\(jm_any_fn\)(\w+),", text)


class TestTheTable:
    def test_the_row_cast_is_rendered(self, project):
        """The premise: jm's row puts ``void (`` in the binding, and the
        header has a callback type -- else the next test proves nothing."""
        frag = (project / "native" / "src" / "m" / "m_ext_o.c").read_text()
        assert re.search(r"\(void \(\*\) ?\(void\)\)O_poke", frag), frag
        assert "void (*xm_o_cb_t)" in INC.core_h(project, "o").read_text()

    def test_no_void(self, project):
        assert "void" not in _table(project), _table(project)

    def test_the_hook_call_is_link_checked(self, project):
        assert "xm_o_poke" in _table(project), _table(project)

    def test_status_agrees(self, project):
        """``status`` replays ``apply`` on a copy: the same bytes there."""
        _jm("status", "--check", cwd=project)


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
def test_the_c_test_builds(project, tmp_path):
    """The artifact: the table compiles and every entry links."""
    bdir = tmp_path / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(project), "-B", str(bdir)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert cfg.returncode == 0, cfg.stdout[-2000:] + cfg.stderr[-2000:]
    r = subprocess.run(
        ["cmake", "--build", str(bdir), "--target", "test_o_core"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
