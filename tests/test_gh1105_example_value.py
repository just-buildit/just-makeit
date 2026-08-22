"""gh-1105: a required init-param jm can construct with, so the suite runs.

`errors_warnings` shipped a project whose own test suite asserted **nothing**:
all eight generated tests skipped. Measured with #1089's gate wired up, it was
the only example in the fleet that did — 207 passed and 8 skipped, and every
one of the 8 was this.

That is `_unseedable_required` doing its job. jm seeds an optional scalar with
the type's **zero**; that constructor validates and rejects `slots == 0`, so a
generated `Allocator(capacity=0, slots=0)` raised the very `ValueError` the
example declares. gh-1088 marked the params `required`, which suppresses the
construction rather than emitting one that cannot work — failing loudly became
skipping quietly, which is better and is not the end state. "No foot-guns, all
green from day one" means the tests pass, not that they decline to run.

Three defects, and the first two were invisible behind the third:

1. **`example_value`** — a value jm may CONSTRUCT with in generated tests and
   doctests. Not a default: the param stays required and the Python signature
   is unchanged, which matters because a default would make `Allocator()`
   legal and refusing that is the whole point of the example.

2. **The accessor and reset tests constructed with `Obj()` — no arguments.**
   They are built in the state half of `make_state_ctx`, from ITS
   `py_create_args`, which is empty whenever init_params are present because
   `ctor_scalars` is cleared then. Every other generated test used
   `Obj(cap=0)`; these two used `Obj()`, a `TypeError` for any required param.
   `_CTOR_OVERRIDE_KEYS` is the allow-list that would have caught it, and its
   own comment warns about exactly this — except one step over: not dropped,
   but kept and built from the wrong half's data.

3. **The post-construction assertion.** `assert obj.get_n_slots() == 0` is
   jm's own generated assignment for a state-var constructor, and a guess
   about the author's code for an init-params one — `errors_warnings` derives
   all three of its fields from `capacity`/`slots`. Asserted now only where
   jm generated the constructor that produces the value. The round-trip stays
   in both cases: it is what an accessor test is for, and "reset restores the
   declared defaults" is still covered by `test_reset`, where the code under
   test really is jm's.

After: **215 passed, 0 skipped, 0 failed** across the fleet, 21 of 26 examples
carrying at least one real test. No example regressed.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._context._state import _unseedable_required  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


#: (name, type, default, default_raw, real_type, real_create_fn, optional,
#:  create_fn, required, doc, capsule, header, derived, c_type, example_value)
def _ip(name, *, example="", required=True):
    return (
        name,
        "size_t",
        "",
        "",
        "",
        "",
        False,
        "",
        required,
        "",
        "",
        "",
        "",
        "",
        example,
    )


def _project(tmp_path, name, init_params, state=None):
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "obj",
        module=None,
        init_params=list(init_params),
        state_vars=list(state or [("n", "size_t", "0")]),
    )
    return root


def _pytest_file(root, name):
    return (root / "src" / name / "tests" / "test_obj.py").read_text(
        encoding="utf-8"
    )


def _ctest_file(root):
    return (root / "native" / "tests" / "test_obj_core.c").read_text(
        encoding="utf-8"
    )


def _body(text: str, fn: str) -> str:
    """The source of one test method, up to the next `def` at method indent."""
    i = text.index(f"def {fn}")
    rest = text[i + len(f"def {fn}") :]
    j = rest.find("\n    def ")
    return rest[:j] if j != -1 else rest


class TestExampleValueMakesTheCtorSeedable:
    def test_without_it_the_param_is_unseedable(self):
        assert _unseedable_required([_ip("cap")]) == ["cap"]

    def test_with_it_the_param_is_seedable(self):
        assert _unseedable_required([_ip("cap", example="1024")]) == []

    def test_the_generated_suite_stops_skipping(self, tmp_path):
        root = _project(tmp_path, "a", [_ip("cap", example="1024")])
        assert "skipTest" not in _pytest_file(root, "a")

    def test_the_python_tests_construct_with_it(self, tmp_path):
        root = _project(tmp_path, "b", [_ip("cap", example="1024")])
        assert "Obj(cap=1024)" in _pytest_file(root, "b")

    def test_the_c_smoke_test_constructs_with_it(self, tmp_path):
        """Both faces, from one declaration — a constructor that refuses the
        type's zero must be exercised identically in C and in Python."""
        root = _project(tmp_path, "c", [_ip("cap", example="1024")])
        assert "obj_create(1024)" in _ctest_file(root)

    def test_it_is_not_a_default(self, tmp_path):
        """The param stays REQUIRED. A default would make `Obj()` legal, and
        for the constructor this exists for that is precisely wrong."""
        root = _project(tmp_path, "d", [_ip("cap", example="1024")])
        pyi = (root / "src" / "d" / "obj.pyi").read_text(encoding="utf-8")
        assert "cap: int)" in pyi or "cap: int," in pyi
        assert "cap: int = " not in pyi


