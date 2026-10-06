"""gh-1997: an ordinary object registers a hand-written method's row.

A module object's ``<mod>_ext_<obj>_extra.c`` could always hold a
hand-written CPython function, but nothing registered it: ``manual_stub``
emits only a ``.pyi`` placeholder, and once the fragment is jm's
(``fragment = "generated"``) its ``PyMethodDef`` table is rendered whole from
the manifest, so a hand-added row is gone on the flip -- which is why
``jm adopt`` refused it with ``only here: fn:<name>``. doppler#1446 has 12
such methods on 4 objects.

``[[<obj>.extra_methods]]`` is gh-1190's composer key, row for row, and goes
through the same emitter (``_extramethods``): jm writes the row, a
prototype above the table, the ``#include`` of the hook and the stub; the
author writes the function, with the signature its ``flags`` imply.

GATE: an object declaring an extra method whose body is in its `_extra.c`
      builds and the method is callable, standalone and in a module.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import pytest
from _compilers import default_cc
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _csym as CSYM  # noqa: E402
from just_makeit import _keys  # noqa: E402

#: Stands for "the core's own reset()", resolved per project.
CORE_RESET = "<core reset>"

_SKIP_BUILD = (
    "cmake not found"
    if shutil.which("cmake") is None
    else "no C compiler found"
    if default_cc() is None
    else None
)


def _jm(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}:\n{r.stdout}{r.stderr}"
    return r.stdout


def _rows(cls: str) -> "list[dict]":
    """The two rows each object declares: both calling conventions a hand
    method most often has, so a prototype for each is compiled."""
    return [
        {
            "name": "twice",
            "fn": f"{cls}_twice",
            "flags": "METH_NOARGS",
            "returns": "float",
            "doc": "Twice the gain.",
        },
        {
            "name": "scaled",
            "fn": f"{cls}_scaled",
            "flags": "METH_VARARGS | METH_KEYWORDS",
            "args": "k: float = 2.0",
            "returns": "float",
            "doc": "The gain times *k*.\n\nA keyword-capable row.",
        },
    ]


def _bodies(cls: str) -> str:
    """The author's C for :func:`_rows`, as it goes in the object's hook."""
    return f"""\
static PyObject *
{cls}_twice(PyObject *self, PyObject *Py_UNUSED(ignored))
{{
    return PyFloat_FromDouble(2.0 * (({cls}Object *)self)->handle->g);
}}

static PyObject *
{cls}_scaled(PyObject *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{"k", NULL}};
    double k = 2.0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|d", kwlist, &k))
        return NULL;
    return PyFloat_FromDouble(k * (({cls}Object *)self)->handle->g);
}}
"""


#: Where each object's hook lives: standalone `<comp>_ext_extra.c`, module
#: `<cname>_ext_<comp>_extra.c`.
SOLO_HOOK = Path("native/src/solo/solo_ext_extra.c")
O_HOOK = Path("native/src/m/m_ext_o_extra.c")
SOLO_EXT = Path("native/src/solo/solo_ext.c")
M_EXT = Path("native/src/m/m_ext.c")
O_FRAG = Path("native/src/m/m_ext_o.c")


def _scaffold(root: Path) -> Path:
    """A standalone `solo` and a module object `o`, each with a `g` field."""
    _jm("new", "xm", cwd=root)
    p = root / "xm"
    _jm("object", "solo", "--state", "g:double:1.5", cwd=p)
    _jm("module", "m", cwd=p)
    _jm("object", "o", "--module", "m", "--state", "g:double:1.5", cwd=p)
    return p


def _declare(p: Path, comp: str, rows: "list[dict]") -> None:
    """Write the rows through the manifest's own writer, then `apply`."""
    cfg = C.load(p)
    C.set_extra_methods(cfg, comp, rows)
    C.save(p, cfg)


