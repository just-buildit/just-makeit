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
    """Flags that feed the component constructor, in create() order.

    A constructor's arguments come from `init_params` when the object declares
    them (the awgn/ddc/no_state pattern), and otherwise from the `--state`
    ctor vars (the simple-object pattern) — mirroring how create() is generated
    (gh-184). A string-enum init param becomes a `choice` flag (its C arg is the
    enum index `int`); array init params have no scalar CLI form and are
    skipped (the body must supply them).
    """
    init = C.init_params(cfg, component)
    if init:
        out = []
        for p in init:
            name, ct, dflt = p[0], p[1], p[2]
            if T.is_array_param_type(ct):
                continue  # arrays aren't CLI scalars
            if T.is_string_enum_type(ct):
                out.append(
                    {
                        "name": name,
                        "type": "int",
                        "default": dflt,
                        "help": "",
                        "ctor": True,
                        "choices": T.string_enum_choices(ct),
                    }
                )
            else:
                out.append(
                    {
                        "name": name,
                        "type": ct,
                        "default": dflt,
                        "help": "",
                        "ctor": True,
                    }
                )
        return out
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
        if f.get("required"):
            spec = "required=True"
        elif pydef is None:
            spec = "default=None"
        elif pytype in ("float", "int", "complex"):
            spec = f"default={pydef}"  # bare numeric literal
        else:
            spec = f"default={pydef!r}"  # quoted string
        lines.append(
            f"    p.add_argument(\n"
            f'        "--{f["name"]}", type={pytype}, {spec},\n'
            f'        help="{helptext}",\n'
            f"    )"
        )
    return "\n".join(lines)


def _py_create_args(flags: list[dict]) -> str:
    """Keyword-args for the Python constructor from ctor flags."""
    return ", ".join(
        f"{f['name']}=args.{f['name']}" for f in flags if f["ctor"]
    )


def _np_dtype_of(t: str) -> str:
    """numpy dtype for a scalar or array (``T[]``) type."""
    elem = T.array_elem_ctype(t) if T.is_array_param_type(t) else t
    return _np_dtype(elem)


def _py_read(dtype: str) -> str:
    return (
        f"    if args.input:\n"
        f"        data = np.fromfile(args.input, dtype={dtype})\n"
        f"    else:\n"
        f"        data = np.frombuffer(sys.stdin.buffer.read(), dtype={dtype})"
    )


_PY_WRITE = (
    "    if args.output:\n"
    "        out.tofile(args.output)\n"
    "    else:\n"
    "        sys.stdout.buffer.write(out.tobytes())"
)


def _py_io_loop(
    shape: str, component: str, Component: str, arg_t: str, ret_t: str
) -> str:
    """4-space-indented Python body for the given object shape."""
    create = f"    obj = {Component}(<<py_create_args>>)"
    if shape == "scalar":
        return "\n".join(
            [
                _py_read(_np_dtype(arg_t)),
                create,
                f"    out = np.array(\n"
                f"        [obj.step(x) for x in data], dtype={_np_dtype(ret_t)}\n"
                f"    )",
                _PY_WRITE,
            ]
        )
    if shape == "blockwise":
        return "\n".join(
            [
                _py_read(_np_dtype_of(arg_t)),
                create,
                f"    out = np.asarray(obj.steps(data), dtype={_np_dtype_of(ret_t)})",
                _PY_WRITE,
            ]
        )
    if shape == "consumer":
        return "\n".join(
            [_py_read(_np_dtype_of(arg_t)), create, "    obj.steps(data)"]
        )
    if shape == "generator":
        return "\n".join(
            [
                create,
                f"    out = np.asarray(\n"
                f"        obj.steps(args.count), dtype={_np_dtype(ret_t)}\n"
                f"    )",
                _PY_WRITE,
            ]
        )
    return ""


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


