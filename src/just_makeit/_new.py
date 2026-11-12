"""
_new.py — `just-makeit new` command.

Creates a new project scaffold in a new directory. With --object also
scaffolds the first object in the same step.  With --module (repeatable)
scaffolds one or more empty extension modules.
"""

from __future__ import annotations

from . import _textio

import sys
from pathlib import Path
from typing import Callable

from . import _color as Color
from . import _config as C
from . import _render as T
from . import _incpath as INC
from . import _pins
from . import _pythons


#: The one way a generated Makefile installs a package into ``$(PYTHON)``
#: (gh-2610): ``$(PYTHON) -c "$(PY_INSTALL)" <pip args>``. With pip in that
#: interpreter it is ``-m pip install``; without -- a venv ``uv`` made, which
#: has no pip by design -- it is ``uv pip install --python <it>``, pip's
#: ``--force-reinstall`` spelled uv's way. With neither it fails naming both
#: commands, never as ``No module named pip``. Python rather than shell so
#: the one line runs under ``cmd.exe`` and ``/bin/sh`` alike; it holds no
#: ``$``, ``#``, ``"``, ``<``, ``>``, ``|`` or ``&``, which make or either
#: shell would read. Rendered into both backends' Makefiles from here, so
#: there is no second copy to drift.
PY_INSTALL = (
    "PY_INSTALL = import importlib.util, shutil, subprocess, sys;"
    " a = sys.argv[1:]; py = sys.executable;"
    " uv = shutil.which('uv');"
    " ua = ['--reinstall' if x == '--force-reinstall' else x for x in a];"
    " cmd = [py, '-m', 'pip', 'install'] + a"
    " if importlib.util.find_spec('pip')"
    " else [uv, 'pip', 'install', '--python', py] + ua if uv else None;"
    " sys.exit(subprocess.call(cmd) if cmd else 'error: ' + py"
    " + ' has no pip and uv is not on PATH, so make cannot install '"
    " + ' '.join(a) + '. Run either: ' + py + ' -m ensurepip, then '"
    " + py + ' -m pip install ' + ' '.join(a) + ' -- or, with uv: uv pip"
    " install --python ' + py + ' ' + ' '.join(ua) + ' (gh-2610).')"
)


#: gh-2640: the project's own test, written with the project. Without it a
#: project with no component yet ran an empty suite, which unittest on
#: Python 3.12+ exits 5 for -- `make test` failed on a tree nobody had
#: touched. It checks that the package imports at the manifest's version.
#:
#: Named for the one top-level manifest table no component can take
#: (`_config.validate_name` refuses a component called ``project``), so no
#: component's ``test_<name>.py`` can land on it; the functions-only contract
#: test is ``test_<module>_functions.py`` in the module's own package.
SMOKE_TEST = "test_project.py"


def smoke_test_rel(pkg: str) -> str:
    """The smoke test's project-relative path.

    >>> smoke_test_rel("demo")
    'src/demo/tests/test_project.py'
    """
    return f"src/{pkg}/tests/{SMOKE_TEST}"


#: The smoke test's version check, by how the package's ``__init__.py``
#: declares ``__version__`` (`_projversion.version_binding`). A literal --
#: the one jm writes, and the one `jm config version` keeps in step -- is
#: held to the manifest. A computed one (``importlib.metadata``, a
#: ``_version.py``, a ``try`` fallback) is the package's own answer: a dev
#: build's ``0.2.1.dev3+g...`` is right and the manifest's ``0.2.0`` is not
#: what it should equal, so it is held only to being a version at all.
_VERSION_TESTS = {
    "literal": (
        "    def test_version_is_the_manifest_version(self):\n"
        "        self.assertEqual(<<package>>.__version__, MANIFEST_VERSION)\n"
    ),
    "computed": (
        "    def test_version_is_a_nonempty_string(self):\n"
        '        """`__init__.py` computes `__version__`, so the package, not\n'
        '        the manifest, says what it is: here, that it says one."""\n'
        "        self.assertIsInstance(<<package>>.__version__, str)\n"
        "        self.assertTrue(<<package>>.__version__)\n"
    ),
}


