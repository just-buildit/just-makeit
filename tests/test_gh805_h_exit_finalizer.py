"""``exit`` — ``__exit__`` finalizes instead of freeing (gh-805 §H).

jm modelled teardown as ONE operation. ``destroy_py_names`` could give it
several Python names, but every one of them bound to the same
``<comp>_destroy``, and ``__exit__`` ran that same body. A C API whose
*finalize* and *free* are separable has two operations, and the context
manager wants the first: a capture's records and its drop verdict only become
valid once the tail is drained, so freeing at ``__exit__`` discards the object
at exactly the moment its results start to matter.

The load-bearing assertion is `TestHandleSurvives`: ``__exit__`` must NOT null
the handle, because that is the single line standing between the natural
Python (run the block, then read what you captured) and ``RuntimeError:
destroyed``.

`TestBothDocFacesMove` is the other half, and it is why this is a jm feature
rather than a downstream patch. ``__enter__``/``__exit__`` are 100% jm-owned
glue: a project that hand-edits the *behaviour* in its sacred fragment keeps
jm's *prose*, which is re-transplanted onto both faces at every apply. A
doc-parity gate compares the two faces against each other, so both carrying
the same wrong sentence stays green — the silent-wrong class.

`TestByteIdenticalWhenUndeclared` is the guard rail: every existing manifest
must render exactly as before, since these are slots that already ship.
"""

import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    # The same shim `_config` and the gh-844 test use. `tomllib` is stdlib
    # only from 3.11, so importing it unconditionally passes a local 3.12 run
    # and fails the 3.9/3.10 matrix legs — which is exactly how this landed
    # here, one day after the identical break was fixed in the gh-844 test.
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._context._destroy import (
    make_destroy_ctx,
    validate_destroy_spec,
)
from just_makeit._gluedoc import glue_methods

# A finalizer shaped like doppler's dp_tlm_capture_close: fallible, with its
# own declared exception and message. __exit__ must inherit all three rather
# than re-declare them -- gh-541 is the bug where the explicit call and the
# context manager disagreed about raising.
_CLOSE = {
    "name": "close",
    "fn": "cap_close",
    "status_return": True,
    "error": "ValueError",
    "error_message": "the capture has a hole: records were dropped",
}
_SPEC = {"returns": "int", "error": "ValueError", "exit": "close"}


def _ctx(spec=None, methods=None):
    return make_destroy_ctx("cap", "CapObj", spec, methods)


class TestValidation:
    """A misdeclared ``exit`` fails at generation time, not at link time."""

    def test_unknown_method_is_refused(self):
        with pytest.raises(ValueError, match="not a declared method"):
            validate_destroy_spec("cap", {"exit": "flush"}, [_CLOSE])

    def test_naming_the_teardown_itself_is_refused(self):
        # Pointing `exit` at the teardown is a no-op dressed as a feature: it
        # is what __exit__ already calls. Refused so the author finds out now
        # rather than wondering why nothing changed.
        with pytest.raises(ValueError, match="names the teardown itself"):
            validate_destroy_spec("cap", {"exit": "destroy"}, [_CLOSE])

    def test_alias_of_the_teardown_is_refused(self):
        spec = {"name": "close", "aliases": ["destroy"], "exit": "destroy"}
        with pytest.raises(ValueError, match="names the teardown itself"):
            validate_destroy_spec("cap", spec, [_CLOSE])

    def test_non_identifier_is_refused(self):
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            validate_destroy_spec("cap", {"exit": "1bad"}, [_CLOSE])

    def test_render_without_the_method_list_raises(self):
        # The alternative -- falling back to the destroy body -- would emit a
        # binding that frees while both doc faces say it finalizes. Loud beats
        # silent for exactly the failure this key exists to remove.
        with pytest.raises(ValueError, match="did not supply the method list"):
            _ctx(_SPEC, None)


