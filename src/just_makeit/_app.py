"""
_app.py — `just-makeit app` command.

Scaffolds a shippable standalone application from an existing component:

    just-makeit app --target c       --object engine --name dsp_tool
    just-makeit app --target console --object engine --name dsp_tool
    just-makeit app --target pep723  --object engine --name dsp_tool

Targets
-------
c        Standalone C executable.  Generates native/src/app/<name>.c and
         appends an add_executable target to CMakeLists.txt.

console  Python console script.  Generates src/<pkg>/cli.py with argparse
         boilerplate and updates [project.scripts] in pyproject.toml.

pep723   PEP 723 inline-script.  Generates <name>.py in the project root
         with an embedded ``# /// script`` dependency block, runnable via
         ``uv run <name>.py`` without a full install.
"""

import sys
from pathlib import Path

from . import _config as C
from . import _render as R
from ._init import _to_title


_APP_CMAKE_SENTINEL = "# ── App ──"
_APP_CMAKE_END = "# ── App end ──"

_PYTYPE = {
    "float": "float",
    "double": "float",
    "float _Complex": "complex",
    "double _Complex": "complex",
    "int": "int",
    "int8_t": "int",
    "int16_t": "int",
    "int32_t": "int",
    "int64_t": "int",
    "uint8_t": "int",
    "uint16_t": "int",
    "uint32_t": "int",
    "uint64_t": "int",
    "size_t": "int",
}


def _py_default(c_default: str) -> str:
    """Strip C suffixes from a default literal to get a Python literal."""
    s = c_default.strip()
    for suffix in ("ull", "ul", "ll", "u", "l", "f"):
        if s.lower().endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def _argparse_block(ctor_scalars: list[tuple]) -> str:
    """Build p.add_argument(...) lines for each ctor scalar, indented 4 sp."""
    lines = []
    for name, ctype, default in ctor_scalars:
        pytype = _PYTYPE.get(ctype, "str")
        pydef = _py_default(default)
        lines.append(
            f"    p.add_argument(\n"
            f'        "--{name}", type={pytype}, default={pydef},\n'
            f'        help="{name} (default: {pydef})",\n'
            f"    )"
        )
    return "\n".join(lines)


def _py_create_args(ctor_scalars: list[tuple]) -> str:
    """Build keyword-argument list for the Python constructor call."""
    return ", ".join(f"{n}=args.{n}" for n, _, _ in ctor_scalars)


def _ctor_c_args(ctor_scalars: list[tuple]) -> str:
    """Build commented C constructor argument list."""
    if not ctor_scalars:
        return ""
    parts = ", ".join(f"/* {n}= */{d}" for n, _, d in ctor_scalars)
    return parts


def _cmake_app_block(name: str, component: str) -> str:
    return (
        f"{_APP_CMAKE_SENTINEL}"
        "─────────────────────────────────────────────────────────\n"
        f"add_executable({name} native/src/app/{name}.c)\n"
        f"target_link_libraries({name} PRIVATE {component}_core)\n"
        f"install(TARGETS {name} DESTINATION bin)\n"
        f"{_APP_CMAKE_END}"
        "─────────────────────────────────────────────────────────\n"
    )


def _splice_cmake(cmake: Path, name: str, component: str) -> None:
    """Insert or replace the App block in CMakeLists.txt."""
    text = cmake.read_text(encoding="utf-8")
    block = _cmake_app_block(name, component)
    start = text.find(_APP_CMAKE_SENTINEL)
    end = text.find(_APP_CMAKE_END)
    if start != -1 and end != -1:
        end = text.index("\n", end) + 1
        text = text[:start] + block + text[end:]
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    cmake.write_text(text, encoding="utf-8")


