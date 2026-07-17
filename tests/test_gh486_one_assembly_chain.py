"""gh-486: every regenerating command renders from the one assembly chain.

`_glue.component_ctx()` exists so the `_ext.c`/`.pyi` context is built exactly
once. `_property` was converted in gh-481 and `_stubs._obj_stub` in gh-446, but
`_method` and `_remove` kept their own inline copies — and predictably drifted:
both still regenerated the stub's doctest as
`>>> from <<package>> import <<Component>>`, the exact bug gh-481 fixed for
`jm property`, because only `_glue` learned to resolve `pyi_examples`.

The mechanism is worth knowing: `render()` makes a *single* pass over
`ctx.items()` in insertion order. `package` is inserted early, so `<<package>>`
is substituted in the template *before* `pyi_examples` (from `make_state_ctx`,
later) inserts text that itself contains `<<package>>`. The freshly-inserted
token is never re-scanned. Any chain that doesn't explicitly rebuild
`pyi_examples` with the real package ships the placeholder.

These tests pin the *property* — one chain — rather than any single symptom.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._method import run as method_run
from just_makeit._property import run as property_run
from just_makeit._remove import run as remove_run
from just_makeit._warning import run as warning_run


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["nco"], [("phase", "double", "0.0")])
    return dest


def _pyi(project):
    return (project / "src" / "dsp" / "nco.pyi").read_text(encoding="utf-8")


def _assert_doctest_intact(project, after):
    text = _pyi(project)
    assert "<<package>>" not in text, (
        f"{after} regenerated the .pyi doctest with an unresolved "
        f"<<package>> placeholder — the chain did not rebuild pyi_examples"
    )
    assert "<<Component>>" not in text, (
        f"{after} left <<Component>> unresolved"
    )
    assert ">>> from dsp import Nco" in text, (
        f"{after} lost the real import line"
    )


class TestDoctestSurvivesEveryCommand:
    """Each of these regenerates the .pyi; none may corrupt its doctest."""

    def test_fresh_scaffold(self, project):
        _assert_doctest_intact(project, "jm new")

    def test_property(self, project):
        property_run(project, "nco", "dropped", None, "size_t", False)
        _assert_doctest_intact(project, "jm property")

    def test_warning(self, project):
        warning_run(project, "nco", "phase", "best effort")
        _assert_doctest_intact(project, "jm warning")

    def test_method(self, project):
        method_run(
            project, "nco", "execute_ctrl", None, "double", "double", False, []
        )
        _assert_doctest_intact(project, "jm method")

    def test_remove_property(self, project):
        property_run(project, "nco", "a", None, "size_t", False)
        property_run(project, "nco", "b", None, "size_t", False)
        remove_run(project, "property", "b", object_name="nco", force=True)
        _assert_doctest_intact(project, "jm remove property")

    def test_remove_method(self, project):
        method_run(
            project, "nco", "execute_ctrl", None, "double", "double", False, []
        )
        remove_run(
            project, "method", "execute_ctrl", object_name="nco", force=True
        )
        _assert_doctest_intact(project, "jm remove method")


class TestSingleAssemblyChain:
    """The structural guarantee, not a symptom of it."""

    def test_method_and_remove_use_glue(self):
        # A new slot added to _glue must reach every regenerating command
        # without touching their files. If either builds its own chain, a
        # future slot silently misses it — which is how gh-486 happened.
        import inspect

        from just_makeit import _method, _remove

        for mod in (_method, _remove):
            src = inspect.getsource(mod)
            assert "_glue.component_ctx" in src or "_glue.regenerate" in src, (
                f"{mod.__name__} does not render via _glue — a second "
                f"assembly chain has been reintroduced (see gh-486)"
            )