class TestTheExampleLabelIsAccurate:
    """The heading `example_value` made visible.

    "Create with defaults:" was true of every object that reached this block
    before — a required init-param with no default suppressed the whole
    Examples section, so the label was never seen over a call that had no
    defaults in it. Making that object documentable is what exposed it.
    """

    def test_a_required_param_is_not_created_with_defaults(self, tmp_path):
        root = _project(tmp_path, "l1", [_ip("cap", example="1024")])
        pyi = (root / "src" / "l1" / "obj.pyi").read_text(encoding="utf-8")
        assert "Create:" in pyi
        assert "Create with defaults:" not in pyi

    def test_a_defaulted_param_keeps_the_old_label(self, tmp_path):
        """No churn for the common shape, where the label is accurate."""
        root = _project(
            tmp_path,
            "l2",
            [
                (
                    "cap",
                    "size_t",
                    "16",
                    "",
                    "",
                    "",
                    False,
                    "",
                    False,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                )
            ],
        )
        pyi = (root / "src" / "l2" / "obj.pyi").read_text(encoding="utf-8")
        assert "Create with defaults:" in pyi

    def test_both_doc_faces_agree(self, tmp_path):
        """The runtime docstring is built by the peer generator, and the two
        held the same literal — a third copy waiting to disagree."""
        root = _project(tmp_path, "l3", [_ip("cap", example="1024")])
        ext = (root / "native" / "src" / "obj" / "obj_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Create with defaults:" not in ext


class TestItRoundTrips:
    def test_the_manifest_keeps_it(self, tmp_path):
        root = _project(tmp_path, "e", [_ip("cap", example="1024")])
        before = C.load(root)["obj"]["init_params"]
        assert before[0]["example_value"] == "1024"
        C.save(root, C.load(root))
        assert C.load(root)["obj"]["init_params"] == before


class TestTheConstructionIsConsistentEverywhere:
    """Defect 2. Every generated test constructs the same way or none does."""

    def test_no_generated_test_constructs_with_no_arguments(self, tmp_path):
        root = _project(tmp_path, "f", [_ip("cap", example="1024")])
        text = _pytest_file(root, "f")
        assert "Obj()" not in text, (
            "a generated test constructs with no arguments while the ctor "
            "requires one — that is a TypeError, and it was invisible while "
            "the whole class skipped"
        )

    def test_the_accessor_and_reset_tests_are_included(self, tmp_path):
        """Named, because they are the two that were wrong: they are built in
        the state half and were absent from `_CTOR_OVERRIDE_KEYS`."""
        root = _project(tmp_path, "g", [_ip("cap", example="1024")])
        text = _pytest_file(root, "g")
        for fn in ("test_getter_setter", "test_reset"):
            assert "Obj(cap=1024)" in _body(text, fn), (
                f"{fn} constructs wrongly"
            )


class TestThePostConstructionAssertion:
    """Defect 3, in both faces."""

    def test_an_init_params_ctor_asserts_only_the_round_trip(self, tmp_path):
        root = _project(tmp_path, "h", [_ip("cap", example="1024")])
        body = _body(_pytest_file(root, "h"), "test_getter_setter")
        assert "obj.set_n(2)" in body
        assert "assert obj.get_n() == 2" in body
        assert "assert obj.get_n() == 0" not in body

    def test_a_state_var_ctor_is_unchanged(self, tmp_path):
        """The fence. jm generates that constructor whole, so the value being
        asserted is jm's own assignment and dropping it would lose real
        coverage for the common shape."""
        root = _project(tmp_path, "i", [])
        body = _body(_pytest_file(root, "i"), "test_getter_setter")
        assert "assert obj.get_n() == 0" in body

    @staticmethod
    def _accessor_block(text: str) -> str:
        """Just the `/* n: getter / setter */` section.

        Scoped deliberately: `reset_test_c` also asserts `get_n(obj) == 0`,
        and that one is CORRECT and stays. reset's contract is "restore the
        declared defaults", and jm generates the `reset()` that does it — so
        the value being asserted there really is jm's own, which is the same
        test this whole change applies.
        """
        i = text.index("getter / setter */")
        j = text.find("/*", i + 1)
        return text[i:j] if j != -1 else text[i:]

    def test_the_c_face_agrees(self, tmp_path):
        """Fixing the Python accessor test and leaving the C one is the peer
        drift this repo keeps paying for."""
        ip_root = _project(tmp_path, "j", [_ip("cap", example="1024")])
        sv_root = _project(tmp_path, "k", [])
        assert "obj_get_n(obj) == 0" not in self._accessor_block(
            _ctest_file(ip_root)
        )
        assert "obj_get_n(obj) == 0" in self._accessor_block(
            _ctest_file(sv_root)
        )

    def test_reset_still_asserts_the_declared_defaults(self, tmp_path):
        """The other half of the rule, and the reason the round-trip losing
        its initial assertion costs no coverage: "reset restores the declared
        defaults" is still checked, on both faces, where the code under test
        is jm's."""
        root = _project(tmp_path, "m", [_ip("cap", example="1024")])
        assert "assert obj.get_n() == 0" in _body(
            _pytest_file(root, "m"), "test_reset"
        )
        assert "CHECK(obj_get_n(obj) == 0);" in _ctest_file(root)


class TestTheExampleActuallyRuns:
    """The issue's own case, end to end."""

    def test_errors_warnings_generated_suite_passes(self, tmp_path):
        import importlib.util
        import os
        import subprocess

        spec = importlib.util.spec_from_file_location(
            "te", Path(__file__).parent / "test_examples.py"
        )
        te = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(te)
        if te._SKIP:
            pytest.skip(te._SKIP)
        d = [
            p for p in te._discover_examples() if p.name == "errors_warnings"
        ][0]
        with contextlib.redirect_stdout(io.StringIO()):
            te._load_run(d)(tmp_path)
        proj = next(tmp_path.rglob("just-makeit.toml")).parent
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(proj / "src"),
                "-q",
                "--no-header",
            ],
            cwd=str(proj),
            env={**os.environ, "PYTHONPATH": str(proj / "src")},
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert " passed" in r.stdout, r.stdout
        assert "skipped" not in r.stdout, (
            "errors_warnings' generated suite still skips — gh-1105 is that "
            "it asserted nothing:\n" + r.stdout
        )
