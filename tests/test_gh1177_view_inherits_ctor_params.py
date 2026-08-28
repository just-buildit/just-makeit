"""gh-1177: a view's PARAMS inherit from the parent's create(); its SUMMARY does not.

A 0.70.0 regression, reported from doppler within the hour of the release and
measured there as **22 init-param descriptions** replaced by name stubs on a
pin bump with no other change.

gh-1160 gave a view's stub overlay its own ``create_fn``, so the class summary
would stop wrongly inheriting the parent's. That was right, and it took the
parameters with it: before, the lookup resolved ``<synth>_create``, which
`_view_doc_blocks` aliases to the parent's block -- so a view inherited the
parent's ``@param`` prose *and* (wrongly) its summary. Pointing `create_fn` at
the view's own constructor fixed the summary and, on every view whose
constructor carries no Doxygen, emptied the ``Parameters`` section.

The rule that resolves it is asymmetric, and the asymmetry is the point:

* **Params inherit.** A view and its parent take the same argument list --
  that is what makes a view a view -- so the parent's ``@param`` prose
  describes the view's parameters exactly.
* **The summary does not.** What the class *is* differs; that is gh-648 and
  gh-1160, and doppler's report confirms it as a real gain in the same bump
  (``BpskReceiver`` stopped claiming to be an M-PSK receiver).

`inherit_ctor_params` is one function because BOTH faces need it and they are
built by different modules -- `_stubs` from an overlay cfg, `_object` from the
raw blocks. The first cut of this fix applied it in `_stubs` only, which left
the `.pyi` carrying the inherited prose while `tp_doc` still said
``m constructor parameter.``: gh-642's two-faces-disagree bug, reintroduced by
its own repair. `TestBothFaces` is what holds that.

The second cut then had the runtime gate on the *brief* alone, so a view with
documented params and no ``@brief`` rendered the bare ``"<Class> type."``
while the stub showed a full block. Both faces now decide from the same
question: is anything authored.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


SRC = Path(__file__).parent.parent / "src"

PARAM_PROSE = "HEADER_PARAM_PROSE: constellation order."


def _cli(*args, cwd) -> subprocess.CompletedProcess:
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


def _build(tmp_path: Path, *, view_brief: str = "") -> Path:
    """A module + object with a documented `create()`, plus a view.

    The parent's ``@brief`` is replaced too: `_load_doc_blocks` filters jm's
    own scaffold boilerplate, so a block still carrying the seeded
    "Create a <x> instance." is discarded whole -- including its ``@param``.
    A fixture that edits only the ``@param`` reproduces nothing, at any
    version. That cost a first repro attempt here.
    """
    assert _cli("new", "d", cwd=tmp_path).returncode == 0
    root = tmp_path / "d"
    assert _cli("module", "tr", cwd=root).returncode == 0
    assert (
        _cli(
            "object",
            "rx",
            "--module",
            "tr",
            "--init-param",
            "m:int:4",
            cwd=root,
        ).returncode
        == 0
    )
    h = root / "native" / "inc" / "rx" / "rx_core.h"
    s = h.read_text(encoding="utf-8")
    assert " * @brief Create a rx instance." in s, s
    s = s.replace(
        " * @brief Create a rx instance.", " * @brief AUTHORED: build one."
    )
    s = s.replace(
        " * @param m  m (default: 4).", f" * @param m  {PARAM_PROSE}"
    )
    h.write_text(s, encoding="utf-8")
    assert (
        _cli(
            "view",
            "rx",
            "RxR",
            "--module",
            "tr",
            "--create-fn",
            "rx_create_r",
            cwd=root,
        ).returncode
        == 0
    )
    if view_brief:
        s = h.read_text(encoding="utf-8")
        decl = re.search(r"^rx_state_t \*rx_create_r.*$", s, re.M)
        assert decl, s
        h.write_text(
            s.replace(
                decl.group(0),
                f"/**\n * @brief {view_brief}\n */\n" + decl.group(0),
                1,
            ),
            encoding="utf-8",
        )
    assert _cli("apply", cwd=root).returncode == 0
    return root


def _stub_class(root: Path, name: str) -> str:
    pyi = (root / "src" / "d" / "tr" / "tr.pyi").read_text(encoding="utf-8")
    body = pyi[pyi.index(f"class {name}") :]
    return body[: body.index("def __init__")]


def _tp_doc(root: Path, frag: str) -> str:
    src = (root / "native" / "src" / "tr" / frag).read_text(encoding="utf-8")
    i = src.index(".tp_doc")
    return src[i : src.index("\n    .tp_", i + 1)]


class TestParamsInherit:
    def test_the_view_keeps_the_parents_param_prose(
        self, tmp_path: Path
    ) -> None:
        """The regression itself: this read `m constructor parameter.`."""
        root = _build(tmp_path)
        cls = _stub_class(root, "RxR")
        assert PARAM_PROSE in cls, cls
        assert "m constructor parameter." not in cls, cls

    def test_the_parent_is_unaffected(self, tmp_path: Path) -> None:
        root = _build(tmp_path)
        assert PARAM_PROSE in _stub_class(root, "Rx")


class TestTheSummaryDoesNotInherit:
    """The gh-648/gh-1160 gain this must not undo."""

    def test_an_undocumented_view_does_not_borrow_the_parents_brief(
        self, tmp_path: Path
    ) -> None:
        root = _build(tmp_path)
        cls = _stub_class(root, "RxR")
        assert "AUTHORED: build one." not in cls, cls
        assert cls.splitlines()[1].strip().startswith('"""RxR'), cls

    def test_a_documented_view_keeps_its_own_brief(
        self, tmp_path: Path
    ) -> None:
        root = _build(tmp_path, view_brief="VIEW_OWN_BRIEF: the R flavour.")
        cls = _stub_class(root, "RxR")
        assert "VIEW_OWN_BRIEF: the R flavour." in cls
        assert "AUTHORED: build one." not in cls
        # ...and still inherits the params it does not document itself.
        assert PARAM_PROSE in cls


