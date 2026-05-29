"""
_init.py — standalone object scaffolding (internal).

Called by `just-makeit object` (no --module) and `just-makeit new --object`.
"""

import json
import re
import sysconfig
import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _render as R
from . import _types as T


def _to_title(snake: str) -> str:
    return "".join(w[0].upper() + w[1:] for w in snake.split("_") if w)


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


# ── Core-file body preservation ──────────────────────────────────────────────
# Regenerating commands re-render <comp>_core.h / <comp>_core.c from templates,
# which would otherwise wipe any hand-written algorithm code.  The helpers
# below splice the bodies of an existing core file into freshly rendered
# template text — the same idea as the `static PyObject *` body preservation
# already done for <module>_ext.c, generalised to plain C functions.


def _matching_brace(source: str, open_idx: int) -> int:
    """Return the index just past the '}' that matches the '{' at open_idx."""
    depth = 0
    for i in range(open_idx, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(source)


def _func_span_before_brace(source: str, brace_idx: int) -> tuple[str, int] | None:
    """``(function_name, name_start)`` for the function whose body opens at
    brace_idx.

    Scans back over whitespace, the balanced ``(...)`` parameter list and more
    whitespace, then reads the identifier.  Returns None when the '{' opens
    something that is not a function definition (e.g. an array initialiser,
    whose '{' is preceded by '=').
    """
    i = brace_idx - 1
    while i >= 0 and source[i] in " \t\r\n":
        i -= 1
    if i < 0 or source[i] != ")":
        return None
    depth = 0
    while i >= 0:
        if source[i] == ")":
            depth += 1
        elif source[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i < 0:
        return None
    i -= 1
    while i >= 0 and source[i] in " \t\r\n":
        i -= 1
    end = i + 1
    while i >= 0 and (source[i].isalnum() or source[i] == "_"):
        i -= 1
    name = source[i + 1 : end]
    return (name, i + 1) if name else None


def _extract_core_c_funcs(source: str) -> dict[str, str]:
    """Map ``{function_name: definition}`` for a generated <comp>_core.c.

    The captured text runs from the function name through the closing brace
    (parameter list + body) so a hand-edited signature — e.g. a dropped
    ``const`` on a mutating step — is preserved too.  The leading return-type
    line is left to the template.  File-scope initialisers are skipped.
    """
    funcs: dict[str, str] = {}
    i = 0
    while True:
        brace = source.find("{", i)
        if brace == -1:
            break
        end = _matching_brace(source, brace)
        span = _func_span_before_brace(source, brace)
        if span:
            funcs[span[0]] = source[span[1] : end]
        i = end
    return funcs


def _restore_core_c_funcs(new_source: str, preserved: dict[str, str]) -> str:
    """Swap each function in *new_source* for its preserved definition.

    Functions that exist only in *new_source* (freshly scaffolded stubs) keep
    their generated body; functions only in *preserved* are dropped.
    """
    out: list[str] = []
    i = 0
    while True:
        brace = new_source.find("{", i)
        if brace == -1:
            out.append(new_source[i:])
            break
        end = _matching_brace(new_source, brace)
        span = _func_span_before_brace(new_source, brace)
        if span and span[0] in preserved:
            out.append(new_source[i : span[1]])
            out.append(preserved[span[0]])
        else:
            out.append(new_source[i:end])
        i = end
    return "".join(out)


_STRUCT_RE = re.compile(r"(typedef struct \{\n)(.*?)(\n\} \w+_state_t;)", re.DOTALL)
_FIELD_RE = re.compile(r"^\s+.*?(\w+)\s*(?:\[[^\]]*\])?\s*;", re.MULTILINE)


def _merge_struct_fields(new_fields: str, old_fields: str) -> str:
    """Keep *new_fields*, appending any field line from *old_fields* whose name
    is absent — so hand-added struct members survive regeneration without
    dropping fields the template now emits.
    """
    new_names = set(_FIELD_RE.findall(new_fields))
    extra = [
        line
        for line in old_fields.split("\n")
        if (m := _FIELD_RE.match(line)) and m.group(1) not in new_names
    ]
    if not extra:
        return new_fields
    return new_fields.rstrip("\n") + "\n" + "\n".join(extra)


def _step_func_span(source: str, comp: str) -> tuple[int, int] | None:
    """(start, end) spanning the inline ``<comp>_step`` name, parameter list
    and body, or None when there is no inline step definition.

    The leading ``JM_FORCEINLINE``/return-type line is excluded so it stays
    in sync with the template; the name onward (including a hand-edited
    non-const ``state`` parameter) is preserved.
    """
    pat = re.compile(r"\b" + re.escape(comp) + r"_step\s*\(")
    for m in pat.finditer(source):
        i = m.end()
        depth = 1
        while i < len(source) and depth:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        while i < len(source) and source[i] in " \t\r\n":
            i += 1
        if i < len(source) and source[i] == "{":
            return m.start(), _matching_brace(source, i)
    return None


def _preserve_core_bodies(
    path: Path,
    new_text: str,
    comp: str,
    exclude: tuple[str, ...] = (),
    skip_struct_merge: bool = False,
) -> str:
    """Splice hand-written bodies from an existing core file into *new_text*.

    For ``<comp>_core.c`` every function body is preserved; for
    ``<comp>_core.h`` the state-struct fields and the inline ``<comp>_step``
    body are preserved.  Returns *new_text* unchanged when *path* does not yet
    exist (first scaffold) or no preservable region is found.

    *exclude* names functions whose freshly rendered body must win over the
    old one — used by ``just-makeit add``, which has to rewrite ``create`` /
    ``reset`` so the newly added state variable is actually initialised.

    *skip_struct_merge* suppresses the struct-field merge for header files.
    Use this when the caller deliberately removed a state field and the new
    template already has the correct struct — merging would re-add the field.
    """
    if not path.exists():
        return new_text
    old = path.read_text(encoding="utf-8")
    if path.suffix == ".c":
        funcs = _extract_core_c_funcs(old)
        for name in exclude:
            funcs.pop(name, None)
        # Getter/setter impls are trivial auto-generated one-liners that
        # users never hand-edit — always re-emit the freshly generated version
        # so parameter/signature changes (e.g. val rename) take effect.
        gs_prefix = (f"{comp}_get_", f"{comp}_set_")
        for fn in list(funcs):
            if fn.startswith(gs_prefix):
                funcs.pop(fn)
        return _restore_core_c_funcs(new_text, funcs)
    # header: optionally merge struct fields, then restore the inline step()
    if not skip_struct_merge:
        old_struct = _STRUCT_RE.search(old)
        new_struct = _STRUCT_RE.search(new_text)
        if old_struct and new_struct:
            merged = _merge_struct_fields(new_struct.group(2), old_struct.group(2))
            new_text = (
                new_text[: new_struct.start(2)] + merged + new_text[new_struct.end(2) :]
            )
    old_step = _step_func_span(old, comp)
    new_step = _step_func_span(new_text, comp)
    if old_step and new_step:
        new_text = (
            new_text[: new_step[0]]
            + old[old_step[0] : old_step[1]]
            + new_text[new_step[1] :]
        )
    return new_text


def _splice_init_py(init_py: Path, component: str, Component: str) -> None:
    """Add `from .component import Component` and update __all__ in-place.

    Only appends — never removes or reorders — so user edits are preserved.
    No-op if the import is already present.
    """
    text = init_py.read_text(encoding="utf-8")
    import_line = f"from .{component} import {Component}\n"
    if import_line in text:
        return

    # Insert after the last `from .xxx import` line, or before __all__ if none.
    lines = text.splitlines(keepends=True)
    last_import_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"^from \.\w+ import \w+", line):
            last_import_idx = i
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, import_line)
    else:
        lines.append(import_line)
    text = "".join(lines)

    # Append Component to __all__ = [...] (handles single- and multi-line).
    # If __all__ is absent entirely, append it.
    all_re = re.compile(r"(__all__\s*=\s*\[)(.*?)(\])", re.DOTALL)
    if all_re.search(text):

        def _splice_all(m: re.Match) -> str:
            inner = m.group(2)
            if f'"{Component}"' in inner or f"'{Component}'" in inner:
                return m.group(0)
            stripped = inner.rstrip()
            sep = ", " if stripped.rstrip(",") else ""
            return f'{m.group(1)}{stripped.rstrip(",")}{sep}"{Component}"{m.group(3)}'

        text = all_re.sub(_splice_all, text)
    else:
        text = text.rstrip("\n") + f'\n\n__all__ = ["{Component}"]\n'

    init_py.write_text(text, encoding="utf-8")
    print(f"  update  {init_py}")


