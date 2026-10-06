"""gh-1931: a test helper the suite shares is written once, and stays so.

Three questions had been answered in private copies, each drifting from the
others:

- **Run an emitted `jm script`.** `tests/_jmrun.replay_script` is the one
  replayer (gh-1923 folded two copies into it); `test_manifest_wiring_gate`
  kept a third, with its own grammar and its own refusals.
- **The oldest Python jm supports.** Two private `_floor()` readers of
  `requires-python`, one answering ``(3, 9)`` and the other ``"3.9"``, and a
  third gate importing one of them out of a test module. Now
  `tests/_pyfloor.python_floor`.
- **Is there a compiler, and which.** 116 lookups in 109 files, over four
  different name lists. Now `tests/_compilers`: `default_cc` /
  `default_cxx` for what CMake picks when nobody chooses, `find_compiler`
  for one named compiler, bare or versioned -- two questions, kept apart.

Every arm reads the source of every test file, so a new file is covered the
moment it exists, and each is anchored on CODE, read from the AST: a
docstring or comment that quotes the shape is not a finding, and neither is
this file.

GATE: a test runs a `jm script` with `_jmrun.replay_script`, reads the
      Python floor with `_pyfloor.python_floor`, and looks a compiler up
      through `_compilers` -- never with a private copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import _compilers
import _pyfloor

TESTS = Path(__file__).parent

#: The program name a `jm script` line opens with. Recognising it is what
#: makes a loop over script lines a REPLAYER rather than a reader.
PROGRAM = "just-makeit"

#: The key a reader of the Python floor looks up, from the one reader.
FLOOR_KEY = _pyfloor.KEY

#: Every name the compiler lookups asked `shutil.which` about.
COMPILERS = frozenset(_compilers.C_COMPILERS + _compilers.CXX_COMPILERS)

#: Lookups left in place because a concurrent change owned the file when
#: gh-1931 landed. A ratchet: the count must match exactly, so the day the
#: file is converted this goes red until the entry comes out.
_COMPILER_RESIDUAL = {"test_new.py": 1}


def _cli_entry_names(tree: ast.Module) -> "set[str]":
    """Names this module binds to jm's CLI entry point, plus `run_cli`.

    ``from just_makeit._cli import main as cli_main`` binds ``cli_main``;
    the private replayer called exactly that.
    """
    names = {"run_cli"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == (
            "just_makeit._cli"
        ):
            names |= {a.asname or a.name for a in node.names}
    return names


def _names_program(node: ast.AST) -> bool:
    """True when *node* tests a string for being a jm command line.

    ``argv[0] == "just-makeit"`` or ``line.startswith("just-makeit ")``.
    """

    def is_program(c: ast.AST) -> bool:
        return (
            isinstance(c, ast.Constant)
            and isinstance(c.value, str)
            and c.value.strip() == PROGRAM
        )

    if isinstance(node, ast.Compare) and all(
        isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops
    ):
        return any(is_program(c) for c in [node.left, *node.comparators])
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "startswith"
    ):
        return any(is_program(a) for a in node.args)
    return False


def _calls_cli(node: ast.AST, entries: "set[str]") -> bool:
    """True when *node* calls jm's CLI: ``run_cli(...)`` or ``main()``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in entries
    )


def replayers(tree: ast.Module) -> "list[str]":
    """Functions that recognise a jm command line AND run it through the
    CLI: what a `jm script` replayer is, whatever it is called."""
    entries = _cli_entry_names(tree)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        inner = list(ast.walk(fn))
        if any(map(_names_program, inner)) and any(
            _calls_cli(n, entries) for n in inner
        ):
            out.append(fn.name)
    return out


def floor_readers(tree: ast.Module) -> "list[int]":
    """Lines whose code looks up `requires-python` -- as a key, a prefix
    or an operand. A docstring holds more than the key, so it never
    equals it."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip().rstrip("=").strip() == FLOOR_KEY
    ]


def compiler_lookups(tree: ast.Module) -> "list[int]":
    """Lines that ask ``shutil.which`` about a compiler by name.

    Both shapes the copies took: ``shutil.which("cc")``, and
    ``shutil.which(c) for c in ("cc", "gcc", "clang")``.
    """

    def is_which(n: ast.AST) -> bool:
        f = getattr(n, "func", None)
        return isinstance(n, ast.Call) and (
            (isinstance(f, ast.Attribute) and f.attr == "which")
            or (isinstance(f, ast.Name) and f.id == "which")
        )

    def names_compiler(n: ast.AST) -> bool:
        return isinstance(n, ast.Constant) and n.value in COMPILERS

    out = []
    for node in ast.walk(tree):
        if is_which(node) and node.args and names_compiler(node.args[0]):
            out.append(node.lineno)
        elif isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            literal = [
                g.iter
                for g in node.generators
                if isinstance(g.iter, (ast.Tuple, ast.List))
                and any(map(names_compiler, g.iter.elts))
            ]
            if literal and any(map(is_which, ast.walk(node.elt))):
                out.append(node.lineno)
    return out


def _scan(arm, owner: str) -> "dict[str, list]":
    """Each test file but *owner* that *arm* finds something in."""
    found = {}
    for path in sorted(TESTS.glob("*.py")):
        if path.name == owner:
            continue
        hits = arm(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            found[path.name] = hits
    return found


def test_no_private_script_replayer():
    found = _scan(replayers, "_jmrun.py")
    assert not found, (
        "these run a `jm script` themselves; replay it with "
        "`_jmrun.replay_script` (or compare with `script_round_trip`), so "
        f"one replayer decides what a script line means: {found}"
    )


def test_no_private_python_floor_reader():
    found = _scan(floor_readers, "_pyfloor.py")
    assert not found, (
        f"these read `{FLOOR_KEY}` themselves; use "
        "`_pyfloor.python_floor()`, and extend it there if another file's "
        f"floor is needed: {found}"
    )


def test_no_private_compiler_lookup():
    found = {
        name: len(lines)
        for name, lines in _scan(compiler_lookups, "_compilers.py").items()
    }
    assert found == _COMPILER_RESIDUAL, (
        "a compiler is looked up through `_compilers`: `default_cc()` / "
        "`default_cxx()` for what CMake picks when nobody chooses, "
        "`find_compiler(name)` for one named compiler. Found (file: "
        f"count) {found}; the residual allowed is {_COMPILER_RESIDUAL}, "
        "and an entry comes out when its file is converted."
    )


def test_every_arm_sees_the_shape_it_refuses():
    """Armed: each detector finds the shared helper's own body, which the
    scans skip by name only, and the historical private copies -- and
    passes over a docstring quoting them."""
    shared = ast.parse((TESTS / "_jmrun.py").read_text(encoding="utf-8"))
    assert replayers(shared) == ["replay_script"]
    floor = ast.parse((TESTS / "_pyfloor.py").read_text(encoding="utf-8"))
    assert floor_readers(floor), "the shared reader's own key is unseen"

    private = ast.parse(
        '"""argv[0] == "just-makeit" and cli_main()"""\n'
        "from just_makeit._cli import main as cli_main\n"
        "def _replay(script):\n"
        "    for argv in _commands(script):\n"
        '        assert argv[0] == "just-makeit"\n'
        "        cli_main()\n"
        "def _floor():\n"
        '    return [ln for ln in t if ln.startswith("requires-python")]\n'
        '_CC = shutil.which("cc") or shutil.which("gcc")\n'
        'ok = any(shutil.which(c) for c in ("c++", "g++"))\n'
    )
    assert replayers(private) == ["_replay"]
    assert floor_readers(private) == [8]
    assert compiler_lookups(private) == [9, 9, 10]