def _declared_project(root: Path) -> Path:
    p = _scaffold(root)
    (p / SOLO_HOOK).write_text(_bodies("Solo"), encoding="utf-8")
    (p / O_HOOK).write_text(_bodies("O"), encoding="utf-8")
    _declare(p, "solo", _rows("Solo"))
    _declare(p, "o", _rows("O"))
    _jm("apply", cwd=p)
    return p


@pytest.fixture(scope="module")
def declared(tmp_path_factory) -> Path:
    return _declared_project(tmp_path_factory.mktemp("gh1997"))


def _cmake_build(root: Path) -> None:
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(root / "build"),
            # The interpreter the import below runs under, so the extension
            # is built for its ABI and found by its numpy.
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(root / "build"), "-j", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, f"{cmd}:\n{r.stdout}\n{r.stderr}"


def _python(root: Path, script: str) -> str:
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


# --------------------------------------------------------------------------
# The gate: it builds, and Python can call it.
# --------------------------------------------------------------------------


class TestItBuildsAndIsCallable:
    """The whole feature, through a compiler and an import. Every other
    assertion in this file is about the text that makes this possible."""

    SCRIPT = (
        "from xm import Solo\n"
        "from xm.m import O\n"
        "for cls in (Solo, O):\n"
        "    x = cls(g=1.5)\n"
        "    assert x.twice() == 3.0, x.twice()\n"
        "    assert x.scaled() == 3.0, x.scaled()\n"
        "    assert x.scaled(4.0) == 6.0\n"
        "    assert x.scaled(k=10.0) == 15.0\n"
        "    assert cls.twice.__doc__ == 'Twice the gain.\\n', "
        "cls.twice.__doc__\n"
        "    print(cls.__name__, 'OK')\n"
    )

    def test_standalone_and_module_objects_call_it(self, declared: Path):
        if _SKIP_BUILD:
            pytest.skip(_SKIP_BUILD)
        # Built here, not in a fixture: a compile error is then this test's
        # FAILURE, where a fixture's would be an ERROR beside it.
        _cmake_build(declared)
        out = _python(declared, self.SCRIPT)
        assert "Solo OK" in out and "O OK" in out, out


# --------------------------------------------------------------------------
# What the build depends on, one face at a time.
# --------------------------------------------------------------------------


class TestTheTableCarriesTheRow:
    @pytest.mark.parametrize(
        "rel,fn",
        [(SOLO_EXT, "Solo_twice"), (O_FRAG, "O_twice")],
    )
    def test_the_row_is_rendered(self, declared: Path, rel: Path, fn: str):
        text = (declared / rel).read_text(encoding="utf-8")
        row = f'{{"twice", (PyCFunction)(void (*)(void)){fn},'
        assert row in text, text

    @pytest.mark.parametrize("rel", [SOLO_EXT, O_FRAG])
    def test_rows_keep_their_declared_order(self, declared: Path, rel: Path):
        text = (declared / rel).read_text(encoding="utf-8")
        assert text.index('{"twice"') < text.index('{"scaled"'), text

    def test_the_flags_are_the_declared_ones(self, declared: Path):
        text = (declared / SOLO_EXT).read_text(encoding="utf-8")
        assert 'METH_VARARGS | METH_KEYWORDS, "The gain times' in text, text