def _c_argv_parser(
    name: str,
    flags: list[dict],
    *,
    want_in: bool = True,
    want_out: bool = True,
) -> str:
    """Generate the C argv parsing block: typed decls + a strcmp loop, the
    requested --input/--output handling, and the file opens. 4-space indented.

    `want_in`/`want_out` follow the object shape: a consumer has no output, a
    generator has no input.
    """
    decls = []
    clauses = []
    usage_parts = []
    for f in flags:
        ct = f["type"]
        if ct not in _C_PARSE:
            continue  # not CLI-parseable; ctor uses its default literal
        decls.append(f"    {ct} {f['name']} = {f['default']};")
        parse = _C_PARSE[ct].format(a="argv[++i]")
        clauses.append(
            f'if (!strcmp(argv[i], "--{f["name"]}") && i + 1 < argc) {{\n'
            f"            {f['name']} = {parse};\n"
            f"        }}"
        )
        usage_parts.append(f"[--{f['name']} V]")

    if want_in:
        decls.append("    const char *in_path = NULL;")
        clauses.append(
            'if ((!strcmp(argv[i], "--input") || !strcmp(argv[i], "-i"))\n'
            "                   && i + 1 < argc) {\n"
            "            in_path = argv[++i];\n"
            "        }"
        )
        usage_parts.append("[--input FILE]")
    if want_out:
        decls.append("    const char *out_path = NULL;")
        clauses.append(
            'if ((!strcmp(argv[i], "--output") || !strcmp(argv[i], "-o"))\n'
            "                   && i + 1 < argc) {\n"
            "            out_path = argv[++i];\n"
            "        }"
        )
        usage_parts.append("[--output FILE]")

    usage = f"usage: {name} " + " ".join(usage_parts)
    if clauses:
        loop = (
            "    for (int i = 1; i < argc; i++) {\n"
            "        " + " else ".join(clauses) + " else {\n"
            f'            fprintf(stderr, "{usage}\\n");\n'
            "            return 2;\n"
            "        }\n"
            "    }"
        )
    else:
        loop = "    (void)argc;\n    (void)argv;"

    # Suppress unused-variable warnings for extra flags the generated loop
    # doesn't consume (non-ctor, non-"consumed" extras for custom logic).
    voids = [
        f"    (void){f['name']};"
        for f in flags
        if f["type"] in _C_PARSE and not f["ctor"] and not f.get("consumed")
    ]

    opens = []
    if want_in:
        opens.append('    FILE *in = in_path ? fopen(in_path, "rb") : stdin;')
    if want_out:
        opens.append(
            '    FILE *out = out_path ? fopen(out_path, "wb") : stdout;'
        )
    if opens:
        cond = (
            "!in || !out"
            if (want_in and want_out)
            else ("!in" if want_in else "!out")
        )
        opens += [
            f"    if ({cond}) {{",
            '        fprintf(stderr, "error: cannot open input/output\\n");',
            "        return 1;",
            "    }",
        ]

    chunks = [*decls, "", loop]
    if voids:
        chunks += ["", *voids]
    if opens:
        chunks += ["", "\n".join(opens)]
    return "\n".join(chunks)


_APP_BLOCK = 4096


def _c_io_loop(shape: str, component: str, arg_t: str, ret_t: str) -> str:
    """4-space-indented C body for the given object shape."""
    n = _APP_BLOCK
    if shape == "scalar":
        return (
            f"    {arg_t} x;\n"
            f"    while (fread(&x, sizeof x, 1, in) == 1) {{\n"
            f"        {ret_t} y = {component}_step(state, x);\n"
            f"        fwrite(&y, sizeof y, 1, out);\n"
            f"    }}"
        )
    if shape == "blockwise":
        ie = T.array_elem_ctype(arg_t)
        oe = T.array_elem_ctype(ret_t)
        return (
            f"    {ie} inbuf[{n}];\n"
            f"    {oe} outbuf[{n}];\n"
            f"    size_t k;\n"
            f"    while ((k = fread(inbuf, sizeof inbuf[0], {n}, in)) > 0) {{\n"
            f"        {component}_steps(state, inbuf, k, outbuf);\n"
            f"        fwrite(outbuf, sizeof outbuf[0], k, out);\n"
            f"    }}"
        )
    if shape == "consumer":
        return (
            f"    {arg_t} inbuf[{n}];\n"
            f"    size_t k;\n"
            f"    while ((k = fread(inbuf, sizeof inbuf[0], {n}, in)) > 0) {{\n"
            f"        {component}_steps(state, inbuf, k);\n"
            f"    }}"
        )
    if shape == "generator":
        return (
            f"    {ret_t} outbuf[{n}];\n"
            f"    size_t produced = 0;\n"
            f"    while (produced < count) {{\n"
            f"        size_t k = (count - produced) < {n}\n"
            f"                       ? (count - produced) : (size_t){n};\n"
            f"        {component}_steps(state, outbuf, k);\n"
            f"        fwrite(outbuf, sizeof outbuf[0], k, out);\n"
            f"        produced += k;\n"
            f"    }}"
        )
    return ""


