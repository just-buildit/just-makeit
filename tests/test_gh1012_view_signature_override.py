"""A view method may override a parent's SIGNATURE, not only its doc.

gh-504 gave a view two ways to declare a method: ADD a name the parent lacks
(which scaffolds a shared C stub), or restate a parent's name to change its
doc. A third shape was expressible and did neither — a restated name carrying
a *different* ``arg_type`` was accepted, written to the manifest, and then
discarded, because the replay copied the parent's entry wholesale and kept
only ``doc``. Both faces bound the parent's dtype and nothing was printed
(gh-1011).

gh-1012 is the feature that shape was reaching for: two objects that differ in
one method's dtype and nothing else. The resolution makes ``fn`` the
discriminator, and that is not a convenience —

    the parent's C symbol has the parent's prototype, so a different signature
    is only callable through a different symbol.

Declaring one IS declaring the other, which is why a single key can carry the
distinction without being a flag standing in for a second question.

So there are three behaviours, and this file pins all three because the middle
one is what regressed:

===========================  ===========================================
declaration                  outcome
===========================  ===========================================
same name, ``fn``            signature override — its own dtype and its
                             own C function (gh-1012)
same name, differing sig,    **error** naming ``--fn`` (gh-1011; was
no ``fn``                    silently ignored)
same name, same signature    doc-only override, shares the parent's
                             symbol (gh-504, unchanged)
===========================  ===========================================
"""

from __future__ import annotations

import pytest

from just_makeit import _config as C
from just_makeit import _view
from just_makeit._keys import METHOD_SIGNATURE_KEYS
from just_makeit._method import _SIGNATURE_COERCIONS
from just_makeit._method import _signature_differences
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._status import run as status_run


