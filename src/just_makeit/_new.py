"""
_new.py — `just-makeit new` command.

Creates a new project scaffold in a new directory. With --object also
scaffolds the first object in the same step.  With --module (repeatable)
scaffolds one or more empty extension modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _color as Color
from . import _config as C
from . import _render as T


def _make_project_ctx(
    project: str,
    version: str = "0.1.0",
    pytest_: bool = False,
) -> dict[str, str]:
    if pytest_:
        ensure_win = '\t$(PYTHON) -c "import pytest" 2>nul || $(PYTHON) -m pip install pytest\n'
        ensure_unix = '\t@$(PYTHON) -c "import pytest" 2>/dev/null || $(PYTHON) -m pip install pytest\n'
        cmd_win = "$(PYTHON) -c \"import subprocess,sys; r=subprocess.run([sys.executable,'-m','pytest','src/','-v']); sys.exit(0 if r.returncode in(0,5) else r.returncode)\""
        cmd_unix = "$(PYTHON) -m pytest src/ -v; ret=$$?; \\\n\t\t[ $$ret -eq 0 ] || [ $$ret -eq 5 ] || exit $$ret"
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
        "ensure_pytest_win": ensure_win,
        "ensure_pytest_unix": ensure_unix,
        "py_test_cmd_win": cmd_win,
        "py_test_cmd_unix": cmd_unix,
        "py_test_label": "pytest" if pytest_ else "unittest",
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  create  {path}")


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
) -> None:
    C.require_name(project, "project")

    root = dest or (Path.cwd() / project)
    if root.exists() and any(root.iterdir()):
        print(
            f"error: '{root}' already exists and is not empty.",
            file=sys.stderr,
        )
        sys.exit(1)

    ctx = _make_project_ctx(project, pytest_=pytest_)

    def r(tmpl):
        return T.render(tmpl, ctx)

    print(f"just-makeit: creating project '{project}' in {root}")
    print()

    if build_system == "cmake":
        _write(root / "CMakeLists.txt", r(T.CMAKE_LISTS_TOP))
        _write(root / "Makefile", r(T.MAKEFILE))
        # Only the cmake build emits a compile database, so only it can run
        # clang-tidy — and a config with nothing to run it is exactly the dead
        # file gh-941 was about. The `make` build system gets no .clang-tidy.
        _write(root / ".clang-tidy", r(T.CLANG_TIDY))
    else:
        _write(root / "Makefile", r(T.MAKEFILE_SIMPLE))
    _write(root / "pyproject.toml", r(T.PYPROJECT_TOML))
    _write(root / "README.md", r(T.README_MD))
    _write(root / ".gitignore", r(T.GITIGNORE))
    _write(root / "Doxyfile", r(T.DOXYFILE))
    _write(root / "zensical.toml", r(T.ZENSICAL_TOML))
    _write(root / "docs" / "index.md", r(T.DOCS_INDEX_MD))
    _write(root / "docs" / "api.md", r(T.DOCS_API_MD))
    _write(root / "native" / "inc" / "clib_common.h", r(T.CLIB_COMMON_H))
    _write(root / "native" / "inc" / "pyex_common.h", r(T.PYEX_COMMON_H))
    _write(root / "native" / "inc" / f"{project}.h", r(T.UMBRELLA_H))
    if perf:
        _write(root / "native" / "inc" / "jm_perf.h", r(T.JM_PERF_H))
        _write(root / "native" / "inc" / "jm_simd.h", T.JM_SIMD_H)

    if build_system == "cmake":
        _write(
            root / "cmake" / f"{project.replace('_', '-')}.pc.in",
            r(T.CMAKE_PC_IN),
        )
        _write(
            root / "cmake" / f"{project}-config.cmake.in", r(T.CMAKE_CONFIG_IN)
        )
        _write(root / "native" / "src" / f"{project}_lib.c", r(T.LIB_STUB_C))

    cfg = C.from_new(
        project,
        build_system=build_system,
        perf=perf,
        pytest_=pytest_,
        pytest_benchmark_=pytest_benchmark_,
    )
    # External-dep declarations land in [project] so jm apply's
    # _splice_cmake_external_deps picks them up and writes the
    # `# ── External deps` sentinel block in the top CMakeLists.txt.
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
        mpath.write_text(
            f"include = {globs}\n\n" + mpath.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    print(f"  create  {root / C.FILENAME}")
    _write(root / "bootstrap.toml", r(T.BOOTSTRAP_TOML))
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
