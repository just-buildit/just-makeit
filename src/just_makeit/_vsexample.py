"""The runner both Visual Studio examples share (gh-2593, gh-2610).

``visual_studio_pip`` and ``visual_studio_uv`` walk the same Windows
workflow -- scaffold, open the folder, pick a preset, build, test -- and
differ in one step: how the project's ``.venv`` is made. Each example is
self-contained, because ``jm example <name>`` copies one folder, so the
README text they share is two files held byte-identical by
``tests/test_gh2610_vs_examples_in_step.py``. The code that RUNS them is
not copied at all: it is this module, in the package, because the wheel
ships the examples and each ``test.py`` imports it from there.

The README's command lines are not restated here. Each ``.steps/*.ps1``
file is the PowerShell the README shows, and :func:`run_example` RUNS
those files, line by line, so the walkthrough cannot say one thing while
the test does another. A line this runner does not recognise fails the
test rather than being skipped.

On Windows -- CI's ``Examples (windows-latest, clang-cl)`` job -- every
line runs as written, the venv lines included: ``py -m venv .venv`` and
``.venv\\Scripts\\python -m pip install numpy`` on the pip page, ``uv
sync`` on the uv page, all of them downloading what they download. The
``windows-*`` presets run as Visual Studio runs them, with ``CC``,
``CMAKE_GENERATOR`` and ``VIRTUAL_ENV`` removed from the environment, so
the preset alone chooses the compiler, generator and interpreter, and no
active venv changes what ``py`` or ``uv`` resolve to. What Visual Studio
itself provides, the MSVC build environment, the job loads instead
(``scripts/msvc-env.sh``); the IDE's clicks have no command to run, and
the README names the command each one maps to.

Elsewhere, the parts that apply run, and each that does not is replaced
for a stated reason, printed when it happens and repeated under any
failure:

- a ``windows-<config>`` preset runs as the host's ``<config>`` preset:
  the ``windows-*`` presets carry a ``hostSystemName == Windows``
  condition, so CMake refuses them on any other host;
- ``.venv\\Scripts\\python`` is ``.venv/bin/python``, where a venv puts it
  off Windows;
- the venv lines do not download. ``py -m venv .venv`` is this
  interpreter's ``-m venv --without-pip`` (CI's Linux image has no
  ``ensurepip``), ``uv sync`` is ``uv venv --python <this interpreter>``
  (without the project's editable install, which compiles nothing), and
  ``pip install numpy`` -- or the numpy ``uv sync`` installs -- is a
  ``.pth`` file linking the venv to the numpy this test runs under, the
  one the build is checked against.

Both examples also configure once BEFORE their venv step, as Visual Studio
does when the folder is opened first, and require the refusal the README
quotes (the root ``CMakeLists.txt`` names the missing ``.venv``), then
delete that build tree -- *Project > Delete Cache and Reconfigure* -- and
carry on.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from ._example import run_jm

_WINDOWS = sys.platform == "win32"

#: The environment every cmake / ctest / venv line runs in: the caller's,
#: minus what would choose the compiler, generator or interpreter for the
#: preset. CI's Windows job sets the first two, and an activated venv the
#: third; Visual Studio sets none of them.
_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in ("CC", "CMAKE_GENERATOR", "VIRTUAL_ENV")
}

#: The preset the walkthrough configures, builds and tests with.
_PRESET = "windows-release"


class _Run:
    """One example's run: where its steps are, and what it substituted."""

    def __init__(self, example: Path) -> None:
        self.name = example.name
        self.steps = example / ".steps"
        self.noted: list[str] = []

    def note(self, text: str) -> None:
        """Record and print a substitution's reason, once per run."""
        if text not in self.noted:
            self.noted.append(text)
            print(f"  [{self.name}] {text}")

    def summary(self) -> str:
        """Where this run departed from the README, line by line."""
        where = "Windows" if _WINDOWS else sys.platform
        if not self.noted:
            return f"[{self.name}] on {where}: every line ran as written."
        lines = "\n".join(f"  - {n}" for n in self.noted)
        return (
            f"[{self.name}] on {where}, not as the README writes it:\n{lines}"
        )

    def sh(self, argv: list[str], cwd: Path) -> str:
        """Run *argv* in :data:`_ENV`; its stdout, or fail with its output."""
        r = self.sh_status(argv, cwd)
        assert r.returncode == 0, (
            f"{' '.join(argv)} exited {r.returncode}\n{r.stdout}\n{r.stderr}"
        )
        return r.stdout

    def sh_status(
        self, argv: list[str], cwd: Path
    ) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            argv,
            cwd=cwd,
            env=_ENV,
            capture_output=True,
            text=True,
            timeout=900,
        )


