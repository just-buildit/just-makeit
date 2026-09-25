"""gh-1489: a constructor parameter's enum type is spellable on the CLI.

``type = "enum:level"`` and ``type = "string_enum:a,b"`` were supported in a
hand-written ``[[obj.init_params]]`` table (gh-1096) and refused on the
command line, because ``--init-param name:type[:default]`` split on every
``:`` and both types carry one of their own -- the parser saw the type
``enum`` and called it unsupported. That broke three things at once, and each
test below holds one:

- the documented spelling (``docs/types.md``) did not work;
- ``jm script`` replayed such a project as ``--init-param`` lines that the
  same jm refused, so the script could not rebuild what it described;
- and it replayed the reference as its expansion (``level:string_enum:a,b``)
  without the ``[[enum]]`` table, so even a parser that accepted it would
  have rebuilt a project whose enum no longer had a single source.

The oracle for "the CLI means what the manifest means" is the manifest path
itself: the same object declared both ways must render byte-identical glue.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import replay_script as _replay
from _jmrun import run_cli  # noqa: E402

from just_makeit._cli_parse import parse_init_param_flag  # noqa: E402

_ENUM_TABLE = '\n[[enum]]\nname = "level"\nvalues = ["a", "b"]\n'

_SPECS = ("level:enum:level:b", "mode:string_enum:x,y:y")


def _parse(spec: str) -> tuple:
    return parse_init_param_flag(["--init-param", spec], 0)[0]


@pytest.mark.parametrize(
    "spec, ctype, default, required",
    [
        ("level:enum:level:b", "enum:level", "b", False),
        ("level:enum:level", "enum:level", "", False),
        ("level:enum:level:required", "enum:level", "", True),
        ("mode:string_enum:x,y:y", "string_enum:x,y", "y", False),
        ("mode:string_enum:x,y", "string_enum:x,y", "", False),
        (
            "mode:string_enum:a=M_A,b=M_B:b",
            "string_enum:a=M_A,b=M_B",
            "b",
            False,
        ),
    ],
)
def test_enum_types_parse(spec, ctype, default, required):
    """The type keeps its own ':'; the default and `required` follow it."""
    p = _parse(spec)
    assert (p[0], p[1], p[2], p[8]) == (
        spec.split(":")[0],
        ctype,
        default,
        required,
    )


def test_refusal_names_the_enum_forms_and_the_manifest_form(capsys):
    """An unknown type says what the CLI CAN spell, and where the rest goes.

    The one family the CLI cannot express is a C typedef jm has no name
    for (gh-1096's `c_type`), so the message must point at the manifest
    table rather than leave the author concluding jm cannot do it.
    """
    with pytest.raises(SystemExit):
        _parse("level:level_t")
    err = capsys.readouterr().err
    assert "Enum types: enum:<name>" in err
    assert "string_enum:a,b" in err
    assert "[[<object>.init_params]]" in err
    assert 'c_type = "level_t"' in err


def _project(tmp_path: Path, name: str, module: bool) -> Path:
    assert run_cli("new", name, cwd=tmp_path).returncode == 0
    root = tmp_path / name
    with (root / "just-makeit.toml").open("a", encoding="utf-8") as fh:
        fh.write(_ENUM_TABLE)
    if module:
        assert run_cli("module", "m", cwd=root).returncode == 0
    return root


def _mod_args(module: bool) -> list[str]:
    return ["--module", "m"] if module else []


def _glue(root: Path, module: bool) -> dict[str, str]:
    """The files `apply` rewrites wholesale from the manifest, by suffix."""
    pkg = root.name
    paths = [root / "src" / pkg / "g.pyi"] if not module else []
    if module:
        paths += [root / "src" / pkg / "m" / "m.pyi"]
        paths += sorted((root / "native" / "src" / "m").glob("*_ext*.c"))
    else:
        paths += [root / "native" / "src" / "g" / "g_ext.c"]
    return {
        p.relative_to(root).as_posix().replace(pkg, "PKG"): p.read_text(
            encoding="utf-8"
        ).replace(pkg, "PKG")
        for p in paths
    }


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
def test_cli_declaration_renders_what_the_manifest_does(tmp_path, module):
    """The CLI and a hand-written table are the same declaration.

    Built both ways; the generated binding and stub must be byte-identical,
    and the CLI must persist the REFERENCE (`enum:level`), not the expansion
    the renderer needs -- the manifest keeps the enum's single source.
    """
    cli = _project(tmp_path, "viacli", module)
    r = run_cli(
        "object",
        "g",
        "--no-step",
        *_mod_args(module),
        *[a for s in _SPECS for a in ("--init-param", s)],
        cwd=cli,
    )
    assert r.returncode == 0, r.stderr
    frag = (cli / "objects" / "g.toml").read_text(encoding="utf-8")
    assert 'type = "enum:level"' in frag
    assert "string_enum:a,b" not in frag

    # The reference materializes the same manifest with `apply` alone, in a
    # fresh project that never saw the CLI -- every file rendered from the
    # tables, none carried over. (Updating an existing object instead would
    # compare against a module fragment frozen at its first render, which
    # is gh-1448's question, not this one.)
    assert run_cli("new", "viatoml", cwd=tmp_path).returncode == 0
    ref = tmp_path / "viatoml"
    for rel in ("just-makeit.toml", "objects/g.toml", "modules/m.toml"):
        if (cli / rel).exists():
            (ref / rel).parent.mkdir(parents=True, exist_ok=True)
            (ref / rel).write_text(
                (cli / rel)
                .read_text(encoding="utf-8")
                .replace("viacli", "viatoml"),
                encoding="utf-8",
            )
    r = run_cli("apply", cwd=ref)
    assert r.returncode == 0, r.stderr

    assert _glue(cli, module) == _glue(ref, module)


def test_undefined_enum_is_refused_before_anything_is_written(tmp_path):
    root = _project(tmp_path, "p", module=False)
    r = run_cli(
        "object", "g", "--no-step", "--init-param", "x:enum:nope", cwd=root
    )
    assert r.returncode == 1
    assert "undefined [[enum]] 'nope'" in r.stderr
    assert not (root / "objects" / "g.toml").exists()
    assert not (root / "native" / "src" / "g").exists()


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
def test_script_replays_the_reference_and_its_table(tmp_path, module):
    """`jm script` rebuilds the same manifest, [[enum]] table included."""
    root = _project(tmp_path, "orig", module)
    r = run_cli(
        "object",
        "g",
        "--no-step",
        *_mod_args(module),
        *[a for s in _SPECS for a in ("--init-param", s)],
        cwd=root,
    )
    assert r.returncode == 0, r.stderr

    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    assert "--init-param level:enum:level:b" in r.stdout
    assert "string_enum:a,b" not in r.stdout

    out = tmp_path / "replayed"
    out.mkdir()
    rebuilt = _replay(r.stdout, out)
    for rel in ("just-makeit.toml", "objects/g.toml"):
        assert (rebuilt / rel).read_text(encoding="utf-8") == (
            root / rel
        ).read_text(encoding="utf-8"), rel
