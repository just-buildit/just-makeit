"""gh-1908, gh-2132, gh-2027: a param or input type jm cannot bind is refused.

Three spellings used to pass a validator and reach the renderer:

- ``float[][]`` as a method's or function's param (gh-1908). The renderer
  strips every ``[]``, so it bound as a flat array and silently flattened a
  (2, 3) input. Only a constructor's ``T[][]`` passes two extents.
- ``bool[]`` / ``int[]`` / ``long double _Complex[]`` (gh-2132): registered
  scalars with no numpy dtype. The CLI refused them as a ``--param`` and the
  manifest did not, so ``jm apply`` died with a ``KeyError`` -- and
  ``--arg-type 'int[]'`` and an init-param refused by neither saved a row
  that broke every later command.
- a ``--param x`` beside an ``--arg-type`` input (gh-2027): the input is
  read as ``x`` too, so the binding declared it twice.

ONE predicate (`_types.array_type_error`) answers the array half, asked by
every CLI flag and by `_config.manifest_type_errors`; `_builtins.input_locals`
answers the third. Each case runs through the CLI and through `apply` on a
hand-edited manifest, because those are two paths (gh-1408), and the types
are DERIVED from `_CTYPE_META`, never listed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _types as T  # noqa: E402


def _errors(r) -> "list[str]":
    return [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _new(tmp_path: Path, *args: str) -> Path:
    r = run_cli("new", "proj", *args, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "proj"


def _refused(r, *needles: str) -> None:
    """One ``error:`` line, no traceback, and the spelling that works."""
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert len(_errors(r)) == 1, r.stderr
    for needle in needles:
        assert needle in r.stderr, (needle, r.stderr)


@pytest.fixture(scope="module")
def obj_root(tmp_path_factory) -> Path:
    root = _new(tmp_path_factory.mktemp("g1908"), "--object", "o")
    r = run_cli("module", "m", cwd=root)
    assert r.returncode == 0, r.stderr
    return root


_SCALARS = sorted(T._CTYPE_META)


def _verdict(elem: str) -> bool:
    return elem in T.SUPPORTED_ARRAY_CTYPES


@pytest.mark.parametrize("elem", _SCALARS)
def test_every_registered_element_cli_and_manifest_agree(
    elem, obj_root, tmp_path
):
    """For every `_CTYPE_META` key as `T[]`: the CLI and `apply` agree.

    A legal element is accepted by the command line; an illegal one is
    refused by it with one `error:` line, and a manifest row spelling it is
    refused by `apply` with the same, leaving the tree byte-identical.
    """
    spec = f"{elem}[]"
    # Each case runs in its own copy of one built project.
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(obj_root, root)
    # A row the CLI accepts is added with a legal element so the manifest
    # half below has a `type = "float[]"` to re-spell.
    cli = run_cli("method", "o", "mx", "--param", f"k:{spec}", cwd=root)
    if _verdict(elem):
        assert cli.returncode == 0, cli.stderr
        return
    _refused(cli, f"'{spec}'", "Supported element types")
    ok = run_cli("method", "o", "mx", "--param", "k:float[]", cwd=root)
    assert ok.returncode == 0, ok.stderr
    # The manifest path: the same row, spelled by hand.
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    assert 'type = "float[]"' in text
    frag.write_text(
        text.replace('type = "float[]"', f'type = "{spec}"'), encoding="utf-8"
    )
    before = _tree(root)
    app = run_cli("apply", cwd=root)
    _refused(app, f"'{spec}'", "Supported element types")
    assert _tree(root) == before


@pytest.mark.parametrize("flag", ["--param", "--out-param"])
def test_function_and_method_refuse_2d_through_the_cli(
    flag, obj_root, tmp_path
):
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(obj_root, root)
    before = _tree(root)
    for argv in (
        ("function", "f2", "--module", "m", flag, "a:float[][]"),
        ("method", "o", "mm", flag, "a:float[][]"),
    ):
        r = run_cli(*argv, cwd=root)
        _refused(r, "'float[][]' is 2-D", "'float[]'", "--init-param")
    assert _tree(root) == before


def test_arg_type_refuses_2d_and_dtypeless_elements(obj_root, tmp_path):
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(obj_root, root)
    before = _tree(root)
    r = run_cli("method", "o", "w", "--arg-type", "float[][]", cwd=root)
    _refused(r, "'float[][]' is 2-D")
    r = run_cli(
        "method",
        "o",
        "w",
        "--arg-type",
        "int[]",
        "--return-type",
        "bool",
        cwd=root,
    )
    _refused(r, "use 'int32_t[]'")
    assert _tree(root) == before


@pytest.mark.parametrize(
    "key, old, new, needle",
    [
        ("params", 'type = "float[]"', 'type = "float[][]"', "is 2-D"),
        ("params", 'type = "float[]"', 'type = "bool[]"', "use 'uint8_t[]'"),
        (
            "arg_type",
            'arg_type = "float[]"',
            'arg_type = "float[][]"',
            "is 2-D",
        ),
        (
            "arg_type",
            'arg_type = "float[]"',
            'arg_type = "int[]"',
            "use 'int32_t[]'",
        ),
    ],
)
def test_apply_refuses_a_hand_written_method_row(
    key, old, new, needle, tmp_path
):
    root = _new(tmp_path, "--object", "o")
    argv = (
        ("--param", "k:float[]")
        if key == "params"
        else ("--arg-type", "float[]")
    )
    r = run_cli("method", "o", "mx", *argv, cwd=root)
    assert r.returncode == 0, r.stderr
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count(old) == 1, text
    frag.write_text(text.replace(old, new), encoding="utf-8")
    before = _tree(root)
    _refused(run_cli("apply", cwd=root), needle)
    assert _tree(root) == before


def test_apply_refuses_a_hand_written_function_row(tmp_path):
    root = _new(tmp_path, "--object", "o")
    assert run_cli("module", "m", cwd=root).returncode == 0
    r = run_cli(
        "function", "f", "--module", "m", "--param", "a:float[]", cwd=root
    )
    assert r.returncode == 0, r.stderr
    frag = root / "modules" / "m.toml"
    text = frag.read_text(encoding="utf-8") if frag.exists() else ""
    if 'type = "float[]"' not in text:
        frag = root / "just-makeit.toml"
        text = frag.read_text(encoding="utf-8")
    assert text.count('type = "float[]"') == 1, text
    frag.write_text(
        text.replace('type = "float[]"', 'type = "float[][]"'),
        encoding="utf-8",
    )
    before = _tree(root)
    _refused(run_cli("apply", cwd=root), "is 2-D", "module 'm' function 'f'")
    assert _tree(root) == before


def test_init_param_element_rule_and_2d_stay_legal(tmp_path):
    """An init-param's `T[][]` is legal; its element still needs a dtype."""
    root = _new(tmp_path, "--object", "o")
    for spec in ("a:bool[]", "a:int[][]"):
        r = run_cli("object", "p", "--init-param", spec, cwd=root)
        _refused(r, "has no numpy dtype")
    ok = run_cli("object", "p", "--init-param", "a:float[][]", cwd=root)
    assert ok.returncode == 0, ok.stderr
    frag = root / "objects" / "p.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count('type = "float[][]"') == 1, text
    frag.write_text(
        text.replace('type = "float[][]"', 'type = "int[]"'), encoding="utf-8"
    )
    before = _tree(root)
    _refused(run_cli("apply", cwd=root), "use 'int32_t[]'")
    assert _tree(root) == before