def commands(script: Path) -> list[list[str]]:
    r"""The command lines of one ``.ps1`` step, split into words.

    One command per line, ``#`` comments and blank lines ignored. A
    backslash is a path separator in PowerShell, not an escape, so each is
    read as ``/`` before splitting: ``.venv\Scripts\python`` is the words
    ``.venv/Scripts/python``.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = Path(d) / "s.ps1"
    ...     _ = p.write_text("# c\n.venv\\Scripts\\python -m x\n")
    ...     commands(p)
    [['.venv/Scripts/python', '-m', 'x']]
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


def _host_preset(run: _Run, word: str) -> str:
    """The preset this host runs for *word*, a preset the README names."""
    if _WINDOWS or not word.startswith("windows-"):
        return word
    host = word[len("windows-") :]
    run.note(
        f"{word} runs as {host}: the windows-* presets are conditioned on a"
        " Windows host, and CMake refuses them here"
    )
    return host


def _link_numpy(run: _Run, proj: Path, what: str) -> None:
    """Point the project's venv at the numpy this test runs under."""
    import numpy

    run.note(
        f"{what} is a .pth link to this interpreter's numpy: the test does"
        " not download"
    )
    purelib = run.sh(
        [
            str(_venv_python(proj)),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        proj,
    ).strip()
    home = Path(numpy.__file__).resolve().parent.parent
    (Path(purelib) / "jm_numpy.pth").write_text(f"{home}\n", encoding="utf-8")


def _require(tool: str) -> str:
    found = shutil.which(tool)
    assert found, (
        f"the README's `{tool}` line needs {tool} on PATH, and there is none"
    )
    return found


def _venv_line(run: _Run, head: str, rest: list[str], cwd: Path) -> bool:
    """Run one of the venv step's lines; False when *head* is not one.

    On Windows every such line runs as written, whatever it says: a page
    that tells the reader something wrong -- a pip line in a venv uv made --
    fails here the way it fails for the reader. Elsewhere only the lines
    with an offline substitute are accepted; any other is refused, as is
    a line no runner knows.
    """
    pip = head == ".venv/Scripts/python" and rest[:2] == ["-m", "pip"]
    if head not in ("py", "uv") and not pip:
        return False
    if _WINDOWS:
        exe = str(_venv_python(cwd)) if pip else _require(head)
        run.sh([exe, *rest], cwd)
        return True
    line = " ".join([head, *rest])
    if line == "py -m venv .venv":
        run.note(
            "py -m venv .venv is this interpreter's -m venv --without-pip:"
            " the pip line is replaced, and this interpreter may have no"
            " ensurepip"
        )
        run.sh([sys.executable, "-m", "venv", "--without-pip", ".venv"], cwd)
    elif line == ".venv/Scripts/python -m pip install numpy":
        _link_numpy(run, cwd, "pip install numpy")
    elif line == "uv sync":
        run.note(
            "uv sync is uv venv --python <this interpreter>: the project's"
            " editable install is not run, and nothing is downloaded"
        )
        run.sh(
            [_require("uv"), "venv", "--python", sys.executable, ".venv"],
            cwd,
        )
        _link_numpy(run, cwd, "uv sync's numpy")
    else:
        raise AssertionError(
            f"{line!r} has no offline substitute off Windows -- teach"
            " _vsexample one, never skip it"
        )
    return True


def _run_step(run: _Run, name: str, cwd: Path) -> Path:
    """Run every line of ``.steps/<name>`` from *cwd*; the cwd it ends in."""
    for argv in commands(run.steps / name):
        head, rest = argv[0], argv[1:]
        if head == "jm":
            run_jm(cwd, *rest)
        elif head == "cd":
            cwd = cwd / rest[0]
        elif _venv_line(run, head, rest, cwd):
            pass
        elif head == ".venv/Scripts/python":
            run.sh([str(_venv_python(cwd)), *rest], cwd)
        elif head in ("cmake", "ctest"):
            run.sh([head, *(_host_preset(run, w) for w in rest)], cwd)
        else:
            raise AssertionError(
                f"{name}: no runner for {' '.join(argv)!r} -- teach"
                " _vsexample this command, never skip it"
            )
    return cwd


def _check_names_against_presets(run: _Run, proj: Path) -> None:
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
        for script in sorted(run.steps.glob("*.ps1"))
        for argv in commands(script)
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
    for md in sorted(run.steps.glob("*.md")):
        for row in md.read_text(encoding="utf-8").splitlines():
            m = cell.search(row)
            if m:
                assert m.group(1) in ps1_lines, (
                    f"{md.name}: the table names {m.group(1)!r}, which no"
                    " .ps1 step runs"
                )

    # The toolbar label the README tells the reader to pick.
    label = have["configure"][_PRESET]["displayName"]
    text = (run.steps / "04_open.md").read_text(encoding="utf-8")
    assert f"**{label}**" in text, (
        f"04_open.md does not name the preset list's label {label!r}"
    )
    # ...and every other label it names is one the list shows.
    labels = {p.get("displayName") for p in have["configure"].values()}
    for named in re.findall(r"\*\*([^*]*\(clang-cl\))\*\*", text):
        assert named in labels, f"04_open.md names no preset {named!r}"


#: The words of the refusal ``04_open.md`` quotes, which the root
#: ``CMakeLists.txt`` prints when the preset's ``.venv`` is missing.
_REFUSAL = "which does not exist"


def _configure_too_early(run: _Run, proj: Path) -> None:
    """Open the folder before the venv exists, as Visual Studio would.

    The configure must stop with the refusal the README quotes and the
    recovery it names, not CMake's bare ``Could NOT find Python3``; then
    the build tree goes, as *Project > Delete Cache and Reconfigure* does.
    """
    page = (run.steps / "04_open.md").read_text(encoding="utf-8")
    assert _REFUSAL in page, f"04_open.md does not quote {_REFUSAL!r}"
    preset = _host_preset(run, _PRESET)
    r = run.sh_status(["cmake", "--preset", preset], proj)
    said = " ".join((r.stdout + r.stderr).split())
    assert r.returncode != 0, f"configured with no .venv:\n{said}"
    for words in (
        _REFUSAL,
        ".venv",
        "uv sync",
        "Delete Cache and Reconfigure",
    ):
        assert words in said, f"the refusal does not say {words!r}:\n{said}"
    shutil.rmtree(proj / "out" / "build" / preset)


def _walk(run: _Run, root: Path) -> None:
    # 1. jm new + the component, from the PowerShell lines.
    proj = _run_step(run, "01_scaffold.ps1", root)
    assert proj == root / "my_vs", proj
    _check_names_against_presets(run, proj)

    # 2. The edit the reader makes in the editor.
    run.sh([sys.executable, str(run.steps / "02_patch.py")], proj)

    # The folder opened before step 3: refused, with the way out.
    _configure_too_early(run, proj)

    # 3-6. The venv, then configure, build and test through the presets.
    for step in (
        "03_venv.ps1",
        "04_configure.ps1",
        "05_build.ps1",
        "06_ctest.ps1",
        "07_python.ps1",
    ):
        _run_step(run, step, proj)

    # The preset, not the environment, chose the interpreter and, on
    # Windows, the compiler: _ENV removed CC and CMAKE_GENERATOR.
    preset = _host_preset(run, _PRESET)
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
    out = run.sh(
        [
            str(_venv_python(proj)),
            "-c",
            "import sys; sys.path.insert(0, 'src');"
            " from my_vs import Scale; print(Scale(factor=3.0).step(2.0))",
        ],
        proj,
    )
    assert float(out) == 6.0, out

    if _WINDOWS:
        # Not a README line: tests/test_examples.py runs the project's own
        # suite under pytest through this .venv, and the page installs only
        # numpy. Off Windows the numpy link already reaches pytest.
        run.note(
            "pytest is added to .venv after the walkthrough, for jm's gate"
        )
        if (proj / ".venv" / "Scripts" / "pip.exe").exists():
            run.sh(
                [str(_venv_python(proj)), "-m", "pip", "install", "pytest"],
                proj,
            )
        else:
            run.sh(
                [
                    _require("uv"),
                    "pip",
                    "install",
                    "--python",
                    str(_venv_python(proj)),
                    "pytest",
                ],
                proj,
            )


def run_example(example: Path, root: Path) -> None:
    """Run the Visual Studio example in *example* under *root*.

    Parameters
    ----------
    example : Path
        The example's directory, holding its ``.steps/``.
    root : Path
        An empty directory to scaffold the project in.

    Raises
    ------
    AssertionError
        On any step that fails, with what this platform substituted
        appended, so the gap between the page and the run is visible.
    """
    run = _Run(example)
    try:
        _walk(run, root)
    except AssertionError as e:
        raise AssertionError(f"{e}\n\n{run.summary()}") from e
    print(run.summary())