class TestThePrototypeAndTheInclude:
    """The table precedes the hook's `#include` (the hook uses the generated
    types), so the row needs a prototype above the table -- and the hook
    must be included whenever a row names a function only it defines."""

    PROTO = "static PyObject *{cls}_twice(PyObject *, PyObject *);"
    KW_PROTO = (
        "static PyObject *{cls}_scaled(PyObject *, PyObject *, PyObject *);"
    )

    def test_standalone_prototypes_precede_the_type(self, declared: Path):
        text = (declared / SOLO_EXT).read_text(encoding="utf-8")
        proto = self.PROTO.format(cls="Solo")
        assert proto in text, text
        assert self.KW_PROTO.format(cls="Solo") in text, text
        assert text.index(proto) < text.index("} SoloObject;"), text
        assert text.index('{"twice"') < text.index(
            '#include "solo_ext_extra.c"'
        ), text

    def test_module_prototypes_precede_the_fragment(self, declared: Path):
        """In the aggregator, not the fragment: a SACRED fragment is never
        rendered whole, so a prototype there would never reach one."""
        agg = (declared / M_EXT).read_text(encoding="utf-8")
        proto = self.PROTO.format(cls="O")
        assert proto in agg, agg
        assert (
            agg.index(proto)
            < agg.index('#include "m_ext_o.c"')
            < agg.index('#include "m_ext_o_extra.c"')
        ), agg
        frag = (declared / O_FRAG).read_text(encoding="utf-8")
        assert proto not in frag, frag

    @pytest.mark.parametrize(
        "comp,ext,include",
        [
            ("solo", SOLO_EXT, '#include "solo_ext_extra.c"'),
            ("o", M_EXT, '#include "m_ext_o_extra.c"'),
        ],
    )
    def test_a_row_includes_the_hook_before_it_exists(
        self, tmp_path: Path, comp: str, ext: Path, include: str
    ):
        """gh-1516's rule for a composer: the function is defined nowhere
        else, so the build fails without the file either way, and `No such
        file` names the fix."""
        p = _scaffold(tmp_path)
        cls = {"solo": "Solo", "o": "O"}[comp]
        _declare(p, comp, _rows(cls))
        _jm("apply", cwd=p)
        assert include in (p / ext).read_text(encoding="utf-8")

    def test_no_row_no_change(self, tmp_path: Path):
        """An object that declares none renders exactly what it did."""
        p = _scaffold(tmp_path)
        _jm("apply", cwd=p)
        for rel in (SOLO_EXT, M_EXT, O_FRAG):
            text = (p / rel).read_text(encoding="utf-8")
            assert "extra_methods (gh-1190)" not in text, rel
            assert "_extra.c" not in text, rel


class TestEveryRenderPathCarriesTheRows:
    """`apply` reaches a standalone object's binding three ways -- the
    creation render, a member's re-render through `_glue`, and the
    post-replay re-render a manifest `doc` triggers -- and `jm method`
    re-renders it a fourth. Each must keep the row and the include; the
    hook file is deliberately absent, so only the row can include it."""

    @pytest.fixture
    def busy(self, tmp_path: Path) -> Path:
        p = _scaffold(tmp_path)
        _jm("method", "solo", "scale", "--arg-type", "double",
            "--return-type", "double", cwd=p)  # fmt: skip
        cfg = C.load(p)
        cfg["solo"]["doc"] = "A documented solo."
        C.save(p, cfg)
        _declare(p, "solo", _rows("Solo"))
        _declare(p, "o", _rows("O"))
        _jm("apply", cwd=p)
        return p

    @staticmethod
    def _carries(p: Path) -> None:
        ext = (p / SOLO_EXT).read_text(encoding="utf-8")
        assert '{"twice", (PyCFunction)(void (*)(void))Solo_twice' in ext
        assert "static PyObject *Solo_twice(PyObject *" in ext, ext
        assert '#include "solo_ext_extra.c"' in ext, ext
        pyi = (p / "src/xm/solo.pyi").read_text(encoding="utf-8")
        assert "    def twice(self) -> float:" in pyi, pyi

    def test_apply(self, busy: Path):
        self._carries(busy)

    def test_a_later_jm_method(self, busy: Path):
        _jm("method", "solo", "offset", "--arg-type", "double",
            "--return-type", "double", cwd=busy)  # fmt: skip
        self._carries(busy)
        _jm("method", "o", "offset", "--module", "m", "--arg-type", "double",
            "--return-type", "double", cwd=busy)  # fmt: skip
        agg = (busy / M_EXT).read_text(encoding="utf-8")
        assert '#include "m_ext_o_extra.c"' in agg, agg
        assert "static PyObject *O_twice(PyObject *" in agg, agg

    def test_a_later_jm_property(self, busy: Path):
        """`jm property` / `error` / `warning` / `remove` re-render through
        `_glue.regenerate_standalone`, not `jm method`'s own render."""
        _jm("property", "solo", "bias", "--type", "double", cwd=busy)
        self._carries(busy)

    def test_status_agrees(self, busy: Path):
        r = run_cli("status", "--check", cwd=busy)
        assert r.returncode == 0, r.stdout + r.stderr


