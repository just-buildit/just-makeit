"""gh-826: `jm object` silently dropped an array init-param's default.

``parse_init_param_flag`` discarded ``parts[2]`` for any array type — no
warning, no error — while a scalar sibling in the same command kept its own.
``--state`` at least warns when it ignores an array default; this path said
nothing.

Two reasons it mattered more than its size:

**It was invisible.** With the default gone, the manifest, the stub and the
binding all agree the parameter is required — because as far as the project is
concerned it *is*. Nothing is red; the author's declaration simply is not
there, and the next reader sees a parameter that was never given a default
rather than one whose default was discarded.

**It hid a real divergence.** ``[]`` is the one array default the manifest path
supports — `_state.py` routes it to ``def_arr``, which is what makes the
parameter omittable. Dropping it left the CLI unable to express a shape the
manifest can, so a CLI-driven reproduction of that shape came out clean no
matter how it was varied (gh-823 Ask A).

The fix passes the default through and deliberately does **not** re-validate
it: `_state.py` already owns the rule that ``[]`` is the only supported array
default, and names the component and parameter when it refuses. A second copy
of that predicate in the CLI is the pair that drifts.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._cli_parse import parse_init_param_flag


def _spec(*parts: str) -> tuple:
    """Parse one --init-param spec, returning the tuple the CLI would pass."""
    got, _ = parse_init_param_flag(["--init-param", ":".join(parts)], 0)
    return got


class TestTheDefaultSurvivesTheParse:
    def test_an_empty_array_default_is_kept(self):
        assert _spec("tmpl", "float[]", "[]")[2] == "[]"

    def test_a_scalar_default_is_still_kept(self):
        """The sibling that always worked — guard against a regression."""
        assert _spec("gain", "double", "1.0")[2] == "1.0"

    def test_no_default_stays_empty(self):
        """An array with no third part is still a required positional."""
        assert _spec("tmpl", "float[]")[2] == ""

    def test_a_non_empty_default_reaches_the_owner_of_the_rule(self):
        """Passed through rather than dropped, so `_state.py` can refuse it.

        The CLI deliberately does not decide this — see the module docstring.
        """
        assert _spec("tmpl", "float[]", "[1.0,2.0]")[2] == "[1.0,2.0]"


class TestTheOtherSpellingsStillParse:
    """The array branch sits after three literal-keyword forms; moving the
    default handling must not shadow them."""

    def test_optional(self):
        got = _spec("tmpl", "float[]", "optional")
        assert got[6] is True, "the `optional` flag"
        assert got[2] == "", "not read as a default named 'optional'"

    def test_optional_with_create_fn(self):
        got = _spec("tmpl", "float[]", "optional", "make_it")
        assert got[6] is True
        assert got[7] == "make_it"

    def test_capsule(self):
        got = _spec("tlm", "dp_tlm_t *", "capsule", "dsp.tlm")
        assert got[10] == "dsp.tlm"
        assert got[8] is True, "a capsule param is always required"

    def test_required_scalar(self):
        got = _spec("fs", "double", "required")
        assert got[8] is True
        assert got[2] == ""


class TestEndToEnd:
    """The declaration must reach the manifest, and both faces must agree."""

    @pytest.fixture()
    def project(self, tmp_path, monkeypatch) -> Path:
        """Driven through the CLI, not `_object.run`.

        Calling `_object.run` with a ready-made init-param tuple bypasses
        `parse_init_param_flag` entirely — which is the code under test — so
        such a fixture passes whether or not the default is dropped. That is
        exactly how this defect stayed invisible; the test must take the same
        path a user does.
        """
        from just_makeit._cli_object import run as cli_object_run
        from just_makeit._module import run as module_run
        from just_makeit._new import run as new_run

        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            module_run(root, "m")
            monkeypatch.chdir(root)
            cli_object_run(
                [
                    "thing",
                    "--module",
                    "m",
                    "--no-state",
                    "--no-step",
                    "--init-param",
                    "tmpl:float[]:[]",
                    "--init-param",
                    "gain:double:1.0",
                ]
            )
        return root

    def test_the_manifest_records_it(self, project):
        cfg = C.load(project)
        tmpl = cfg["thing"]["init_params"][0]
        assert tmpl["name"] == "tmpl"
        assert tmpl.get("default") == "[]", "the declaration reached the TOML"

    def test_both_faces_call_it_optional(self, project):
        """The divergence this unblocks: the binding's `|` and the stub's
        `= ...` must say the same thing about the same parameter."""
        ext = (project / "native" / "src" / "m" / "m_ext_thing.c").read_text()
        pyi = (project / "src" / "proj" / "m" / "m.pyi").read_text()
        i = ext.index('kwds, "')
        fmt = ext[i + 7 : ext.index('"', i + 7)]
        assert fmt.startswith("|"), f"tmpl was hoisted before the `|`: {fmt!r}"
        assert "tmpl: NDArray[np.float32] = ..." in pyi
