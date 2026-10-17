"""gh-2314: a comment inside a declaration is part of that declaration.

C lets a comment sit anywhere a space can, so ``float /* hot */ q_step(``
is one declaration. Two readers in `_init` found where a declaration's
code begins by cutting at the last ``*/`` before its name, and both lost
the part before such a comment:

* `declared_return` read ``void /* x */ acc_set_alpha(`` as returning
  ``""``, and gh-2302's check refused a setter that returns nothing (the
  spellings are in `test_gh2302_setter_status_refused.py`);
* `step_def_start` put the start of the inline step's declaration AT the
  comment, so every prototype jm inserts above the step (gh-1679) split it,
  landing between ``static inline float`` and the step's name.

Both now find the declaration through one primitive, `_init._decl_start`,
which reads the header with its comments blanked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _incpath as INC


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr


def _module_acc(tmp_path: Path) -> Path:
    """A module object with a writable ``alpha``; its header is only ever
    added to (gh-627), so what the test writes there stays."""
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    _ok("module", "dsp", cwd=root)
    _ok("object", "acc", "--module", "dsp", cwd=root)
    _ok("property", "acc", "alpha", "--type", "double", "--writable", cwd=root)
    return root


def test_a_comment_in_the_step_declaration_stays_in_it(tmp_path) -> None:
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    # A module object: its header is only ever added to (gh-627), so the
    # prototype lands where step_def_start says.
    _ok("module", "dsp", cwd=root)
    _ok(
        "object",
        "lo",
        "--module",
        "dsp",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    header = INC.core_h(root, "lo")
    text = header.read_text(encoding="utf-8")
    decl = "static inline float\np_lo_step("
    assert text.count(decl) == 1, text
    commented = "static inline float /* hot; cold */\np_lo_step("
    header.write_text(text.replace(decl, commented), encoding="utf-8")
    _ok("property", "lo", "level", "--type", "float", cwd=root)
    text = header.read_text(encoding="utf-8")
    # The step's declaration is still whole, and the getter jm inserted
    # sits above it -- above its doc comment too, which stays with it.
    assert text.count(commented) == 1, text
    step = text.index(commented)
    doc = text.rindex("/**", 0, step)
    assert text[doc:step].count("*/") == 1, text[doc:step]
    assert text.index("p_lo_get_level(") < doc, text


def test_a_type_that_cannot_be_read_is_not_a_finding(tmp_path) -> None:
    """Nothing but comments before the setter's name is "can't tell".

    The multi-line lookup can start inside the comment above a prototype,
    so once that comment is cut nothing may be left. That is no evidence of
    a status to discard, so gh-2302's check must not refuse it.
    """
    root = _module_acc(tmp_path)
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    decl = "void p_acc_set_alpha("
    assert text.count(decl) == 1, text
    header.write_text(
        text.replace(decl, "/* Set alpha.\n   Returns 0 */ p_acc_set_alpha("),
        encoding="utf-8",
    )
    _ok("apply", cwd=root)


@pytest.mark.parametrize(
    "above",
    [
        "/* a; b */\n",
        "// a; b\n",
        "#define P_ALPHA 1\n",
        "#define P_ALPHA(x) \\\n    (x)\n",
        'extern "C" {\n',
    ],
    ids=["block", "line", "define", "continued", "brace"],
)
def test_the_type_starts_after_what_precedes_it(tmp_path, above) -> None:
    """The return type is read back from the name to the previous
    statement, brace or preprocessor line -- never through one, and never
    stopping at a ``;`` inside a comment."""
    root = _module_acc(tmp_path)
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    decl = "void p_acc_set_alpha("
    assert text.count(decl) == 1, text
    header.write_text(
        text.replace(decl, above + "int /* status */ p_acc_set_alpha("),
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    assert "declares it returning `int`:" in r.stderr, r.stderr
