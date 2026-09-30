"""gh-1734: the ``jm_array_arg`` marker reads the project's formatted C.

The formatter is the adversary: the ``out=`` guard pattern once matched
every layout but GNU's ``name (`` and reported 74 false findings on doppler
(gh-1448 review). This renders the fragment through the project's own
clang-format in GNU style at 79 columns -- doppler's house style -- and
requires the advisory to be silent on it, and still to name the member once
the call is the bare ``PyArray_FROM_OTF`` a pre-gh-1700 fragment has.

Lives on the PROJECT_ENV_TESTS path because it needs the pinned
clang-format (gh-1442).

GATE: a GNU-formatted current fragment is not reported; a GNU-formatted
pre-gh-1700 one is.
"""

from __future__ import annotations

from _jmrun import run_cli

from test_gh1734_array_arg_advisory import (
    _CALL_RE,
    FRAG,
    WHY,
    _apply,
    _array_project,
)


def test_gnu_formatted_fragments(tmp_path):
    proj = _array_project(tmp_path)
    (proj / ".clang-format").write_text(
        "BasedOnStyle: GNU\nColumnLimit: 79\nReflowComments: true\n"
    )
    cfg = proj / "just-makeit.toml"
    cfg.write_text(
        cfg.read_text().replace(
            "[project]", '[project]\nc_style = "clang-format"', 1
        )
    )
    frag = proj.joinpath(*FRAG)
    frag.unlink()  # regenerate it in the house style
    assert run_cli("apply", cwd=proj).returncode == 0
    src = frag.read_text()
    # The fixture must actually produce the layout it claims to test.
    assert "jm_array_arg (" in src, src

    out = _apply(proj)
    assert "no longer matches" not in out, out

    old, n = _CALL_RE.subn(
        r"(PyArrayObject *)PyArray_FROM_OTF (\1, \2, \3)", src
    )
    assert n == 1
    frag.write_text(old)
    assert WHY in _apply(proj)