def _cmake_app_block(name: str, link_target: str) -> str:
    return (
        f"{_APP_CMAKE_SENTINEL}"
        "─────────────────────────────────────────────────────────\n"
        f"add_executable({name} native/src/app/{name}.c)\n"
        f"target_link_libraries({name} PRIVATE {link_target})\n"
        f"install(TARGETS {name} DESTINATION bin)\n"
        f"{_APP_CMAKE_END}"
        "─────────────────────────────────────────────────────────\n"
    )


def _splice_cmake(cmake: Path, name: str, link_target: str) -> None:
    """Insert or replace the App block in CMakeLists.txt."""
    text = cmake.read_text(encoding="utf-8")
    block = _cmake_app_block(name, link_target)
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


def _is_scalar(t: str) -> bool:
    return t in T._CTYPE_META and t != "const char *"


def _is_scalar_array(t: str) -> bool:
    if not T.is_array_param_type(t):
        return False
    return _is_scalar(T.array_elem_ctype(t))


def _app_shape(cfg: dict, component: str) -> str | None:
    """Classify the object's I/O shape so the right parser + loop can be
    generated: 'scalar', 'blockwise', 'consumer', 'generator', or None (an
    unsupported shape that falls back to an <<IMPLEMENT>> stub)."""
    if cfg.get(component, {}).get("no_step") in (True, "true"):
        return None
    arg_t = C.arg_type(cfg, component)
    ret_t = C.return_type(cfg, component)
    if _is_scalar(arg_t) and _is_scalar(ret_t):
        return "scalar"
    if _is_scalar_array(arg_t) and _is_scalar_array(ret_t):
        return "blockwise"
    if _is_scalar(arg_t) and ret_t == "void":
        return "consumer"
    if arg_t == "void" and _is_scalar(ret_t):
        return "generator"
    return None


# ── module-function apps ─────────────────────────────────────────────────────
# printf format + cast per scalar return type.
_C_PRINTF = {
    "float": ("%g", "(double)"),
    "double": ("%g", "(double)"),
    "int": ("%d", ""),
    "int8_t": ("%d", "(int)"),
    "int16_t": ("%d", "(int)"),
    "int32_t": ("%ld", "(long)"),
    "int64_t": ("%lld", "(long long)"),
    "uint8_t": ("%u", "(unsigned)"),
    "uint16_t": ("%u", "(unsigned)"),
    "uint32_t": ("%lu", "(unsigned long)"),
    "uint64_t": ("%llu", "(unsigned long long)"),
    "size_t": ("%zu", ""),
}


def _find_fn(cfg: dict, function: str, module: str | None):
    """Return (module, fn_dict) for the named function, or (None, None)."""
    mods = [module] if module else C.modules(cfg)
    for m in mods:
        for fn in C.module_functions(cfg, m):
            if fn["name"] == function:
                return m, fn
    return None, None


def _fn_generatable(fn: dict) -> bool:
    """A function app is generatable when every param is a CLI-parseable scalar
    and the return is a scalar (or void)."""
    for p in fn.get("params", []):
        if p["type"] not in _C_PARSE:
            return False
    ret = fn.get("return_type", "void")
    return ret == "void" or ret in _C_PRINTF


def _fn_flags(params: list[dict]) -> list[dict]:
    return [
        {
            "name": p["name"],
            "type": p["type"],
            "default": T._CTYPE_META.get(p["type"], {}).get("zero", "0"),
            "help": p["name"],
            "ctor": False,
            "consumed": True,
            "required": True,
        }
        for p in params
    ]


def _c_call_print(function: str, ret_t: str, param_names: list[str]) -> str:
    args = ", ".join(param_names)
    if ret_t == "void" or ret_t not in _C_PRINTF:
        return f"    {function}({args});"
    fmt, cast = _C_PRINTF[ret_t]
    return (
        f"    {ret_t} result = {function}({args});\n"
        f'    printf("{fmt}\\n", {cast}result);'
    )


def _build_fn_ctx(
    cfg: dict, module: str, function: str, name: str, fn: dict
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    params = fn.get("params", [])
    ret_t = fn.get("return_type", "void")
    fn_flags = _fn_flags(params)
    pnames = [p["name"] for p in params]
    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": C.project_version(cfg),
        "module": module,
        "function": function,
        "argparse_state_args": _argparse_block(fn_flags),
        "arg_parse_block": _c_argv_parser(
            name, fn_flags, want_in=False, want_out=False
        ),
        "call_and_print": _c_call_print(function, ret_t, pnames),
        "py_call_args": ", ".join(f"args.{n}" for n in pnames),
    }


