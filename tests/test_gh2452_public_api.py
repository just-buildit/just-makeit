"""gh-2452: `components` and `Refusal` are public, and public docs say so.

gh-2455 adds `is_versioned` under the same two properties: doppler's
`status --check` gate imported the private `_createonly.is_versioned` to
prove jm still compares each vendored header, since an empty OUTDATED
list reads the same when jm has stopped comparing one.

doppler's `scripts/check_bench_coverage.py` imported the private
`_config.components`, because no public name listed a manifest's
components and a script cannot derive the list on its own: the union of
the ``[module.X] objects`` lists misses standalone objects, and the right
definition reads private constants (a copy of it is gh-2434).
`load_manifest`'s Raises named ``just_makeit._report.Refusal``, so a
caller who wanted ``.errors`` had to import a private module.

Two properties hold the fix:

- **One implementation each.** ``just_makeit.components`` and
  ``just_makeit.Refusal`` ARE the private objects, re-exported, so a fix
  to either reaches both spellings.
- **Public docs cite only public names.** Every code reference the three
  public docstrings make -- a ``literal``, a `default-role` span, or a
  :role:`target` -- is parsed out of its span and matched whole, not found
  by a substring search. A dotted path has no ``_``-prefixed segment, and
  one rooted at ``just_makeit`` resolves through the package namespace. A
  single name in a role or single backticks (`:meth:` ``report``) names a
  public attribute of the documented object or of the package. Each
  docstring must cite at least one ``just_makeit.<path>``, so the check
  cannot pass on a docstring it failed to read.
"""

from __future__ import annotations

import pkgutil
import re
import subprocess
import sys

import pytest

import just_makeit
from just_makeit import _config, _createonly, _report

#: The public API whose docstrings the citation check reads, by name.
PUBLIC = ("load_manifest", "components", "Refusal", "is_versioned")

#: One reST code span: an optional ``:role:``, then ``literal`` or `tick`.
#: The double-backtick branch comes first, so ``x`` is not read as `x`.
_SPAN = re.compile(
    r"(?::(?P<role>[\w.:-]+):)?(?:``(?P<lit>[^`]+)``|`(?P<tick>[^`]+)`)"
)

#: A Python path, optionally called: ``just_makeit.load_manifest(root)``.
_PATH = re.compile(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(.*\))?")


#: The one-word names a ``literal`` cannot use as a manifest key: every
#: submodule of the package, and every ``_``-prefixed attribute of it.
#: ``_template`` and ``_group`` are neither, so they keep the key reading.
_JM_INTERNAL = {m.name for m in pkgutil.iter_modules(just_makeit.__path__)} | {
    n for n in vars(just_makeit) if n.startswith("_") and not n.endswith("__")
}


def _citations(doc: str) -> list[tuple[str, bool]]:
    """Each code reference in *doc*: ``(path, is_xref)``.

    *is_xref* is true for a role or single-backtick span, which names an
    object; a ``literal`` naming one bare word (``include``, ``_group``)
    is a manifest key or a value, so only its dotted paths count -- unless
    the word is one of `_JM_INTERNAL`, which names jm's internals. A
    role's ``title <target>`` form is read for its target, and a leading
    ``~`` or ``!`` is dropped.
    """
    out = []
    for m in _SPAN.finditer(doc):
        text = (m["lit"] or m["tick"]).strip()
        is_xref = m["lit"] is None
        if is_xref:
            target = re.fullmatch(r".*<(.+)>", text)
            text = (target[1] if target else text).lstrip("~!")
        path = _PATH.fullmatch(text)
        if path and (is_xref or "." in path[1] or path[1] in _JM_INTERNAL):
            out.append((path[1], is_xref))
    return out


def _private(segment: str) -> bool:
    """A ``_name``, not a dunder such as ``__version__``."""
    return segment.startswith("_") and not segment.endswith("__")


def test_components_is_the_config_function():
    assert just_makeit.components is _config.components


def test_refusal_is_the_report_class():
    assert just_makeit.Refusal is _report.Refusal


def test_is_versioned_is_the_createonly_function():
    assert just_makeit.is_versioned is _createonly.is_versioned


def test_dir_lists_the_lazy_names():
    """Before first access: a fresh interpreter, because the identity
    tests above cache each name into the package's globals, where
    ``dir()`` would find it whatever `__dir__` returned."""
    code = (
        "import just_makeit; "
        "print(sorted({'components', 'Refusal', 'is_versioned'} "
        "- set(dir(just_makeit))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == "[]"


def test_all_names_the_public_api():
    assert set(just_makeit.__all__) == {*PUBLIC, "__version__"}


@pytest.mark.parametrize("module", ["_config", "_createonly"])
def test_import_does_not_load_the_defining_module(module):
    """The re-export is lazy: `import just_makeit` stays off `_config`
    and `_createonly`, which every CLI invocation would otherwise pay for
    (gh-764)."""
    code = (
        "import sys, just_makeit; "
        f"print('just_makeit.{module}' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == "False"


@pytest.mark.parametrize("name", PUBLIC)
def test_public_docstring_cites_only_public_names(name):
    obj = getattr(just_makeit, name)
    cited = _citations(obj.__doc__ or "")
    rooted = [p for p, _ in cited if p.split(".")[0] == "just_makeit"]
    assert rooted, f"just_makeit.{name}'s docstring cites no public name"
    for path, is_xref in cited:
        segments = path.split(".")
        private = [s for s in segments if _private(s)]
        assert not private, (
            f"just_makeit.{name}'s docstring cites {path!r}: "
            f"{private} is private; cite the just_makeit.<name> re-export"
        )
        if len(segments) == 1:
            # A relative reference: a member of the documented object,
            # or a name the package exports.
            assert is_xref, (
                f"just_makeit.{name}'s docstring cites ``{path}``, a "
                f"just_makeit submodule; cite the public re-export"
            )
            assert hasattr(obj, path) or hasattr(just_makeit, path), (
                f"just_makeit.{name}'s docstring cites {path!r}, which is "
                f"neither its own attribute nor a just_makeit name"
            )
            continue
        if segments[0] != "just_makeit":
            continue
        target = just_makeit
        for seg in segments[1:]:
            assert hasattr(target, seg), (
                f"just_makeit.{name}'s docstring cites {path!r}, "
                f"which does not resolve at {seg!r}"
            )
            target = getattr(target, seg)


def test_the_parser_reads_every_span_shape():
    """Armed: each shape a private path could hide in is read as one."""
    doc = (
        "``a.b`` `c.d` :class:`e.f` :meth:`~g.h` :func:`t <i.j>` "
        ":meth:`report` ``include`` ``_template`` ``_report`` "
        "``scripts`` ``x.y(root)``"
    )
    assert _citations(doc) == [
        ("a.b", False),
        ("c.d", True),
        ("e.f", True),
        ("g.h", True),
        ("i.j", True),
        ("report", True),
        ("_report", False),
        ("scripts", False),
        ("x.y", False),
    ]
