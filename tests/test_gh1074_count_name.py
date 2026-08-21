"""gh-1074: the synthesized count kwarg gets a NAME, not just a default.

A `variable_output` method with `arg_type = "void"` and no params is the
*generator* shape: there is no input to size the output from, so jm synthesizes
a leading count argument. `count_default` (gh-1051) made its **value**
settable. Its **name** was hard-coded `"count"` in seven places, so a project
whose C API calls that quantity something else could not say so.

That is sharper than a preference, because jm's own `_max_out_count_param`
(gh-607) derives the paired `<m>_max_out(self, n)` **from the C signature** —
explicitly "rather than inventing a fourth name for the same concept". So the
two halves of one generated pair disagreed with each other:

    def ptr(self, count: int = ..., out=None) -> NDArray[...]
    def ptr_max_out(self, n: int) -> int

Both jm's, for the same number. doppler hand-renamed the kwlist to `n` for
years, which left the binding accepting `n=` under a stub publishing `count=` —
a `TypeError` for a caller following either one.

**Declaring the count as a real `param` is not the workaround it looks like.**
It gives both faces the name and leaves the injected C prototype byte-identical
— and drops the **default**, because the method is no longer the generator
shape and there is no `count_default` to seed from. Asserted below, since the
argument for adding a key is that the existing alternative costs something.

It used to cost the `out=` buffer as well, which was half that argument.
gh-1079 gave the all-scalar shape its buffer back, so this file says so rather
than keeping the stronger claim. The default is still a real cost, and for a
generator it is not a small one: the zero-arg call's behaviour IS its default.

Two gates here, and the second is the one that matters:

* the name must reach **every** face, from one accessor — the seven copies are
  the shape a drifted copy is found in, and gh-1042/gh-1051 already found this
  exact pair of stub generators disagreeing about jm's own binding arguments,
  twice;
* a new method key must reach **every** site that enumerates method keys one by
  one. `_apply` and `_script` do, and CLAUDE.md records what that cost last
  time: `record_dtype` went missing from the replay and `apply` rewrote the
  sacred header prototype to the wrong shape. `TestANewMethodKeyReachesEveryEnumerator`
  derives the enumerators' key sets from the source and compares them against
  the manifest's own key table, so the NEXT key is covered with nothing to
  remember.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _apply  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _gluedoc  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _method  # noqa: E402
from just_makeit import _script  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


#: The installed console script. Driving the real entry point matters here:
#: the check being tested lives in the CLI parser, and a test that reached
#: `_method.run` directly would pass over a flag nobody can invoke.
_JM = shutil.which("just-makeit") or shutil.which("jm")


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "d74"
    _quiet(new_run, "d74", root)
    _quiet(
        object_run,
        root,
        "delay",
        module=None,
        arg_type="double _Complex",
        return_type="double _Complex",
        state_vars=[("n", "size_t", "16")],
    )
    return root


def _generator(root: Path, name: str, **kw):
    """The generator shape: variable_output, void input, no params."""
    _quiet(
        method_run,
        root,
        "delay",
        name,
        None,
        arg_type="void",
        return_type="double _Complex",
        variable_output=True,
        multi_output=[],
        **kw,
    )


def _pyi(root: Path) -> str:
    return (root / "src" / "d74" / "delay.pyi").read_text(encoding="utf-8")


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "delay" / "delay_ext.c").read_text(
        encoding="utf-8"
    )


def _sig(pyi: str, name: str) -> str:
    """The `.pyi` signature for *name*, whitespace-normalised.

    Read off the file rather than matched against a literal — the point is
    which NAMES it publishes, and a literal is what let the two generators
    disagree in the first place.
    """
    m = re.search(rf"\n    def {re.escape(name)}\((.*?)\)\s*->", pyi, re.S)
    assert m is not None, f"no def {name} in\n{pyi}"
    return " ".join(m.group(1).split())


def _kwlists(ext: str) -> list[list[str]]:
    return [
        [
            w.strip().strip('"')
            for w in m.group(1).split(",")
            if w.strip() and w.strip() != "NULL"
        ]
        for m in re.finditer(r"_kwlist\[\] =\s*\{([^}]*)\}", ext)
    ]


# ── the accessor ──────────────────────────────────────────────────────────


class TestTheOnePlaceThatAnswers:
    def test_unset_is_count(self):
        assert _gluedoc.count_kwarg_name() == "count"
        assert _gluedoc.count_kwarg_name("") == "count"

    def test_a_name_is_used_and_stripped(self):
        assert _gluedoc.count_kwarg_name("  n  ") == "n"

    def test_the_doc_map_follows_the_name(self):
        """It is looked up BY the name in the signature.

        gh-1042 established that every parameter in the signature has an
        entry; a renamed count with a `"count"`-keyed map would silently lose
        its description, which is that rule's exception reappearing.
        """
        assert set(_gluedoc.binding_param_docs()) == {"count", "out"}
        assert set(_gluedoc.binding_param_docs("n")) == {"n", "out"}
        assert all(
            v.strip() for v in _gluedoc.binding_param_docs("n").values()
        )


# ── every face ────────────────────────────────────────────────────────────


class TestTheNameReachesEveryFace:
    """Binding, stub, docstring — a rename that reaches two of three is a
    `TypeError` for a caller following the third."""

    def test_the_default_is_unchanged(self, project):
        """No churn for free: a project that says nothing moves nothing."""
        _generator(project, "ptr")
        assert "count: int = 1" in _sig(_pyi(project), "ptr")
        assert ["count", "out"] in _kwlists(_ext(project))

    def test_the_kwlist_carries_it(self, project):
        _generator(project, "ptr", count_name="n")
        assert ["n", "out"] in _kwlists(_ext(project))
        assert not any("count" in kl for kl in _kwlists(_ext(project)))

    def test_the_stub_signature_carries_it(self, project):
        _generator(project, "ptr", count_name="n")
        sig = _sig(_pyi(project), "ptr")
        assert "n: int" in sig
        assert "count" not in sig

    def test_the_docstring_documents_it_under_that_name(self, project):
        """A `Parameters` entry keyed on the old name is a doc for an
        argument the signature no longer has."""
        _generator(project, "ptr", count_name="n")
        pyi = _pyi(project)
        body = pyi[pyi.index("def ptr(") :]
        body = body[: body.index("def ptr_max_out")]
        assert "n : int" in body
        assert "count : int" not in body

    def test_it_agrees_with_the_max_out_sibling(self, project):
        """The whole reason the knob exists.

        `ptr_max_out`'s parameter name is DERIVED from the C signature by
        gh-607. With the rename the pair jm generates finally agrees with
        itself about what the number is called.
        """
        _generator(project, "ptr", count_name="n", count_default="state->n")
        pyi = _pyi(project)
        assert "n: int" in _sig(pyi, "ptr")
        assert "n: int" in _sig(pyi, "ptr_max_out")

    def test_it_composes_with_count_default(self, project):
        """The two knobs are the value and the name of one argument."""
        _generator(project, "ptr", count_name="n", count_default="state->n")
        assert "n: int = ..." in _sig(_pyi(project), "ptr")


class TestBothStubGeneratorsAgree:
    """The standalone `.pyi` and the module-aggregated one.

    These two have now been found disagreeing about jm's own binding
    arguments twice — gh-1042 over whether they are documented at all, and
    gh-1051 over the default's value. The name gets the same treatment rather
    than a third local copy, and this asserts it.
    """

    def test_a_module_object_publishes_the_same_name(self, tmp_path):
        root = tmp_path / "d74m"
        _quiet(new_run, "d74m", root)
        from just_makeit._module import run as module_run

        _quiet(module_run, root, "dsp")
        _quiet(
            object_run,
            root,
            "delay",
            module="dsp",
            arg_type="double _Complex",
            return_type="double _Complex",
            state_vars=[("n", "size_t", "16")],
        )
        _quiet(
            method_run,
            root,
            "delay",
            "ptr",
            "dsp",
            arg_type="void",
            return_type="double _Complex",
            variable_output=True,
            multi_output=[],
            count_name="n",
        )
        pyi = (root / "src" / "d74m" / "dsp" / "dsp.pyi").read_text(
            encoding="utf-8"
        )
        sig = _sig(pyi, "ptr")
        assert "n: int" in sig, sig
        # The whole point of the class: the module-aggregated writer must not
        # still be publishing the old name while the standalone one moved.
        body = pyi[pyi.index("def ptr(") :]
        body = body[: body.index("def ptr_max_out")]
        assert "count" not in body, body


class TestTheDeclaredParamAlternativeStillCostsTheDefault:
    """The reason a key was the right answer rather than "just declare it".

    A declared param leaves the injected C prototype byte-identical and gives
    both faces the name — and drops the default with it, because the method
    stops being the generator shape. Asserted so the argument for this
    feature stays checkable rather than remembered, and updated when gh-1079
    removed the other half of it.
    """

    def test_a_declared_param_offers_no_default(self, project):
        _quiet(
            method_run,
            project,
            "delay",
            "ptr2",
            None,
            arg_type="void",
            return_type="double _Complex",
            variable_output=True,
            multi_output=[],
            params=[("n", "size_t")],
        )
        sig = _sig(_pyi(project), "ptr2")
        assert "n: int" in sig
        # No default: `n` is a required positional, so a bare `obj.ptr2()`
        # is a TypeError where the generator shape would have used its
        # `count_default`.
        assert "n: int =" not in sig
        # gh-1079: `out=` IS offered now — the all-scalar shape gained it.
        # Asserted rather than deleted, so the row that changed is visible
        # instead of merely absent.
        assert "out:" in sig

    def test_count_name_keeps_both(self, project):
        _generator(project, "ptr", count_name="n", count_default="state->n")
        sig = _sig(_pyi(project), "ptr")
        assert "n: int = ..." in sig
        assert "out:" in sig


# ── the round trip ────────────────────────────────────────────────────────


class TestItSurvivesTheRoundTrip:
    def test_the_manifest_records_it(self, project):
        _generator(project, "ptr", count_name="n")
        (m,) = [
            m
            for m in C.methods(C.load(project), "delay")
            if m["name"] == "ptr"
        ]
        assert m["count_name"] == "n"

    def test_an_unset_name_writes_no_key(self, project):
        """Like `count_default` beside it: a project that never asked for the
        rename gains no manifest key and no `jm status --check` drift."""
        _generator(project, "ptr")
        (m,) = [
            m
            for m in C.methods(C.load(project), "delay")
            if m["name"] == "ptr"
        ]
        assert "count_name" not in m

    def test_the_key_is_recognised(self, project):
        """`_keys` is what says "this table may carry that".

        A key absent from it is reported to the author as a typo on every
        command — jm telling them the thing it just wrote is unrecognised.
        """
        _generator(project, "ptr", count_name="n")
        unknown = _keys.unknown_keys(C.load(project))
        assert not [u for u in unknown if "count_name" in str(u)], unknown

    def test_jm_script_replays_the_flag(self, project):
        _generator(project, "ptr", count_name="n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _script.run(project)
        assert "--count-name n" in out.getvalue()

    def test_apply_is_idempotent(self, project):
        """`apply` enumerates method keys ONE BY ONE. A key it does not name
        is silently absent from the replay — which is how `record_dtype` made
        `apply` rewrite the sacred `_core.h` prototype to the wrong shape.
        """
        _generator(project, "ptr", count_name="n", count_default="state->n")
        before = _pyi(project)
        _quiet(_apply.run, project)
        assert _pyi(project) == before
        assert ["n", "out"] in _kwlists(_ext(project))


@pytest.mark.skipif(_JM is None, reason="just-makeit not on PATH")
class TestTheCliRefusesAnUnusableName:
    """A name that cannot be a keyword argument must fail at declaration.

    Left unchecked it reaches the generated C as a `_kwlist` entry and the
    `.pyi` as a syntax error, which is a worse place to find out.
    """

    def _run(self, project, name):
        return subprocess.run(
            [
                _JM,
                "method",
                "delay",
                "ptr",
                "--arg-type",
                "void",
                "--return-type",
                "double _Complex",
                "--variable-output",
                "--count-name",
                name,
            ],
            cwd=project,
            capture_output=True,
            text=True,
        )

    @pytest.mark.parametrize(
        "name", ["2n", "with space", "class", "out", ""], ids=repr
    )
    def test_it_is_refused(self, project, name):
        proc = self._run(project, name)
        assert proc.returncode != 0, proc.stdout
        assert "count-name" in (proc.stderr + proc.stdout)

    def test_a_usable_name_is_accepted(self, project):
        """The guard against over-refusing."""
        proc = self._run(project, "n")
        assert proc.returncode == 0, proc.stderr


# ── the class gate ────────────────────────────────────────────────────────


def _keys_named(module) -> set[str]:
    """Every manifest key name that appears as a literal in *module*.

    String constants rather than `x.get("k")` calls, and that is not
    laziness: `_script` emits some keys through a
    ``for key, flag in (("record_name", "--record-name"), ...)`` loop, where
    the key is a variable at the `.get` and a constant in the tuple. A scan
    keyed on the call shape reported four keys missing that are emitted
    perfectly well — a detector wrong in the direction that manufactures
    work, which is how a gate gets switched off.

    Read out of the source, so the NEXT key added is measured the same way
    with nothing to update here.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


