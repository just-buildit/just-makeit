"""
_init.py — `just-makeit init` command.

Creates a complete working C99 Python extension project in a new directory.
"""

import json
import sysconfig
import sys
from pathlib import Path

from . import _config as C
from . import _templates as T


def _to_title(snake: str) -> str:
    return "".join(w.title() for w in snake.split("_"))


def _make_ctx(component: str, version: str = "0.1.0") -> dict[str, str]:
    return {
        "component": component,
        "Component": _to_title(component),
        "COMPONENT": component.upper(),
        "package": component,
        "project": component.replace("_", "-"),
        "project_underscore": component,
        "version": version,
    }


def _write_compile_commands(root: Path, component: str) -> None:
    r = root.resolve()
    python_inc = sysconfig.get_path("include")
    try:
        import numpy as np

        numpy_inc = np.get_include()
    except ImportError:
        numpy_inc = None

    inc_dirs = [
        str(r / "native" / "inc"),
        str(r / "native" / "inc" / component),
    ]
    if python_inc:
        inc_dirs.append(python_inc)
    if numpy_inc:
        inc_dirs.append(numpy_inc)

    inc_flags = " ".join(f"-I{d}" for d in inc_dirs)
    base = f"cc -std=c99 -Wall -Wextra -fPIC {inc_flags}"

    def entry(src_rel: str, flags: str) -> dict:
        abs_src = str(r / src_rel)
        return {
            "directory": str(r),
            "command": f"{flags} -c {abs_src} -o /dev/null",
            "file": abs_src,
        }

    entries = [
        entry(f"native/src/{component}/{component}_core.c", base),
        entry(f"native/src/{component}/{component}_ext.c", base),
        entry(
            f"native/tests/test_{component}_core.c",
            f"cc -std=c99 -Wall -I{r / 'native' / 'inc'} -I{r / 'native' / 'inc' / component}",
        ),
    ]

    dest = root / "compile_commands.json"
    dest.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"  create  {dest}")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  create  {path}")


def run(
    component: str,
    dest: Path | None = None,
    state_vars: list[tuple[str, str, str]] | None = None,
) -> None:
    if not component.replace("_", "").isalnum() or component[0].isdigit():
        print(
            f"error: '{component}' is not a valid component name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = dest or (Path.cwd() / component)
    if root.exists() and any(root.iterdir()):
        print(
            f"error: '{root}' already exists and is not empty.",
            file=sys.stderr,
        )
        sys.exit(1)

    vars_ = state_vars or [("gain", "double", "0.0")]
    ctx = _make_ctx(component)
    ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], vars_))

    def r(tmpl):
        return T.render(tmpl, ctx)

    print(f"just-makeit: initialising '{component}' in {root}")
    print()

    comp = ctx["component"]
    pkg = ctx["package"]

    _write(root / "CMakeLists.txt", r(T.CMAKE_LISTS))
    _write(root / "Makefile", r(T.MAKEFILE))
    _write(root / "pyproject.toml", r(T.PYPROJECT_TOML))
    _write(root / "README.md", r(T.README_MD))
    _write(root / ".gitignore", r(T.GITIGNORE))

    _write(root / "native" / "inc" / "clib_common.h", r(T.CLIB_COMMON_H))
    _write(root / "native" / "inc" / "pyex_common.h", r(T.PYEX_COMMON_H))
    _write(root / "native" / "inc" / comp / f"{comp}_core.h", r(T.COMPONENT_CORE_H))

    _write(root / "native" / "src" / comp / f"{comp}_core.c", r(T.COMPONENT_CORE_C))
    _write(root / "native" / "src" / comp / f"{comp}_ext.c", r(T.COMPONENT_EXT_C))

    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(T.COMPONENT_TEST_C))

    _write(root / "src" / pkg / "__init__.py", r(T.PACKAGE_INIT_PY))
    _write(root / "src" / pkg / f"{comp}.pyi", r(T.COMPONENT_PYI))
    _write(root / "src" / pkg / "tests" / "__init__.py", T.TESTS_INIT_PY)
    _write(root / "src" / pkg / "tests" / f"test_{comp}.py", r(T.PYTEST_TEST))

    _write_compile_commands(root, comp)

    cfg = C.from_init(comp, ctx["version"], vars_)
    C.save(root, cfg)
    print(f"  create  {root / C.FILENAME}")

    print()
    print(f"Done!  cd {root.name} && make && make test")
