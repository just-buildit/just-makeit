"""gh-2534 -- a warning's expression condition is documented by its property.

gh-601 let a warning's ``condition`` be a C expression, and the class
docstring printed it verbatim, so doppler's ``Acquisition`` -- a
cross-module view (gh-504, gh-2516) -- told a Python caller::

    Emitted after construction when ``self->handle->search->underpowered``
    holds: ``Acquisition is under-powered: ...``

while the same view declares ``underpowered`` as a property whose ``expr``
is that very C. Before gh-601 a bare identifier rendered as the name a
caller can read.

The maintainer decision: no new key. When a property declared on the SAME
class has an ``expr`` equal to the condition -- after whitespace that cannot
separate tokens and redundant OUTER parentheses are stripped, nothing
looser -- the docstring names that property; otherwise it is unchanged.
One helper (`condition_doc_name`) decides it, and both faces (the runtime
``tp_doc`` and the ``.pyi``) read it through `class_diagnostics`.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _docsync as DS  # noqa: E402
from just_makeit._context._diagnostics import (  # noqa: E402
    condition_doc_name,
)

UNDER = "self->handle->under"
NEG = "self->handle->gain < 0"
HOT = "self->handle->gain > 1"


class TestTheComparison:
    """What counts as "the same expression": whitespace and redundant outer
    parentheses, nothing looser."""

    @pytest.mark.parametrize(
        "expr",
        [
            UNDER,
            f"({UNDER})",
            f"(( {UNDER} ))",
            "  self->handle -> under ",
            "( self -> handle -> under )",
        ],
    )
    def test_a_respelling_names_the_property(self, expr):
        props = [{"name": "up", "expr": expr}]
        assert condition_doc_name(UNDER, props) == "up"
        assert condition_doc_name(f"({UNDER})", props) == "up"

    def test_no_matching_property_keeps_the_expression(self):
        props = [{"name": "up", "expr": "self->handle->other"}]
        assert condition_doc_name(UNDER, props) == UNDER

    def test_a_property_without_expr_is_not_a_candidate(self):
        props = [{"name": "under", "field": "under"}]
        assert condition_doc_name(UNDER, props) == UNDER

    @pytest.mark.parametrize("expr", [UNDER, "under", "(under)"])
    def test_a_bare_identifier_is_unchanged(self, expr):
        """A bare identifier names a state field and already renders as a
        name; the property lookup is for expressions only."""
        props = [{"name": "up", "expr": expr}]
        assert condition_doc_name("under", props) == "under"

    def test_whitespace_between_words_is_kept(self):
        """``unsigned int`` is two tokens; ``unsignedint`` is one."""
        cond = "(unsigned int)self->handle->n > 3"
        props = [{"name": "n3", "expr": "(unsignedint)self->handle->n > 3"}]
        assert condition_doc_name(cond, props) == cond

    def test_unmatched_outer_parens_are_not_stripped(self):
        """``(a) || (b)`` starts and ends with a parenthesis that are not a
        pair; stripping them gives ``a) || (b``, which must not compare
        equal to a property spelled that way."""
        cond = "(self->handle->a) || (self->handle->b)"
        trap = [{"name": "t", "expr": "self->handle->a) || (self->handle->b"}]
        assert condition_doc_name(cond, trap) == cond
        same = [{"name": "ab", "expr": f"( {cond} )"}]
        assert condition_doc_name(cond, same) == "ab"

    def test_whitespace_that_separates_tokens_is_kept(self):
        """``a - -b`` and ``a--b`` lex differently in C."""
        props = [{"name": "d", "expr": "self->handle->a--self->handle->b"}]
        cond = "self->handle->a - -self->handle->b"
        assert condition_doc_name(cond, props) == cond

    def test_the_first_matching_property_wins(self):
        props = [
            {"name": "first", "expr": UNDER},
            {"name": "second", "expr": f"({UNDER})"},
        ]
        assert condition_doc_name(UNDER, props) == "first"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A module object and a cross-module view of it (doppler's shape).

    The parent ``Rc`` declares ``up`` and ``pneg``; the view ``Acq`` lives
    in another module, excludes ``pneg``, inherits ``up`` and adds
    ``vhot``. Each class warns on all three expressions, so each face shows
    a property it has, an excluded parent property, and the other class's
    own property.
    """
    base = tmp_path_factory.mktemp("gh2534")
    _ok("new", "pp", cwd=base)
    root = base / "pp"
    _ok("module", "m", cwd=root)
    _ok("module", "acquire", cwd=root)
    _ok(
        "object", "rc", "--module", "m",
        "--state", "under:bool:false", "--state", "gain:double:1.0",
        cwd=root,
    )  # fmt: skip
    _ok(
        "object", "other", "--module", "acquire", "--state", "g:double:1.0",
        cwd=root,
    )  # fmt: skip
    _ok(
        "property", "rc", "up", "--module", "m", "--type", "bool",
        "--expr", "( self->handle->under )",
        cwd=root,
    )  # fmt: skip
    _ok(
        "property", "rc", "pneg", "--module", "m", "--type", "bool",
        "--expr", "((self->handle->gain<0))",
        cwd=root,
    )  # fmt: skip
    _ok(
        "view", "rc", "Acq", "--module", "acquire",
        "--create-fn", "pp_rc_create_acq", "--exclude-property", "pneg",
        cwd=root,
    )  # fmt: skip
    _ok(
        "property", "rc", "vhot", "--module", "m", "--view", "Acq",
        "--type", "bool", "--expr", HOT,
        cwd=root,
    )  # fmt: skip
    for cond in (UNDER, NEG, f"({HOT})"):
        _ok(
            "warning", "rc", "--module", "m", "--condition", cond,
            "--message", f"rc {cond}",
            cwd=root,
        )  # fmt: skip
        _ok(
            "warning", "rc", "--module", "m", "--view", "Acq",
            "--condition", cond, "--message", f"acq {cond}",
            cwd=root,
        )  # fmt: skip
    _ok("apply", cwd=root)
    return root


def _runtime_doc(frag: Path, cls: str) -> str:
    text = frag.read_text(encoding="utf-8")
    mask = DS._code_mask(text)
    at = mask.index(f"PyTypeObject {cls}Type")
    field = DS._tp_doc_span(text[at:], mask[at:])[2]
    lit = "".join(DS._STR_LIT_RE.findall(field))
    return lit.encode("utf-8").decode("unicode_escape")


def _stub_doc(pyi: Path, cls: str) -> str:
    tree = ast.parse(pyi.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    )
    return ast.get_docstring(node) or ""


def _faces(root: Path, cls: str) -> "dict[str, str]":
    module = "m" if cls == "Rc" else "acquire"
    frag = root / "native" / "src" / module / f"{module}_ext_{cls.lower()}.c"
    pyi = root / "src" / "pp" / module / f"{module}.pyi"
    return {
        "runtime": " ".join(_runtime_doc(frag, cls).split()),
        "stub": " ".join(_stub_doc(pyi, cls).split()),
    }


def _named(doc: str, label: str) -> str:
    """What the Warns entry for the warning whose message is *label*
    names as its condition."""
    msg = re.escape(label)
    m = re.search(rf"when ``([^`]+)`` holds: ``{msg}``", doc)
    assert m, (label, doc)
    return m.group(1)


# (class, condition as declared) -> the name each face must show.
CASES = [
    # Rc's own property, spelled with spaces and parentheses.
    ("Rc", UNDER, "up"),
    # Rc's own property, doubly parenthesised.
    ("Rc", NEG, "pneg"),
    # The VIEW's property: Rc does not expose it.
    ("Rc", f"({HOT})", f"({HOT})"),
    # Inherited from the parent: the view binds it.
    ("Acq", UNDER, "up"),
    # Excluded from the view: the parent's property is not the view's.
    ("Acq", NEG, NEG),
    # The view's own property; the condition is the parenthesised spelling.
    ("Acq", f"({HOT})", "vhot"),
]


class TestEveryFaceNamesTheProperty:
    @pytest.mark.parametrize("face", ["runtime", "stub"])
    @pytest.mark.parametrize("cls,cond,want", CASES)
    def test_the_condition_shown(self, project, face, cls, cond, want):
        label = f"{'rc' if cls == 'Rc' else 'acq'} {cond}"
        doc = _faces(project, cls)[face]
        assert _named(doc, label) == want, doc
