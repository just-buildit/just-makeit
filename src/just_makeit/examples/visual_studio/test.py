"""End-to-end test: the Visual Studio user's workflow on Windows (gh-2593).

The README's command lines are not restated here: each ``.steps/*.ps1`` file
is the PowerShell the README shows, and this test RUNS those files, line by
line, so the walkthrough cannot say one thing while the test does another.
A line this runner does not recognise fails the test rather than being
skipped.

On Windows -- CI's ``Examples (windows-latest, clang-cl)`` job -- every line
runs as written, through the ``windows-*`` presets Visual Studio shows, with
``CC`` and ``CMAKE_GENERATOR`` removed from the environment so the preset
alone chooses the compiler and generator, as it does in the IDE. What Visual
Studio itself provides, the MSVC build environment, the job loads instead
(``scripts/msvc-env.sh``); the IDE's clicks have no command to run, and the
README names the command each one maps to.

Elsewhere, the parts that apply run, and each that does not is replaced for
a stated reason, printed when it happens:

- a ``windows-<config>`` preset runs as the host's ``<config>`` preset: the
  ``windows-*`` presets carry a ``hostSystemName == Windows`` condition, so
  CMake refuses them on any other host;
- ``.venv\\Scripts\\python`` is ``.venv/bin/python``, where a venv puts it
  off Windows.

On every platform ``pip install numpy`` is replaced by a ``.pth`` file
linking the venv to the numpy this test runs under: the test must not need
the network, and that numpy is the one the build is checked against. So
``py -m venv .venv`` runs ``--without-pip``: nothing uses the venv's pip,
and an interpreter with no ``ensurepip`` -- a Debian Python without
``python3-venv``, as in CI's Linux image -- cannot make a venv with one.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/visual_studio/test.py
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from just_makeit._example import run_jm, scratch_dir

HERE = Path(__file__).parent
STEPS = HERE / ".steps"
_WINDOWS = sys.platform == "win32"

#: The environment every cmake / ctest / venv-python line runs in: the
#: caller's, minus what would choose the compiler or generator for the
#: preset. CI's Windows job sets both, and Visual Studio sets neither.
_ENV = {
    k: v for k, v in os.environ.items() if k not in ("CC", "CMAKE_GENERATOR")
}


_NOTED: set[str] = set()


def _note(text: str) -> None:
    """Print a substitution's reason, once per run."""
    if text not in _NOTED:
        _NOTED.add(text)
        print(f"  [visual_studio] {text}")


def _commands(script: Path) -> list[list[str]]:
    r"""The command lines of one ``.ps1`` step, split into words.

    One command per line, ``#`` comments and blank lines ignored. A
    backslash is a path separator in PowerShell, not an escape, so each is
    read as ``/`` before splitting: ``.venv\Scripts\python`` is the words
    ``.venv/Scripts/python``.
    """
    out = []
    for line in script.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(shlex.split(line.replace("\\", "/")))
    return out


def _venv_python(proj: Path) -> Path:
    sub = "Scripts/python.exe" if _WINDOWS else "bin/python"
    return proj / ".venv" / sub


def _host_preset(word: str) -> str:
    """The preset this host runs for *word*, a preset the README names."""
    if _WINDOWS or not word.startswith("windows-"):
        return word
    host = word[len("windows-") :]
    _note(
        f"{word} runs as {host}: the windows-* presets are conditioned on a"
        " Windows host, and CMake refuses them here"
    )
    return host


def _sh(argv: list[str], cwd: Path) -> str:
    r = subprocess.run(
        argv, cwd=cwd, env=_ENV, capture_output=True, text=True, timeout=600
    )
    assert r.returncode == 0, (
        f"{' '.join(argv)} exited {r.returncode}\n{r.stdout}\n{r.stderr}"
    )
    return r.stdout


