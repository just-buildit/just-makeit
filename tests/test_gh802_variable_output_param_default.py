"""
gh-802 — a ``params`` entry with a ``default`` is honoured on a plain method
and was *dropped* on a ``variable_output`` one: the branch that builds that
shape's parse block concatenated its format chars instead of going through
``_join_fmt_with_optional``, so the ``|`` never appeared, and it seeded the
parse local with the type's zero instead of the declared default.

Both faces then disagreed about the same manifest — the generated ``.pyi``
advertised ``read(n: int = 0)`` while the extension raised
``TypeError: function missing required argument 'n' (pos 1)``.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._method import run as method_run


def _ext(tmp_path, params):
    """Scaffold a component with one variable_output method and return its
    generated ``_ext.c`` text. *params* is passed through verbatim, so a
    3-tuple carries the param's default."""
    project = tmp_path / "dsp"
    new_run("dsp", project, ["tlm"], [("cap", "size_t", "0")])
    method_run(
        project,
        "tlm",
        "read",
        None,
        "void",
        "double",
        True,
        [],
        params=params,
    )
    return (project / "native" / "src" / "tlm" / "tlm_ext.c").read_text(
        encoding="utf-8"
    )


def _method_parse(ext):
    """The method wrapper's parse call, as one string.

    Matched on the `_kwlist` continuation line: the constructor's own parse
    call is spelled with `kwlist` (no underscore) and would otherwise satisfy
    an assertion about the method — it is already correctly optional, which is
    exactly what made this bug asymmetric.
    """
    i = ext.index("            _kwlist,")
    return ext[ext.rindex("PyArg_ParseTupleAndKeywords", 0, i) : i + 60]


class TestDefaultedParamIsOptional:
    def test_format_string_has_the_optional_marker(self, tmp_path):
        ext = _ext(tmp_path, [("n", "size_t", "4")])
        assert '"|K"' in _method_parse(ext)

    def test_parse_local_seeds_the_declared_default(self, tmp_path):
        # An omitted optional arg is left untouched by PyArg_*, so the
        # declared default IS whatever the local was initialised to.
        ext = _ext(tmp_path, [("n", "size_t", "4")])
        assert "unsigned long long n_raw = 4;" in ext

    def test_undefaulted_param_stays_required(self, tmp_path):
        # The control: without a `default` the argument is still mandatory,
        # so no `|` may appear and the local seeds to the type's zero.
        ext = _ext(tmp_path, [("n", "size_t")])
        assert '"K"' in _method_parse(ext)
        assert "unsigned long long n_raw = 0ULL;" in ext

    def test_required_param_may_precede_a_defaulted_one(self, tmp_path):
        ext = _ext(tmp_path, [("lo", "double"), ("n", "size_t", "4")])
        assert '"d|K"' in _method_parse(ext)