def _update_pyproject_scripts(root: Path, name: str, pkg: str) -> bool:
    """Add/update [project.scripts] in pyproject.toml using tomlkit.

    Returns True on success, False if tomlkit is absent or pyproject.toml
    does not exist (caller should print manual instructions instead)."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        import tomlkit as _tk
    except ModuleNotFoundError:
        return False

    doc = _tk.loads(pyproject.read_text(encoding="utf-8"))
    if "project" not in doc:
        doc.add("project", _tk.table())
    if "scripts" not in doc["project"]:
        doc["project"].add("scripts", _tk.table())
    doc["project"]["scripts"][name] = f"{pkg}.cli:main"
    pyproject.write_text(_tk.dumps(doc), encoding="utf-8")
    return True


def _build_ctx(
    cfg: dict,
    component: str,
    name: str,
    target: str,
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    Component = _to_title(component)
    ctor_scalars = [
        (n, t, d)
        for n, t, d in C.state_vars(cfg, component)
        if not any(
            s.get("no_ctor")
            for s in cfg.get(component, {}).get("state", [])
            if s["name"] == n
        )
    ]
    c_args = _ctor_c_args(ctor_scalars)
    create_call = f"{component}_create({c_args})" if c_args else f"{component}_create()"
    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": version,
        "component": component,
        "Component": Component,
        "argparse_state_args": _argparse_block(ctor_scalars),
        "py_create_args": _py_create_args(ctor_scalars),
        "ctor_c_args": c_args,
        "app_create_line": (f"    {component}_state_t *state = {create_call};"),
    }


def run(
    root: Path,
    cfg: dict | None = None,
    *,
    target: str = "c",
    name: str | None = None,
    object_: str | None = None,
) -> None:
    if cfg is None:
        cfg_path = root / C.FILENAME
        if not cfg_path.exists():
            print(
                f"error: no {C.FILENAME} found in {root}.\n"
                "Run 'just-makeit new' first.",
                file=sys.stderr,
            )
            sys.exit(1)
        cfg = C.load(root)

    pkg = C.project_name(cfg)
    if not pkg:
        print("error: [project].name missing from just-makeit.toml.", file=sys.stderr)
        sys.exit(1)

    comps = C.components(cfg)
    if object_ is None:
        if not comps:
            print(
                "error: no components found — run 'just-makeit object' first.",
                file=sys.stderr,
            )
            sys.exit(1)
        object_ = comps[0]
    elif object_ not in comps:
        print(f"error: object '{object_}' not found.", file=sys.stderr)
        sys.exit(1)

    if name is None:
        name = pkg

    if target not in ("c", "console", "pep723"):
        print(
            f"error: unknown target '{target}'. Use c, console, or pep723.",
            file=sys.stderr,
        )
        sys.exit(1)

    ctx = _build_ctx(cfg, object_, name, target)

    print(f"just-makeit: scaffolding app '{name}' (target={target})")
    print()

    if target == "c":
        _run_c(root, cfg, ctx, name, object_)
    elif target == "console":
        _run_console(root, cfg, ctx, name, pkg)
    else:
        _run_pep723(root, ctx, name)

    C.set_app(cfg, target, name, object_)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")
    print()
    _print_summary(target, root, name, pkg)


def _run_c(root: Path, cfg: dict, ctx: dict, name: str, component: str) -> None:
    app_dir = root / "native" / "src" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    main_c = app_dir / f"{name}.c"
    main_c.write_text(R.render(R.APP_MAIN_C, ctx), encoding="utf-8")
    verb = "update" if main_c.exists() else "create"
    print(f"  {verb}  {main_c}")

    cmake = root / "CMakeLists.txt"
    if cmake.exists():
        _splice_cmake(cmake, name, component)
        print(f"  update  {cmake}")
    else:
        print(
            f"  note: CMakeLists.txt not found — add this manually:\n"
            f"    add_executable({name} native/src/app/{name}.c)\n"
            f"    target_link_libraries({name} PRIVATE {component}_core)"
        )


def _run_console(root: Path, cfg: dict, ctx: dict, name: str, pkg: str) -> None:
    cli_py = root / "src" / pkg / "cli.py"
    cli_py.parent.mkdir(parents=True, exist_ok=True)
    cli_py.write_text(R.render(R.APP_CONSOLE_CLI, ctx), encoding="utf-8")
    verb = "update" if cli_py.exists() else "create"
    print(f"  {verb}  {cli_py}")

    updated = _update_pyproject_scripts(root, name, pkg)
    if updated:
        print(f"  update  {root / 'pyproject.toml'}")
    else:
        print(
            f"  note: add to pyproject.toml manually:\n"
            f"    [project.scripts]\n"
            f'    {name} = "{pkg}.cli:main"'
        )


def _run_pep723(root: Path, ctx: dict, name: str) -> None:
    script = root / f"{name}.py"
    script.write_text(R.render(R.APP_PEP723, ctx), encoding="utf-8")
    verb = "update" if script.exists() else "create"
    print(f"  {verb}  {script}")


def _print_summary(target: str, root: Path, name: str, pkg: str) -> None:
    if target == "c":
        print("Done!  C executable scaffold created.")
        print(f"  Implement I/O in native/src/app/{name}.c")
        print(f"  Build:  make && ./build/{name}")
    elif target == "console":
        print("Done!  Console script scaffold created.")
        print(f"  Implement processing loop in src/{pkg}/cli.py")
        print("  Install:  pip install -e .")
        print(f"  Run:      {name} --help")
    else:
        print(f"Done!  PEP 723 script created: {name}.py")
        print(f"  Implement processing loop in {name}.py")
        print(f"  Run:      uv run {name}.py --help")
        print(f"  Share:    distribute {name}.py — no install needed")
        print(f"  Note:     requires {pkg} on PyPI")
