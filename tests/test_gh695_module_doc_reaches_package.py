"""gh-695: `[module.X] doc` must reach the package users actually import.

gh-645 added `[module.X] doc` and wired it to two faces: the C extension's
`PyModuleDef.m_doc` and the generated re-export `__init__.py`. The second half
only ever fired through the `MODULE_INIT_PY` template -- and that template is
rendered **only when the file does not yet exist**. `_merge_module_init` (and
its `apply`-side caller) merge the import line and `__all__` into an existing
shim and nothing else.

So a module that gained a `doc` after it was scaffolded -- which is every
module in an existing project, i.e. all 26 of doppler's -- kept a
docstring-less shim forever, while the same manifest string *did* reach
`m_doc`. The result was the worst of both faces:

    >>> doppler.filter.__doc__          # the package everyone imports
    None
    >>> doppler.filter.filter.__doc__   # the inner C ext, where it landed
    'FIR filtering: ...'

This is the "apply has its own copy of the write path" class that has now bitten
four times (count_default, [module.X] doc, tp_doc, the accessor lookups), so the
tests below drive `apply` rather than the render helpers -- driving the helper
is exactly what let gh-645 ship looking correct.

The two rules that are not obvious, and are asserted here rather than assumed:

- an undeclared `doc` must **not** strip a docstring somebody wrote by hand;
- a declared `doc` **replaces** the leading docstring rather than stacking a
  second one, or `apply` would not be idempotent and the drift gate would
  never settle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402

DOC = "FIR filtering: a direct-form complex or real FIR."


def _project(tmp_path: Path, doc: str = "") -> Path:
    """A project with one module holding one object."""
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "filter", doc=doc)
    object_run(root, "fir", "filter")
    return root


def _init_py(root: Path) -> Path:
    return root / "src" / "demo" / "filter" / "__init__.py"


def _set_doc(root: Path, doc: str) -> None:
    """Declare `doc` on an existing `[module.filter]`, as an author would."""
    frag = root / "modules" / "filter.toml"
    path = frag if frag.exists() else root / "just-makeit.toml"
    text = path.read_text(encoding="utf-8")
    assert "[module.filter]" in text, (
        "module section not found in the manifest"
    )
    lines = [
        ln
        for ln in text.splitlines(keepends=True)
        if not ln.startswith("doc = ")
    ]
    text = "".join(lines).replace(
        "[module.filter]", f'[module.filter]\ndoc = "{doc}"', 1
    )
    path.write_text(text, encoding="utf-8")


class TestDocAddedToAnExistingModule:
    """The reported case: the module exists, then gains a `doc`."""

    def test_doc_reaches_the_package_init(self, tmp_path):
        root = _project(tmp_path)
        assert not _init_py(root).read_text().startswith('"""')
        _set_doc(root, DOC)
        apply_run(root)
        assert _init_py(root).read_text().startswith(f'"""{DOC}"""')

    def test_apply_is_idempotent(self, tmp_path):
        """Two applies must agree, or the drift gate never settles."""
        root = _project(tmp_path)
        _set_doc(root, DOC)
        apply_run(root)
        once = _init_py(root).read_text()
        apply_run(root)
        assert _init_py(root).read_text() == once
        assert once.count('"""') == 2, "a second docstring was stacked on"

    def test_status_check_is_clean_after_apply(self, tmp_path):
        """`jm status --check` must not report the file it just wrote."""
        root = _project(tmp_path)
        _set_doc(root, DOC)
        apply_run(root)
        assert status_run(root, check=True) == 0

    def test_editing_the_doc_updates_the_package(self, tmp_path):
        """The manifest is the source of truth, so a change propagates."""
        root = _project(tmp_path)
        _set_doc(root, DOC)
        apply_run(root)
        _set_doc(root, "Rewritten.")
        apply_run(root)
        text = _init_py(root).read_text()
        assert text.startswith('"""Rewritten."""')
        assert DOC not in text

    def test_user_code_below_survives(self, tmp_path):
        """The shim is a merge target, not a jm-owned file."""
        root = _project(tmp_path)
        p = _init_py(root)
        p.write_text(
            p.read_text() + "\n\nclass Wrapper:\n    pass\n", encoding="utf-8"
        )
        _set_doc(root, DOC)
        apply_run(root)
        text = p.read_text()
        assert text.startswith(f'"""{DOC}"""')
        assert "class Wrapper:" in text
        assert '__all__ = ["Fir"]' in text


class TestUndeclaredDoc:
    """jm owns the docstring only when the manifest declares one."""

    def test_a_hand_written_docstring_is_not_stripped(self, tmp_path):
        root = _project(tmp_path)
        p = _init_py(root)
        p.write_text('"""Mine, by hand."""\n\n' + p.read_text(), "utf-8")
        apply_run(root)
        assert p.read_text().startswith('"""Mine, by hand."""')

    def test_no_docstring_stays_absent(self, tmp_path):
        root = _project(tmp_path)
        apply_run(root)
        assert not _init_py(root).read_text().startswith('"""')


class TestFreshScaffold:
    """The path gh-645 already covered must keep working."""

    def test_doc_at_creation_reaches_the_package(self, tmp_path):
        root = _project(tmp_path, doc=DOC)
        assert _init_py(root).read_text().startswith(f'"""{DOC}"""')

    def test_and_survives_a_later_apply(self, tmp_path):
        root = _project(tmp_path, doc=DOC)
        apply_run(root)
        assert _init_py(root).read_text().startswith(f'"""{DOC}"""')


@pytest.mark.parametrize(
    "doc",
    [
        'Contains a "quoted" phrase.',
        'Ends with a quote"',
        "Has a backslash \\ in it.",
    ],
    ids=["embedded-quotes", "trailing-quote", "backslash"],
)
def test_awkward_prose_still_parses(tmp_path, doc):
    """The prose comes from TOML, so it can contain anything.

    A docstring that does not parse breaks the generated package outright, so
    this is a build break rather than a cosmetic issue.
    """
    root = _project(tmp_path)
    _set_doc(root, doc.replace("\\", "\\\\").replace('"', '\\"'))
    apply_run(root)
    text = _init_py(root).read_text()
    compile(text, "__init__.py", "exec")