class TestHandleSurvives:
    """``__exit__`` finalizes; the object stays usable afterwards."""

    def test_exit_calls_the_finalizer_not_destroy(self):
        body = _ctx(_SPEC, [_CLOSE])["destroy_exit_body"]
        assert "cap_close(self->handle)" in body
        assert "cap_destroy" not in body

    def test_exit_does_not_clear_the_handle(self):
        # THE assertion. `self->handle = NULL` here is what turns every
        # post-block read into RuntimeError: destroyed.
        body = _ctx(_SPEC, [_CLOSE])["destroy_exit_body"]
        assert "self->handle = NULL" not in body

    def test_destroy_still_clears_the_handle(self):
        # The finalizer must not weaken the teardown: destroy still releases
        # and still nulls, so a second call is a no-op rather than a double
        # free (gh-541).
        ctx = _ctx(_SPEC, [_CLOSE])
        assert "self->handle = NULL" in ctx["destroy_method_body"]
        assert "cap_destroy" in ctx["destroy_method_body"]

    def test_dealloc_still_frees(self):
        # tp_dealloc is unchanged and is now the ONLY free, so the memory is
        # released exactly once whether or not the with block ran.
        assert "cap_destroy" in _ctx(_SPEC, [_CLOSE])["destroy_dealloc_call"]

    def test_exit_inherits_the_finalizers_exception(self):
        body = _ctx(_SPEC, [_CLOSE])["destroy_exit_body"]
        assert "PyExc_ValueError" in body
        assert "the capture has a hole" in body

    def test_plain_finalizer_needs_no_status(self):
        body = _ctx({"exit": "flush"}, [{"name": "flush"}])[
            "destroy_exit_body"
        ]
        assert "(void)cap_flush(self->handle)" in body
        assert "PyErr" not in body


class TestBothDocFacesMove:
    """The prose follows the CALL, on the fragment and the stub alike."""

    @pytest.mark.parametrize("face", ["cm_exit_doc", "pyi_exit_doc"])
    def test_exit_doc_names_the_finalizer(self, face):
        assert "close()" in _ctx(_SPEC, [_CLOSE])[face]

    @pytest.mark.parametrize("face", ["cm_exit_doc", "pyi_exit_doc"])
    def test_exit_doc_does_not_promise_release(self, face):
        # "releasing the Cap" over a body that finalizes is the silent-wrong
        # this feature removes -- and a doc-parity gate cannot catch it,
        # because both faces would carry the identical wrong word.
        doc = _ctx(_SPEC, [_CLOSE])[face]
        assert "finalizing the Cap" in doc
        assert "releasing the Cap" not in doc

    @pytest.mark.parametrize("face", ["pyi_enter_doc", "cm_enter_doc"])
    def test_enter_doc_agrees(self, face):
        assert "finalized deterministically" in _ctx(_SPEC, [_CLOSE])[face]

    def test_stub_says_the_object_survives(self):
        # The reader at the REPL needs to know they may still use it.
        doc = _ctx(_SPEC, [_CLOSE])["pyi_exit_doc"]
        assert "not** released" in doc or "not released" in doc
        assert "stays usable" in doc

    def test_finalizes_flag_is_independent_of_the_name(self):
        # `close_name` answers "what is called", `finalizes` answers "what
        # survives". A name cannot carry the second -- an object whose
        # teardown is merely NAMED close still releases.
        named = glue_methods("Cap", close_name="close")["__exit__"]
        final = glue_methods("Cap", close_name="close", finalizes=True)[
            "__exit__"
        ]
        assert "releasing the Cap" in named.block.brief
        assert "finalizing the Cap" in final.block.brief