# ── subcommand apps ──────────────────────────────────────────────────────────
def _cmd_flag_dicts(flags: list[dict]) -> list[dict]:
    out = []
    for f in flags:
        t = f["type"]
        out.append(
            {
                "name": f["name"],
                "type": t,
                "default": f.get("default")
                or T._CTYPE_META.get(t, {}).get("zero", "0"),
                "help": f.get("help", ""),
                "ctor": False,
            }
        )
    return out


def _c_command_handlers(commands: list[dict]) -> str:
    parts = []
    for c in commands:
        flags = _cmd_flag_dicts(c.get("flags", []))
        parse = _c_argv_parser(c["name"], flags, want_in=False, want_out=False)
        parts.append(
            f"static int\ncmd_{c['name']}(int argc, char *argv[])\n{{\n"
            f"{parse}\n"
            f"    /* <<IMPLEMENT: {c['name']}>> */\n"
            f"    return 0;\n}}"
        )
    return "\n\n".join(parts)


def _c_dispatch(commands: list[dict]) -> str:
    return "\n".join(
        f'    if (!strcmp(argv[1], "{c["name"]}")) {{\n'
        f"        return cmd_{c['name']}(argc - 1, argv + 1);\n"
        f"    }}"
        for c in commands
    )


def _cmd_usage(name: str, commands: list[dict]) -> str:
    names = ", ".join(c["name"] for c in commands)
    return f"usage: {name} <command> [options]  (commands: {names})"


def _py_command_fns(commands: list[dict]) -> str:
    return "\n\n".join(
        f"def _cmd_{c['name']}(args: argparse.Namespace) -> None:\n"
        f"    # <<IMPLEMENT: {c['name']}>>\n"
        f"    _ = args"
        for c in commands
    )


def _py_subparsers(commands: list[dict]) -> str:
    lines = []
    for c in commands:
        var = f"p_{c['name']}"
        lines.append(
            f'    {var} = sub.add_parser("{c["name"]}", '
            f'help="{c.get("help", "")}")'
        )
        for f in _cmd_flag_dicts(c.get("flags", [])):
            pytype = _PYTYPE.get(f["type"], "str")
            pydef = _py_default(f["default"]) if f["default"] else None
            if pydef is None:
                dr = "None"
            elif pytype in ("float", "int", "complex"):
                dr = pydef
            else:
                dr = repr(pydef)
            lines.append(
                f'    {var}.add_argument("--{f["name"]}", type={pytype}, '
                f'default={dr}, help="{f["help"] or f["name"]}")'
            )
        lines.append(f"    {var}.set_defaults(_fn=_cmd_{c['name']})")
    return "\n".join(lines)


