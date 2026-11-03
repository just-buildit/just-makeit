"""gh-2452: `components` and `Refusal` are public, and public docs say so.

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
- **Public docs cite only public names.** Every ``just_makeit.<path>``
  the three public docstrings cite in double backticks resolves through
  the package namespace with no ``_``-prefixed segment, and none of them
  cites a bare private module path (``_config.load``). The citations are
  parsed out of the reST literal spans, not found by a substring search,
  and each docstring must cite at least one public name, so the check
  cannot pass on a docstring it failed to read.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

import just_makeit
from just_makeit import _config, _report

#: The public API whose docstrings the citation check reads, by name.
PUBLIC = ("load_manifest", "components", "Refusal")

#: One reST inline literal: ``...``.
_LITERAL = re.compile(r"``([^`]+)``")

#: A dotted Python path, optionally called: ``just_makeit.load_manifest(root)``.
_DOTTED = re.compile(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?:\(.*\))?")


def _dotted_citations(doc: str) -> list[str]:
    """Every literal span of *doc* that is a whole dotted Python path."""
    out = []
    for span in _LITERAL.findall(doc):
        m = _DOTTED.fullmatch(span.strip())
        if m:
            out.append(m.group(1))
    return out


def test_components_is_the_config_function():
    assert just_makeit.components is _config.components


def test_refusal_is_the_report_class():
    assert just_makeit.Refusal is _report.Refusal


def test_dir_lists_the_lazy_names():
    assert {"components", "Refusal"} <= set(dir(just_makeit))


def test_import_does_not_load_config():
    """The re-export is lazy: `import just_makeit` stays off `_config`,
    which every CLI invocation would otherwise pay for (gh-764)."""
    code = (
        "import sys, just_makeit; print('just_makeit._config' in sys.modules)"
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
    doc = getattr(just_makeit, name).__doc__ or ""
    cited = _dotted_citations(doc)
    public = [c for c in cited if c.split(".")[0] == "just_makeit"]
    assert public, f"just_makeit.{name}'s docstring cites no public name"
    for path in cited:
        segments = path.split(".")
        private = [s for s in segments if s.startswith("_")]
        assert not private, (
            f"just_makeit.{name}'s docstring cites ``{path}``: "
            f"{private} is private; cite the just_makeit.<name> re-export"
        )
        if segments[0] != "just_makeit":
            continue
        obj = just_makeit
        for seg in segments[1:]:
            assert hasattr(obj, seg), (
                f"just_makeit.{name}'s docstring cites ``{path}``, "
                f"which does not resolve at {seg!r}"
            )
            obj = getattr(obj, seg)