def _link_numpy(proj: Path) -> None:
    """Point the project's venv at the numpy this test runs under."""
    import numpy

    _note(
        "pip install numpy is a .pth link to this interpreter's numpy:"
        " the test does not download"
    )
    purelib = _sh(
        [
            str(_venv_python(proj)),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        proj,
    ).strip()
    home = Path(numpy.__file__).resolve().parent.parent
    (Path(purelib) / "jm_numpy.pth").write_text(f"{home}\n", encoding="utf-8")


def _run_step(name: str, cwd: Path) -> Path:
    """Run every line of ``.steps/<name>`` from *cwd*; the cwd it ends in."""
    for argv in _commands(STEPS / name):
        head, rest = argv[0], argv[1:]
        if head == "jm":
            run_jm(cwd, *rest)
        elif head == "cd":
            cwd = cwd / rest[0]
        elif head == "py":
            if rest[:2] == ["-m", "venv"]:
                # pip is only ever used for `pip install numpy`, which is a
                # .pth link here; and an interpreter without ensurepip (a
                # Debian python without python3-venv, CI's Linux image)
                # cannot make a venv WITH pip at all.
                _note(
                    "py -m venv runs --without-pip: the venv's only pip"
                    " line is replaced, and this interpreter may have no"
                    " ensurepip"
                )
                rest = [*rest[:2], "--without-pip", *rest[2:]]
            _sh([sys.executable, *rest], cwd)
        elif head == ".venv/Scripts/python":
            if rest[:3] == ["-m", "pip", "install"]:
                assert rest[3:] == ["numpy"], rest
                _link_numpy(cwd)
            else:
                _sh([str(_venv_python(cwd)), *rest], cwd)
        elif head in ("cmake", "ctest"):
            _sh([head, *(_host_preset(w) for w in rest)], cwd)
        else:
            raise AssertionError(
                f"{name}: no runner for {' '.join(argv)!r} -- teach"
                " _run_step this command, never skip it"
            )
    return cwd


def _check_names_against_presets(proj: Path) -> None:
    """Every preset the README names is one the project generated.

    The names are read from the ``.ps1`` lines and the README's tables, and
    the toolbar label from ``04_open.md``; the generated
    ``CMakePresets.json`` is the authority for all of them.
    """
    presets = json.loads(
        (proj / "CMakePresets.json").read_text(encoding="utf-8")
    )
    kinds = {
        "configure": "configurePresets",
        "build": "buildPresets",
        "test": "testPresets",
    }
    have = {
        kind: {p["name"]: p for p in presets[key]}
        for kind, key in kinds.items()
    }
    ps1_lines = {
        " ".join(argv)
        for script in sorted(STEPS.glob("*.ps1"))
        for argv in _commands(script)
    }
    for line in ps1_lines:
        words = line.split()
        if "--preset" not in words:
            continue
        name = words[words.index("--preset") + 1]
        kind = (
            "test"
            if words[0] == "ctest"
            else "build"
            if "--build" in words
            else "configure"
        )
        assert name in have[kind], f"{line}: no {kind} preset {name!r}"

    # A table's command-line cell is a line the test runs, verbatim.
    cell = re.compile(r"\|\s*`((?:cmake|ctest|cd) [^`]+)`\s*\|\s*$")
    for md in sorted(STEPS.glob("*.md")):
        for row in md.read_text(encoding="utf-8").splitlines():
            m = cell.search(row)
            if m:
                assert m.group(1) in ps1_lines, (
                    f"{md.name}: the table names {m.group(1)!r}, which no"
                    " .ps1 step runs"
                )

    # The toolbar label the README tells the reader to pick.
    label = have["configure"]["windows-release"]["displayName"]
    text = (STEPS / "04_open.md").read_text(encoding="utf-8")
    assert f"**{label}**" in text, (
        f"04_open.md does not name the preset list's label {label!r}"
    )
    # ...and every other label it names is one the list shows.
    labels = {p.get("displayName") for p in have["configure"].values()}
    for named in re.findall(r"\*\*([^*]*\(clang-cl\))\*\*", text):
        assert named in labels, f"04_open.md names no preset {named!r}"


def run(root: Path) -> None:
    # 1. jm new + the component, from the PowerShell lines.
    proj = _run_step("01_scaffold.ps1", root)
    assert proj == root / "my_vs", proj
    _check_names_against_presets(proj)

    # 2. The edit the reader makes in the editor.
    _sh([sys.executable, str(STEPS / "02_patch.py")], proj)

    # 3-6. The venv, then configure, build and test through the presets.
    for step in (
        "03_venv.ps1",
        "04_configure.ps1",
        "05_build.ps1",
        "06_ctest.ps1",
        "07_python.ps1",
    ):
        _run_step(step, proj)

    # The preset, not the environment, chose the interpreter and, on
    # Windows, the compiler: _ENV removed CC and CMAKE_GENERATOR.
    preset = _host_preset("windows-release")
    cache = (proj / "out" / "build" / preset / "CMakeCache.txt").read_text(
        encoding="utf-8"
    )
    py_line = cache.split("Python3_EXECUTABLE:")[1].splitlines()[0]
    assert ".venv" in py_line, py_line
    if _WINDOWS:
        cc_line = cache.split("CMAKE_C_COMPILER:")[1].splitlines()[0]
        assert "clang-cl" in cc_line.lower(), cc_line
    assert not (proj / "build").exists(), "the preset wrote make's build/"

    # The implemented step() reached Python, through the venv interpreter.
    out = _sh(
        [
            str(_venv_python(proj)),
            "-c",
            "import sys; sys.path.insert(0, 'src');"
            " from my_vs import Scale; print(Scale(factor=3.0).step(2.0))",
        ],
        proj,
    )
    assert float(out) == 6.0, out


if __name__ == "__main__":
    with scratch_dir() as tmp:
        run(Path(tmp))
    print("visual_studio: PASSED")
