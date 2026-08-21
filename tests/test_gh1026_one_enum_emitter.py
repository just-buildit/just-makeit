"""gh-1026: one emitter for the enum-index C, and it names the choices.

jm emitted "validate a choice string to its `[[enum]]` int" in **four** places
— a module-function param, a handle create-arg, a composer serializer param,
and an object property / method param. The lookup *body* was already shared;
the **tables** and the **call sites** were not, which is the half that drifted.

gh-1021 gave method parameters the property path's message:

    ValueError: invalid kind 'nope' (choices: none, rs, conv)

while the module-function path — the same feature on a different surface —
still said only `invalid sample_type 'nope'`. One manifest, two wordings of one
refusal, decided by nothing a user could see. That is the cost of the
duplication, not anyone's decision, and it is what `TestEveryFaceNamesTheChoices`
locks down.

The gate below is the registration-free half, and it is deliberately not "four
call sites use `_enumc`". It scans every generator for a `PyErr_Format` that
refuses an enum choice and requires each one to route through the shared
emitter — so a FIFTH face added tomorrow is measured with nothing to register
here. A count would have passed the whole time this bug existed: there were
four sites, and four was correct.

**The fifth spelling is knowingly out of scope**, and asserted as such rather
than left to be rediscovered. An `init_param`'s `type = "enum:<name>"` flattens
through `C.resolve_enum_type` to `string_enum:a,b,c` and emits an inline
`strcmp` chain — no table, no lookup, and by then the enum's *name* is gone, so
it cannot name the choices even in principle. Folding it in is a feature (teach
`init_params` the `enum` key, or unflatten the type string), not a
de-duplication, and doing it inside this change would hide it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _enumc  # noqa: E402

SRC = Path(__file__).parent.parent / "src" / "just_makeit"

#: Every module that emits CPython glue. Enumerated by scanning for the
#: emitter's own fingerprint rather than listed, so a new generator is covered.
_GENERATORS = sorted(
    p
    for p in list(SRC.glob("*.py")) + list(SRC.glob("_context/*.py"))
    if p.name != "_enumc.py"
)


class TestTheEmitter:
    def test_bare_symbols_for_the_module_scoped_faces(self):
        assert _enumc.symbols("", "kind") == ("_enum_index", "_enum_kind")

    def test_namespaced_symbols_for_a_type(self):
        """A module's `_ext.c` includes every object's fragment into ONE
        translation unit, and a view adds a second type over the same
        component — so two types there may reference the same `[[enum]]`."""
        assert _enumc.symbols("Acq", "kind") == (
            "_enum_index_Acq",
            "_enum_Acq_kind",
        )

    def test_the_two_namespaces_cannot_collide(self):
        bare = set(_enumc.symbols("", "kind"))
        scoped = set(_enumc.symbols("Acq", "kind"))
        assert not (bare & scoped)

    def test_the_table_order_is_the_declaration_order(self):
        """Order IS the C int — the `[[enum]]` SSOT contract, and the reason
        that list is append-only. A table that sorted its choices would
        silently renumber every persisted value."""
        out = _enumc.render_tables(["k"], {"k": ["z", "a", "m"]})
        assert '    "z",\n    "a",\n    "m",\n    NULL,' in out

    def test_choices_are_named(self):
        assert _enumc.choices_suffix("k", {"k": ["a", "b"]}) == (
            " (choices: a, b)"
        )

    @pytest.mark.parametrize("registry", [None, {}, {"k": []}])
    def test_an_absent_registry_drops_the_suffix(self, registry):
        """`jm bind` has no manifest to read `[[enum]]` from, and
        `(choices: )` would be worse than no suffix at all."""
        assert _enumc.choices_suffix("k", registry) == ""

    def test_the_failure_statement_is_a_parameter(self):
        """`return -1` in a `tp_init`, `return NULL` in a wrapper.

        Hard-coding `NULL` inside an initproc compiles and reports SUCCESS,
        which is the failure mode the capsule emitters already carry a knob
        for. Same question, same answer.
        """
        wrapper = _enumc.validate_c("k", "e", {"e": ["a"]})
        init = _enumc.validate_c("k", "e", {"e": ["a"]}, fail="return -1;")
        assert "return NULL;" in wrapper and "return -1;" not in wrapper
        assert "return -1;" in init and "return NULL;" not in init

    def test_cleanup_runs_before_the_failure(self):
        """Arrays and path objects acquired earlier in the same parse block
        must be released on the refusal path, or a bad choice leaks."""
        out = _enumc.validate_c(
            "k", "e", {"e": ["a"]}, cleanup=" Py_DECREF(x_arr);"
        )
        assert out.index("Py_DECREF(x_arr);") < out.index("return NULL;")


class TestEveryFaceNamesTheChoices:
    """The live inconsistency gh-1026 was filed for.

    Each face is driven through its own renderer, and each refusal must carry
    the choices. Asserted per face rather than once, because "they all call
    the emitter" is a claim about the code and this is a claim about the C.
    """

    ENUMS = {"log_kind": ["raw", "json", "csv"]}

    def test_a_module_function_param(self):
        from just_makeit._render import make_functions_ctx

        ctx = make_functions_ctx(
            "dsp",
            "Dsp",
            [
                {
                    "name": "open_log",
                    "return_type": "void",
                    "params": [
                        {"name": "kind", "type": "int", "enum": "log_kind"}
                    ],
                }
            ],
            enums=self.ENUMS,
        )
        assert "(choices: raw, json, csv)" in ctx["function_wrappers"]

    def test_an_object_method_param(self):
        """The face that already named them — gh-1021 — kept honest.

        A consolidation that moved everyone onto the *shorter* message would
        satisfy "one emitter" and make the product worse.
        """
        from just_makeit._context._methods import make_methods_ctx

        ctx = make_methods_ctx(
            "acq",
            "Acq",
            [
                {
                    "name": "configure",
                    "arg_type": "void",
                    "return_type": "void",
                    "params": [
                        {"name": "kind", "type": "int", "enum": "log_kind"}
                    ],
                }
            ],
            enums=self.ENUMS,
        )
        assert "(choices: raw, json, csv)" in ctx["extra_methods_c"]


class TestNoFifthCopy:
    """The registration-free gate.

    Not "four sites call `_enumc`" — a count passed the entire time this bug
    existed, because four was the correct count. The property is that every
    place jm refuses an enum choice does so through the shared emitter, so a
    fifth face is measured with nothing to register here.
    """

    #: A `PyErr_Format` refusing an enum choice, as the emitter spells it and
    #: as all four hand-written copies spelled it: `invalid <name> '%s'`.
    _REFUSAL = re.compile(r"invalid \{?\w+\}? \\?'%s")

    @staticmethod
    def _refusal_sites(path: Path) -> list[int]:
        """Lines emitting an enum refusal that `_enumc` did not produce."""
        text = path.read_text(encoding="utf-8")
        out = []
        for i, line in enumerate(text.splitlines(), 1):
            if TestNoFifthCopy._REFUSAL.search(line):
                out.append(i)
        return out

    def test_the_detector_sees_the_shape(self):
        """Armed, proven against the emitter's own output.

        A scan matching nothing would report every generator clean, which is
        indistinguishable from every generator being right.
        """
        emitted = _enumc.validate_c("kind", "e", {"e": ["a"]})
        assert self._REFUSAL.search(emitted), emitted

    def test_no_generator_spells_the_refusal_itself(self):
        offenders = {
            p.name: lines
            for p in _GENERATORS
            if (lines := self._refusal_sites(p))
        }
        assert not offenders, (
            "these generators spell an enum refusal themselves instead of "
            f"calling `_enumc.validate_c`: {offenders}. Four copies of this "
            "produced two different wordings of one refusal for the same "
            "manifest (gh-1026) — route it through the emitter so a fifth "
            "cannot."
        )


class TestTheInitParamSpellingIsOutOfScope:
    """Stated and asserted, rather than left to be rediscovered.

    An `init_param`'s `type = "enum:<name>"` flattens to
    `string_enum:a,b,c` before any generator sees it, so the enum's NAME is
    already gone — it cannot index a table and cannot name its choices from
    a registry, because it no longer knows which entry it came from.

    Asserted so the claim in this module's docstring stays checkable, and so
    whoever picks up the follow-up finds the mechanism rather than the
    conclusion.
    """

    def test_the_enum_name_is_lost_by_flattening(self):
        from just_makeit import _config as C

        cfg = {
            "enum": [{"name": "log_kind", "values": ["raw", "json", "csv"]}]
        }
        flat = C.resolve_enum_type(cfg, "enum:log_kind")
        assert flat.startswith("string_enum:")
        assert "log_kind" not in flat
