"""gh-1271: an optional `const char *`, and the C literals that reached stubs.

There was no spelling of an optional string parameter that worked on both
faces, and finding out why turned up a strictly worse defect sitting beside it.

**The issue's two halves.**

1. ``default = ""`` is read as *no* default, so the parameter stayed required
   and the refusal arrived two parameters later naming a different one.
2. ``default = "NULL"`` got past that and reached the stub verbatim --
   ``dataset: str = NULL``, a ``NameError`` under `mypy` and a collection
   error under ``pytest --doctest-glob='*.pyi'``.

**The worse one, invisible in the issue.** Both param-stub producers emitted
the declared C literal *verbatim*, for every type. So a `uint64_t` parameter
declared ``default = "0U"`` rendered ``count: int = 0U`` -- not a NameError
but a **SyntaxError**, which kills the whole file rather than one name.
Measured on `main`: the generated `.pyi` does not parse. `_py_default_stub`
exists for exactly this and neither producer was calling it; gh-515 and
gh-1043 had already fixed the same class on the *state* and *init-param*
faces, and the parameter faces were the ones nobody came back to.

**Why `NULL` can now be `None`.** `_context/_types._py_default` mapped it to
``""`` and said why in a comment: *"None would fit the semantics better, but
the generated CPython binding uses the `s` format code which rejects None."*
That is a workaround for a format char, and ``""`` is a **different value**
from the declared one -- it reaches the author's C as a valid empty string
rather than as NULL, and the generated doctest constructed objects with it.
`_types.param_fmt` emits ``z`` ("str or None") for a parameter seeded NULL,
so the constraint is gone and the declared value can be the rendered one.

**What this file guards that the parity gate cannot.** Parity asks whether the
two faces AGREE; two producers with the same defect agree perfectly. The
assertions here are about the binding itself -- the format char in the emitted
C -- because the annotation and the char are one decision read in two places,
and a stub promising ``str | None`` against an ``s`` is gh-805 §E again: a
call the stub type-checks and the extension refuses. Sabotaging `param_fmt`
back to ``s`` leaves every parity test green, which is how this file earned
its place.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _types as T  # noqa: E402

_FMT_RE = re.compile(
    r'PyArg_ParseTupleAndKeywords\(\s*args,\s*kwds,\s*\n?\s*"([^"]*)"'
)


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


_DECL = """
[[elog.init_params]]
name = "tag"
type = "const char *"
default = "NULL"

[[elog.methods]]
name = "fin"
arg_type = "void"
return_type = "void"
params = [
  { name = "meta", type = "const char *" },
  { name = "ds", type = "const char *", default = "NULL" },
  { name = "cnt", type = "uint64_t", default = "0U" },
]
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("optstr")
    assert _cli("new", "lg", cwd=tmp).returncode == 0
    root = tmp / "lg"
    r = _cli("object", "elog", "--state", "n:int:0", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    frag = root / "objects" / "elog.toml"
    frag.write_text(frag.read_text(encoding="utf-8") + _DECL, encoding="utf-8")
    r = _cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "elog" / "elog_ext.c").read_text(
        encoding="utf-8"
    )


def _pyi(root: Path) -> str:
    return (root / "src" / "lg" / "elog.pyi").read_text(encoding="utf-8")


class TestTheBindingAcceptsNone:
    """The half no parity comparison can see.

    Sabotaging `param_fmt` back to `"s"` leaves every face-parity test green,
    because both faces keep saying `str | None` -- they agree with each other
    about a call the extension rejects.
    """

    def test_the_constructor_parses_with_z(self, project: Path):
        fmts = _FMT_RE.findall(_ext(project))
        assert fmts, "no PyArg_ParseTupleAndKeywords found — sweep not armed"
        assert "|z" in fmts[0], fmts

    def test_the_method_parses_with_z(self, project: Path):
        """`s` for the required string, `z` for the one seeded NULL."""
        assert "s|zK" in _FMT_RE.findall(_ext(project)), _FMT_RE.findall(
            _ext(project)
        )

    def test_a_required_string_is_not_nullable(self, project: Path):
        """Nothing declared that NULL means anything for `meta`.

        The pair, not the type: making every `const char *` take `z` would
        let `None` through on a parameter whose C has no branch for it.
        """
        assert T.param_fmt("const char *") == "s"
        assert T.param_fmt("const char *", '"/tmp/x"') == "s"