class TestTheStubCarriesIt:
    """A row with no stub is a member a type checker rejects a call to."""

    @pytest.mark.parametrize(
        "rel", ["src/xm/solo.pyi", "src/xm/m/m.pyi"], ids=["solo", "module"]
    )
    def test_both_stub_writers_declare_it(self, declared: Path, rel: str):
        pyi = (declared / rel).read_text(encoding="utf-8")
        assert "    def twice(self) -> float:" in pyi, pyi
        assert '        """Twice the gain."""' in pyi, pyi
        assert "    def scaled(self, k: float = 2.0) -> float:" in pyi, pyi
        assert "        A keyword-capable row." in pyi, pyi


class TestApplyAndStatusAgree:
    def test_status_is_clean_after_apply(self, declared: Path):
        """`status` replays the manifest and diffs; a key the replay dropped
        would be dropped on both sides, so it is the BUILD gate above that
        proves the replay carried it, and this that nothing disagrees."""
        r = run_cli("status", "--check", cwd=declared)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_a_second_apply_writes_nothing(self, declared: Path):
        out = _jm("apply", cwd=declared)
        assert "update" not in out, out


# --------------------------------------------------------------------------
# `jm adopt`: a declared row is what makes the flip safe (doppler#1446).
# --------------------------------------------------------------------------

_HAND_BODY = """
static PyObject *
O_twice(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    return PyFloat_FromDouble(2.0 * ((OObject *)self)->handle->g);
}
"""
#: The row as doppler writes one: a plain `(PyCFunction)` cast.
_HAND_ROW = (
    '    {"twice", (PyCFunction)O_twice, METH_NOARGS, "Twice the gain."},\n'
)


def _hand_fragment(p: Path, *, with_body: bool) -> None:
    """Give the sacred fragment a hand row (and, optionally, its body),
    where jm's render puts a declared method's: before `destroy`."""
    frag = p / O_FRAG
    text = frag.read_text(encoding="utf-8")
    anchor = '    {"destroy",'
    assert text.count(anchor) == 1, text
    text = text.replace(anchor, _HAND_ROW + anchor, 1)
    if with_body:
        table = "static PyMethodDef O_methods[]"
        assert text.count(table) == 1, text
        text = text.replace(table, _HAND_BODY.lstrip() + "\n" + table, 1)
    frag.write_text(text, encoding="utf-8")


@pytest.fixture
def adoptable(tmp_path: Path) -> Path:
    """`o` as doppler's fft is today: sacred, with a hand row and body."""
    p = _scaffold(tmp_path)
    _jm("apply", cwd=p)
    _hand_fragment(p, with_body=True)
    return p