def _smoke_test_text(root: Path, project: str, render) -> str:
    """The smoke test for the project at *root*, born owned.

    Its version check follows the package ``__init__.py`` of the REAL
    project -- in `apply`'s replay that is not *root*, which is a scratch
    tree (`_init._real_root`). With no ``__init__.py`` yet, or one binding
    no ``__version__``, it is the literal check: `jm new` writes a literal,
    and `apply` adds one to a file that binds none.
    """
    from . import _projversion
    from ._init import _real_root

    init = _real_root(root) / "src" / project / "__init__.py"
    binding = None
    if init.is_file():
        binding = _projversion.version_binding(
            init.read_text(encoding="utf-8")
        )
    kind = "computed" if binding == _projversion.COMPUTED else "literal"
    text = T.PROJECT_TEST_PY.replace("<<version_test>>", _VERSION_TESTS[kind])
    return T.owned_scaffold(render(text), SMOKE_TEST)


def _make_project_ctx(
    project: str,
    version: str = "0.1.0",
    pytest_: bool = False,
) -> dict[str, str]:
    if pytest_:
        ensure_win = (
            '\t$(PYTHON) -c "import pytest" 2>nul || '
            f'$(PYTHON) -c "$(PY_INSTALL)" {_pins.PYTEST_DQ}\n'
        )
        ensure_unix = (
            '\t@$(PYTHON) -c "import pytest" 2>/dev/null || '
            f'$(PYTHON) -c "$(PY_INSTALL)" {_pins.PYTEST_SHELL}\n'
        )
        # gh-2640: pytest's exit 5 (no tests collected) is a failure, as
        # unittest's is: every project carries `SMOKE_TEST`, so an empty run
        # means the tests are gone, and passing it would prove nothing.
        cmd_win = "$(PYTHON) -m pytest src/ -v"
        cmd_unix = "$(PYTHON) -m pytest src/ -v"
    else:
        ensure_win = ""
        ensure_unix = ""
        cmd_win = '$(PYTHON) -m unittest discover -s src -p "test_*.py" -v'
        cmd_unix = '$(PYTHON) -m unittest discover -s src -p "test_*.py" -v'

    return {
        "package": project,
        "PACKAGE": project.upper(),
        "project": project.replace("_", "-"),
        "project_underscore": project,
        "version": version,
        # gh-2083/gh-2084: the CMake copy's spelling, rendered beside the full
        # version. `project(VERSION)` takes the release segment only.
        "cmake_version": C.cmake_version(version),
        "py_install": PY_INSTALL,
        "ensure_pytest_win": ensure_win,
        "ensure_pytest_unix": ensure_unix,
        "py_test_cmd_win": cmd_win,
        "py_test_cmd_unix": cmd_unix,
        "py_test_label": "pytest" if pytest_ else "unittest",
        # gh-2629: a new project starts out supporting what jm supports,
        # read from jm's own metadata; from then on its pyproject.toml is
        # the one list, and its CI is rendered from it.
        "requires_python": _pythons.jm_requires_python(),
        "python_classifiers": _pythons.classifier_lines(
            _pythons.jm_supported()
        ),
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _textio.write_text(path, content)
    print(f"  create  {path}")


def _write_project_wiring(root: Path, project: str, cfg: dict) -> None:
    """Write the root wiring *cfg*'s ``[project]`` table declares.

    Two things in the root ``CMakeLists.txt`` follow from ``[project]``
    alone, with no component involved: an ``add_subdirectory`` for each
    ``c_deps`` entry, and the ``# ── External deps`` block that
    ``find_packages`` / ``pkg_modules`` (and the public defines and link
    libraries) fill. `apply` writes both, and until gh-2062 nothing else
    did: a project was born without them, and its first component left
    the file STALE. That went unseen because `status` stopped early on a
    manifest declaring no component, so the project read clean exactly
    while nothing was being checked (gh-2076).

    So the creating command writes them, through the writers that put the
    same lines in an existing project -- never a copy of their text:

    - each c_dep through :func:`_libwiring.splice_cmake_component`, the
      writer every adder wires its own block with, which places it in the
      order `apply` uses (:func:`_libwiring.section_order` lists the
      c_deps first);
    - the external-deps block through `apply`'s own splice of it, which
      leaves the file untouched when the manifest declares nothing.

    Parameters
    ----------
    root : Path
        The new project's directory. Its manifest must already be saved:
        the c_dep splice reads the order of the blocks from it.
    project : str
        The package name, which names the combined C library targets.
    cfg : dict
        The manifest just saved.
    """
    from ._apply import _splice_cmake_external_deps
    from ._libwiring import splice_cmake_component

    for dep in C.c_deps(cfg):
        splice_cmake_component(root, project, dep, [], c_dep=True)
    cmake = root / "CMakeLists.txt"
    if _splice_cmake_external_deps(cmake, cfg):
        print(f"  update  {cmake}")


#: The generated README's backend-specific text, one row per build backend,
#: filled into the slots of the ONE ``templates/doc/README.md``. Everything
#: else in the README is the same for both backends -- the dependency install,
#: Quickstart, Package -- and a second template copied all of it, so a fix to
#: one would have missed the other (the gh-1860 shape). `run` picks the row
#: beside the Makefile it describes.
README_BY_BACKEND = {
    "cmake": {
        "readme_requirements": (
            "- CMake ≥ 3.16\n"
            "- A C99 compiler — GCC or Clang on Linux and macOS. On\n"
            "  Windows, **clang-cl** (Visual Studio Build Tools' C++\n"
            "  workload plus LLVM), run from a Developer PowerShell; the\n"
            "  `Makefile` selects it with Ninja. Not MSVC's own `cl.exe`:\n"
            "  it has no C99 `float _Complex`. To use the `Makefile`,\n"
            "  install GNU make (`winget install ezwinports.make`); or open\n"
            "  the folder in Visual Studio, which configures through the\n"
            "  generated `CMakePresets.json` -- after creating `.venv`,\n"
            "  with pip or `uv sync`, as\n"
            "  [Building on Windows]"
            "(https://just-buildit.github.io/just-makeit/windows/"
            "#the-projects-venv)\n"
            "  shows."
        ),
        "readme_make_builds": "cmake configure + build",
        "readme_c_tests": "CTest",
        "readme_jm_build": (
            " `just-makeit build`\n"
            "does the same when just-buildit is installed in the environment\n"
            "just-makeit runs from (`pip install just-makeit just-buildit`)."
        ),
        "readme_repair_windows": ", `delvewheel` on Windows",
    },
    # The make backend drives `cc` directly: no CMake, no Windows build
    # (its Makefile `$(error)`s there, gh-1368), no `make docs`, and
    # `just-makeit build` always configures with CMake.
    "make": {
        "readme_requirements": (
            "- A C99 compiler (GCC or Clang) and GNU make, on Linux or\n"
            "  macOS. The `make` build backend does not build on Windows."
        ),
        "readme_make_builds": "build the extension(s)",
        "readme_c_tests": "C tests",
        "readme_jm_build": "",
        "readme_repair_windows": "",
    },
}


#: `run`'s ``c_prefix`` when the caller says nothing: the project's name.
#: A sentinel, not None, because None is a real answer -- "no prefix" -- that
#: `--no-c-prefix` and the replay of an unprefixed project both give.
DEFAULT_C_PREFIX = object()


def makefile_text(
    project: str,
    build_system: str,
    *,
    pytest_: bool,
    schema: int,
    c_prefix: "str | None",
) -> str:
    """The Makefile `run` writes for *build_system*, as text.

    gh-1899. One render path for both `run` and the backend check in
    `makefile_backend`: a second copy of this context would be exactly the
    peer implementation that drifts, so `run` calls this too. The context is
    the one `run` builds for the Makefile: the project, the pytest flag, and the
    owner the include slots read (schema, and the C prefix when there is one).
    """
    owner = {"project": {"name": project, "schema": str(schema)}}
    if c_prefix is not None:
        owner["project"]["c_prefix"] = c_prefix
    ctx = _make_project_ctx(project, pytest_=pytest_)
    ctx.update(INC.ctx_slots(owner))
    tmpl = T.MAKEFILE if build_system == "cmake" else T.MAKEFILE_SIMPLE
    return T.render(tmpl, ctx)


#: A line that only one backend's Makefile carries, and that a patched
#: rendering keeps: the make Makefile's TARGETS list, the cmake Makefile's
#: cache rule. Each is checked against its template when used, so a template
#: that loses its marker fails the gate rather than silently skipping the check.
BACKEND_MARKER = {
    "make": "TARGETS :=",
    "cmake": "$(BUILD_DIR)/CMakeCache.txt:",
}


def other_backend(cfg: dict) -> str:
    """The build backend *cfg* does NOT name: the one a stale Makefile could be.

    gh-1899. Spelled once, because the flip is asked for in three places (the
    other-backend replay, the reconcile step and the status check), and a
    second spelling is where they would drift apart.
    """
    return "make" if C.build_system(cfg) == "cmake" else "cmake"


def _marked(text: str, backend: str) -> bool:
    """True when *text* has *backend*'s marker line (gh-1899)."""
    prefix = BACKEND_MARKER[backend]
    return any(line.lstrip().startswith(prefix) for line in text.splitlines())


def is_other_backend_makefile(
    root: Path,
    current: str,
    other_name: str,
    other: "Callable[[], str | None]",
) -> bool:
    """True when the project's Makefile is the OTHER backend's render (gh-1899).

    Switching `[project] build` leaves the old backend's Makefile in place: it
    is create-only, and nothing ties it to the manifest. *current* is the
    Makefile the replay renders for the manifest as it stands. A Makefile equal
    to that is not a mismatch. A Makefile without *other_name*'s marker cannot
    be that backend's render, so it is not one, and no replay is needed.
    Otherwise *other* is called (a whole replay) for that backend's Makefile,
    and a match with it is the mismatch. An author's edit matches neither, and
    is theirs, so jm does not claim it. CRLF is compared as LF, as `status`
    compares every other generated file (gh-1641).
    """
    path = root / "Makefile"
    if not path.is_file():
        return False
    real = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if real == current or not _marked(real, other_name):
        return False
    text = other()
    return text is not None and real == text.replace("\r\n", "\n")


def run(
    project: str,
    dest: Path | None = None,
    object_names: list[str] | None = None,
    state_vars: list[tuple[str, str, str]] | None = None,
    modules: list[str] | None = None,
    build_system: str = "cmake",
    perf: bool = False,
    mutable: bool = False,
    no_step: bool = False,
    no_reset: bool = False,
    no_state: bool = False,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    pytest_: bool = False,
    pytest_benchmark_: bool = False,
    find_packages: list[str] | None = None,
    pkg_modules: list[str] | None = None,
    c_deps: list[str] | None = None,
    platforms: list[str] | None = None,
    fragments: bool = False,
    c_style: str = "",
    c_format_command: list[str] | None = None,
    schema: int | None = None,
    c_prefix: "str | None | object" = DEFAULT_C_PREFIX,
    version: str = "0.1.0",
    layout: "str | None" = None,
) -> None:
    C.require_name(project, "project")
    # gh-1583: the schema decides the header layout, and the manifest that
    # records it is written last -- so the writers below are handed the
    # manifest's [project] table as their owner. The replay passes the real
    # project's schema, so a schema-8 project replays into its own layout.
    schema = C.CURRENT_SCHEMA if schema is None else schema
    owner = {"project": {"name": project, "schema": str(schema)}}
    # gh-1591 (decided on the issue): a NEW project is namespaced from its
    # first file, by its package name, so it never needs `jm upgrade`'s
    # respell -- on every path that creates one, the CLI and the Python API
    # the bundled examples use alike. `apply`'s replay passes the real
    # project's own key, None included, so an existing project without one
    # replays bare instead of acquiring this default.
    if c_prefix is DEFAULT_C_PREFIX:
        c_prefix = project
    # gh-1591: the C symbol prefix, validated before anything is written.
    # The replay passes the real project's, so a prefixed project replays
    # into its own symbols -- a replay without it would render every derived
    # name bare and `apply` would splice those into the sacred header.
    if c_prefix is not None:
        owner["project"]["c_prefix"] = c_prefix
        C.c_prefix(owner)
    # gh-2423: the native layout, validated before anything is written. Only
    # `apply`'s replay passes it -- `jm new` itself writes no file it moves,
    # so a new project declares it in its manifest before its first object.
    if layout is not None:
        owner["project"]["layout"] = layout
        INC.layout(owner)

    root = dest or (Path.cwd() / project)
    if root.exists() and any(root.iterdir()):
        print(
            f"error: '{root}' already exists and is not empty.",
            file=sys.stderr,
        )
        sys.exit(1)

    ctx = _make_project_ctx(project, version=version, pytest_=pytest_)
    ctx.update(INC.ctx_slots(owner))

    def r(tmpl):
        return T.render(tmpl, ctx)

    print(f"just-makeit: creating project '{project}' in {root}")
    print()

    if build_system == "cmake":
        _write(root / "CMakeLists.txt", r(T.CMAKE_LISTS_TOP))
        _write(
            root / "Makefile",
            makefile_text(
                project,
                "cmake",
                pytest_=pytest_,
                schema=schema,
                c_prefix=c_prefix,
            ),
        )
        # Only the cmake build emits a compile database, so only it can run
        # clang-tidy — and a config with nothing to run it is exactly the dead
        # file gh-941 was about. The `make` build system gets no .clang-tidy.
        _write(root / ".clang-tidy", r(T.CLANG_TIDY))
        # gh-1376: an IDE opening the folder configures through a preset, not
        # through the Makefile, and neither Visual Studio nor VS Code finds a
        # venv's interpreter on its own. The preset names the project's
        # `.venv` explicitly rather than setting Python3_ROOT_DIR, because a
        # root dir falls back to whatever interpreter CMake finds when the
        # venv is absent -- the wrong-numpy configure gh-814 refuses.
        _write(root / "CMakePresets.json", T.CMAKE_PRESETS_JSON)
        ctx.update(README_BY_BACKEND["cmake"])
    else:
        _write(
            root / "Makefile",
            makefile_text(
                project,
                "make",
                pytest_=pytest_,
                schema=schema,
                c_prefix=c_prefix,
            ),
        )
        # The README describes the build its own Makefile runs: one README
        # for both backends advertised CMake, a Windows build and
        # `make docs` to a make-backend project, which has none of them.
        ctx.update(README_BY_BACKEND["make"])
    _write(root / "pyproject.toml", r(T.PYPROJECT_TOML))
    # gh-2629: the README names the floor the pyproject.toml just written
    # declares, read back from it -- that file is the one statement of it.
    ctx["python_floor"] = _pythons.floor(root)
    _write(root / "README.md", r(T.README_MD))
    _write(root / ".gitignore", r(T.GITIGNORE))
    # gh-1641: jm renders LF everywhere (gh-1368), and Git for Windows
    # checks text out CRLF unless the project says otherwise -- which read
    # every managed file as STALE on a fresh Windows clone.
    _write(root / ".gitattributes", T.GITATTRIBUTES)
    _write(root / "Doxyfile", r(T.DOXYFILE))
    _write(root / "zensical.toml", r(T.ZENSICAL_TOML))
    _write(root / "docs" / "index.md", r(T.DOCS_INDEX_MD))
    _write(root / "docs" / "api.md", r(T.DOCS_API_MD))
    _write(INC.path(root, "clib_common.h", owner), r(T.CLIB_COMMON_H))
    _write(INC.path(root, "pyex_common.h", owner), r(T.PYEX_COMMON_H))
    _write(INC.path(root, f"{project}.h", owner), r(T.UMBRELLA_H))
    if perf:
        _write(INC.path(root, "jm_perf.h", owner), r(T.JM_PERF_H))
        _write(INC.path(root, "jm_simd.h", owner), T.JM_SIMD_H)

    if build_system == "cmake":
        # gh-1589: both packaging templates are born owned -- `apply` renders
        # them whole while the token names the file.
        # gh-1581: the .pc is named for the library it describes; the file
        # name IS the pkg-config package name.
        pc_in = f"{project}.pc.in"
        _write(
            root / "cmake" / pc_in, T.owned_packaging(r(T.CMAKE_PC_IN), pc_in)
        )
        config_in = f"{project}-config.cmake.in"
        _write(
            root / "cmake" / config_in,
            T.owned_packaging(r(T.CMAKE_CONFIG_IN), config_in),
        )
        _write(root / INC.lib_c_rel(project), r(T.LIB_STUB_C))

    cfg = C.from_new(
        project,
        build_system=build_system,
        perf=perf,
        pytest_=pytest_,
        pytest_benchmark_=pytest_benchmark_,
        schema=schema,
    )
    if c_prefix is not None:
        cfg.setdefault("project", {})["c_prefix"] = c_prefix
    if layout is not None:
        cfg.setdefault("project", {})["layout"] = layout
    # External-dep declarations land in [project]; the root wiring they
    # declare is written below, once the manifest holding them is saved.
    if find_packages:
        cfg.setdefault("project", {})["find_packages"] = list(find_packages)
    if pkg_modules:
        cfg.setdefault("project", {})["pkg_modules"] = list(pkg_modules)
    if c_deps:
        cfg.setdefault("project", {})["c_deps"] = list(c_deps)
    # Persist a non-default platform set (e.g. one that opts into Windows) so
    # apply / status render the same CMakeLists; absence means the default
    # linux/macos (gh-213).
    if platforms:
        cfg.setdefault("project", {})["platforms"] = list(platforms)
    # gh-265: seed a .clang-format so the post-command formatting pass has a
    # house style to format to. gh-773: the manifest records
    # `c_format_command`, not `c_style` — declaring the command is the opt-in
    # (as it already is for py_format_command), and it is also the only one of
    # the two that says *which* binary, which is what makes the output the
    # same on two machines. A new project should not be scaffolded into the
    # spelling that leaves that to PATH.
    #
    # gh-960: `c_format_command` is how `_apply._replay` carries the real
    # project's declared command in verbatim. It is a separate argument rather
    # than a value of `c_style` because the two answer different questions —
    # *whether* to format and *which binary does it* — and the replay has to
    # reproduce both, not the defaults for both.
    if c_format_command is not None:
        cfg.setdefault("project", {})["c_format_command"] = list(
            c_format_command
        )
    if c_style:
        if c_style == "clang-format":
            cfg.setdefault("project", {}).setdefault(
                "c_format_command", list(C.DEFAULT_C_FORMAT_COMMAND)
            )
        else:
            cfg.setdefault("project", {})["c_style"] = c_style
    # Keyed on what the manifest now says rather than on the arguments: a
    # project reaches the formatting opt-in through either spelling, and the
    # scaffold must grow the style file in both cases (gh-960 — without it the
    # replay `apply` compares against has no `.clang-format` of its own, so an
    # out-of-date one can never be seen).
    if C.c_formatting_on(cfg):
        cf = root / ".clang-format"
        if not cf.exists():
            _write(cf, T.CLANG_FORMAT)
    C.save(root, cfg)
    # Opt into the per-component fragment layout up front: with the include
    # globs already in the manifest, every object/module scaffolded below
    # (and later) routes to its own objects/<name>.toml / modules/<name>.toml
    # fragment instead of the central manifest. Empty globs resolve to
    # nothing, so this is safe before any fragment exists.
    if fragments:
        globs = C._toml_string_array(["objects/*.toml", "modules/*.toml"])
        mpath = root / C.FILENAME
        _textio.write_text(
            mpath, f"include = {globs}\n\n" + mpath.read_text(encoding="utf-8")
        )
    print(f"  create  {root / C.FILENAME}")
    _write(root / "bootstrap.toml", r(T.BOOTSTRAP_TOML))
    if build_system == "cmake":
        _write_project_wiring(root, project, cfg)
    # gh-2640: born owned, like each component's scaffolded test (gh-1489):
    # `apply` renders it whole while the token is there, so it follows the
    # manifest's version, and deleting the token makes it the author's.
    _write(root / "src" / project / "tests" / "__init__.py", T.TESTS_INIT_PY)
    _write(root / smoke_test_rel(project), _smoke_test_text(root, project, r))
    print()

    if object_names:
        from . import _object

        for obj in object_names:
            _object.run(
                root,
                obj,
                None,
                None if (no_state or not state_vars) else state_vars,
                perf=perf,
                mutable=mutable,
                no_step=no_step,
                no_reset=no_reset,
                no_state=no_state,
                arg_type=arg_type,
                return_type=return_type,
                _hint=False,
            )
            print()
        print(
            f"{Color.done('Done!')}  {Color.cmd(f'cd {root.name} && make && make test')}"
        )
    elif modules:
        from . import _module

        _write(
            root / "src" / ctx["package"] / "__init__.py",
            r(T.PACKAGE_INIT_PY_MINIMAL),
        )
        _write(root / "benchmarks" / "history" / ".gitkeep", "")
        print()
        for mod in modules:
            _module.run(root, mod)
        print()
        print(
            f"{Color.done('Done!')}  {Color.cmd(f'cd {root.name} && just-makeit object <name> --module <module>')}"
        )
    else:
        _write(
            root / "src" / ctx["package"] / "__init__.py",
            r(T.PACKAGE_INIT_PY_MINIMAL),
        )
        _write(root / "benchmarks" / "history" / ".gitkeep", "")
        print()
        print(
            f"{Color.done('Done!')}  {Color.cmd(f'cd {root.name} && just-makeit object <name>')}"
        )

    # gh-265: format the freshly scaffolded tree to the project's house style.
    # `new` creates its own subdir (root != cwd), so the CLI's post-command
    # hook does not cover it — sweep it here. No-op unless the manifest opts
    # into C formatting.
    if C.c_formatting_on(cfg):
        from . import _cfmt

        _cfmt.format_project(root, cfg)
    # gh-746: same for generated Python — `new` creates its own subdir, so the
    # CLI's post-command hook does not reach it. No-op unless the manifest
    # declares py_format_command.
    from . import _pyfmt

    _pyfmt.format_project(root, cfg)