def _build_cmd_ctx(
    cfg: dict, name: str, commands: list[dict]
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": C.project_version(cfg),
        "command_handlers": _c_command_handlers(commands),
        "dispatch": _c_dispatch(commands),
        "usage": _cmd_usage(name, commands),
        "command_fns": _py_command_fns(commands),
        "subparsers": _py_subparsers(commands),
    }


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

    shape = _app_shape(cfg, component)
    if shape is not None:
        # A generator produces N samples from internal state with no input,
        # driven by a synthetic --count flag; a consumer has no output.
        want_in = shape != "generator"
        want_out = shape != "consumer"
        parse_flags = list(all_flags)
        if shape == "generator":
            parse_flags.append(
                {
                    "name": "count",
                    "type": "size_t",
                    "default": "1024",
                    "help": "number of samples to generate",
                    "ctor": False,
                    "consumed": True,
                }
            )
        argparse_flags = parse_flags
        arg_parse_block = _c_argv_parser(
            name, parse_flags, want_in=want_in, want_out=want_out
        )
        create_call = _create_call(parsed=True)
        io_loop = _c_io_loop(shape, component, arg_t, ret_t)
        py_io_loop = R.render(
            _py_io_loop(shape, component, Component, arg_t, ret_t),
            {"py_create_args": _py_create_args(all_flags)},
        )
        tail = []
        if want_in:
            tail.append("    if (in != stdin) fclose(in);")
        if want_out:
            tail.append("    if (out != stdout) fclose(out);")
        cleanup_tail = "\n".join(tail)
    else:
        # Fall back to a stub for shapes we don't generate a loop for. The
        # --argc-argv opt-in still controls whether an argv-parsing skeleton or
        # a plain (void) suppression is emitted here.
        argparse_flags = all_flags
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
        "argparse_state_args": _argparse_block(argparse_flags),
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
    function_: str | None = None,
    module: str | None = None,
    flags: list[dict] | None = None,
    commands: list[dict] | None = None,
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

    if target not in ("c", "console", "pep723"):
        print(
            f"error: unknown target '{target}'. Use c, console, or pep723.",
            file=sys.stderr,
        )
        sys.exit(1)

    if function_ is not None:
        # ── module-function app ──────────────────────────────────────────
        mod, fn = _find_fn(cfg, function_, module)
        if fn is None:
            where = f" in module '{module}'" if module else ""
            print(
                f"error: function '{function_}' not found{where}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not _fn_generatable(fn):
            print(
                f"error: function '{function_}' has non-scalar params or "
                "return; `jm app --function` supports scalar signatures only.",
                file=sys.stderr,
            )
            sys.exit(1)
        if name is None:
            name = function_
        ctx = _build_fn_ctx(cfg, mod, function_, name, fn)
        C.set_app(cfg, target, name, function=function_, module=mod)
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_FN_C,
            R.APP_CONSOLE_CLI_FN,
            R.APP_PEP723_FN,
        )
        link_target = f"{mod}_core"
    elif commands or (object_ is None and C.app_commands(cfg)):
        # ── multi-command app ────────────────────────────────────────────
        if name is None:
            name = pkg
        C.set_app(cfg, target, name)
        for c in commands or []:
            C.add_app_command(cfg, c)
        eff_cmds = C.app_commands(cfg)
        if not eff_cmds:
            print("error: no commands declared.", file=sys.stderr)
            sys.exit(1)
        ctx = _build_cmd_ctx(cfg, name, eff_cmds)
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_CMD_C,
            R.APP_CONSOLE_CLI_CMD,
            R.APP_PEP723_CMD,
        )
        # Stub command bodies link the project's aggregate static lib so any
        # component/function symbol is reachable once the user fills them in.
        link_target = f"{pkg.replace('-', '_')}_lib_static"
    else:
        # ── object app ───────────────────────────────────────────────────
        comps = C.components(cfg)
        if object_ is None:
            if not comps:
                print(
                    "error: no components found — run "
                    "'just-makeit object' first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            object_ = comps[0]
        elif object_ not in comps:
            print(f"error: object '{object_}' not found.", file=sys.stderr)
            sys.exit(1)
        if name is None:
            name = pkg
        # Persist + merge flags before codegen so stored [[app.flags]] from
        # prior runs are reflected in the generated parsers (reproducible).
        C.set_app(cfg, target, name, object_=object_)
        for f in flags or []:
            C.add_app_flag(cfg, f)
        ctx = _build_ctx(
            cfg,
            object_,
            name,
            target,
            flags=C.app_flags(cfg),
            argc_argv=argc_argv,
        )
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_C,
            R.APP_CONSOLE_CLI,
            R.APP_PEP723,
        )
        link_target = f"{object_}_core"

    print(f"just-makeit: scaffolding app '{name}' (target={target})")
    print()

    if target == "c":
        _run_c(root, ctx, name, link_target, main_tmpl)
    elif target == "console":
        _run_console(root, ctx, name, pkg, console_tmpl)
    else:
        _run_pep723(root, ctx, name, pep_tmpl)

    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")
    print()
    _print_summary(target, root, name, pkg)


def _run_c(
    root: Path,
    ctx: dict,
    name: str,
    link_target: str,
    tmpl: str = R.APP_MAIN_C,
) -> None:
    app_dir = root / "native" / "src" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    main_c = app_dir / f"{name}.c"
    main_c.write_text(R.render(tmpl, ctx), encoding="utf-8")
    verb = "update" if main_c.exists() else "create"
    print(f"  {verb}  {main_c}")

    cmake = root / "CMakeLists.txt"
    if cmake.exists():
        _splice_cmake(cmake, name, link_target)
        print(f"  update  {cmake}")
    else:
        print(
            f"  note: CMakeLists.txt not found — add this manually:\n"
            f"    add_executable({name} native/src/app/{name}.c)\n"
            f"    target_link_libraries({name} PRIVATE {link_target})"
        )


def _run_console(
    root: Path,
    ctx: dict,
    name: str,
    pkg: str,
    tmpl: str = R.APP_CONSOLE_CLI,
) -> None:
    cli_py = root / "src" / pkg / "cli.py"
    cli_py.parent.mkdir(parents=True, exist_ok=True)
    cli_py.write_text(R.render(tmpl, ctx), encoding="utf-8")
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


def _run_pep723(
    root: Path, ctx: dict, name: str, tmpl: str = R.APP_PEP723
) -> None:
    script = root / f"{name}.py"
    script.write_text(R.render(tmpl, ctx), encoding="utf-8")
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
