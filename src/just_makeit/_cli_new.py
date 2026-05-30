"""CLI handler for `just-makeit new`."""

import sys
from pathlib import Path

from ._cli_parse import parse_state_flag


def run(args: list[str]) -> None:
    if len(args) < 1:
        print("error: 'new' requires a project name.", file=sys.stderr)
        sys.exit(1)
    from . import _new
    from . import _types as T

    project = args[0]
    dest = None
    object_names: list[str] = []
    modules: list[str] = []
    build_system = "cmake"
    perf = False
    pytest_ = False
    pytest_benchmark_ = False
    mutable = False
    arg_type = "float _Complex"
    return_type = None
    state_vars: list[tuple[str, str, str]] = []
    find_packages: list[str] = []
    pkg_modules: list[str] = []
    c_deps: list[str] = []

    remaining = args[1:]
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        if tok == "--object":
            i += 1
            if i >= len(remaining):
                print("error: --object requires a name", file=sys.stderr)
                sys.exit(1)
            object_names.append(remaining[i])
            i += 1
        elif tok == "--module":
            i += 1
            if i >= len(remaining):
                print("error: --module requires a name", file=sys.stderr)
                sys.exit(1)
            modules.append(remaining[i])
            i += 1
        elif tok in ("--state", "--param"):
            var, i = parse_state_flag(remaining, i)
            state_vars.append(var)
        elif tok == "--build-system":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --build-system requires a value (cmake, make)",
                    file=sys.stderr,
                )
                sys.exit(1)
            val = remaining[i]
            if val not in ("cmake", "make"):
                print(
                    f"error: --build-system '{val}' is not valid. "
                    "Choose cmake or make.",
                    file=sys.stderr,
                )
                sys.exit(1)
            build_system = val
            i += 1
        elif tok == "--basic":
            print(
                "warning: --basic is deprecated; use --build-system make",
                file=sys.stderr,
            )
            build_system = "make"
            i += 1
        elif tok == "--perf":
            perf = True
            i += 1
        elif tok == "--pytest":
            pytest_ = True
            i += 1
        elif tok == "--pytest-benchmark":
            pytest_benchmark_ = True
            i += 1
        elif tok == "--find-package":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --find-package requires a CMake package name",
                    file=sys.stderr,
                )
                sys.exit(1)
            find_packages.append(remaining[i])
            i += 1
        elif tok == "--pkg-module":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --pkg-module requires a pkg-config module name",
                    file=sys.stderr,
                )
                sys.exit(1)
            pkg_modules.append(remaining[i])
            i += 1
        elif tok == "--c-dep":
            i += 1
            if i >= len(remaining):
                print(
                    "error: --c-dep requires a vendored subdirectory name",
                    file=sys.stderr,
                )
                sys.exit(1)
            c_deps.append(remaining[i])
            i += 1
        elif tok == "--mutable":
            mutable = True
            i += 1
        elif tok in ("--arg-type", "--return-type"):
            i += 1
            if i >= len(remaining):
                print(f"error: {tok} requires a type", file=sys.stderr)
                sys.exit(1)
            val = remaining[i]
            if val.endswith("[]"):
                if tok == "--return-type":
                    print(
                        "error: --return-type cannot be an array type.\n"
                        "Use a scalar type or void.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                elem = val[:-2]
                if elem not in T._CTYPE_META:
                    print(
                        f"error: --arg-type array element type '{elem}' "
                        "is not supported.\n"
                        f"Supported element types: "
                        f"{', '.join(sorted(T._CTYPE_META))}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif val != "void" and val not in T._CTYPE_META:
                print(
                    f"error: {tok} '{val}' is not a supported scalar type.\n"
                    f"Supported: void, {', '.join(sorted(T._CTYPE_META))}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if tok == "--arg-type":
                arg_type = val
            else:
                return_type = val
            i += 1
        elif dest is None and not tok.startswith("-"):
            dest = Path(tok)
            i += 1
        else:
            print(f"error: unexpected argument '{tok}'", file=sys.stderr)
            sys.exit(1)

    _new.run(
        project,
        dest,
        object_names or None,
        state_vars or None,
        modules=modules,
        build_system=build_system,
        perf=perf,
        mutable=mutable,
        arg_type=arg_type,
        return_type=return_type,
        pytest_=pytest_,
        pytest_benchmark_=pytest_benchmark_,
        find_packages=find_packages or None,
        pkg_modules=pkg_modules or None,
        c_deps=c_deps or None,
    )