def test_a_record_alias_of_a_dtypeless_scalar_is_refused(tmp_path):
    """`s[]` over `type = "bool"` is `bool[]`, on both paths (gh-2132)."""
    root = _new(tmp_path, "--object", "o")
    r = run_cli("record", "o", "s", "--type", "bool", cwd=root)
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "method",
        "o",
        "w",
        "--arg-type",
        "s[]",
        "--return-type",
        "bool",
        cwd=root,
    )
    _refused(r, "(record 's')", "use 'uint8_t[]'")
    ok = run_cli("record", "o", "t", "--type", "float", cwd=root)
    assert ok.returncode == 0, ok.stderr
    ok = run_cli("method", "o", "w", "--arg-type", "t[]", cwd=root)
    assert ok.returncode == 0, ok.stderr
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count('type = "float"\n') >= 1
    # Re-spell the record `t` as `bool`: the method row is untouched.
    head, tail = text.split('name = "t"')
    frag.write_text(
        head
        + 'name = "t"'
        + tail.replace('type = "float"', 'type = "bool"', 1),
        encoding="utf-8",
    )
    before = _tree(root)
    _refused(run_cli("apply", cwd=root), "(record 't')", "'bool[]'")
    assert _tree(root) == before


# -- gh-2027 ----------------------------------------------------------------

#: (arg_type, the param names the input claims, a name it leaves free)
_INPUTS = [
    ("float", ["x"], "y"),
    ("size_t", ["x", "x_raw", "x_obj"], "y"),
    ("float[]", ["x", "x_obj", "x_arr", "x_raw", "x_len"], "y"),
]


@pytest.mark.parametrize("arg_type, claimed, free", _INPUTS)
def test_a_param_named_for_the_input_is_refused(
    arg_type, claimed, free, tmp_path
):
    root = _new(tmp_path, "--object", "o")
    before = _tree(root)
    for name in claimed:
        r = run_cli(
            "method",
            "o",
            "f",
            "--arg-type",
            arg_type,
            "--return-type",
            "float",
            "--param",
            f"{name}:double",
            cwd=root,
        )
        _refused(r, f"param '{name}'", f"arg_type '{arg_type}'")
    assert _tree(root) == before
    # A free name is accepted, and so is a `void` input beside an `x`.
    ok = run_cli(
        "method",
        "o",
        "f",
        "--arg-type",
        arg_type,
        "--return-type",
        "float",
        "--param",
        f"{free}:double",
        cwd=root,
    )
    assert ok.returncode == 0, ok.stderr
    ok = run_cli(
        "method",
        "o",
        "g",
        "--return-type",
        "float",
        "--param",
        "x:double",
        cwd=root,
    )
    assert ok.returncode == 0, ok.stderr


def test_apply_refuses_a_param_named_for_the_input(tmp_path):
    root = _new(tmp_path, "--object", "o")
    r = run_cli(
        "method",
        "o",
        "f",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--param",
        "y:double",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count('name = "y"') == 1, text
    frag.write_text(text.replace('name = "y"', 'name = "x"'), encoding="utf-8")
    before = _tree(root)
    _refused(run_cli("apply", cwd=root), "param 'x'", "arg_type 'float'")
    assert _tree(root) == before