class TestAdoptSeesTheDeclaredRow:
    def test_a_hand_body_refuses_the_flip(self, adoptable: Path):
        """The premise, and still true with the row declared: the body is
        only in the fragment, so a flip would delete it."""
        r = run_cli("adopt", "--check", cwd=adoptable)
        assert r.returncode == 1, r.stdout
        assert "only here: fn:O_twice" in r.stdout, r.stdout

    def test_the_declared_row_makes_the_flip_safe(self, adoptable: Path):
        """The migration: the body moves to the hook, the row to the
        manifest. Nothing then exists only on disk, and the table differs
        only by what the render ADDS (the cast) -- so nothing is lost."""
        frag = adoptable / O_FRAG
        frag.write_text(
            frag.read_text(encoding="utf-8").replace(_HAND_BODY.lstrip(), ""),
            encoding="utf-8",
        )
        (adoptable / O_HOOK).write_text(_HAND_BODY, encoding="utf-8")
        _declare(adoptable, "o", [_rows("O")[0]])
        r = run_cli("adopt", "--check", cwd=adoptable)
        assert "only here" not in r.stdout, r.stdout
        assert "differs:" not in r.stdout, r.stdout
        flip = run_cli("adopt", "o", "--accept-additions", cwd=adoptable)
        assert flip.returncode == 0, flip.stdout + flip.stderr
        assert C.fragment_kind(C.load(adoptable), "o") == "generated"
        text = frag.read_text(encoding="utf-8")
        assert "(PyCFunction)(void (*)(void))O_twice" in text, text
        st = run_cli("status", "--check", cwd=adoptable)
        assert st.returncode == 0, st.stdout + st.stderr


# --------------------------------------------------------------------------
# `manual_stub` for the same name: the row replaces its placeholder.
# --------------------------------------------------------------------------


class TestManualStubOfTheSameName:
    """A `manual_stub` method emits no row and only a placeholder stub; the
    row is exactly what it lacked. Both may stand: the row registers the
    function and types the member, and a stub the author wrote by hand
    stays theirs."""

    @pytest.fixture
    def both(self, tmp_path: Path) -> Path:
        p = _scaffold(tmp_path)
        for comp, mod in (("solo", ()), ("o", ("--module", "m"))):
            _jm("method", comp, "twice", *mod, "--manual-stub", cwd=p)
        _declare(p, "solo", [_rows("Solo")[0]])
        _declare(p, "o", [_rows("O")[0]])
        _jm("apply", cwd=p)
        return p

    @pytest.mark.parametrize("rel", ["src/xm/solo.pyi", "src/xm/m/m.pyi"])
    def test_one_member_and_it_is_the_rows(self, both: Path, rel: str):
        pyi = (both / rel).read_text(encoding="utf-8")
        assert pyi.count("    def twice(") == 1, pyi
        assert "    def twice(self) -> float:" in pyi, pyi
        assert "<<MANUAL_STUB>>" not in pyi, pyi

    def test_a_hand_written_stub_stays_hand_written(self, both: Path):
        pyi_path = both / "src/xm/m/m.pyi"
        pyi = pyi_path.read_text(encoding="utf-8")
        hand = pyi.replace(
            "    def twice(self) -> float:",
            "    def twice(self) -> float:  # written by hand",
        )
        assert hand != pyi
        pyi_path.write_text(hand, encoding="utf-8")
        _jm("apply", cwd=both)
        assert "# written by hand" in pyi_path.read_text(encoding="utf-8")

    def test_the_c_side_has_one_row(self, both: Path):
        for rel in (SOLO_EXT, O_FRAG):
            text = (both / rel).read_text(encoding="utf-8")
            assert text.count('{"twice"') == 1, text

    def test_each_stub_writer_renders_one_member(self):
        """At the writers themselves. Downstream, the manual-stub splice
        carries an earlier stub's member over the fresh render's, which can
        hide a doubled member on every path that has an earlier stub."""
        from just_makeit import _stubs
        from just_makeit._context import make_methods_ctx

        row = _rows("Solo")[0]
        standalone = make_methods_ctx(
            "solo",
            "Solo",
            [{"name": "twice", "manual_stub": True}],
            csym="solo",
            extra_methods=[row],
        )["pyi_extra_methods"]
        assert standalone.count("    def twice(") == 1, standalone
        assert "<<MANUAL_STUB>>" not in standalone, standalone
        cfg = {
            "project": {"name": "xm", "version": "0.1.0"},
            "solo": {
                "arg_type": "float",
                "return_type": "float",
                "methods": [{"name": "twice", "manual_stub": True}],
                "extra_methods": [row],
            },
        }
        module = _stubs._obj_stub(cfg, "solo")
        assert module.count("    def twice(") == 1, module
        assert "<<MANUAL_STUB>>" not in module, module

    def test_a_project_built_from_its_manifest_alone(
        self, both: Path, tmp_path: Path
    ):
        """No earlier stub to splice from: what the renderers emit is what
        lands. The incremental path above hides a doubled member behind the
        splice, which a materialize from the manifest does not have."""
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        shutil.copy2(both / C.FILENAME, fresh / C.FILENAME)
        for d in ("objects", "modules"):
            if (both / d).is_dir():
                shutil.copytree(both / d, fresh / d)
        _jm("apply", cwd=fresh)
        for rel in ("src/xm/solo.pyi", "src/xm/m/m.pyi"):
            pyi = (fresh / rel).read_text(encoding="utf-8")
            assert pyi.count("    def twice(") == 1, pyi
            assert "    def twice(self) -> float:" in pyi, pyi