class TestTheStubMatchesTheBinding:
    def test_the_stub_parses_at_all(self, project: Path):
        """On `main` it does not: `cnt: int = 0U` is a SyntaxError.

        Asserted as *parsing*, not as the absence of a token, because that is
        the thing a downstream `mypy` or `--doctest-glob='*.pyi'` run needs
        and the reason this outranked the issue's own report.
        """
        import ast

        ast.parse(_pyi(project))

    def test_the_nullable_string_is_annotated_and_defaulted(
        self, project: Path
    ):
        pyi = _pyi(project)
        assert "def __init__(self, tag: str | None = None) -> None: ..." in pyi
        assert "ds: str | None = None" in pyi

    def test_no_c_literal_survives_into_the_stub(self, project: Path):
        sigs = [
            ln
            for ln in _pyi(project).splitlines()
            if ln.lstrip().startswith("def ")
        ]
        blob = "\n".join(sigs)
        for token in ("0U", "NULL"):
            assert token not in blob, blob

    def test_the_c_default_still_reaches_the_c(self, project: Path):
        """Python sees `None`; the C local is still seeded `NULL`."""
        ext = _ext(project)
        assert "const char * tag = NULL;" in ext
        assert "const char * ds = NULL;" in ext


class TestTheEmptyDefaultIsRefused:
    """Half 1: `default = ""` is absent, and the old refusal said so late.

    The reading is right -- `""` is how every manifest spells "no default" --
    but a `const char *` author reaches for it meaning the empty *string*, and
    what came back named a different parameter two entries later.
    """

    def test_it_names_the_parameter_and_both_spellings(self):
        with pytest.raises(ValueError) as e:
            T._join_fmt_with_optional(
                ["s", "s"],
                [
                    {"name": "meta", "type": "const char *"},
                    {"name": "ds", "type": "const char *", "default": ""},
                ],
            )
        msg = str(e.value)
        assert "'ds'" in msg
        assert "read as NO default" in msg
        # Both ways out, because the author meant one of them.
        assert "default = '\"\"'" in msg
        assert 'default = "NULL"' in msg

    def test_an_absent_key_is_still_simply_absent(self):
        """No key at all means required, and must not be refused."""
        assert (
            T._join_fmt_with_optional(
                ["s", "d"],
                [{"name": "a", "type": "const char *"}, {"name": "b"}],
            )
            == "sd"
        )


class TestEveryLiteralKindSurvivesTheRoundTrip:
    """The class, not the instance.

    `NULL` was the reported one; the numeric suffixes are the ones that break
    the file rather than a name. Checked through the shared helper so a fourth
    kind added to `_CTYPE_META` is one line away from being covered here.
    """

    @pytest.mark.parametrize(
        ("ctype", "c_default", "expected"),
        [
            ("uint64_t", "0U", "0"),
            ("uint64_t", "5ULL", "5"),
            ("float", "1.5f", "1.5"),
            ("double", "2", "2.0"),
            ("bool", "true", "True"),
            ("bool", "false", "False"),
            ("const char *", "NULL", "None"),
            ("const char *", '"/dev/null"', '"/dev/null"'),
        ],
    )
    def test_the_python_literal(self, ctype, c_default, expected):
        from just_makeit._stubs import _py_default_stub
        from just_makeit._context._types import _py_default

        assert _py_default_stub(ctype, c_default) == expected
        # The peer. gh-1043's lesson was that these two answer the same
        # question and had drifted; the `str` kind was the branch one of them
        # did not have at all.
        assert _py_default(ctype, c_default) == expected

    @pytest.mark.parametrize(
        ("ctype", "c_default"),
        [
            ("uint64_t", "0U"),
            ("float", "1.5f"),
            ("bool", "true"),
            ("const char *", "NULL"),
        ],
    )
    def test_the_literal_is_valid_python(self, ctype, c_default):
        """The property behind all of the above, stated once.

        `0U` is not a NameError, it is a SyntaxError, and a table of expected
        strings would not notice if a future entry stopped being Python.
        """
        import ast

        from just_makeit._stubs import _py_default_stub

        ast.parse(f"x = {_py_default_stub(ctype, c_default)}")