def _write_compile_commands(
    root: Path,
    all_components: list[str],
    all_modules: list[str] | None = None,
) -> None:
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
    base_flags = "cc -std=c99 -Wall -Wextra -fPIC " + " ".join(
        f"-I{d}" for d in base_inc
    )

    def _entry(src_rel: str, flags: str) -> dict:
        abs_src = str(r / src_rel)
        return {
            "directory": str(r),
            "command": f"{flags} -c {abs_src} -o /dev/null",
            "file": abs_src,
        }

    entries = []

    for comp in all_components:
        comp_inc = base_inc + [str(r / "native" / "inc" / comp)]
        comp_flags = "cc -std=c99 -Wall -Wextra -fPIC " + " ".join(
            f"-I{d}" for d in comp_inc
        )
        test_flags = (
            f"cc -std=c99 -Wall"
            f" -I{r / 'native' / 'inc'}"
            f" -I{r / 'native' / 'inc' / comp}"
        )
        entries.append(_entry(f"native/src/{comp}/{comp}_core.c", comp_flags))
        # Standalone objects have their own _ext.c; module objects do not
        # (they share <module>_ext.c, added below in the modules loop).
        ext_c = r / "native" / "src" / comp / f"{comp}_ext.c"
        if ext_c.exists():
            entries.append(_entry(f"native/src/{comp}/{comp}_ext.c", comp_flags))
        entries += [
            _entry(f"native/tests/test_{comp}_core.c", test_flags),
            _entry(f"native/benchmarks/bench_{comp}_core.c", test_flags),
        ]

    for mod in all_modules or []:
        entries.append(_entry(f"native/src/{mod}/{mod}_ext.c", base_flags))
        entries.append(_entry(f"native/src/{mod}/{mod}_core.c", base_flags))

    dest = root / "compile_commands.json"
    verb = "create" if not dest.exists() else "update"
    dest.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"  {verb}  {dest}")


