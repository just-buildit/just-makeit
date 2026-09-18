"""A composer source's steps/step/reset say one thing, on both faces (gh-1356).

The ``.pyi`` and the runtime ``PyMethodDef`` each carried a hand-written
sentence per member, with nothing reconciling them::

    stub     Generate *n* complex samples.
    runtime  steps(n) -> complex64[n] — generate n samples standalone.

and neither said what the call raises, though the first ``steps()``/``step()``
builds the generator through ``bridge_fn`` and that can refuse -- as
``ValueError`` with the project's reason once gh-1307's ``bridge_error_fn`` is
declared, ``RuntimeError`` otherwise.

Both faces now render one declaration (``_composer._source_gen_members``)
through the numpy section builder object methods use, so the gate is gh-642's:
the runtime text IS the stub text, once the signature line is set aside.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from test_composer_codegen import _gen_cfg  # noqa: E402
from test_gh642_runtime_doc_parity import _runtime_doc, _stub_doc  # noqa: E402

from just_makeit import _composer  # noqa: E402

MEMBERS = ["steps", "step", "reset"]
_MOD = "wfm_compose"


def _cfg(error_fn: bool) -> dict:
    cfg = _gen_cfg()
    if error_fn:
        cfg["module"][_MOD]["source"]["generates"]["bridge_error_fn"] = (
            "wfm_source_why_not"
        )
    return cfg


def _faces(error_fn: bool, member: str) -> tuple[list[str], list[str]]:
    """(stub lines, runtime lines) for the SOURCE type's *member*."""
    cfg = _cfg(error_fn)
    tname = cfg["module"][_MOD]["source"]["type_name"]
    pyi = _composer.render_pyi(cfg, _MOD)
    cls = pyi[pyi.index(f"\nclass {tname}:") :]
    cls = cls[: cls.index("\nclass ", 1)]
    ext = _composer.render_ext(cfg, _MOD)
    table = ext[ext.index(f"{tname}_methods[] = {{") :]
    runtime = _runtime_doc(table, member)
    assert runtime[0].startswith(f"{member}("), runtime[0]
    return _stub_doc(cls, member), runtime[2:]


@pytest.mark.parametrize("error_fn", [False, True], ids=["plain", "why"])
@pytest.mark.parametrize("member", MEMBERS)
def test_the_runtime_text_is_the_stub_text(error_fn, member):
    stub, runtime = _faces(error_fn, member)
    assert [ln for ln in stub if ln.strip()] == [
        ln for ln in runtime if ln.strip()
    ]


@pytest.mark.parametrize("member", ["steps", "step"])
def test_a_build_names_what_it_raises(member):
    stub, _ = _faces(False, member)
    text = "\n".join(stub)
    assert "Raises" in text and "RuntimeError" in text
    assert "wfm_source_to_synth" in text


@pytest.mark.parametrize("member", ["steps", "step"])
def test_a_declared_reason_is_documented_as_value_error(member):
    """gh-1307's category, stated where the caller reads."""
    # Whitespace-normalised: the renderer wraps prose to the stub's width.
    without = " ".join(" ".join(_faces(False, member)[0]).split())
    with_fn = " ".join(" ".join(_faces(True, member)[0]).split())
    assert "the message is its reason" in with_fn
    assert "the message is its reason" not in without


def test_steps_documents_its_own_refusal_once():
    """`n < 0` and a refused build are both ValueError: one entry."""
    text = "\n".join(_faces(True, "steps")[0])
    assert text.count("ValueError") == 1
    assert "If `n` is negative." in text


def test_reset_raises_nothing():
    """It never builds -- it rewinds a generator that exists."""
    assert "Raises" not in "\n".join(_faces(True, "reset")[0])