# --------------------------------------------------------------------------
# Refusals: a row jm cannot render as written is refused before any write.
# --------------------------------------------------------------------------


class TestRefusals:
    @pytest.fixture
    def scaffold(self, tmp_path: Path) -> Path:
        p = _scaffold(tmp_path)
        _jm("property", "o", "level", "--module", "m", "--type", "double",
            cwd=p)  # fmt: skip
        _jm("method", "o", "gain2", "--module", "m", "--arg-type", "double",
            "--return-type", "double", cwd=p)  # fmt: skip
        _jm("apply", cwd=p)
        return p

    @pytest.mark.parametrize(
        "row,says",
        [
            ({"name": "reset", "fn": "O_r"}, "already a built-in method"),
            ({"name": "level", "fn": "O_l"}, "already a declared property"),
            ({"name": "__enter__", "fn": "O_e"}, "context-manager protocol"),
            ({"name": "gain2", "fn": "O_g"}, "declares 'gain2' too"),
            ({"name": "hand"}, "needs both `name` and `fn`"),
            ({"name": "hand", "fn": "O hand"}, "is not a C identifier"),
            ({"name": "hand", "fn": CORE_RESET}, "declared in o_core.h"),
        ],
        ids=[
            "builtin",
            "property",
            "reserved",
            "method",
            "no-fn",
            "bad-fn",
            "core-fn",
        ],
    )
    def test_apply_refuses_and_writes_nothing(
        self, scaffold: Path, row: dict, says: str
    ):
        if row.get("fn") == CORE_RESET:
            # The core's own `reset`, spelled as the project spells it (a
            # `c_prefix` moves it), so the case cannot pass by naming a
            # function the header does not declare.
            row = {**row, "fn": f"{CSYM.stem(C.load(scaffold), 'o')}_reset"}
        before = (scaffold / M_EXT).read_bytes()
        _declare(scaffold, "o", [row])
        r = run_cli("apply", cwd=scaffold)
        assert r.returncode == 1, r.stdout + r.stderr
        assert says in r.stderr, r.stderr
        assert (scaffold / M_EXT).read_bytes() == before

    def test_two_rows_one_name(self, scaffold: Path):
        row = {"name": "hand", "fn": "O_hand"}
        _declare(scaffold, "o", [row, {**row, "fn": "O_hand2"}])
        r = run_cli("apply", cwd=scaffold)
        assert r.returncode == 1 and "two rows are named" in r.stderr

    def test_one_fn_two_signatures(self, scaffold: Path):
        _declare(
            scaffold,
            "o",
            [
                {"name": "a", "fn": "O_hand"},
                {"name": "b", "fn": "O_hand", "flags": "METH_FASTCALL"},
            ],
        )
        r = run_cli("apply", cwd=scaffold)
        assert r.returncode == 1 and "different flags" in r.stderr

    def test_one_fn_two_names_is_an_alias(self, scaffold: Path):
        """Two rows sharing a function and its flags are two names for it,
        which C allows -- declared once."""
        _declare(
            scaffold,
            "o",
            [{"name": "a", "fn": "O_hand"}, {"name": "b", "fn": "O_hand"}],
        )
        _jm("apply", cwd=scaffold)
        agg = (scaffold / M_EXT).read_text(encoding="utf-8")
        assert agg.count("static PyObject *O_hand(") == 1, agg

    def test_jm_method_refuses_a_name_a_row_holds(self, scaffold: Path):
        _declare(scaffold, "o", [{"name": "hand", "fn": "O_hand"}])
        r = run_cli(
            "method", "o", "hand", "--module", "m", "--arg-type", "double",
            "--return-type", "double", cwd=scaffold,
        )  # fmt: skip
        assert r.returncode == 1, r.stdout + r.stderr
        assert "extra_methods" in r.stderr, r.stderr