def run(
    root: Path,
    component: str,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    mutable: bool = False,
    impl_body: str | None = None,
    create_impl_body: str | None = None,
    reset_impl_body: str | None = None,
    destroy_impl_body: str | None = None,
    init_params: list[tuple[str, str, str]] = (),
    opaque_fields: list[tuple[str, str]] = (),
    no_ctor_names: "frozenset[str]" = frozenset(),
    pytest_: bool | None = None,
    pytest_benchmark_: bool | None = None,
    class_name: str | None = None,
    depends_on: list[str] = (),
    extra_link_libs: list[str] = (),
    extra_include_dirs: list[str] = (),
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

    # Only inject the default "gain" field when there are no regular state
    # vars AND no opaque fields — opaque-only structs are fully user-managed.
    _has_opaque = bool(opaque_fields)
    vars_ = (
        []
        if no_state
        else (state_vars or ([] if _has_opaque else [("gain", "float", "0.0f")]))
    )
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)
    if pytest_ is not None:
        cfg.setdefault("project", {})["pytest"] = "true" if pytest_ else "false"
    if pytest_benchmark_ is not None:
        cfg.setdefault("project", {})["pytest_benchmark"] = (
            "true" if pytest_benchmark_ else "false"
        )

    ctx = _make_component_ctx(component)
    if class_name is not None:
        ctx["Component"] = class_name
    ctx.update(
        {
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )

    sample_ctx = Ctx.make_sample_ctx(arg_type, return_type)
    ctx.update(sample_ctx)

    ctx.update(
        Ctx.make_state_ctx(
            ctx["component"],
            ctx["Component"],
            vars_,
            array_args=array_args,
            no_state=no_state,
            init_params=init_params,
            opaque_fields=opaque_fields,
            no_ctor_names=no_ctor_names,
        )
    )
    ctx.update(Ctx.make_perf_ctx(perf))
    _rt = return_type or ("void" if arg_type.endswith("[]") else arg_type)
    ctx.update(Ctx.make_step_ctx(ctx, arg_type, _rt, no_step=no_step, mutable=mutable))
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=no_state,
        )
    )
    # Re-generate pyi_examples with the actual package name (not placeholder).
    scalar_state = (
        [(n, ct, dflt) for n, ct, dflt in (vars_ or []) if not T.parse_array_type(ct)]
        if not no_state
        else []
    )
    import_line = f"from {pkg} import {ctx['Component']}"
    ctx["pyi_examples"] = (
        Ctx._pyi_examples_block(
            scalar_state,
            bool(array_args),
            import_line,
            ctx.get("py_create_args", ""),
            ctx["Component"],
        )
        if scalar_state
        else ""
    )

    if create_impl_body is not None:
        from ._object import _indent_body

        ctx["create_assignments"] = _indent_body(create_impl_body)
    if reset_impl_body is not None:
        from ._object import _indent_body

        ctx["reset_assignments"] = _indent_body(reset_impl_body)
    if destroy_impl_body is not None:
        from ._object import _indent_body

        ctx["destroy_impl"] = _indent_body(destroy_impl_body) + "\n"

    extra_link_libs_block = (
        "\n    ".join(extra_link_libs) + "\n    " if extra_link_libs else ""
    )
    ctx["extra_link_libs_block"] = extra_link_libs_block
    # extra_include_dirs is a list of CMake include dirs (literals or ${VAR}
    # references). Each dir lands on its own indented line inside the
    # target_include_directories(...) blocks; leading "\n    " puts the first
    # entry on a new line so the closing ')' stays clean.
    extra_include_dirs_block = (
        "\n    " + "\n    ".join(extra_include_dirs) if extra_include_dirs else ""
    )
    ctx["extra_include_dirs_block"] = extra_include_dirs_block

    def r(tmpl):
        return R.render(tmpl, ctx)

    comp = ctx["component"]

    # extra_link_on_core: propagates external includes to the OBJECT library
    # so that its header files can #include external library headers directly.
    if extra_link_libs:
        parts = "\n    ".join(extra_link_libs)
        ctx["extra_link_on_core"] = (
            f"target_link_libraries({comp}_core PUBLIC\n    {parts})\n"
        )
    else:
        ctx["extra_link_on_core"] = ""
    # extra_include_dirs_on_core: PUBLIC include dirs on the OBJECT library so
    # downstream consumers (Python ext, test, bench) inherit them transitively.
    if extra_include_dirs:
        parts = "\n    ".join(extra_include_dirs)
        ctx["extra_include_dirs_on_core"] = (
            f"target_include_directories({comp}_core PUBLIC\n    {parts})\n"
        )
    else:
        ctx["extra_include_dirs_on_core"] = ""

    print(f"just-makeit: adding component '{comp}' to project '{pkg}'")
    print()

    if perf and not C.is_perf(cfg):
        cfg.setdefault("project", {})["perf"] = "true"

    if perf:
        perf_h = root / "native" / "inc" / "jm_perf.h"
        if not perf_h.exists():
            _write(perf_h, r(R.JM_PERF_H))
        simd_h = root / "native" / "inc" / "jm_simd.h"
        if not simd_h.exists():
            _write(simd_h, R.JM_SIMD_H)

    core_h_tmpl = R.COMPONENT_CORE_H
    core_c_tmpl = R.COMPONENT_CORE_C
    ext_c_tmpl = R.COMPONENT_EXT_C
    test_c_tmpl = R.COMPONENT_TEST_C
    bench_c_tmpl = R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C
    pyi_tmpl = R.COMPONENT_PYI
    pytest_tmpl = R.PYTEST_TEST_PURE if C.is_pytest(cfg) else R.PYTEST_TEST
    bench_py_tmpl = (
        R.COMPONENT_BENCH_PYTEST_BM
        if C.is_pytest_benchmark(cfg)
        else R.COMPONENT_BENCH_PY
    )
    init_py_tmpl = R.PACKAGE_INIT_PY

    # C headers
    core_h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
    _write(
        core_h_path,
        _preserve_core_bodies(core_h_path, r(core_h_tmpl), comp),
        "update" if core_h_path.exists() else "create",
    )
    if impl_body is not None and not no_step:
        from . import _impl as I

        h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
        h_text = h_path.read_text(encoding="utf-8")
        h_text = I.patch_function_body(h_text, f"{comp}_step", impl_body)
        h_path.write_text(h_text, encoding="utf-8")

    # C sources
    core_c_path = root / "native" / "src" / comp / f"{comp}_core.c"
    _write(
        core_c_path,
        _preserve_core_bodies(core_c_path, r(core_c_tmpl), comp),
        "update" if core_c_path.exists() else "create",
    )
    _write(root / "native" / "src" / comp / f"{comp}_ext.c", r(ext_c_tmpl))

    build = C.build_system(cfg)

    if build == "cmake":
        _write(
            root / "native" / "src" / comp / "CMakeLists.txt",
            r(R.CMAKE_LISTS_COMPONENT),
        )

    # C test
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(test_c_tmpl))

    # C benchmark
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(bench_c_tmpl),
    )
    jm_bench_h = root / "native" / "benchmarks" / "jm_bench.h"
    if not jm_bench_h.exists():
        _write(jm_bench_h, R.JM_BENCH_H)

    # Python package — create __init__.py on first component; splice on subsequent ones
    init_py = root / "src" / pkg / "__init__.py"
    if not init_py.exists():
        _write(init_py, r(init_py_tmpl))
    else:
        _splice_init_py(init_py, comp, ctx["Component"])

    _write(root / "src" / pkg / f"{comp}.pyi", r(pyi_tmpl))
    _write(root / "src" / pkg / "tests" / "__init__.py", R.TESTS_INIT_PY)
    _write(root / "src" / pkg / "tests" / f"test_{comp}.py", r(pytest_tmpl))

    # Python benchmark
    benchmarks_init = root / "src" / pkg / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(
        root / "src" / pkg / "benchmarks" / f"bench_{comp}.py",
        r(bench_py_tmpl),
    )

    # Benchmark history dir — dated snapshots committed to git
    gitkeep = root / "benchmarks" / "history" / ".gitkeep"
    if not gitkeep.exists():
        _write(gitkeep, "")

    if build == "cmake":
        # Write cmake/pkg.pc.in if the project predates v0.4
        pc_in = root / "cmake" / f"{pkg.replace('_', '-')}.pc.in"
        if not pc_in.exists():
            _write(pc_in, R.render(R.CMAKE_PC_IN, ctx))

        # Write or update the umbrella header
        umbrella = root / "native" / "inc" / f"{pkg}.h"
        include_line = f'#include "{comp}/{comp}_core.h"\n'
        if not umbrella.exists():
            _write(umbrella, R.render(R.UMBRELLA_H, ctx))
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            # Insert before the closing #endif
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

        # Insert add_subdirectory + target_sources into the `# ── Components`
        # sentinel section — matches `_object.run`'s placement so a project
        # built via `jm new --object` and one built via `jm new` + `jm object`
        # have identical CMakeLists, and so `jm apply`'s aggregate reconcile
        # is a no-op on either.
        cmake_path = root / "CMakeLists.txt"
        if cmake_path.exists():
            cmake_text = cmake_path.read_text(encoding="utf-8")
            sub = f"add_subdirectory(native/src/{comp})\n"
            if sub not in cmake_text:
                obj_lines = ""
                if f"{pkg}_lib" in cmake_text:
                    for dep in depends_on:
                        obj_lines += (
                            f"target_sources({pkg}_lib PRIVATE "
                            f"$<TARGET_OBJECTS:{dep}_core>)\n"
                        )
                    obj_lines += (
                        f"target_sources({pkg}_lib PRIVATE "
                        f"$<TARGET_OBJECTS:{comp}_core>)\n"
                    )
                sentinel = "# ── Components"
                if sentinel in cmake_text:
                    idx = cmake_text.index(sentinel)
                    idx = cmake_text.index("\n", idx) + 1
                    cmake_text = cmake_text[:idx] + sub + obj_lines + cmake_text[idx:]
                else:
                    cmake_text += sub + obj_lines
                cmake_path.write_text(cmake_text, encoding="utf-8")
                print(f"  update  {cmake_path}")

        # compile_commands.json
        all_comps = C.components(cfg) + [comp]
        _write_compile_commands(root, all_comps, C.modules(cfg))

    else:
        # Patch TARGETS and C_TESTS lists, insert compile rules into Makefile
        mf_path = root / "Makefile"
        mf = mf_path.read_text(encoding="utf-8")
        target = f"src/{pkg}/{comp}$(EXT)"
        ctest = f"test_{comp}_core"
        mf = re.sub(r"^(TARGETS\s*:=.*)$", rf"\1 {target}", mf, flags=re.M)
        mf = re.sub(r"^(C_TESTS\s*:=.*)$", rf"\1 {ctest}", mf, flags=re.M)
        rules = R.render(R.MAKEFILE_SIMPLE_COMPONENT, ctx)
        mf = mf.replace("# ── Fixed targets", rules + "# ── Fixed targets")
        mf_path.write_text(mf, encoding="utf-8")
        print(f"  update  {mf_path}")

    # just-makeit.toml
    C.add_component(
        cfg,
        comp,
        vars_,
        arg_type_=arg_type,
        return_type_=return_type,
        array_args_=array_args,
        no_state_=no_state,
        no_step_=no_step,
        mutable_=mutable,
        init_params_=init_params,
        class_name_=class_name,
        depends_on_=list(depends_on),
        extra_link_libs_=list(extra_link_libs),
        extra_include_dirs_=list(extra_include_dirs),
    )
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    if _hint:
        print("Done!  make && make test")
