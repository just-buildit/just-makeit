"""Review follow-ups to gh-1021 (`enum` on a method parameter).

Four defects a review of the merged change turned up. They are grouped because
they are one shape: gh-1021 taught the PRIMARY renderer about a new manifest
key and left the other readers of that key behind — the incremental splice, the
second `.pyi` producer, the emitter's branch order, and `jm script`.

The load-bearing one is TestIncrementalSplice: `jm apply` produced a fragment
that does not compile, from the ordinary workflow of adding a method to an
existing module object.
"""

from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._context._parse import _build_params_parse  # noqa: E402
from just_makeit._docsync import _file_scope_decls  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._script import run as script_run  # noqa: E402

ENUMS = {"kindE": ["a", "b"]}


@pytest.fixture()
def project(tmp_path):
    """A module object applied ONCE, then given an enum method.

    The second apply is the incremental path (`transplant_missing_bindings` /
    `_splice_first_array`), not a fresh render — deleting the fragment first
    would take the full-render path and prove nothing about the splice.
    """
    dest = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", dest, modules=["wfm"])
        object_run(
            dest,
            "frame",
            module="wfm",
            arg_type="float _Complex",
            return_type="float _Complex",
            state_vars=[("n", "uint64_t", "0")],
        )
        apply_run(dest)
    cfg = C.load(dest)
    cfg.setdefault("enum", []).append(
        {"name": "kindE", "values": list(ENUMS["kindE"])}
    )
    cfg["frame"]["methods"] = [
        {
            "name": "add",
            "return_type": "int",
            # A numpydoc Parameters block only renders when there is prose to
            # hang it on; a bare method emits a one-line docstring and the
            # `k : int` defect has nowhere to appear.
            "doc": "Add a stage.\n\nLonger description for the block.",
            "params": [
                {
                    "name": "k",
                    "type": "int",
                    "enum": "kindE",
                    "default": "a",
                    "doc": "Which kind.",
                }
            ],
        }
    ]
    C.save(dest, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(dest)
    return dest


def _frag(project):
    return (project / "native" / "src" / "wfm" / "wfm_ext_frame.c").read_text(
        encoding="utf-8"
    )


class TestIncrementalSplice:
    """The splice carried the enum TABLE but not the lookup FUNCTION.

    `_FILE_SCOPE_RE` matches only `=`-initialised statics, i.e. variables, so
    a wrapper's dependency on a static *function* was invisible to the
    "registration-free" carry its own docstring describes. The fragment
    gained the wrapper and the table and nothing defining
    `_enum_index_<Component>`.
    """

    def test_the_lookup_function_is_carried(self, project):
        assert "_enum_index_Frame(const char" in _frag(project)

    def test_each_symbol_is_defined_exactly_once(self, project):
        """The other half. `_file_scope_decls` now sees functions, and a
        wrapper's own name occurs inside its own body — so without seeding the
        dedupe with the wrappers being appended, the splice carries a wrapper
        as its own dependency AND appends it."""
        src = _frag(project)
        assert src.count("_enum_index_Frame(const char") == 1
        assert src.count("static const char *const _enum_Frame_kindE[]") == 1
        assert src.count("Frame_add(FrameObject") == 1

    def test_file_scope_decls_sees_a_static_function(self):
        text = (
            "static int\n"
            "_enum_index_X(const char *const *tab, const char *s)\n"
            "{\n"
            "    return -1;\n"
            "}\n"
            "static const char *const _tab[] = { NULL };\n"
        )
        decls = _file_scope_decls(text)
        assert "_enum_index_X" in decls
        assert decls["_enum_index_X"].rstrip().endswith("}")
        assert "_tab" in decls

    def test_a_prototype_is_not_mistaken_for_a_definition(self):
        assert "f" not in _file_scope_decls("static int f(int a);\n")


class TestStubDocAgreesWithStubSignature:
    def test_the_parameters_block_says_str(self, project):
        """One generated file said `def add(self, k: str = 'a')` over a doc
        line reading `k : int` — the two faces of one parameter disagreeing,
        inside a single stub."""
        stub = (project / "src" / "proj" / "wfm" / "wfm.pyi").read_text(
            encoding="utf-8"
        )
        assert "k: str = 'a'" in stub
        assert "k : int" not in stub
        assert "k : str" in stub


class TestIncoherentCombinationsAreRefused:
    """`enum` was tested FIRST in the emitter and LAST in both `.pyi`
    producers, so a param carrying two of them generated mismatched C rather
    than a diagnostic. The property path already refuses the analogous
    `enum` + `buf_field` pair."""

    def test_enum_on_an_array_param(self):
        with pytest.raises(ValueError, match="array parameter"):
            _build_params_parse(
                [{"name": "k", "type": "float[]", "enum": "kindE"}],
                "Frame",
                ENUMS,
            )

    def test_enum_with_a_capsule(self):
        with pytest.raises(ValueError, match="two different"):
            _build_params_parse(
                [
                    {
                        "name": "k",
                        "type": "int",
                        "enum": "kindE",
                        "capsule": "dp_x_t",
                    }
                ],
                "Frame",
                ENUMS,
            )

    def test_a_plain_enum_param_is_still_accepted(self):
        block, call, _ = _build_params_parse(
            [{"name": "k", "type": "int", "enum": "kindE"}], "Frame", ENUMS
        )
        assert call == "_arg_k"


class TestScriptDeclaresWhatItCannotRebuild:
    def _script(self, project):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            script_run(project)
        return buf.getvalue()

    def test_the_note_is_emitted(self, project):
        out = self._script(project)
        assert "NOTE: param 'k' declares enum" in out
        assert "kindE" in out

    def test_the_note_does_not_swallow_the_rest_of_the_command(self, project):
        """The first attempt appended the note to the command's FLAGS.

        `_render_cmd` joins flags with a backslash continuation, so a `#`
        among them comments out every flag after it — `--return-type int`
        vanished. Silently lossy became silently wrong, which is worse than
        the bug being fixed.
        """
        out = self._script(project)
        cmd = [
            ln
            for ln in out.splitlines()
            if "just-makeit method frame add" in ln
        ]
        assert cmd, "no method line emitted"
        after = out.split("just-makeit method frame add", 1)[1]
        block = after.split("\n\n", 1)[0]
        assert "--return-type int" in block
        assert "#" not in block

    def test_the_note_precedes_the_command(self, project):
        out = self._script(project)
        assert out.index("NOTE: param 'k'") < out.index(
            "just-makeit method frame add"
        )
