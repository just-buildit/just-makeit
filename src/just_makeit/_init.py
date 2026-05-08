"""
_init.py — `just-makeit init` command.

Adds a new C extension component to an existing project.
"""

import json
import re
import sysconfig
import sys
from pathlib import Path

from . import _config as C
from . import _templates as T


def _to_title(snake: str) -> str:
    return "".join(w.title() for w in snake.split("_"))


def _make_component_ctx(component: str) -> dict[str, str]:
    return {
        "component": component,
        "Component": _to_title(component),
        "COMPONENT": component.upper(),
    }


def _write(path: Path, content: str, verb: str = "create") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  {verb}  {path}")


def _write_compile_commands(root: Path, all_components: list[str]) -> None:
    r = root.resolve()
    python_inc = sysconfig.get_path("include")
    try:
        import numpy as np

        numpy_inc = np.get_include()
    except ImportError:
        numpy_inc = None

    base_inc = [str(r / "native" / "inc")]
    if python_inc:
        base_inc.append(python_inc)
    if numpy_inc:
        base_inc.append(numpy_inc)

    entries = []
    for comp in all_components:
        comp_inc = base_inc + [str(r / "native" / "inc" / comp)]
        inc_flags = " ".join(f"-I{d}" for d in comp_inc)
        base = f"cc -std=c99 -Wall -Wextra -fPIC {inc_flags}"
        test_flags = (
            f"cc -std=c99 -Wall"
            f" -I{r / 'native' / 'inc'}"
            f" -I{r / 'native' / 'inc' / comp}"
        )

        def _entry(src_rel, flags):
            abs_src = str(r / src_rel)
            return {
                "directory": str(r),
                "command": f"{flags} -c {abs_src} -o /dev/null",
                "file": abs_src,
            }

        entries += [
            _entry(f"native/src/{comp}/{comp}_core.c", base),
            _entry(f"native/src/{comp}/{comp}_ext.c", base),
            _entry(f"native/tests/test_{comp}_core.c", test_flags),
            _entry(f"native/benchmarks/bench_{comp}_core.c", test_flags),
        ]

    dest = root / "compile_commands.json"
    verb = "create" if not dest.exists() else "update"
    dest.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"  {verb}  {dest}")


