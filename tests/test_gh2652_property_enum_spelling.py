"""gh-2652: an enum spelled the init_param way names the form that works.

doppler wanted a read-only property reporting an enum by name, wrote
``jm property rx window --type enum:win`` -- the spelling an init_param
uses, one table over -- and got a list of scalar types with nothing to act
on, so it fell back to an int. The capability existed (``--type int --enum
win``, gh-519); the refusal hid it.

Two layers, both gated here:

- ``jm property --type enum:<name>`` is now a command-line synonym for
  ``--type int --enum <name>``: every file it writes is byte-identical to
  the canonical spelling's (`_property.enum_type_flag`).
- Every refusal of an enum spelling on a ``--type``-like surface -- a
  property's inline ``string_enum:``, a manifest property row's
  ``type = "enum:..."``, a function's and a method's ``--param``, a
  ``--state`` field -- names that surface's route, through the one
  `_types.enum_spelling_hint`.

What is not done here is tracked: a manifest property row spelled
``type = "enum:<name>"`` and an inline ``string_enum:`` property (gh-2664),
and an enum ``--param`` on ``jm method`` (gh-2665).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402

#: Every file a property writes or touches on a module-less object.
GENERATED = (
    "objects/rx.toml",
    "native/src/rx/rx_ext.c",
    "native/inc/q/rx/rx_core.h",
    "src/q/rx.pyi",
)


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _refused(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode != 0, (args, r.stdout, r.stderr)
    return r.stderr


def _project(root: Path) -> Path:
    """A project ``q`` with ``[[enum]] win`` and an object ``rx`` whose int
    state field ``w`` holds the enum's index."""
    root.mkdir(parents=True, exist_ok=True)
    _ok("new", "q", "--no-c-prefix", cwd=root)
    q = root / "q"
    cfg = C.load(q)
    cfg["enum"] = [{"name": "win", "values": ["hann", "flat"]}]
    C.save(q, cfg)
    _ok("object", "rx", "--state", "w:int:0", cwd=q)
    return q


def _generated(q: Path) -> dict[str, str]:
    return {f: (q / f).read_text(encoding="utf-8") for f in GENERATED}


def test_type_enum_is_the_same_property_as_int_plus_enum(tmp_path):
    """The two spellings write the same files, byte for byte -- so the
    getter decodes to the string exactly as gh-519's does, and the
    manifest keeps the canonical row `jm script` replays."""
    sugar = _project(tmp_path / "a")
    _ok("property", "rx", "w", "--field", "--type", "enum:win", cwd=sugar)
    canon = _project(tmp_path / "b")
    _ok(
        "property", "rx", "w", "--field", "--type", "int", "--enum", "win",
        cwd=canon,
    )  # fmt: skip
    assert _generated(sugar) == _generated(canon)
    (row,) = C.load(sugar)["rx"]["properties"]
    assert (row["type"], row["enum"]) == ("int", "win"), row
    pyi = (sugar / "src/q/rx.pyi").read_text(encoding="utf-8")
    assert 'def w(self) -> Literal["hann", "flat"]:' in pyi, pyi


def test_type_enum_and_a_different_enum_flag_are_refused(tmp_path):
    q = _project(tmp_path)
    err = _refused(
        "property", "rx", "w", "--field", "--type", "enum:win",
        "--enum", "other", cwd=q,
    )  # fmt: skip
    assert "name two different enums" in err, err
    assert "--type int --enum win" in err, err


def test_type_enum_of_an_undeclared_enum_lists_the_known_ones(tmp_path):
    """The rewrite feeds gh-519's own check, not a second one."""
    q = _project(tmp_path)
    err = _refused(
        "property", "rx", "w", "--field", "--type", "enum:nope", cwd=q
    )
    assert "unknown enum 'nope'" in err and "Known enums: win" in err, err


def test_a_property_string_enum_names_the_enum_to_declare(tmp_path):
    q = _project(tmp_path)
    err = _refused(
        "property", "rx", "w", "--field", "--type", "string_enum:hann,flat",
        cwd=q,
    )  # fmt: skip
    assert "unsupported --type 'string_enum:hann,flat'" in err, err
    assert 'values = ["hann", "flat"]' in err, err
    assert "For an enum, use: --type enum:<enum>" in err, err
    assert "--type int --enum <enum>" in err, err


def test_a_manifest_property_row_spelled_enum_names_the_row_that_works(
    tmp_path,
):
    """`jm apply` replays the row through the same refusal; the hint gives
    the manifest form, since that is where the author wrote it."""
    q = _project(tmp_path)
    _ok("property", "rx", "w", "--field", "--type", "enum:win", cwd=q)
    frag = q / "objects/rx.toml"
    text = frag.read_text(encoding="utf-8")
    text = text.replace(
        'name = "w"\ntype = "int"\nfield = true\nenum = "win"',
        'name = "w"\ntype = "enum:win"\nfield = true',
    )
    assert 'type = "enum:win"' in text, text
    frag.write_text(text, encoding="utf-8")
    err = _refused("apply", cwd=q)
    assert "unsupported --type 'enum:win'" in err, err
    assert 'type = "int" and enum = "win"' in err, err


def test_a_function_param_string_enum_names_the_enum_form(tmp_path):
    q = _project(tmp_path)
    err = _refused(
        "function", "f", "--module", "q", "--param", "k:string_enum:a,b",
        cwd=q,
    )  # fmt: skip
    assert 'values = ["a", "b"]' in err, err
    assert "For an enum, use: --param k:enum:<enum>" in err, err


@pytest.mark.parametrize("ptype", ["enum:win", "string_enum:a,b"])
def test_a_method_param_enum_names_the_manifest_row(tmp_path, ptype):
    q = _project(tmp_path)
    err = _refused("method", "rx", "go", "--param", f"k:{ptype}", cwd=q)
    assert "[[<obj>.methods]]" in err, err
    name = "win" if ptype == "enum:win" else "<enum>"
    assert f'{{ name = "k", type = "int", enum = "{name}" }}' in err, err


def test_a_state_field_enum_names_int_and_the_property(tmp_path):
    """`k:enum:win` splits as type `enum`, default `win`; the refusal still
    names the enum the author meant."""
    q = _project(tmp_path)
    err = _refused("object", "ry", "--state", "k:enum:win", cwd=q)
    assert "unsupported type 'enum:win' for state field 'k'" in err, err
    assert "--state k:int" in err, err
    assert "just-makeit property <obj> k --field --type enum:win" in err, err


def test_a_non_enum_refusal_carries_no_enum_hint(tmp_path):
    q = _project(tmp_path)
    err = _refused("property", "rx", "w", "--field", "--type", "word", cwd=q)
    assert "unsupported --type 'word'" in err, err
    assert "For an enum" not in err, err
