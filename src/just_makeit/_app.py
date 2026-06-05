"""
_app.py — `just-makeit app` command.

Scaffolds a shippable standalone application from an existing component:

    just-makeit app --target c       --object engine --name dsp_tool
    just-makeit app --target console --object engine --name dsp_tool
    just-makeit app --target pep723  --object engine --name dsp_tool

For a scalar ``step(x) -> y`` object, all three targets are generated as
*working* sample-stream tools: a real argument parser (one ``--flag`` per
ctor state var, plus ``--input``/``--output``) and a read -> step() -> write
loop over the object's sample type — no hand-editing required. Extra flags can
be declared with ``--flag name:type[:default[:help]]`` (persisted as
``[[app.flags]]``) and appear in both the C and Python parsers.

Objects that don't fit the scalar-stream shape (``void`` arg/return, generators,
consumers, ``no_step``) fall back to an ``<<IMPLEMENT>>`` stub.

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

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _render as R
from . import _types as T
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

# C types whose CLI value can be parsed from a string into a typed local.
# `{a}` is the argv token expression. Types absent here (complex, string) are
# not turned into CLI flags; their ctor default is used verbatim instead.
_C_PARSE = {
    "float": "strtof({a}, NULL)",
    "double": "strtod({a}, NULL)",
    "int": "(int)strtol({a}, NULL, 10)",
    "int8_t": "(int8_t)strtol({a}, NULL, 10)",
    "int16_t": "(int16_t)strtol({a}, NULL, 10)",
    "int32_t": "(int32_t)strtol({a}, NULL, 10)",
    "int64_t": "(int64_t)strtoll({a}, NULL, 10)",
    "uint8_t": "(uint8_t)strtoul({a}, NULL, 10)",
    "uint16_t": "(uint16_t)strtoul({a}, NULL, 10)",
    "uint32_t": "(uint32_t)strtoul({a}, NULL, 10)",
    "uint64_t": "(uint64_t)strtoull({a}, NULL, 10)",
    "size_t": "(size_t)strtoull({a}, NULL, 10)",
}


def _py_default(c_default: str) -> str:
    """Strip C suffixes from a default literal to get a Python literal."""
    s = c_default.strip()
    for suffix in ("ull", "ul", "ll", "u", "l", "f"):
        if s.lower().endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def _np_dtype(ctype: str) -> str:
    """NumPy dtype string for a scalar C type (e.g. 'float' -> 'np.float32')."""
    return T._CTYPE_META.get(ctype, {}).get("py_type", "np.float32")


def _flag_help(name: str, supplied: str, default) -> str:
    return supplied if supplied else f"{name} (default: {default})"


# ── flag model ───────────────────────────────────────────────────────────────
# A "flag" is a dict {name, type, default, help, ctor}. `ctor=True` means it
# feeds the component constructor (derived from a ctor state var); `ctor=False`
# is an extra [[app.flags]] flag available for custom logic.
def _ctor_flags(cfg: dict, component: str) -> list[dict]:
    state = cfg.get(component, {}).get("state", [])
    no_ctor = {s["name"] for s in state if s.get("no_ctor")}
    out = []
    for n, t, d in C.state_vars(cfg, component):
        if n in no_ctor:
            continue
        out.append(
            {"name": n, "type": t, "default": d, "help": "", "ctor": True}
        )
    return out


def _extra_flags(flags: list[dict] | None) -> list[dict]:
    return [
        {
            "name": f["name"],
            "type": f["type"],
            "default": f.get("default", ""),
            "help": f.get("help", ""),
            "ctor": False,
        }
        for f in flags or []
    ]


# ── Python argparse generation ───────────────────────────────────────────────
def _argparse_block(flags: list[dict]) -> str:
    """Build p.add_argument(...) lines for each flag, indented 4 sp."""
    lines = []
    for f in flags:
        pytype = _PYTYPE.get(f["type"], "str")
        pydef = _py_default(f["default"]) if f["default"] else None
        helptext = _flag_help(f["name"], f["help"], pydef)
        if pydef is None:
            default_repr = "None"
        elif pytype in ("float", "int", "complex"):
            default_repr = pydef  # bare numeric literal
        else:
            default_repr = repr(pydef)  # quoted string
        lines.append(
            f"    p.add_argument(\n"
            f'        "--{f["name"]}", type={pytype}, default={default_repr},\n'
            f'        help="{helptext}",\n'
            f"    )"
        )
    return "\n".join(lines)


def _py_create_args(flags: list[dict]) -> str:
    """Keyword-args for the Python constructor from ctor flags."""
    return ", ".join(
        f"{f['name']}=args.{f['name']}" for f in flags if f["ctor"]
    )


def _py_io_loop(component: str, Component: str, arg_t: str, ret_t: str) -> str:
    """4-space-indented Python body: read -> obj.step() -> write."""
    in_dtype = _np_dtype(arg_t)
    out_dtype = _np_dtype(ret_t)
    return (
        f"    if args.input:\n"
        f"        data = np.fromfile(args.input, dtype={in_dtype})\n"
        f"    else:\n"
        f"        buf = sys.stdin.buffer.read()\n"
        f"        data = np.frombuffer(buf, dtype={in_dtype})\n"
        f"    obj = {Component}(<<py_create_args>>)\n"
        f"    out = np.array(\n"
        f"        [obj.step(x) for x in data], dtype={out_dtype}\n"
        f"    )\n"
        f"    if args.output:\n"
        f"        out.tofile(args.output)\n"
        f"    else:\n"
        f"        sys.stdout.buffer.write(out.tobytes())"
    )


# ── C generation ─────────────────────────────────────────────────────────────
def _ctor_c_args(flags: list[dict], parsed: bool) -> str:
    """C constructor args. When `parsed`, a CLI-parseable ctor flag passes its
    parsed local by name; otherwise (and for non-parseable types) the default
    literal is passed inline (commented)."""
    parts = []
    for f in flags:
        if not f["ctor"]:
            continue
        if parsed and f["type"] in _C_PARSE:
            parts.append(f["name"])
        else:
            parts.append(f"/* {f['name']}= */{f['default']}")
    return ", ".join(parts)


def _c_argv_parser(name: str, flags: list[dict]) -> str:
    """Generate the C argv parsing block: typed decls + a strcmp loop +
    --input/--output, then open the files. 4-space indented."""
    decls = []
    matches = []
    usage_parts = []
    for f in flags:
        ct = f["type"]
        if ct not in _C_PARSE:
            continue  # not CLI-parseable; ctor uses its default literal
        decls.append(f"    {ct} {f['name']} = {f['default']};")
        parse = _C_PARSE[ct].format(a="argv[++i]")
        matches.append(
            f'        if (!strcmp(argv[i], "--{f["name"]}") && i + 1 < argc) {{\n'
            f"            {f['name']} = {parse};\n"
            f"        }} else "
        )
        usage_parts.append(f"[--{f['name']} V]")

    decls.append("    const char *in_path = NULL;")
    decls.append("    const char *out_path = NULL;")
    usage_parts += ["[--input FILE]", "[--output FILE]"]
    usage = f"usage: {name} " + " ".join(usage_parts)

    loop = (
        "    for (int i = 1; i < argc; i++) {\n"
        + "".join(matches)
        + 'if ((!strcmp(argv[i], "--input") || !strcmp(argv[i], "-i"))\n'
        "                   && i + 1 < argc) {\n"
        "            in_path = argv[++i];\n"
        '        } else if ((!strcmp(argv[i], "--output")\n'
        '                    || !strcmp(argv[i], "-o")) && i + 1 < argc) {\n'
        "            out_path = argv[++i];\n"
        "        } else {\n"
        f'            fprintf(stderr, "{usage}\\n");\n'
        "            return 2;\n"
        "        }\n"
        "    }"
    )
    # Suppress unused-variable warnings for extra (non-ctor) flags the
    # generated loop doesn't consume — they're for hand-written custom logic.
    voids = [
        f"    (void){f['name']};"
        for f in flags
        if f["type"] in _C_PARSE and not f["ctor"]
    ]
    open_files = (
        '    FILE *in = in_path ? fopen(in_path, "rb") : stdin;\n'
        '    FILE *out = out_path ? fopen(out_path, "wb") : stdout;\n'
        "    if (!in || !out) {\n"
        '        fprintf(stderr, "error: cannot open input/output\\n");\n'
        "        return 1;\n"
        "    }"
    )
    chunks = [*decls, "", loop]
    if voids:
        chunks += ["", *voids]
    chunks += ["", open_files]
    return "\n".join(chunks)


def _c_io_loop(component: str, arg_t: str, ret_t: str) -> str:
    """4-space-indented C body: read -> <comp>_step() -> write."""
    return (
        f"    {arg_t} x;\n"
        f"    while (fread(&x, sizeof x, 1, in) == 1) {{\n"
        f"        {ret_t} y = {component}_step(state, x);\n"
        f"        fwrite(&y, sizeof y, 1, out);\n"
        f"    }}"
    )


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


def _generatable(cfg: dict, component: str) -> bool:
    """True if the object is a scalar step(x)->y over a known sample type —
    the shape we can generate a complete parser + I/O loop for."""
    if cfg.get(component, {}).get("no_step") in (True, "true"):
        return False
    arg_t = C.arg_type(cfg, component)
    ret_t = C.return_type(cfg, component)
    return (
        arg_t in T._CTYPE_META
        and ret_t in T._CTYPE_META
        and arg_t != "const char *"
        and ret_t != "const char *"
    )


def _build_ctx(
    cfg: dict,
    component: str,
    name: str,
    target: str,
    flags: list[dict] | None = None,
    argc_argv: bool = False,
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    Component = _to_title(component)

    all_flags = _ctor_flags(cfg, component) + _extra_flags(flags)
    arg_t = C.arg_type(cfg, component)
    ret_t = C.return_type(cfg, component)

    def _create_call(parsed: bool) -> str:
        a = _ctor_c_args(all_flags, parsed)
        return f"{component}_create({a})" if a else f"{component}_create()"

    if _generatable(cfg, component):
        arg_parse_block = _c_argv_parser(name, all_flags)
        create_call = _create_call(parsed=True)
        io_loop = _c_io_loop(component, arg_t, ret_t)
        py_io_loop = R.render(
            _py_io_loop(component, Component, arg_t, ret_t),
            {"py_create_args": _py_create_args(all_flags)},
        )
        cleanup_tail = (
            "    if (in != stdin) fclose(in);\n"
            "    if (out != stdout) fclose(out);"
        )
    else:
        # Fall back to a stub for shapes we don't generate a loop for. The
        # --argc-argv opt-in still controls whether an argv-parsing skeleton or
        # a plain (void) suppression is emitted here.
        arg_parse_block = (
            "    if (argc > 1) {\n"
            "        /* <<IMPLEMENT: parse argv>> */\n"
            "    }"
            if argc_argv
            else "    (void)argc;\n    (void)argv;"
        )
        create_call = _create_call(parsed=False)
        io_loop = (
            "    /* <<IMPLEMENT: read stdin, call step()/steps(), "
            "write stdout>> */"
        )
        py_io_loop = R.render(
            f"    obj = {Component}(<<py_create_args>>)\n"
            "    # <<IMPLEMENT: open input/output, call obj.step(), write>>\n"
            "    _ = obj\n"
            "    sys.exit(0)",
            {"py_create_args": _py_create_args(all_flags)},
        )
        cleanup_tail = ""

    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": version,
        "component": component,
        "Component": Component,
        "argparse_state_args": _argparse_block(all_flags),
        "py_io_loop": py_io_loop,
        "arg_parse_block": arg_parse_block,
        "io_loop": io_loop,
        "app_create_line": f"    {component}_state_t *state = {create_call};",
        "cleanup_tail": cleanup_tail,
    }


def run(
    root: Path,
    cfg: dict | None = None,
    *,
    target: str = "c",
    name: str | None = None,
    object_: str | None = None,
    flags: list[dict] | None = None,
    argc_argv: bool = False,
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
        print(
            "error: [project].name missing from just-makeit.toml.",
            file=sys.stderr,
        )
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

    # Persist + merge flags before codegen so stored [[app.flags]] from prior
    # runs are reflected in the generated parsers (reproducible re-runs).
    C.set_app(cfg, target, name, object_)
    for f in flags or []:
        C.add_app_flag(cfg, f)
    effective_flags = C.app_flags(cfg)

    ctx = _build_ctx(
        cfg, object_, name, target, flags=effective_flags, argc_argv=argc_argv
    )

    print(f"just-makeit: scaffolding app '{name}' (target={target})")
    print()

    if target == "c":
        _run_c(root, cfg, ctx, name, object_)
    elif target == "console":
        _run_console(root, cfg, ctx, name, pkg)
    else:
        _run_pep723(root, ctx, name)

    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")
    print()
    _print_summary(target, root, name, pkg)


def _run_c(
    root: Path, cfg: dict, ctx: dict, name: str, component: str
) -> None:
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


def _run_console(
    root: Path, cfg: dict, ctx: dict, name: str, pkg: str
) -> None:
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
        print(f"  Build:  make && ./build/{name}")
    elif target == "console":
        print("Done!  Console script scaffold created.")
        print("  Install:  pip install -e .")
        print(f"  Run:      {name} --help")
    else:
        print(f"Done!  PEP 723 script created: {name}.py")
        print(f"  Run:      uv run {name}.py --help")
        print(f"  Share:    distribute {name}.py — no install needed")
        print(f"  Note:     requires {pkg} on PyPI")