def _base(tmp_path):
    """A module object with a complex `steps` and a view over it."""
    dest = tmp_path / "demo"
    new_run("demo", dest, [], [], build_system="cmake")
    module_run(dest, "dsp")
    object_run(
        dest,
        "rx",
        module="dsp",
        state_vars=[("sps", "double", "8.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    method_run(
        dest,
        "rx",
        "block",
        "dsp",
        "float _Complex",
        "float _Complex",
        True,
        [],
    )
    _view.run(dest, "rx", "RxReal", "dsp", "rx_create_real")
    return dest


def _pyi(dest):
    return (dest / "src" / "demo" / "dsp" / "dsp.pyi").read_text(
        encoding="utf-8"
    )


def _faces(dest):
    """The parent's and the view's slice of the module stub."""
    text = _pyi(dest)
    return (
        text[text.index("class Rx") : text.index("class RxReal")],
        text[text.index("class RxReal") :],
    )


# ── gh-1012: the override is honoured ───────────────────────────────────────


@pytest.fixture()
def override_project(tmp_path):
    dest = _base(tmp_path)
    method_run(
        dest,
        "rx",
        "block",  # the PARENT's name, deliberately
        "dsp",
        "float",  # ...with its own input dtype
        "float _Complex",
        True,
        [],
        fn="rx_block_real",  # ...carried by its own C symbol
        view="RxReal",
    )
    return dest


class TestSignatureOverride:
    def test_view_face_takes_its_own_dtype(self, override_project):
        _, view = _faces(override_project)
        assert "x: NDArray[np.float32]" in view

    def test_parent_face_is_untouched(self, override_project):
        parent, _ = _faces(override_project)
        assert "x: NDArray[np.complex64]" in parent
        assert "np.float32" not in parent

    def test_both_faces_keep_the_same_python_name(self, override_project):
        parent, view = _faces(override_project)
        # The whole point: one name, two signatures. A rename would be the
        # workaround this feature exists to avoid.
        assert "def block(" in parent and "def block(" in view

    def test_view_binding_calls_its_own_symbol(self, override_project):
        frag = (
            override_project / "native" / "src" / "dsp" / "dsp_ext_rxreal.c"
        ).read_text(encoding="utf-8")
        assert "rx_block_real(" in frag
        # and converts the input as float, not complex
        assert "NPY_FLOAT," in frag

    def test_parent_binding_still_calls_the_shared_symbol(
        self, override_project
    ):
        frag = (
            override_project / "native" / "src" / "dsp" / "dsp_ext_rx.c"
        ).read_text(encoding="utf-8")
        assert "rx_block(" in frag
        assert "rx_block_real" not in frag

    def test_both_prototypes_reach_the_shared_header(self, override_project):
        h = (
            override_project / "native" / "inc" / "rx" / "rx_core.h"
        ).read_text(encoding="utf-8")
        assert "rx_block(rx_state_t *state, const float _Complex *" in h
        assert "rx_block_real(rx_state_t *state, const float *" in h

    def test_the_stub_is_scaffolded_into_the_shared_core(
        self, override_project
    ):
        c = (
            override_project / "native" / "src" / "rx" / "rx_core.c"
        ).read_text(encoding="utf-8")
        assert "rx_block_real" in c

    def test_round_trips_through_the_manifest(self, override_project):
        cfg = C.load(override_project)
        view = C.views(cfg, "rx")[0]
        entry = next(m for m in C.view_methods(view) if m["name"] == "block")
        # Both halves must survive: the dtype AND the symbol carrying it.
        assert entry["arg_type"] == "float"
        assert entry["fn"] == "rx_block_real"

    def test_it_does_not_warn_about_the_parents_declaration(
        self, tmp_path, capsys
    ):
        """The gh-137 safety net must read `fn`, not the derived name.

        That net warns when the symbol a `variable_output` method is about to
        declare is already in the header, and it derived the symbol from the
        method NAME. Harmless while every `fn` named something nothing else
        used — and wrong by construction here, because a signature override
        exists precisely to reuse a name whose `<obj>_<name>` is taken. It
        fired on the parent's own declaration every time and advised removing
        a capacity parameter that was never there.
        """
        dest = _base(tmp_path)
        capsys.readouterr()
        method_run(
            dest,
            "rx",
            "block",
            "dsp",
            "float",
            "float _Complex",
            True,
            [],
            fn="rx_block_real",
            view="RxReal",
        )
        assert "already declared" not in capsys.readouterr().err

    def test_status_check_is_clean(self, override_project):
        # The acceptance test for the whole round-trip — apply's replay must
        # reproduce the view fragment and the stub byte-for-byte.
        assert status_run(override_project, check=True) == 0


# ── gh-1011: the silent case now refuses ────────────────────────────────────


class TestCollidingWithoutFn:
    def test_a_differing_signature_without_fn_is_refused(
        self, tmp_path, capsys
    ):
        dest = _base(tmp_path)
        with pytest.raises(SystemExit) as exc:
            method_run(
                dest,
                "rx",
                "block",
                "dsp",
                "float",  # differs from the parent
                "float _Complex",
                True,
                [],
                view="RxReal",  # ...and no fn to carry it
            )
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "arg_type" in err  # names WHAT differs
        assert "--fn" in err  # names the way forward

    def test_the_refusal_happens_before_anything_is_written(self, tmp_path):
        dest = _base(tmp_path)
        before = _pyi(dest)
        with pytest.raises(SystemExit):
            method_run(
                dest,
                "rx",
                "block",
                "dsp",
                "float",
                "float _Complex",
                True,
                [],
                view="RxReal",
            )
        # A refused declaration must leave no half-made tree, and no manifest
        # entry that a later `apply` would resurrect.
        assert _pyi(dest) == before
        cfg = C.load(dest)
        assert C.view_methods(C.views(cfg, "rx")[0]) == []

    def test_fn_equal_to_the_parents_symbol_is_refused(self, tmp_path, capsys):
        dest = _base(tmp_path)
        with pytest.raises(SystemExit):
            method_run(
                dest,
                "rx",
                "block",
                "dsp",
                "float",
                "float _Complex",
                True,
                [],
                fn="rx_block",  # the symbol the parent already binds
                view="RxReal",
            )
        # Left to the compiler this is a conflicting redefinition; jm should
        # say so in its own terms.
        assert "already binds" in capsys.readouterr().err


# ── gh-504: the doc-only override must still work ───────────────────────────


class TestDocOnlyOverrideStillWorks:
    @pytest.fixture()
    def doc_override(self, tmp_path):
        dest = _base(tmp_path)
        method_run(
            dest,
            "rx",
            "block",
            "dsp",
            "float _Complex",  # the parent's signature, restated
            "float _Complex",
            True,
            [],
            doc="real-side block",
            view="RxReal",
        )
        return dest

    def test_the_doc_reaches_the_view_only(self, doc_override):
        parent, view = _faces(doc_override)
        assert "real-side block" in view
        assert "real-side block" not in parent

    def test_it_still_shares_the_parents_symbol(self, doc_override):
        frag = (
            doc_override / "native" / "src" / "dsp" / "dsp_ext_rxreal.c"
        ).read_text(encoding="utf-8")
        assert "rx_block(" in frag
        assert "rx_block_real" not in frag

    def test_the_view_keeps_the_parents_dtype(self, doc_override):
        _, view = _faces(doc_override)
        assert "x: NDArray[np.complex64]" in view

    def test_an_omitted_key_is_not_a_difference(self, tmp_path):
        """The trap this fix had to avoid, and nearly did not.

        A doc-only override reaches `run` with `params=None` while the
        parent's manifest has no `params` key at all. Two spellings of
        "absent", and comparing them naively reports a difference that is not
        one — which refused every doc-only override on the first cut of this
        change, with the suite catching it.
        """
        parent = {"name": "block", "arg_type": "float _Complex"}
        assert (
            _signature_differences(
                parent,
                arg_type="float _Complex",
                params=None,
                result_fields=None,
                out_type=None,
                nogil=False,
                multi_output=[],
            )
            == []
        )


# ── the paths the first cut did not cover ───────────────────────────────────


class TestHandWrittenManifest:
    """`jm apply` over a hand-edited manifest — how both issues were filed.

    The first cut of this fix passed every test above and still refused a
    hand-written doc-only override, because these tests drive `_method.run`
    directly, where every argument is explicit. `_apply._replay_method`
    substitutes `run`'s defaults for absent manifest keys BEFORE `run` sees
    them, so absence had already become `arg_type="void"` by the time anything
    compared it. A whole class fixed on one path and live on the other.
    """

    def test_doc_only_override_from_a_hand_written_block(self, tmp_path):
        dest = _base(tmp_path)
        cfg_path = dest / "just-makeit.toml"
        cfg_path.write_text(
            cfg_path.read_text(encoding="utf-8")
            + '\n[[rx.views.methods]]\nname = "block"\n'
            'doc = "Block, on a real IF."\n',
            encoding="utf-8",
        )
        from just_makeit import _apply

        _apply.run(dest)  # must not raise SystemExit
        _, view = _faces(dest)
        assert "Block, on a real IF." in view
        # ...and the signature it never mentioned is the parent's.
        assert "x: NDArray[np.complex64]" in view

    def test_signature_override_from_a_hand_written_block(self, tmp_path):
        dest = _base(tmp_path)
        cfg_path = dest / "just-makeit.toml"
        cfg_path.write_text(
            cfg_path.read_text(encoding="utf-8")
            + '\n[[rx.views.methods]]\nname = "block"\n'
            'arg_type = "float"\nreturn_type = "float _Complex"\n'
            'variable_output = true\nfn = "rx_block_real"\n',
            encoding="utf-8",
        )
        from just_makeit import _apply

        _apply.run(dest)
        _, view = _faces(dest)
        assert "x: NDArray[np.float32]" in view

    def test_a_differing_signature_without_fn_still_refuses(self, tmp_path):
        dest = _base(tmp_path)
        cfg_path = dest / "just-makeit.toml"
        cfg_path.write_text(
            cfg_path.read_text(encoding="utf-8")
            + '\n[[rx.views.methods]]\nname = "block"\n'
            'arg_type = "float"\n',
            encoding="utf-8",
        )
        from just_makeit import _apply

        with pytest.raises(SystemExit):
            _apply.run(dest)


class TestCliPath:
    """Driven through `_cli_method.run`, where the DEFAULTS are the hazard.

    This parser fills `arg_type="void"` and `return_type="float _Complex"`
    whether or not the caller typed them, so a `--doc`-only override arrives
    looking like a deliberate signature change. Nothing exercised this path in
    the first cut, and codecov said so.
    """

    def test_doc_only_override_needs_no_signature_flags(
        self, tmp_path, monkeypatch
    ):
        dest = _base(tmp_path)
        monkeypatch.chdir(dest)
        from just_makeit import _cli_method

        _cli_method.run(
            [
                "rx",
                "block",
                "--module",
                "dsp",
                "--view",
                "RxReal",
                "--doc",
                "Block, real IF.",
            ]
        )
        _, view = _faces(dest)
        assert "Block, real IF." in view
        assert "x: NDArray[np.complex64]" in view

    def test_a_stated_signature_without_fn_is_still_refused(
        self, tmp_path, monkeypatch
    ):
        dest = _base(tmp_path)
        monkeypatch.chdir(dest)
        from just_makeit import _cli_method

        with pytest.raises(SystemExit):
            _cli_method.run(
                [
                    "rx",
                    "block",
                    "--module",
                    "dsp",
                    "--view",
                    "RxReal",
                    "--arg-type",
                    "float",
                ]
            )

    def test_the_override_works_through_the_cli(self, tmp_path, monkeypatch):
        dest = _base(tmp_path)
        monkeypatch.chdir(dest)
        from just_makeit import _cli_method

        _cli_method.run(
            [
                "rx",
                "block",
                "--module",
                "dsp",
                "--view",
                "RxReal",
                "--arg-type",
                "float",
                "--return-type",
                "float _Complex",
                "--variable-output",
                "--fn",
                "rx_block_real",
            ]
        )
        _, view = _faces(dest)
        assert "x: NDArray[np.float32]" in view


# ── the gate: a new signature key cannot go uncompared ──────────────────────


def test_every_signature_key_has_a_comparison():
    """`_SIGNATURE_COERCIONS` must cover `METHOD_SIGNATURE_KEYS` exactly.

    Registration-free in the direction that matters: `METHOD_SIGNATURE_KEYS`
    is derived from `METHOD_KEYS` by subtraction, so adding a method key makes
    it a signature key by default and fails this test until someone says how
    it compares. The alternative — a key nobody compares — is exactly gh-1011:
    a declared difference that reaches no face and no diagnostic.

    `extra_args` is the one exclusion, and it is not a waiver: `_replay_method`
    funnels it into `params`, so comparing both would ask one question twice.
    """
    compared = set(_SIGNATURE_COERCIONS)
    assert METHOD_SIGNATURE_KEYS - compared - {"extra_args"} == set()
    assert compared - METHOD_SIGNATURE_KEYS == set()


def test_signature_keys_exclude_the_ones_that_are_not_the_call():
    """`doc` and `fn` must stay OUT of the comparison.

    `doc` is the whole point of the override that must keep working, and `fn`
    is the declaration that a separate signature exists — including it would
    make every signature override differ from itself and refuse the feature
    this file exists to add.
    """
    assert "doc" not in METHOD_SIGNATURE_KEYS
    assert "fn" not in METHOD_SIGNATURE_KEYS
    assert "arg_type" in METHOD_SIGNATURE_KEYS
