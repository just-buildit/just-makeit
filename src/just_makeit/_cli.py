"""_cli.py — just-makeit command-line interface."""

import os
import re
import sys
from pathlib import Path


_USAGE = """\

Usage: just-makeit  (alias: jm)  <command> [options]

Commands:
  new <proj> [dir] [OPTIONS]    Create a new project scaffold.
    --object name               Also scaffold a standalone object; repeatable.
    --module name               Also scaffold an extension module; repeatable.
    --state name:type[:default] Initial state variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --mutable                   Remove const from state pointer in step().
    --build-system <cmake|make> Build system to use (default: cmake).
    --basic                     Deprecated alias for --build-system make.
    --pytest                    Generate pure pytest tests (no unittest shim).
    --pytest-benchmark          Generate pytest-benchmark bench files.
    --find-package NAME         CMake `find_package(NAME REQUIRED)` (repeatable; e.g. Doppler).
    --pkg-module NAME           pkg-config module via `pkg_check_modules` (repeatable).
    --c-dep DIR                 Vendored C subdir under native/src/DIR (repeatable; no Python).

  module <name> [OPTIONS]       Add an extension module subpackage to a project.
    --extra-include-dirs DIR    CMake include path; repeatable (e.g. ${DOPPLER_INCLUDE_DIR}).
    --extra-link-libs TARGET    CMake link target; repeatable (e.g. PkgConfig::DOPPLER).
    --extra-types NAME          Hand-written Python type to register in PyInit_; repeatable.

  object <name> [OPTIONS]       Add a Python-wrapped C type to a project.
    --preset NAME               Named shorthand for a common shape:
                                processor (default), blockwise, generator,
                                consumer, reader. Expands into the
                                equivalent flag combination; passing the
                                flags directly is always equivalent.
    --module name               Place object inside this module's .so.
    --state name:type[:default] State variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --mutable                   Remove const from state pointer in step().
    --no-state                  Generate empty state struct; user fills in fields manually.
    --no-step                   Omit step() method.
    --init-param name:type[:default]  User-facing constructor param; repeatable.
                                      Composes with --state: init params drive the ctor,
                                      state stays internal (manage via --impl create::...).
                                      Optional array form: name:type[]:optional[:create_fn]
    --class-name NAME           Override Python class name (e.g. NCO instead of Nco).
    --extra-include-dirs DIR    CMake include path for this component; repeatable.
    --impl file::funcname       Lift step() body from funcname in file.
    --impl file::N:M            Lift lines N..M (inclusive) instead of a function.
    --impl SLOT::file::funcname Lift body into SLOT = create / reset / destroy.
    --replace old::new          String substitution on --impl body; repeatable.

  method <obj> <name> [OPTIONS] Add a named execute variant to an object.
    --module name               Module the object lives in.
    --param name:type           Input parameter; repeatable.
    --arg-type TYPE             Bulk-input array type.
    --return-type TYPE          Return type.
    --variable-output           Output length determined at runtime.
    --max-out N                 Worst-case output count returned by <comp>_<name>_max_out().
                                Composes with --variable-output (skips the IMPLEMENT stub).
    --multi-output TYPE         Emit a second output array of this type.
    --out-type TYPE             Allocate an output array per call; length = in_len / out-divisor.
    --out-divisor N             Divide input length by N for output array (default: 1).
    --result-field name:type    Append a field to a returned record list; repeatable.
    --batch                     Generate 1:1-rate array transform (allocates output per call).
    --no-bench                  Exclude this method from the C benchmark.
    --impl file::funcname       Lift method body from funcname in file.
    --replace old::new          String substitution on --impl body; repeatable.

  property <obj> <name> [OPTIONS]  Add a Python property to an object.
    --module name               Module the object lives in.
    --type TYPE                 C type of the property value.
    --writable                  Generate a setter in addition to the getter.
    --field                     Back property with a struct field (no getter C fn).

  function <name> [OPTIONS]     Add a module-level C function.
    --module name               Module to add the function to (required).
    --param name:type           Input parameter; repeatable.
    --out-param name:type[]     Writable output array param (drops const); repeatable.
    --return-type TYPE          Return type (default: void).
    --out-type TYPE             Return a fresh ndarray of TYPE; size from first array
                                param's length, or the first integer scalar param.
    --result-field name:type    Append a field to a returned record list; repeatable.
    --doc "text"                Docstring shown in Python help().
    --inline                    Emit static inline body in _core.h (no _core.c entry).
    --impl file::funcname       Lift function body from funcname in file.
    --replace old::new          String substitution on --impl body; repeatable.

  add [OPTIONS]                 Append variables to the current object.
    --state name:type[:default] Add a state variable.
    --param name:type[:default] Add a constructor parameter.

  remove <kind> <name> [OPTIONS]  Delete a scaffolded object/module/method/etc.
    kind is object|module|method|property|function.
    --object name               Object the method/property lives on (required for those).
    --module name               Module the function lives in (required for function).
    --force, -f                 Skip the confirmation prompt.

  app [OPTIONS]                 Scaffold a shippable standalone application from an object.
    --target c|console|pep723   Output target (default: c).
    --object name               Component to scaffold from (default: first object).
    --name name                 App/script name (default: project name).

  perf                          Retrofit JM_HOT/JM_FORCEINLINE without touching user code.
  apply [fragment]              Materialize just-makeit.toml: create missing files,
                                regenerate glue (binding/stub/cmake), preserve sacred
                                _core.c. With a fragment path, copy it into objects/,
                                add to include, then materialize.
  regenerate <component>        Delete a component's generated files and rebuild them
                                from just-makeit.toml (discards _core.c edits — stash
                                first). --force skips the confirmation.
  split-objects                 Move each [obj] section out of the manifest into
                                objects/<name>.toml and add the include glob.
  script                        Print a shell script that fully reconstructs this project via CLI.
  config [key value]            Show all config keys, or get/set one value.
  bench [comp …] [OPTIONS]      Build, run C + Python benchmarks; save a dated
                                snapshot to benchmarks/history/.
    --tag TAG                   Snapshot tag (default: UTC timestamp).
    --c-only / --python-only    Restrict to one benchmark side.
  build [dir]                   Build C extensions and package a wheel (default: dist/).
  test                          Build then run CTest + pytest.
  dry-run                       Show what would be compiled without building.
  install-deps [OPTIONS] [path] Install cmake, C compiler, numpy, and create a venv.
    --check                     Report status only; exit 1 if anything is missing.
    -h, --help                  Show detailed help for this command.
  example [name]                Run a bundled end-to-end example (omit name to list).
  version                       Show just-makeit's version.
  help                          Show this message.

Types (--arg-type / --return-type / --param / --state):
  void  float  double  float _Complex  double _Complex
  int  int8_t…int64_t  uint8_t…uint64_t  size_t  ptrdiff_t
  Append [] for array params:    float _Complex[]  int16_t[]  …
  Append [N] for fixed-length state: float[64]  double _Complex[32]

Examples:
  # project scaffold only
  jm new my_filter

  # project + first object
  jm new my_filter --object my_filter

  # object with state variables
  jm new my_bpf --object bpf --state center:double --state bw:double

  # project + one module / two modules
  jm new my_filters --module filter
  jm new my_dsp --module osc --module env

  # sink object (no return value)
  jm object sink --arg-type "float _Complex" --return-type void

  # generator object (no input)
  jm object gen --arg-type void --return-type "float _Complex"

  # mutable generator (NCO, counter)
  jm object nco --arg-type void --return-type "float _Complex" --mutable

  # standalone stateful object / object with state var / object in a module
  jm object engine --state rate:double:1.0
  jm object norm --state scale:double:1.0
  jm object fir --module filter

  # named execute method with params
  just-makeit method nco configure --module dsp \\
      --param freq:float --param phase:float --return-type void

  # method returning runtime-length array
  just-makeit method resamp execute_ctrl --module dsp \\
      --param ctrl:"float _Complex[]" --return-type size_t

  # variable-output generator method
  just-makeit method nco execute_cf32 --module dsp \\
      --arg-type void --return-type "float _Complex" --variable-output

  # dual-output method
  just-makeit method nco execute_u32_ovf --module dsp \\
      --arg-type void --return-type uint32_t --variable-output --multi-output uint8_t

  # module-level function
  just-makeit function apply_window --module fft \\
      --param data:"float _Complex[]" --return-type void

  # properties (read-only and writable)
  jm property nco phase --module dsp --type uint32_t
  jm property buffer dropped --type size_t

  # add state var / constructor param
  jm add --state order:int:4
  jm add --param n_taps:int:16

  # scaffold a C executable from an existing object
  jm app --target c --object engine --name dsp_tool

  # scaffold a Python console script (updates pyproject.toml [project.scripts])
  jm app --target console --object engine --name dsp_tool

  # scaffold a PEP 723 inline script (runnable via uv run, no install needed)
  jm app --target pep723 --object engine --name dsp_tool

  # config, build, test
  jm config
  jm config version 0.2.0
  jm build
  jm test
  jm dry-run
"""


