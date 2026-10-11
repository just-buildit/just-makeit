"""gh-2006: a spliced table row goes where a render puts it.

`jm apply` adds a member declared since a SACRED module fragment was written
by splicing it in (gh-440, gh-729): the wrapper above the table, its row into
the table. Every row went before the ``{NULL}`` sentinel, spelled
``"    {row},\\n"`` at the sentinel's ``{`` -- the first row at two indents,
the sentinel at none -- while a render puts a declared method's row, and an
``extra_methods`` row, ahead of jm's trailing built-ins (``destroy``,
``__enter__``, ``__exit__``). So `jm adopt --check` reported ``differs:
table:PyMethodDef`` on a table only jm had touched, and refused the flip it
had called safe one `apply` earlier.

The getset table took the same splice, and so did the re-render that carries
an author's hand-written row into jm's fresh render (gh-770): both now go
through one ``_splice_rows``, which reads the position from the order of the
table the row comes from.

GATE: a sacred fragment `adopt --check` calls safe, given a method, an
      ``extra_methods`` row and a property through `apply`, still reads safe,
      and its method and getset tables equal a fresh render byte for byte; a
      hand-written row keeps its place through a re-render.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _object as O  # noqa: E402
from just_makeit import _render as R  # noqa: E402

FRAG = "native/src/m/m_ext_{}.c"

#: A binding table, from its declaration to its closing brace, by kind.
_TABLE_RE = re.compile(
    r"^static (PyMethodDef|PyGetSetDef) \w+\[\] = \{\n.*?^\};", re.M | re.S
)


def _jm(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}:\n{r.stdout}{r.stderr}"
    return r.stdout


def _tables(text: str) -> "dict[str, str]":
    return {m.group(1): m.group(0) for m in _TABLE_RE.finditer(text)}


def _fresh(root: Path, obj: str) -> str:
    """The fragment jm renders for *obj* now -- what `adopt --check` holds
    the file to -- by the call it makes."""
    cfg = C.load(root)
    (ctx,) = [
        c
        for c in O.build_component_ctxs(root, cfg, "m", C.project_name(cfg))
        if c["component"] == obj
    ]
    return R.render_module_ext_fragment(ctx)


def _adopt_check(root: Path) -> str:
    return run_cli("adopt", "--check", cwd=root).stdout


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Module `m`: `o` with a property (so it has a getset table to splice
    into), `p` with none. Both fragments sacred and adopt-safe."""
    _jm("new", "xm", cwd=tmp_path)
    p = tmp_path / "xm"
    _jm("module", "m", cwd=p)
    _jm("object", "o", "--module", "m", "--state", "g:double:1.5", cwd=p)
    _jm("property", "o", "level", "--module", "m", "--type", "double",
        cwd=p)  # fmt: skip
    _jm("object", "p", "--module", "m", "--state", "h:double:2.0", cwd=p)
    _jm("apply", cwd=p)
    check = _adopt_check(p)
    assert "2 of 2 object(s) could flip" in check, check
    return p


def _declare(root: Path) -> None:
    """A method, an `extra_methods` row and a property on `o`, a first
    property on `p` -- each new since the fragments were written."""
    cfg = C.load(root)
    cfg["o"].setdefault("methods", []).append(
        {"name": "scale", "arg_type": "double", "return_type": "double"}
    )
    C.set_extra_methods(cfg, "o", [{"name": "twice", "fn": "O_twice"}])
    cfg["o"]["properties"].append({"name": "bias", "type": "double"})
    cfg["p"].setdefault("properties", []).append(
        {"name": "tone", "type": "double"}
    )
    C.save(root, cfg)


class TestApplySplicesWhereARenderPuts:
    @pytest.fixture
    def spliced(self, project: Path) -> Path:
        _declare(project)
        _jm("apply", cwd=project)
        return project

    @pytest.mark.parametrize("kind", ["PyMethodDef", "PyGetSetDef"])
    @pytest.mark.parametrize("obj", ["o", "p"])
    def test_each_table_equals_a_fresh_render(
        self, spliced: Path, obj: str, kind: str
    ):
        disk = _tables((spliced / FRAG.format(obj)).read_text("utf-8"))
        assert disk.get(kind) == _tables(_fresh(spliced, obj))[kind]

    def test_the_rows_were_spliced_not_rendered(self, spliced: Path):
        """The premise: a sacred fragment is never rendered whole, so the
        new rows reached it through the splice this is about."""
        text = (spliced / FRAG.format("o")).read_text("utf-8")
        assert "Hand-patches to this file are preserved" in text, text
        for row in ('{"scale"', '{"twice"', '{ "bias"'):
            assert row in text, row

    def test_adopt_still_reads_safe(self, spliced: Path):
        check = _adopt_check(spliced)
        assert "differs" not in check, check
        assert "2 of 2 object(s) could flip" in check, check


class TestAHandRowKeepsItsPlace:
    """The re-render that carries an author's row into jm's render
    (`transplant_hand_written`, gh-770): the row stays where it was written,
    before `destroy`, at the table's indent."""

    HAND = '    {"hand", (PyCFunction)O_hand, METH_NOARGS, NULL},\n'
    BODY = (
        "static PyObject *\n"
        "O_hand(PyObject *self, PyObject *Py_UNUSED(ignored))\n"
        "{\n"
        "    Py_RETURN_NONE;\n"
        "}\n\n"
    )

    def test_through_a_jm_method(self, project: Path):
        frag = project / FRAG.format("o")
        text = frag.read_text("utf-8")
        destroy = '    {"destroy",'
        table = "static PyMethodDef O_methods[]"
        assert text.count(destroy) == 1 and text.count(table) == 1, text
        text = text.replace(destroy, self.HAND + destroy, 1)
        frag.write_text(text.replace(table, self.BODY + table, 1), "utf-8")
        _jm("method", "o", "scale", "--module", "m", "--arg-type", "double",
            "--return-type", "double", cwd=project)  # fmt: skip
        methods = _tables(frag.read_text("utf-8"))["PyMethodDef"]
        assert self.HAND + destroy in methods, methods
        assert methods.endswith("\n    {NULL, NULL, 0, NULL}\n};"), methods


def test_the_state_triplet_safety_net_keeps_the_indent():
    """`transplant_state_triplet` (gh-404) inserts rows that carry their own
    indent, so it inserts them at the start of the sentinel's line, not at
    its `{`. It has no reference render to read an order from; the general
    splice above places the triplet first, and this stays its idempotent
    safety net."""
    from just_makeit import _docsync as D
    from just_makeit._context._methods import serializable_triplet_parts

    funcs, pmd, _ = serializable_triplet_parts("o", "O", "O", csym="xm_o")
    head = (
        "static PyMethodDef O_methods[] = {\n"
        '    {"a", A, METH_NOARGS, NULL},\n'
    )
    tail = "    {NULL, NULL, 0, NULL}\n};\n"
    out = D.transplant_state_triplet(head + tail, funcs, pmd)
    assert out.endswith(head + pmd + tail), out
