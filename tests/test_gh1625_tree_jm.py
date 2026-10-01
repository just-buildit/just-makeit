"""gh-1625: what a recipe, script or test drives is THIS checkout's jm.

`scripts/consumer-smoke.sh` took ``JM=${JM:-just-makeit}`` and
`make consumer-smoke` passed nothing, so the jm under test was whichever
`just-makeit` came first on PATH. On a developer's box that is a `uv tool`
install, and it goes stale silently: reproduced with 0.74.0 against a
0.89.0 tree (alpha..gamma "ok" against a jm fifteen releases old, then a
failure on the harness), and again with 0.53.0 against 0.96.0 -- the
generated manifest read ``jm_version = "0.53.0"``. CI was right only by the
accident of running `make tool-install` first.

The repair has one name for the tree's jm in each world:

- **make and the scripts it runs**: the Makefile's ``JM``, an absolute
  ``uv run --with-editable $(CURDIR)`` command line, handed to the script by
  the recipe. The script has no default, and refuses a jm whose
  ``--version`` is not the tree's ``[project] version``.
- **tests**: `tests/_jmrun.run_cli`, which runs this tree's `main()` in
  process (gh-1374) -- the conftest puts this ``src/`` first on the path.

This file refuses the bare name coming back, registration-free: every make
file at the root, every script under ``scripts/`` and every test is read,
so a new one is covered the moment it exists.

The bare name can also arrive as TEXT a shell runs: two tests replayed
`jm script` output with ``subprocess.run(["bash", "-s"], input=...)``, and
every ``just-makeit ...`` line in it went through PATH -- green under
`make test` (the venv is first there), red from a plain shell with a stale
tool install. No argv names jm, so the argv check could not see it. A test
therefore may not hand a shell its program as text (``-s``, ``-c``, or no
script operand at all); `_jmrun.replay_script` replays a script through
this tree, and `SHELL_TEXT_ALLOWED` names the files whose subject IS a
shell text that runs no jm.

GATE: no recipe, script or test runs a bare `just-makeit`/`jm` from PATH,
      nor a test a shell reading its program from text;
      the consumer smoke refuses a jm that is not this tree's version.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "scripts" / "consumer-smoke.sh"
NAMES = ("just-makeit", "jm")
_NAME = r"(?:just-makeit|jm)"

# ── shell and make ──────────────────────────────────────────────────────────

# The program word of a command: at the start of a line (after a make recipe's
# tab and `@`/`-`/`+`), after a separator, a subshell or substitution opener,
# or a keyword that starts a command -- and after any `VAR=value` prefixes.
# Not "anywhere the name appears": `just-makeit.toml`, the repo slug, an
# `echo "  just-makeit -- ..."` banner and `RW_PKG=just-makeit` (a package
# name handed to a script) are all innocent.
_COMMAND = re.compile(
    r"(?:^[ \t]*[@+-]*|[;&|({`]|\$\(|\b(?:then|do|else|exec|time)\b)"
    r"[ \t]*(?:\w+=\S*[ \t]+)*"
    rf"{_NAME}(?=[ \t]|$|[;&|)`])",
    re.MULTILINE,
)
# Naming it as a value someone will run: the old `${JM:-just-makeit}`, or a
# make variable that IS the bare name.
_DEFAULT = re.compile(rf":-{_NAME}\}}")
# `JM` is the name this repo gives the jm under test; it is never the bare
# one, quoted or not (`JM=just-makeit make consumer-smoke`).
_JM_VALUE = re.compile(rf"\bJM=(['\"]?){_NAME}\1(?=[ \t]|$)", re.MULTILINE)
_MAKE_VAR = re.compile(
    rf"^[ \t]*\w+[ \t]*[:?+!]?=[ \t]*{_NAME}[ \t]*$", re.MULTILINE
)


#: `X='...'`: a single-quoted literal assigned as DATA, which may span lines
#: -- complex-spelling-check.sh's allow-list carries `...; jm upgrade
#: respells it` as a reason string. Single quotes have no escapes, so the
#: literal ends at the next quote.
_QUOTED_DATA = re.compile(r"(?<==)'[^']*'")


def _blank(m: re.Match) -> str:
    # A literal that IS the bare name is left for `_JM_VALUE` to judge.
    if m.group()[1:-1] in NAMES:
        return m.group()
    return "\n" * m.group().count("\n")


def _uncommented(text: str) -> str:
    """*text* without whole-line comments or quoted data, lines kept."""
    text = _QUOTED_DATA.sub(_blank, text)
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in text.splitlines()
    )


def bare_shell_jm(text: str) -> list[int]:
    """Line numbers in shell or make *text* that run a bare jm from PATH."""
    body = _uncommented(text)
    hits = set()
    for rx in (_COMMAND, _DEFAULT, _JM_VALUE, _MAKE_VAR):
        for m in rx.finditer(body):
            hits.add(body.count("\n", 0, m.end()) + 1)
    return sorted(hits)


def _shell_files() -> list[Path]:
    files = [
        *sorted(ROOT.glob("Makefile")),
        *sorted(ROOT.glob("*.mk")),
        *sorted((ROOT / "scripts").glob("*.sh")),
    ]
    # A glob that finds nothing would make this vacuous.
    names = {p.name for p in files}
    assert {"Makefile", "local.mk", "consumer-smoke.sh"} <= names, names
    return files


@pytest.mark.parametrize(
    "path", _shell_files(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_no_recipe_or_script_runs_a_bare_jm(path):
    lines = bare_shell_jm(path.read_text(encoding="utf-8"))
    assert not lines, (
        f"{path.relative_to(ROOT)} runs a bare jm at line(s) {lines}: that is "
        "whichever `just-makeit` is first on PATH, often a stale tool "
        "install (gh-1625). In a recipe use $(JM) from the Makefile; a "
        "script takes it as JM from the recipe that calls it."
    )


# ── Python ──────────────────────────────────────────────────────────────────

_SPAWNERS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "system",
    "execvp",
    "spawnvp",
}


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _is_bare(arg: ast.AST) -> bool:
    """An argv whose program is the bare name, or a shell line starting so."""
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        first = arg.elts[0]
        return isinstance(first, ast.Constant) and first.value in NAMES
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value.split(" ", 1)[0] in NAMES and " " in arg.value
    return False


#: A shell whose program can arrive as text. Matched on the basename, so
#: ``/bin/sh`` counts.
SHELLS = ("bash", "sh", "dash", "zsh", "ksh")

#: Test files whose subject is a shell TEXT, which they may hand to a shell
#: as ``-c``/``-s``. Each must say why that text cannot run a jm; an entry
#: whose file no longer does so fails `test_shell_text_allowances_are_live`.
SHELL_TEXT_ALLOWED = {
    "test_ci_passed_aggregator.py": (
        "runs ci.yml's `CI passed` run: block, the text GitHub runs, with "
        "PATH pinned to /usr/bin:/bin; it calls no jm"
    ),
}


def _argv_words(arg: ast.AST) -> list[str | None] | None:
    """*arg* as argv words, ``None`` for one not known statically.

    A list/tuple literal, or a constant string (a `shell=True` line or an
    `os.system` one) split as a shell would.
    """
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        return [
            e.value
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
            else None
            for e in arg.elts
        ]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        try:
            return list(shlex.split(arg.value)) or None
        except ValueError:
            return None
    return None


def _shell_reads_text(arg: ast.AST) -> bool:
    """An argv running a shell whose program is text, not a script file.

    `-c` takes it from an argument and `-s` (or no operand at all) from
    stdin. Only the options BEFORE the first operand are the shell's --
    after it they are the script's own (``bash x.sh -s``). An operand that
    is not a literal (``str(script)``) is taken to be a file: innocent.
    """
    words = _argv_words(arg)
    if not words or words[0] is None:
        return False
    if os.path.basename(words[0]) not in SHELLS:
        return False
    for word in words[1:]:
        if word is None or not word.startswith("-") or word == "-":
            return word == "-"  # `-` is stdin too; anything else a file
        if word == "--":
            return False
        if not word.startswith("--") and ({"c", "s"} & set(word[1:])):
            return True
    return True  # options only: the shell reads its program from stdin


def _scan_python(text: str) -> tuple[list[int], list[int]]:
    """One AST pass: (lines running a bare jm, lines spawning shell text).

    A spawner is `subprocess`'s family, `os.system`/`exec*` -- and any
    function the same file defines that itself calls `subprocess`, since a
    test's own `_run(cmd, cwd)` wrapper is how most of them spell it. That
    keeps `sys.argv = ["jm", ...]` (an in-process CLI call) and
    `_script._render_cmd(["just-makeit", ...])` (text about a command) out.
    """
    tree = ast.parse(text)
    wrappers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "subprocess"
            for n in ast.walk(fn)
        )
    }
    hits, shells = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name == "which" and any(
            isinstance(a, ast.Constant) and a.value in NAMES for a in node.args
        ):
            hits.add(node.lineno)
        elif (name in _SPAWNERS or name in wrappers) and node.args:
            if _is_bare(node.args[0]):
                hits.add(node.lineno)
            if _shell_reads_text(node.args[0]):
                shells.add(node.lineno)
    return sorted(hits), sorted(shells)


def bare_python_jm(text: str) -> list[int]:
    """Line numbers in Python *text* that run, or look up, a bare jm."""
    return _scan_python(text)[0]


def shell_text_spawns(text: str) -> list[int]:
    """Line numbers in Python *text* handing a shell its program as text.

    That text's commands resolve through PATH, so a `jm script` replayed
    this way runs whatever `just-makeit` is first there (gh-1625).
    """
    return _scan_python(text)[1]


def _python_files() -> list[Path]:
    files = sorted((ROOT / "tests").glob("*.py")) + sorted(
        (ROOT / "scripts").glob("*.py")
    )
    assert len(files) > 100, f"only {len(files)} Python files found"
    return files


@pytest.mark.parametrize(
    "path", _python_files(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_no_test_or_script_runs_a_bare_jm(path):
    lines = bare_python_jm(path.read_text(encoding="utf-8"))
    assert not lines, (
        f"{path.relative_to(ROOT)} runs a bare jm at line(s) {lines}: that "
        "is whatever `just-makeit` is on PATH, not this tree (gh-1625). "
        "Use `from _jmrun import run_cli` -- this tree's CLI, in process."
    )


def _test_files() -> list[Path]:
    return [p for p in _python_files() if p.parent.name == "tests"]


@pytest.mark.parametrize(
    "path", _test_files(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_no_test_hands_a_shell_its_program_as_text(path):
    if path.name in SHELL_TEXT_ALLOWED:
        return
    lines = shell_text_spawns(path.read_text(encoding="utf-8"))
    assert not lines, (
        f"{path.relative_to(ROOT)} hands a shell its program as text at "
        f"line(s) {lines} (`bash -s`, `sh -c`, ...): every `just-makeit` in "
        "it resolves through PATH, often a stale tool install, not this "
        "tree (gh-1625). To replay `jm script` output use "
        "`from _jmrun import replay_script` -- this tree's CLI, in process. "
        "A test whose subject IS a shell text that runs no jm is named, "
        "with its reason, in SHELL_TEXT_ALLOWED."
    )


def test_shell_text_allowances_are_live():
    """An allowance outliving its use would excuse the next one silently."""
    for name in SHELL_TEXT_ALLOWED:
        path = ROOT / "tests" / name
        assert path.is_file(), f"SHELL_TEXT_ALLOWED names missing {name}"
        assert shell_text_spawns(path.read_text(encoding="utf-8")), (
            f"{name} no longer spawns shell text; drop its allowance"
        )


def test_the_detectors_catch_each_spelling_and_pass_innocent_ones():
    """Built from parts, so the literals above cannot satisfy it."""
    jm = "just-" + "makeit"
    for bad in (
        f"{jm} new alpha",
        f"\t@{jm} apply",
        f"(cd x && {jm} apply >/dev/null)",
        "    jm new p --object fir",
        f"FOO=1 {jm} --version",
        f"got=$({jm} --version)",
        "JM=${JM:-" + jm + "}",
        f"JM ?= {jm}",
        f"JM={jm} make consumer-smoke",
        f"\tJM='{jm}' bash scripts/consumer-smoke.sh",
    ):
        assert bare_shell_jm(bad) == [1], bad
    # Quoted data spanning lines is blanked, and the lines after it still
    # count from the right place.
    assert bare_shell_jm(f"X='a|b; {jm} upgrade\nc'\n{jm} new p") == [3]
    for fine in (
        f'@echo "  {jm} -- development"',
        f"toml_add {jm}.toml project x",
        f"REPO=just-buildit/{jm} RW_PKG={jm} \\",
        "$(JM) new alpha",
        "tree_jm new alpha",
        f"# {jm} new alpha, in a comment",
        f"JM = $(UV) run --with-editable $(CURDIR) {jm}",
        "\tJM='$(JM)' bash scripts/consumer-smoke.sh",
        f"ALLOWED='x|frozen at jm 0.33; {jm} upgrade respells it'",
    ):
        assert bare_shell_jm(fine) == [], fine

    for bad in (
        f'subprocess.run(["{jm}", "new", "p"])',
        f'shutil.which("{jm}")',
        f'os.system("{jm} apply")',
        "import subprocess\n"
        "def _run(cmd):\n    return subprocess.run(cmd)\n"
        f'_run(["{jm}", "perf"])',
    ):
        assert bare_python_jm(bad), bad
    for fine in (
        f'monkeypatch.setattr(sys, "argv", ["{jm}", "new"])',
        f'_script._render_cmd(["{jm}", "new", "p"], [])',
        'run_cli("new", "p")',
        f'subprocess.run(["make", "test"], cwd="{jm}")',
    ):
        assert not bare_python_jm(fine), fine

    for bad in (
        'subprocess.run(["bash", "-s"], input=script)',
        'subprocess.run(["sh", "-c", text])',
        'subprocess.run(("/bin/bash", "-c", text))',
        'subprocess.run(["bash", "-ec", text])',
        'subprocess.run(["bash", "-e", "-s"], input=script)',
        'subprocess.run(["bash"], input=script)',
        'subprocess.run(["sh", "-"], input=script)',
        'subprocess.check_output(["zsh", "-lc", text])',
        'subprocess.run("bash -s", shell=True, input=script)',
        'os.system("sh -c true")',
        "import subprocess\n"
        "def _run(cmd):\n    return subprocess.run(cmd)\n"
        '_run(["bash", "-s"])',
    ):
        assert shell_text_spawns(bad), bad
    for fine in (
        'subprocess.run(["bash", str(script)])',
        'subprocess.run(["bash", "-e", str(script)])',
        'subprocess.run(["sh", "x.sh", "-s"])',
        'subprocess.run(["bash", "--", "x.sh"])',
        'subprocess.run(["make", "-s", "test"])',
        "replay_script(out.stdout, replay)",
        'run_cli("script")',
    ):
        assert not shell_text_spawns(fine), fine


# ── the smoke: make names the tree's jm, and the script checks it ───────────


def _tree_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "(.*)"$', text, re.MULTILINE)
    assert m, "no [project] version in pyproject.toml"
    return m.group(1)


def _fake_jm(bindir: Path, version: str) -> Path:
    """A `just-makeit` that reports *version* and does nothing else."""
    bindir.mkdir(parents=True, exist_ok=True)
    fake = bindir / "just-makeit"
    fake.write_text(
        "#!/bin/sh\n"
        f'[ "$1" = --version ] && {{ echo {version}; exit 0; }}\n'
        "echo 'fake jm: only --version' >&2\nexit 3\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def _env_with_stale_jm(tmp_path: Path) -> dict:
    """PATH with a stale `just-makeit` FIRST: the developer's-box trap."""
    stale = tmp_path / "stale-bin"
    _fake_jm(stale, "0.0.1")
    return {**os.environ, "PATH": f"{stale}{os.pathsep}{os.environ['PATH']}"}