def _color_supported() -> bool:
    return (
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
    )


def _colorize(text: str) -> str:
    if not _color_supported():
        return text

    RST = "\x1b[0m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    CYAN = "\x1b[36m"
    BOLD_CYAN = "\x1b[1;36m"
    BOLD_GREEN = "\x1b[1;32m"
    BOLD_YELLOW = "\x1b[1;33m"

    def c(code: str, s: str) -> str:
        return code + s + RST

    def colorize_flags(s: str) -> str:
        return re.sub(r"(--[\w-]+)", lambda m: c(CYAN, m.group(1)), s)

    def italicize_desc(s: str) -> str:
        # Case A: no signature — leading spaces then description (perf, script,
        # dry-run, etc.). Without this, the non-greedy .*?\S latches onto a
        # capital mid-description (e.g. JM_HOT, CLI) and starts italic there.
        m = re.match(r"^(\s{2,})([A-Z].*)$", s)
        if m:
            desc = m.group(2).replace(RST, RST + ITALIC)
            return m.group(1) + ITALIC + desc + RST
        # Case B: signature + whitespace + description. \s+ (not \s{2,}) so
        # that 1-space gaps like --state name:type[:default] Initial... match.
        m = re.match(r"^(.*?\S)(\s+)([A-Z].*)$", s)
        if m:
            desc = m.group(3).replace(RST, RST + ITALIC)
            return m.group(1) + m.group(2) + ITALIC + desc + RST
        return s

    lines = text.splitlines(keepends=True)
    out = []
    section = ""

    for line in lines:
        raw = line.rstrip("\n")
        nl = "\n" if line.endswith("\n") else ""

        if raw.startswith("Usage:"):
            colored = (
                c(BOLD_CYAN, "Usage:")
                + " "
                + c(BOLD_GREEN, "just-makeit")
                + c(DIM, "  (alias: jm)  ")
                + c(BOLD_GREEN, "<command>")
                + " "
                + c(CYAN, "[options]")
            )
            out.append(colored + nl)
            continue

        if raw.startswith("Commands:"):
            section = "commands"
            out.append(c(BOLD_CYAN, raw) + nl)
            continue

        if raw.startswith("Types ("):
            section = "types"
            out.append(c(BOLD_CYAN, colorize_flags(raw)) + nl)
            continue

        if raw.startswith("Examples:"):
            section = "examples"
            out.append(c(BOLD_CYAN, raw) + nl)
            continue

        if section == "commands":
            m = re.match(r"^(  )([a-z][\w-]*)(.*)$", raw)
            if m:
                indent, cmd, rest = m.groups()
                rest = italicize_desc(colorize_flags(rest))
                out.append(indent + c(BOLD_GREEN, cmd) + rest + nl)
            else:
                out.append(italicize_desc(colorize_flags(raw)) + nl)

        elif section == "types":
            # Bold the type-list lines; colorize flags on the Append lines
            if re.match(r"^  \w", raw) and "Append" not in raw:
                out.append(c(BOLD_YELLOW, raw) + nl)
            else:
                out.append(colorize_flags(raw) + nl)

        elif section == "examples":
            # Group header comments (leading "  # ...")
            if re.match(r"^  # ", raw):
                out.append(c(DIM, raw) + nl)
                continue
            # Dim trailing comments, bold command name, cyan flags
            raw = re.sub(r"(\s+#.*)$", lambda m: c(DIM, m.group(1)), raw)
            raw = re.sub(
                r"^(  )(just-makeit|jm)(\s+)([a-z][\w-]*)",
                lambda m: (
                    m.group(1)
                    + c(BOLD_GREEN, m.group(2))
                    + m.group(3)
                    + c(BOLD_GREEN, m.group(4))
                ),
                raw,
            )
            raw = re.sub(r"(--[\w-]+)", lambda m: c(CYAN, m.group(1)), raw)
            out.append(raw + nl)

        else:
            out.append(line)

    return "".join(out)


def _warn_schema() -> None:
    """Print a warning if the project's schema is behind CURRENT_SCHEMA."""
    from . import _config as C

    cfg = C.load(Path.cwd())
    if not cfg:
        return
    v = C.schema_version(cfg)
    if v < C.CURRENT_SCHEMA:
        print(
            f"warning: project schema is v{v}, current is v{C.CURRENT_SCHEMA}. "
            "Run 'just-makeit upgrade' to get new features.",
            file=sys.stderr,
        )


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(_colorize(_USAGE), end="")
        return

    cmd = args[0]

    if cmd == "new":
        from ._cli_new import run as _cmd_new

        _cmd_new(args[1:])

    elif cmd == "module":
        _warn_schema()
        if len(args) < 2:
            print("error: 'module' requires a module name.", file=sys.stderr)
            sys.exit(1)
        from . import _module

        mod_name = args[1]
        mod_extra_inc: list[str] = []
        mod_extra_libs: list[str] = []
        mod_extra_types: list[str] = []
        rest = args[2:]
        j = 0
        while j < len(rest):
            tok = rest[j]
            if tok == "--extra-include-dirs":
                j += 1
                if j >= len(rest):
                    print(
                        "error: --extra-include-dirs requires a path or ${VAR}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                mod_extra_inc.append(rest[j])
                j += 1
            elif tok == "--extra-link-libs":
                j += 1
                if j >= len(rest):
                    print(
                        "error: --extra-link-libs requires a target name",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                mod_extra_libs.append(rest[j])
                j += 1
            elif tok == "--extra-types":
                j += 1
                if j >= len(rest):
                    print(
                        "error: --extra-types requires a Python type name",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                mod_extra_types.append(rest[j])
                j += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)
        _module.run(
            Path.cwd(),
            mod_name,
            extra_include_dirs=mod_extra_inc or None,
            extra_link_libs=mod_extra_libs or None,
            extra_types=mod_extra_types or None,
        )

    elif cmd == "object":
        _warn_schema()
        from ._cli_object import run as _cmd_object

        _cmd_object(args[1:])

    elif cmd == "method":
        _warn_schema()
        from ._cli_method import run as _cmd_method

        _cmd_method(args[1:])

    elif cmd == "remove":
        _warn_schema()
        from ._cli_remove import run as _cmd_remove

        _cmd_remove(args[1:])

    elif cmd == "property":
        _warn_schema()
        if len(args) < 3:
            print(
                "error: 'property' requires an object name and a property name.",
                file=sys.stderr,
            )
            sys.exit(1)
        from . import _property
        from . import _types as T

        object_name = args[1]
        prop_name = args[2]
        module = None
        ctype = "size_t"
        writable = False
        field = False

        remaining = args[3:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--module":
                i += 1
                if i >= len(remaining):
                    print("error: --module requires a name", file=sys.stderr)
                    sys.exit(1)
                module = remaining[i]
                i += 1
            elif tok == "--type":
                i += 1
                if i >= len(remaining):
                    print("error: --type requires a type", file=sys.stderr)
                    sys.exit(1)
                val = remaining[i]
                if val not in T._CTYPE_META:
                    print(
                        f"error: --type '{val}' is not a supported scalar type.\n"
                        f"Supported: {', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                ctype = val
                i += 1
            elif tok == "--writable":
                writable = True
                i += 1
            elif tok == "--field":
                field = True
                i += 1
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        _property.run(
            Path.cwd(), object_name, prop_name, module, ctype, writable, field
        )

    elif cmd == "function":
        _warn_schema()
        from ._cli_function import run as _cmd_function

        _cmd_function(args[1:])

    elif cmd == "add":
        _warn_schema()
        from . import _add
        from ._cli_parse import parse_state_flag

        component = None
        state_vars: list[tuple[str, str, str]] = []
        remaining = args[1:]
        i = 0
        while i < len(remaining):
            tok = remaining[i]
            if tok == "--object":
                i += 1
                if i >= len(remaining):
                    print("error: --object requires a name", file=sys.stderr)
                    sys.exit(1)
                component = remaining[i]
                i += 1
            elif tok in ("--state", "--param"):
                var, i = parse_state_flag(remaining, i)
                state_vars.append(var)
            else:
                print(f"error: unexpected argument '{tok}'", file=sys.stderr)
                sys.exit(1)

        if not state_vars:
            print(
                "error: 'add' requires at least one --state or --param flag.",
                file=sys.stderr,
            )
            sys.exit(1)

        _add.run(Path.cwd(), component, state_vars)

    elif cmd == "perf":
        from . import _perf

        _perf.run(Path.cwd())

    elif cmd == "script":
        from . import _script

        _script.run(Path.cwd())

    elif cmd == "config":
        from . import _config as C

        root = Path.cwd()
        cfg = C.load(root)
        if not cfg:
            print(
                f"error: no {C.FILENAME} found in {root}.",
                file=sys.stderr,
            )
            sys.exit(1)

        if len(args) == 1:
            proj = cfg.get("project", {})
            print(f"project:  {proj.get('name', '?')}")
            print(f"version:  {proj.get('version', '0.1.0')}")
            for comp in C.components(cfg):
                print(f"\n{comp}:")
                for s in cfg[comp].get("state", []):
                    print(f"  {s['name']}:  {s['type']} = {s['default']}")
        elif len(args) == 3:
            key, value = args[1], args[2]
            if key == "version":
                cfg.setdefault("project", {})["version"] = value
                C.save(root, cfg)
                print(f"version = {value!r}")
            else:
                print(f"error: unknown config key '{key}'", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                "Usage: just-makeit config [key value]\nSupported keys: version",
                file=sys.stderr,
            )
            sys.exit(1)

    elif cmd == "bench":
        from . import _bench

        rest = args[1:]
        tag: str | None = None
        do_c = True
        do_python = True
        comps: list[str] = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--tag" and i + 1 < len(rest):
                tag = rest[i + 1]
                i += 2
            elif a == "--c-only":
                do_python = False
                i += 1
            elif a == "--python-only":
                do_c = False
                i += 1
            else:
                comps.append(a)
                i += 1
        _bench.run(
            Path.cwd(),
            components=comps or None,
            tag=tag,
            do_c=do_c,
            do_python=do_python,
        )

    elif cmd == "bind":
        from . import _bind

        rest = args[1:]
        check = False
        comp: str | None = None
        for a in rest:
            if a == "--check":
                check = True
            elif a.startswith("-"):
                print(f"error: unknown flag for bind: {a}", file=sys.stderr)
                sys.exit(1)
            else:
                if comp is not None:
                    print(
                        "error: bind takes one component name", file=sys.stderr
                    )
                    sys.exit(1)
                comp = a
        if comp is None:
            print(
                "Usage: just-makeit bind <component> [--check]",
                file=sys.stderr,
            )
            sys.exit(1)
        if check:
            rendered = _bind.run(Path.cwd(), comp, write=False)
            existing = (
                Path.cwd() / "native" / "src" / comp / f"{comp}_ext.c"
            ).read_text(encoding="utf-8")
            if rendered != existing:
                print(f"error: {comp}_ext.c is out of date with {comp}_core.h")
                sys.exit(1)
            print(f"  ok  {comp}_ext.c matches {comp}_core.h")
        else:
            _bind.run(Path.cwd(), comp)

    elif cmd == "build":
        from . import _build

        _build.cmd_build(args[1:])

    elif cmd == "test":
        from . import _build

        _build.cmd_test(args[1:])

    elif cmd == "dry-run":
        from . import _build

        _build.cmd_dry_run()

    elif cmd == "install-deps":
        from . import _scripts

        sys.argv = [sys.argv[0]] + args[1:]
        _scripts.install_deps()

    elif cmd == "app":
        _warn_schema()
        from . import _app

        target = "c"
        name: str | None = None
        object_: str | None = None
        i = 1
        while i < len(args):
            tok = args[i]
            if tok in ("--target", "-t") and i + 1 < len(args):
                i += 1
                target = args[i]
            elif tok.startswith("--target="):
                target = tok[len("--target=") :]
            elif tok == "--object" and i + 1 < len(args):
                i += 1
                object_ = args[i]
            elif tok.startswith("--object="):
                object_ = tok[len("--object=") :]
            elif tok == "--name" and i + 1 < len(args):
                i += 1
                name = args[i]
            elif tok.startswith("--name="):
                name = tok[len("--name=") :]
            i += 1
        _app.run(Path.cwd(), target=target, name=name, object_=object_)

    elif cmd == "example":
        from . import _example

        _example.run(args[1] if len(args) > 1 else None)

    elif cmd == "upgrade":
        from . import _upgrade

        _upgrade.run(Path.cwd())

    elif cmd == "split-objects":
        _warn_schema()
        from . import _split_objects

        _split_objects.run(Path.cwd())

    elif cmd == "apply":
        _warn_schema()
        from . import _apply

        only: str | None = None
        positional: list[str] = []
        for a in args[1:]:
            if a.startswith("--only="):
                only = a[len("--only=") :]
            elif a == "--only":
                print(
                    "error: --only requires a value (--only=NAME).",
                    file=sys.stderr,
                )
                sys.exit(1)
            else:
                positional.append(a)
        if len(positional) > 1:
            print(
                "error: 'apply' takes at most one fragment path.",
                file=sys.stderr,
            )
            sys.exit(1)
        fragment = Path(positional[0]) if positional else None
        _apply.run(Path.cwd(), fragment=fragment, only=only)

    elif cmd == "regenerate":
        _warn_schema()
        from . import _regenerate

        force = False
        names: list[str] = []
        for a in args[1:]:
            if a == "--force":
                force = True
            else:
                names.append(a)
        if len(names) != 1:
            print(
                "error: 'regenerate' takes exactly one component name.\n"
                "Usage: just-makeit regenerate <component> [--force]",
                file=sys.stderr,
            )
            sys.exit(1)
        _regenerate.run(Path.cwd(), names[0], force=force)

    elif cmd == "version":
        from . import __version__

        print(__version__)

    else:
        print(f"just-makeit: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'just-makeit help' for usage.", file=sys.stderr)
        sys.exit(1)