# --------------------------------------------------------------------------
# The rest of the surface a manifest key touches.
# --------------------------------------------------------------------------


class TestAViewDoesNotInheritThem:
    """A row's function is written against the parent's struct, so a view
    (a second class over the core) gets none -- on either face."""

    def test_neither_face(self, tmp_path: Path):
        p = _scaffold(tmp_path)
        _jm("view", "o", "Peek", "--module", "m", "--create-fn",
            "o_create_peek", cwd=p)  # fmt: skip
        _declare(p, "o", [_rows("O")[0]])
        _jm("apply", cwd=p)
        view_frag = (p / "native/src/m/m_ext_peek.c").read_text("utf-8")
        assert "twice" not in view_frag, view_frag
        agg = (p / M_EXT).read_text(encoding="utf-8")
        assert agg.count("static PyObject *O_twice(") == 1, agg
        assert "m_ext_peek_extra.c" not in agg, agg
        pyi = (p / "src/xm/m/m.pyi").read_text(encoding="utf-8")
        peek = pyi[pyi.index("class Peek") :]
        assert "def twice" not in peek, peek
        assert "def twice" in pyi[: pyi.index("class Peek")], pyi


class TestTheManifestKey:
    def test_it_is_recognised(self):
        cfg = {"o": {"extra_methods": _rows("O")}}
        assert _keys.unknown_keys(cfg) == []

    def test_a_typo_on_a_row_is_reported(self):
        cfg = {"o": {"extra_methods": [{**_rows("O")[0], "flgs": "METH_O"}]}}
        msgs = [u.message() for u in _keys.unknown_keys(cfg)]
        assert any("flgs" in m for m in msgs), msgs

    def test_the_composers_type_key_is_named_as_the_composers(self):
        """An object has one type; `type` is a composer row's key."""
        cfg = {"o": {"extra_methods": [{**_rows("O")[0], "type": "X"}]}}
        (u,) = _keys.unknown_keys(cfg)
        assert u.key == "type"
        assert "composer extra_method" in u.valid_for, u.valid_for

    def test_the_writer_round_trips_every_row_key(self):
        """`_dump` is what `split-objects` and a fresh save go through: a
        key it drops is gone from the manifest with no error."""
        rows = _rows("O") + [{"name": "x", "fn": "O_x", "flgs": "typo"}]
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "o": {"arg_type": "float", "extra_methods": rows},
        }
        back = tomllib.loads(C._dump(cfg))["o"]["extra_methods"]
        # A multi-line string reads back with the newline before its closing
        # quotes, which every `doc` writer strips again (gh-192).
        assert [
            {k: v.rstrip("\n") if k == "doc" else v for k, v in r.items()}
            for r in back
        ] == rows

    def test_jm_script_names_them(self, declared: Path):
        out = _jm("script", cwd=declared)
        assert "# NOTE: [[solo.extra_methods]] (twice, scaled)" in out, out
        assert "# NOTE: [[o.extra_methods]] (twice, scaled)" in out, out