def test_make_hands_the_smoke_this_trees_jm(tmp_path):
    """The JM the recipe passes is the tree's, even with a stale jm first.

    Read from the recipe make would run, then RUN from somewhere else -- the
    script cds into its work tree, so a relative spelling would not hold.
    """
    env = _env_with_stale_jm(tmp_path)
    plan = subprocess.run(
        ["make", "-n", "--no-print-directory", "consumer-smoke"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    m = re.search(r"JM='([^']*)'", plan)
    assert m, f"the consumer-smoke recipe passes no JM:\n{plan}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    got = subprocess.run(
        [*shlex.split(m.group(1)), "--version"],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == _tree_version(), (
        f"make's JM ran {got.stdout.strip()!r}, the tree is "
        f"{_tree_version()!r}: {m.group(1)}"
    )


def _smoke(tmp_path: Path, **env) -> subprocess.CompletedProcess:
    work = tmp_path / "work"
    # A JM in the caller's environment must not leak into the unset case.
    full = {k: v for k, v in os.environ.items() if k != "JM"}
    full.update(WORK=str(work), **env)
    return subprocess.run(
        ["bash", str(SMOKE)],
        cwd=tmp_path,
        env=full,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_the_smoke_refuses_a_jm_that_is_not_this_trees(tmp_path):
    fake = _fake_jm(tmp_path / "bin", "0.0.1")
    r = _smoke(tmp_path, JM=str(fake))
    assert r.returncode != 0
    assert "is not this checkout's jm" in r.stderr, r.stderr
    assert f"0.0.1 but this tree is {_tree_version()}" in r.stderr
    # Refused before anything ran: no work tree, no CMake download.
    assert not (tmp_path / "work").exists()


def test_the_smoke_refuses_to_guess_when_no_jm_is_named(tmp_path):
    r = _smoke(tmp_path)
    assert r.returncode != 0
    assert "JM is unset" in r.stderr, r.stderr


def test_the_smoke_accepts_this_trees_version_and_says_which_it_ran(
    tmp_path,
):
    """The guard against over-refusing: the tree's version gets through.

    The fake then fails the first real command, which is all this needs --
    the full run is `make consumer-smoke`, a CI job of its own.
    """
    fake = _fake_jm(tmp_path / "bin", _tree_version())
    r = _smoke(tmp_path, JM=str(fake))
    assert "is not this checkout's jm" not in r.stderr, r.stderr
    assert f"==> jm {_tree_version()}: {fake} ({fake})" in r.stdout, r.stdout
