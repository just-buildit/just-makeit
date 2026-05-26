"""
_new.py — `just-makeit new` command.

Creates a new project scaffold in a new directory. With --object also
scaffolds the first object in the same step.  With --module (repeatable)
scaffolds one or more empty extension modules.
"""

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
        ensure_win = (
            '\t$(PYTHON) -c "import pytest" 2>nul || $(PYTHON) -m pip install pytest\n'
        )
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
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    pytest_: bool = False,
    pytest_benchmark_: bool = False,
) -> None:
    if not project.replace("_", "").isalnum() or project[0].isdigit():
        print(
            f"error: '{project}' is not a valid project name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

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
        _write(root / "cmake" / f"{project.replace('_', '-')}.pc.in", r(T.CMAKE_PC_IN))
        _write(root / "cmake" / f"{project}-config.cmake.in", r(T.CMAKE_CONFIG_IN))
        _write(root / "native" / "src" / f"{project}_lib.c", r(T.LIB_STUB_C))

    cfg = C.from_new(
        project,
        build_system=build_system,
        perf=perf,
        pytest_=pytest_,
        pytest_benchmark_=pytest_benchmark_,
    )
    C.save(root, cfg)
    print(f"  create  {root / C.FILENAME}")
    _write(root / "jb.toml", r(T.JB_TOML))
    print()

    if object_names:
        from . import _object

        for obj in object_names:
            _object.run(
                root,
                obj,
                None,
                state_vars,
                perf=perf,
                mutable=mutable,
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
            root / "src" / ctx["package"] / "__init__.py", r(T.PACKAGE_INIT_PY_MINIMAL)
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
            root / "src" / ctx["package"] / "__init__.py", r(T.PACKAGE_INIT_PY_MINIMAL)
        )
        _write(root / "benchmarks" / "history" / ".gitkeep", "")
        print()
        print(
            f"{Color.done('Done!')}  {Color.cmd(f'cd {root.name} && just-makeit object <name>')}"
        )