def run(
    root: Path,
    component: str,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    pure: bool = False,
    _hint: bool = True,
) -> None:
    if not component.replace("_", "").isalnum() or component[0].isdigit():
        print(
            f"error: '{component}' is not a valid component name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    if component in C.components(cfg):
        print(
            f"error: component '{component}' already exists in this project.",
            file=sys.stderr,
        )
        sys.exit(1)

    vars_ = state_vars or [("gain", "double", "0.0")]
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)

    ctx = _make_component_ctx(component)
    ctx.update(
        {
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )

    if pure:
        pure_ctx = T.make_pure_ctx(ctx["component"], ctx["Component"], vars_)
        ctx.update(pure_ctx)
        pure_style = pure_ctx["pure_style"]
    else:
        ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], vars_))
        pure_style = None

    ctx.update(T.make_perf_ctx(perf))

    def r(tmpl):
        return T.render(tmpl, ctx)

    comp = ctx["component"]

    print(f"just-makeit: adding component '{comp}' to project '{pkg}'")
    print()

    if perf and not C.is_perf(cfg):
        cfg.setdefault("project", {})["perf"] = "true"

    if perf:
        perf_h = root / "native" / "inc" / "jm_perf.h"
        if not perf_h.exists():
            _write(perf_h, r(T.JM_PERF_H))
        simd_h = root / "native" / "inc" / "jm_simd.h"
        if not simd_h.exists():
            _write(simd_h, T.JM_SIMD_H)

    if pure_style == "scalar":
        core_h_tmpl = T.PURE_SCALAR_CORE_H
        core_c_tmpl = T.PURE_SCALAR_CORE_C
        ext_c_tmpl = T.PURE_SCALAR_EXT_C
        test_c_tmpl = T.PURE_SCALAR_TEST_C
        bench_c_tmpl = T.PURE_SCALAR_BENCH_C
        pyi_tmpl = T.PURE_SCALAR_PYI
        pytest_tmpl = T.PYTEST_PURE_SCALAR_TEST
        bench_py_tmpl = T.PURE_SCALAR_BENCH_PY
        init_py_tmpl = T.PACKAGE_INIT_PY_PURE_SCALAR
    elif pure_style == "struct":
        core_h_tmpl = T.PURE_STRUCT_CORE_H
        core_c_tmpl = T.PURE_STRUCT_CORE_C
        ext_c_tmpl = T.PURE_STRUCT_EXT_C
        test_c_tmpl = T.PURE_STRUCT_TEST_C
        bench_c_tmpl = T.PURE_STRUCT_BENCH_C
        pyi_tmpl = T.PURE_STRUCT_PYI
        pytest_tmpl = T.PYTEST_PURE_STRUCT_TEST
        bench_py_tmpl = T.PURE_STRUCT_BENCH_PY
        init_py_tmpl = T.PACKAGE_INIT_PY
    else:
        core_h_tmpl = T.COMPONENT_CORE_H
        core_c_tmpl = T.COMPONENT_CORE_C
        ext_c_tmpl = T.COMPONENT_EXT_C
        test_c_tmpl = T.COMPONENT_TEST_C
        bench_c_tmpl = T.COMPONENT_BENCH_C
        pyi_tmpl = T.COMPONENT_PYI
        pytest_tmpl = T.PYTEST_TEST
        bench_py_tmpl = T.COMPONENT_BENCH_PY
        init_py_tmpl = T.PACKAGE_INIT_PY

    # C headers
    _write(root / "native" / "inc" / comp / f"{comp}_core.h", r(core_h_tmpl))

    # C sources
    _write(root / "native" / "src" / comp / f"{comp}_core.c", r(core_c_tmpl))
    _write(root / "native" / "src" / comp / f"{comp}_ext.c", r(ext_c_tmpl))

    build = C.build_system(cfg)

    if build == "cmake":
        _write(
            root / "native" / "src" / comp / "CMakeLists.txt",
            r(T.CMAKE_LISTS_COMPONENT),
        )

    # C test
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(test_c_tmpl))

    # C benchmark
    _write(root / "native" / "benchmarks" / f"bench_{comp}_core.c", r(bench_c_tmpl))

    # Python package — write __init__.py only if it doesn't exist yet
    init_py = root / "src" / pkg / "__init__.py"
    if not init_py.exists():
        _write(init_py, r(init_py_tmpl))

    _write(root / "src" / pkg / f"{comp}.pyi", r(pyi_tmpl))
    _write(root / "src" / pkg / "tests" / "__init__.py", T.TESTS_INIT_PY)
    _write(root / "src" / pkg / "tests" / f"test_{comp}.py", r(pytest_tmpl))

    # Python benchmark
    benchmarks_init = root / "src" / pkg / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(root / "src" / pkg / "benchmarks" / f"bench_{comp}.py", r(bench_py_tmpl))

    # Benchmark history dir (committed to git)
    gitkeep = root / ".benchmarks" / ".gitkeep"
    if not gitkeep.exists():
        _write(gitkeep, "")

    if build == "cmake":
        # Write cmake/pkg.pc.in if the project predates v0.4
        pc_in = root / "cmake" / f"{pkg.replace('_', '-')}.pc.in"
        if not pc_in.exists():
            _write(pc_in, T.render(T.CMAKE_PC_IN, ctx))

        # Write or update the umbrella header
        umbrella = root / "native" / "inc" / f"{pkg}.h"
        include_line = f'#include "{comp}/{comp}_core.h"\n'
        if not umbrella.exists():
            _write(umbrella, T.render(T.UMBRELLA_H, ctx))
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            # Insert before the closing #endif
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

        # Append add_subdirectory + wire component into combined lib
        cmake_path = root / "CMakeLists.txt"
        if cmake_path.exists():
            cmake_text = cmake_path.read_text(encoding="utf-8")
            cmake_text += f"add_subdirectory(native/src/{comp})\n"
            # Only add target_link_libraries if the combined lib target exists (v0.4+)
            if f"{pkg}_lib" in cmake_text:
                cmake_text += (
                    f"target_link_libraries({pkg}_lib PRIVATE {comp}_core)\n"
                )
            cmake_path.write_text(cmake_text, encoding="utf-8")
            print(f"  update  {cmake_path}")

        # compile_commands.json
        all_comps = C.components(cfg) + [comp]
        _write_compile_commands(root, all_comps)

    else:
        # Patch TARGETS and C_TESTS lists, insert compile rules into Makefile
        mf_path = root / "Makefile"
        mf = mf_path.read_text(encoding="utf-8")
        target = f"src/{pkg}/{comp}$(EXT)"
        ctest = f"test_{comp}_core"
        mf = re.sub(r"^(TARGETS\s*:=.*)$", rf"\1 {target}", mf, flags=re.M)
        mf = re.sub(r"^(C_TESTS\s*:=.*)$", rf"\1 {ctest}", mf, flags=re.M)
        rules = T.render(T.MAKEFILE_SIMPLE_COMPONENT, ctx)
        mf = mf.replace("# ── Fixed targets", rules + "# ── Fixed targets")
        mf_path.write_text(mf, encoding="utf-8")
        print(f"  update  {mf_path}")

    # just-makeit.toml
    C.add_component(cfg, comp, vars_, pure=pure_style)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    if _hint:
        print("Done!  make && make test")