class TestByteIdenticalWhenUndeclared:
    """No `exit` -> every slot renders exactly as it did before gh-805 §H."""

    @pytest.mark.parametrize("spec", [None, {}, {"returns": "int"}])
    def test_exit_body_is_the_destroy_body(self, spec):
        ctx = _ctx(spec, [_CLOSE])
        assert ctx["destroy_exit_body"] == ctx["destroy_method_body"]

    def test_docs_still_say_release(self):
        assert "releasing the Cap" in _ctx({"returns": "int"})["pyi_exit_doc"]

    def test_declaring_a_close_METHOD_alone_changes_nothing(self):
        # Guard against the tempting inference "there is a close method, so
        # __exit__ should call it". Only `exit` opts in.
        ctx = _ctx({"returns": "int"}, [_CLOSE])
        assert "cap_close" not in ctx["destroy_exit_body"]


class TestOneConditionOneDeclaration:
    """The teardown inherits the finalizer's error when it states none.

    Raised in review of #853. Once `exit` splits the two calls apart they are
    still ONE condition reached by two routes -- the finalizer latches the
    verdict, the destructor reports the same hole on the GC path. Left to the
    ordinary defaults the minimal adoption rendered a different exception
    CLASS and a different sentence for that one condition, so this was the
    out-of-the-box result rather than a drift risk.
    """

    _MSG = "the capture has a hole: records were dropped"

    def _bodies(self, spec):
        ctx = _ctx(spec, [_CLOSE])
        return ctx["destroy_exit_body"], ctx["destroy_method_body"]

    def test_minimal_adoption_agrees_on_class_and_text(self):
        exit_body, destroy_body = self._bodies(
            {"returns": "int", "exit": "close"}
        )
        for body in (exit_body, destroy_body):
            assert "PyExc_ValueError" in body
            assert self._MSG in body
        assert "reported failure" not in destroy_body

    def test_explicit_teardown_error_still_wins(self):
        # Saying something different on purpose stays possible.
        _, destroy_body = self._bodies(
            {
                "returns": "int",
                "exit": "close",
                "error": "OSError",
                "error_message": "my own words",
            }
        )
        assert "PyExc_OSError" in destroy_body
        assert "my own words" in destroy_body

    def test_declaring_either_key_keeps_both_explicit(self):
        # Both or neither: a half-inherited pair would pin someone else's
        # message under the author's category, which is a THIRD message
        # rather than one fewer.
        _, destroy_body = self._bodies(
            {"returns": "int", "exit": "close", "error": "OSError"}
        )
        assert "PyExc_OSError" in destroy_body
        assert self._MSG not in destroy_body

    def test_without_exit_the_generic_default_is_untouched(self):
        _, destroy_body = self._bodies({"returns": "int"})
        assert "PyExc_RuntimeError" in destroy_body
        assert "cap_destroy reported failure" in destroy_body

    def test_void_teardown_inherits_nothing(self):
        # No status to report, so there is no message to agree about.
        _, destroy_body = self._bodies({"exit": "close"})
        assert "PyErr" not in destroy_body


class TestApplyResolvesTheFinalizer:
    """`jm apply` must resolve `exit` -- gh-856.

    §H shipped with every unit-level assertion green and was still unreachable
    through the one command every project uses to render. `jm apply` REPLAYS
    into a half-built temp tree: the object is created before its methods are
    replayed, so the temp manifest legitimately has no `close` yet and `exit`
    resolved against nothing. The behaviour inverted -- an object with no
    methods rendered fine, and one declaring the finalizer `exit` exists to
    name was refused *because* it declared it.

    Nothing below touches make_destroy_ctx directly. That is the point: the
    unit tests all passed while this was broken, because they call the builder
    with a method list the real replay path never had.
    """

    @pytest.fixture()
    def applied(self, tmp_path):
        from just_makeit._apply import run as apply_run
        from just_makeit._method import run as method_run
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        dest = tmp_path / "cap"
        new_run("cap", dest, [], [])
        object_run(dest, "widget", None, arg_type="float", return_type="float")
        method_run(
            dest,
            "widget",
            "close",
            None,
            "void",
            "int",
            False,
            [],
            status_return=True,
            error="ValueError",
            error_message="the capture has a hole",
        )
        cfg = C.load(dest)
        C.set_destroy_spec(cfg, "widget", {"returns": "int", "exit": "close"})
        C.save(dest, cfg)
        apply_run(dest)  # must not raise
        return dest

    def _ext_c(self, root):
        return (root / "native" / "src" / "widget" / "widget_ext.c").read_text(
            encoding="utf-8"
        )

    def test_apply_succeeds(self, applied):
        assert self._ext_c(applied)

    def test_exit_calls_the_finalizer(self, applied):
        exit_fn = self._ext_c(applied).split("_exit(")[1].split("\n}")[0]
        assert "widget_close(self->handle)" in exit_fn
        assert "widget_destroy" not in exit_fn

    def test_exit_leaves_the_handle_set(self, applied):
        exit_fn = self._ext_c(applied).split("_exit(")[1].split("\n}")[0]
        assert "self->handle = NULL" not in exit_fn

    def test_stub_says_finalizing(self, applied):
        pyi = (applied / "src" / "cap" / "widget.pyi").read_text(
            encoding="utf-8"
        )
        assert "finalizing" in pyi