class TestBothFaces:
    """One merge, read by two modules. Applying it in one is the bug."""

    def test_the_runtime_carries_the_inherited_prose(
        self, tmp_path: Path
    ) -> None:
        root = _build(tmp_path)
        doc = _tp_doc(root, "tr_ext_rxr.c")
        assert PARAM_PROSE in doc, doc
        assert "m constructor parameter." not in doc, doc

    def test_the_parameters_section_agrees_on_both(
        self, tmp_path: Path
    ) -> None:
        """The section this fix owns, compared line for line.

        Deliberately NOT the whole docstring. On a MODULE object the runtime
        fragment is rendered from `apply`'s temp replay tree, whose header
        carries only jm's scaffold Doxygen -- so a `@brief` added to the real
        header reaches the `.pyi` and not `tp_doc`. That divergence is gh-1172
        (no post-replay re-render on the module path; the standalone path got
        one in gh-1165), it predates this fix, and asserting it here would be
        testing that bug rather than this one.

        The params come through on both because they are inherited from the
        PARENT's `create()`, which the scaffold does document -- which is also
        why the regression was visible on both faces to begin with.
        """
        root = _build(tmp_path, view_brief="VIEW_OWN_BRIEF: the R flavour.")
        doc = _tp_doc(root, "tr_ext_rxr.c")
        cls = _stub_class(root, "RxR")
        params = cls[cls.index("Parameters") : cls.index("Examples")]
        for line in (ln.strip() for ln in params.splitlines() if ln.strip()):
            assert line in doc, f"{line!r} missing from tp_doc:\n{doc}"


class TestZeroChurn:
    def test_a_view_with_nothing_authored_keeps_the_BARE_fallback(
        self, tmp_path: Path
    ) -> None:
        """`_docsync` refreshes a `tp_doc` only while `_is_generic_tp_doc`
        recognises it, so widening the runtime gate must not turn the bare
        placeholder into a full block for a view that documents nothing."""
        assert _cli("new", "e", cwd=tmp_path).returncode == 0
        root = tmp_path / "e"
        assert _cli("module", "tr", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "rx",
                "--module",
                "tr",
                "--state",
                "g:double:1.0",
                cwd=root,
            ).returncode
            == 0
        )
        assert (
            _cli(
                "view",
                "rx",
                "RxR",
                "--module",
                "tr",
                "--create-fn",
                "rx_create_r",
                cwd=root,
            ).returncode
            == 0
        )
        assert _cli("apply", cwd=root).returncode == 0
        rt = (root / "native" / "src" / "tr" / "tr_ext_rxr.c").read_text(
            encoding="utf-8"
        )
        assert '.tp_doc       = "RxR type.\\n",' in rt, rt[:400]

        from just_makeit import _docsync

        assert _docsync._is_generic_tp_doc('"RxR type.\\n"')

    def test_status_check_is_clean_and_apply_idempotent(
        self, tmp_path: Path
    ) -> None:
        root = _build(tmp_path)
        assert _cli("status", "--check", cwd=root).returncode == 0
        before = _stub_class(root, "RxR"), _tp_doc(root, "tr_ext_rxr.c")
        assert _cli("apply", cwd=root).returncode == 0
        assert (_stub_class(root, "RxR"), _tp_doc(root, "tr_ext_rxr.c")) == (
            before
        )


class TestTheMergeItself:
    def test_a_view_param_of_its_own_wins(self) -> None:
        """The specific block beats the inherited one, as a trailing member
        doc beats a leading one."""
        from just_makeit._docstring import DoxyBlock, inherit_ctor_params

        par = DoxyBlock(brief="Parent.", params=[("m", "Parent m.")])
        own = DoxyBlock(brief="Own.", params=[("m", "Own m.")])
        out = inherit_ctor_params(
            {"o_create": par, "o_create_r": own}, "o_create_r", "o_create"
        )
        assert out["o_create_r"].params == [("m", "Own m.")]

    def test_the_inherited_block_carries_no_brief(self) -> None:
        """What stops the summary inheriting: params only, so
        `authored_class_brief` still finds nothing."""
        from just_makeit._docstring import DoxyBlock, inherit_ctor_params

        par = DoxyBlock(brief="Parent.", params=[("m", "Parent m.")])
        out = inherit_ctor_params({"o_create": par}, "o_create_r", "o_create")
        assert out["o_create_r"].params == [("m", "Parent m.")]
        assert out["o_create_r"].brief == ""

    def test_nothing_to_inherit_returns_the_same_object(self) -> None:
        """The common case allocates nothing."""
        from just_makeit._docstring import DoxyBlock, inherit_ctor_params

        # No parent block at all.
        blocks = {"other": DoxyBlock(brief="x")}
        assert inherit_ctor_params(blocks, "a", "b") is blocks

        # A parent block that documents no params.
        blocks = {"b": DoxyBlock(brief="Parent, no params.")}
        assert inherit_ctor_params(blocks, "a", "b") is blocks