class TestANewMethodKeyReachesEveryEnumerator:
    """The registration-free gate, and the reason this file is long.

    `_apply` rebuilds every method by naming its keys one at a time, and
    `_script` reconstructs the CLI the same way. A key absent from either is
    not an error — it is a **default**, silently, and the tree that comes out
    of `jm apply` is a different project from the one that went in. CLAUDE.md
    records the last time: `record_dtype` dropped out of the replay and
    `apply` rewrote the sacred header prototype to the wrong shape.

    So this does not check `count_name`. It compares the manifest's own key
    table against what each enumerator actually names, and carries a ratchet
    of the keys already known to be absent. A NEW key that misses an
    enumerator pushes past the ratchet and fails here, by name.
    """

    #: Keys `_apply`'s replay legitimately does not name. Measured, and
    #: empty — the replay forwards all of them today. Shrinking this is
    #: always an improvement; growing it needs a reason in writing.
    APPLY_EXEMPT: frozenset = frozenset()

    #: `jm script` prints a CLI, and a key with no flag has no way to appear
    #: in one. Each of these is TOML-only — measured by grepping the method
    #: parser for the flag, not assumed:
    #:
    #:   max_results     the result-count cap; docs/commands/extend.md says
    #:                   outright there is no `--max-results` yet
    #:   none_on_empty   declared in TOML
    #:   codec, sink_fn  declarative shapes with no CLI form
    #:
    #: Naming them is the point: a list of four with reasons is a statement,
    #: and the next key that lands here has to earn its place beside them.
    SCRIPT_EXEMPT = frozenset(
        {"max_results", "none_on_empty", "codec", "sink_fn"}
    )

    @staticmethod
    def _manifest_keys() -> set[str]:
        """Every key a method table may carry, from the writer's own table."""
        return set(_method._SIGNATURE_COERCIONS)

    def test_the_gate_is_armed(self):
        keys = self._manifest_keys()
        assert "count_default" in keys and "count_name" in keys
        assert _keys_named(_apply), "found no key literals at all"

    def test_apply_forwards_every_signature_key(self):
        keys = self._manifest_keys()
        missing = sorted(
            k for k in keys - _keys_named(_apply) if k not in self.APPLY_EXEMPT
        )
        assert not missing, (
            "`_apply`'s replay enumerates method keys one by one, so these "
            "are silently replaced by their default when `jm apply` rebuilds "
            f"the method: {missing}. Forward each in `_replay_method`."
        )

    def test_script_replays_every_signature_key(self):
        """`jm script` reconstructs the CLI from the manifest.

        A key it omits produces a command that does not rebuild the project
        it was derived from — the documented use of that command.
        """
        keys = self._manifest_keys()
        named = _keys_named(_script)
        missing = sorted(keys - named - self.SCRIPT_EXEMPT)
        assert not missing, (
            "`jm script` reconstructs the CLI key by key, so these are "
            f"missing from the command it prints: {missing}."
        )