class TestApplyResolvesTheFinalizerForAModuleObject:
    """The same, for an object declared inside `[module.X]` -- gh-860.

    gh-856 wired the STANDALONE replay and its end-to-end test covered the
    standalone shape, so the module-object replay -- a separate loop in
    `_apply._replay`, reached through `_object.run(..., mod)` -- kept reading
    the half-built temp cfg. `exit` was therefore unusable for the more common
    shape: nearly every doppler object is a module object.

    "Reading ONE face misleads: test standalone AND module" is a rule this
    repo already had. gh-856's fix and its test both honoured the first half.
    """

    @pytest.fixture()
    def applied(self, tmp_path):
        from just_makeit._apply import run as apply_run
        from just_makeit._method import run as method_run
        from just_makeit._module import run as module_run
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        dest = tmp_path / "cap"
        new_run("cap", dest, [], [])
        module_run(dest, "tele")
        object_run(
            dest, "widget", "tele", arg_type="float", return_type="float"
        )
        method_run(
            dest,
            "widget",
            "close",
            "tele",
            "void",
            "int",
            False,
            [],
            status_return=True,
            error="ValueError",
            error_message="the capture has a hole",
        )
        cfg = C.load(dest)
        C.set_destroy_spec(cfg, "widget", {"returns": "int", "exit": "close"})
        C.save(dest, cfg)
        apply_run(dest)  # must not raise
        return dest

    def _frag(self, root):
        return (
            root / "native" / "src" / "tele" / "tele_ext_widget.c"
        ).read_text(encoding="utf-8")

    def test_module_exit_calls_the_finalizer(self, applied):
        exit_fn = self._frag(applied).split("_exit(")[1].split("\n}")[0]
        assert "widget_close(self->handle)" in exit_fn
        assert "widget_destroy" not in exit_fn

    def test_module_exit_leaves_the_handle_set(self, applied):
        exit_fn = self._frag(applied).split("_exit(")[1].split("\n}")[0]
        assert "self->handle = NULL" not in exit_fn


class TestManifestRoundTrip:
    """The key survives the serializer -- an unnamed key is silently lost."""

    def test_dump_emits_and_tomllib_reads_back(self):
        cfg = {"cap": {"destroy": dict(_SPEC), "methods": [dict(_CLOSE)]}}
        rt = tomllib.loads(C._dump(cfg))
        assert rt["cap"]["destroy"]["exit"] == "close"

    def test_reader_returns_empty_when_undeclared(self):
        assert (
            C.destroy_exit({"cap": {"destroy": {"returns": "int"}}}, "cap")
            == ""
        )

    def test_set_destroy_spec_preserves_it(self):
        cfg = {"cap": {}}
        C.set_destroy_spec(cfg, "cap", dict(_SPEC))
        assert C.destroy_exit(cfg, "cap") == "close"
