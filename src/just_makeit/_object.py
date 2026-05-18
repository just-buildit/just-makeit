"""
_object.py — `just-makeit object` command.

Adds a Python type to an existing project:

  Standalone (own .so):
    just-makeit object gain                  # no --module -> standalone
    from my_pkg import Gain

  In-module (shared .so subpackage):
    just-makeit object fir --module filter   # grouped under filter subpackage
    from my_pkg.filter import Fir
"""

import re
import sys
from pathlib import Path

from . import _color as Color
from . import _config as C
from . import _stubs as S
from . import _templates as T
from ._init import (
    _make_component_ctx,
    _to_title,
    _write,
    _write_compile_commands,
)


def _make_object_ctx(
    component: str,
    module: str,
    pkg: str,
    version: str,
    state_vars: list[tuple[str, str, str]],
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    perf: bool = False,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    mutable: bool = False,
    init_params: list[tuple[str, str, str]] = (),
    class_name: str | None = None,
) -> dict:
    """Build the render ctx for an object."""
    ctx = _make_component_ctx(component)
    if class_name is not None:
        ctx["Component"] = class_name
    ctx.update(
        {
            "module": module,
            "Module": _to_title(module),
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )
    ctx.update(T.make_sample_ctx(arg_type, return_type))
    ctx.update(
        T.make_state_ctx(
            ctx["component"],
            ctx["Component"],
            state_vars,
            array_args=array_args,
            no_state=no_state,
            init_params=init_params,
        )
    )
    ctx.update(T.make_perf_ctx(perf))
    _rt = return_type or ("void" if arg_type.endswith("[]") else arg_type)
    ctx.update(T.make_step_ctx(ctx, arg_type, _rt, no_step=no_step, mutable=mutable))
    # Re-generate pyi_examples now that package and Component are in ctx.
    # make_state_ctx emits placeholder text; we replace it with the real values.
    scalar_state = (
        [
            (n, ct, dflt)
            for n, ct, dflt in (state_vars or [])
            if not T.parse_array_type(ct)
        ]
        if not no_state
        else []
    )
    has_aa = bool(array_args)
    import_line = f"from {pkg} import {ctx['Component']}"
    ctx["pyi_examples"] = (
        T._pyi_examples_block(
            scalar_state,
            has_aa,
            import_line,
            ctx.get("py_create_args", ""),
            ctx["Component"],
        )
        if (scalar_state or has_aa)
        else ""
    )
    return ctx


def _copy_external_cmake_blocks(
    root: Path, new_comp: str, new_cmake_path: Path
) -> None:
    """Copy ``if(VAR) … endif()`` blocks from sibling CMakeLists to *new_comp*.

    Looks at ``native/src/*/CMakeLists.txt`` files (excluding the new
    component's own file) for ``if(SOME_VAR)`` blocks that contain
    ``target_include_directories`` or ``target_link_libraries``.  The first
    match is adapted (component name replaced) and appended to *new_cmake_path*
    so the new OBJECT library gets the same external library wiring without
    manual edits.

    This handles projects like doppler-based extensions that set
    ``if(DOPPLER_C_LIB)`` blocks — every new component inherits the same
    conditional include/link paths automatically.
    """
    src_dir = root / "native" / "src"
    if not src_dir.exists():
        return

    # Match a standalone if(VAR) … endif() block at the top level of cmake
    block_pat = re.compile(
        r"(if\s*\(\s*\w+\s*\)\n(?:[^\n]*\n)*?endif\s*\(\s*\))",
        re.MULTILINE,
    )

    for cmake_file in sorted(src_dir.glob("*/CMakeLists.txt")):
        if cmake_file.parent.name == new_comp:
            continue
        text = cmake_file.read_text(encoding="utf-8")
        for m in block_pat.finditer(text):
            block = m.group(1)
            if (
                "target_include_directories" in block
                or "target_link_libraries" in block
            ):
                old_comp = cmake_file.parent.name
                adapted = block.replace(old_comp, new_comp)
                existing = new_cmake_path.read_text(encoding="utf-8")
                if adapted not in existing:
                    new_cmake_path.write_text(
                        existing.rstrip("\n") + "\n\n" + adapted + "\n",
                        encoding="utf-8",
                    )
                    print(f"  update  {new_cmake_path}  (external lib block)")
                return  # one source file is enough


def _merge_module_init(existing: str, module: str, all_exports: list[str]) -> str:
    """Merge new exports into an existing __init__.py without destroying content.

    Updates only the ``from .<module> import ...`` line and ``__all__`` list to
    include any newly added type/function names, leaving wrapper classes,
    docstrings, and all other user content intact.

    >>> src = '# dsp/__init__.py\\nfrom .dsp import Nco\\n__all__ = ["Nco"]\\n'
    >>> print(_merge_module_init(src, 'dsp', ['Nco', 'Mixer']))
    # dsp/__init__.py
    from .dsp import Nco, Mixer
    __all__ = ["Nco", "Mixer"]
    <BLANKLINE>
    """
    # Match the import line whether it has names or is empty (e.g. after
    # `just-makeit module foo` before any objects are added).
    import_pat = re.compile(
        rf"^from \.{re.escape(module)} import([^#\n]*)(?:#[^\n]*)?$",
        re.MULTILINE,
    )
    all_pat = re.compile(r"^__all__\s*=\s*\[([^\]]*)\]", re.MULTILINE)

    existing_names: list[str] = []
    existing_set: set[str] = set()
    m = import_pat.search(existing)
    if m:
        for n in m.group(1).split(","):
            name = n.strip()
            if name and name not in existing_set:
                existing_names.append(name)
                existing_set.add(name)

    merged: list[str] = list(existing_names)
    for name in all_exports:
        if name not in existing_set:
            merged.append(name)
            existing_set.add(name)

    if not merged:
        return existing

    imports_str = ", ".join(merged)
    all_str = ", ".join(f'"{n}"' for n in merged)
    new_import = f"from .{module} import {imports_str}  # noqa: E402"
    new_all = f"__all__ = [{all_str}]"

    result = import_pat.sub(new_import, existing) if m else existing
    if all_pat.search(result):
        result = all_pat.sub(new_all, result)
    else:
        result = result.rstrip("\n") + f"\n{new_all}\n"
    return result


def _extract_c_function_bodies(source: str) -> dict[str, str]:
    """Extract ``static PyObject *`` function bodies from C source.

    Returns ``{function_name: full_function_text}`` for every
    ``static PyObject *`` function in *source*.  Used to preserve
    user-edited implementations when regenerating module_ext.c.

    Uses brace-counting rather than a regex for the body so that nested
    braces and parentheses inside parameter lists (e.g. ``Py_UNUSED(...)``)
    are handled correctly.
    """
    # Locate every "static PyObject *\n<name>(" header.
    header_pat = re.compile(r"static PyObject \*\n(\w+)\(")
    result: dict[str, str] = {}
    for hm in header_pat.finditer(source):
        fn_name = hm.group(1)
        start = hm.start()
        # Scan forward from the match to find the opening '{' of the body.
        i = hm.end()
        # Skip past the parameter list (balanced parens from the '(' we just
        # passed — but header_pat consumed the '(' so depth starts at 1).
        depth = 1
        while i < len(source) and depth:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        # Now scan for the opening '{'.
        while i < len(source) and source[i] != "{":
            i += 1
        if i >= len(source):
            continue
        brace_start = i
        # Collect the full body using balanced brace counting.
        depth = 0
        end = brace_start
        while end < len(source):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        result[fn_name] = source[start:end]
    return result


def _restore_c_function_bodies(new_source: str, preserved: dict[str, str]) -> str:
    """Replace stub implementations in *new_source* with *preserved* bodies.

    Only replaces functions that already existed in the old source AND still
    exist in the newly generated source.  New functions (first-time stubs) are
    left unchanged, so fresh scaffolded methods get their TODO stubs.
    """
    for fn_name, old_body in preserved.items():
        # Locate the function in new_source using the same brace-counting
        # approach (handles Py_UNUSED and other nested-paren params).
        header_pat = re.compile(r"static PyObject \*\n" + re.escape(fn_name) + r"\(")
        hm = header_pat.search(new_source)
        if not hm:
            continue
        start = hm.start()
        i = hm.end()
        depth = 1
        while i < len(new_source) and depth:
            if new_source[i] == "(":
                depth += 1
            elif new_source[i] == ")":
                depth -= 1
            i += 1
        while i < len(new_source) and new_source[i] != "{":
            i += 1
        if i >= len(new_source):
            continue
        depth = 0
        end = i
        while end < len(new_source):
            if new_source[end] == "{":
                depth += 1
            elif new_source[end] == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        new_source = new_source[:start] + old_body + new_source[end:]
    return new_source


def _regenerate_module(root: Path, cfg: dict, module: str, pkg: str) -> None:
    """Regenerate module_ext.c, module CMakeLists, and subpackage __init__."""
    object_names = C.module_objects(cfg, module)
    Module = _to_title(module)

    comp_ctxs: list[dict] = []
    for obj in object_names:
        state_vars = C.state_vars(cfg, obj)
        arg_type_ = C.arg_type(cfg, obj)
        return_type_ = C.return_type(cfg, obj)
        perf = C.is_perf(cfg)
        ctx = _make_object_ctx(
            obj,
            module,
            pkg,
            C.project_version(cfg),
            state_vars,
            arg_type_,
            return_type_,
            perf=perf,
            array_args=C.array_args(cfg, obj),
            no_state=C.is_no_state(cfg, obj),
            no_step=C.is_no_step(cfg, obj),
            mutable=C.is_mutable(cfg, obj),
            init_params=C.init_params(cfg, obj),
            class_name=C.class_name(cfg, obj),
        )
        ctx.update(
            T.make_methods_ctx(
                ctx["component"],
                ctx["Component"],
                C.methods(cfg, obj),
                pkg=pkg,
                py_create_args=ctx.get("py_create_args", ""),
            )
        )
        ctx.update(
            T.make_properties_ctx(
                ctx["component"],
                ctx["Component"],
                C.properties(cfg, obj),
                frozenset(n for n, _, _ in state_vars),
            )
        )
        comp_ctxs.append(ctx)

    # Module ext.c — preserve any user-edited C function bodies before
    # overwriting so that implementations added via `just-makeit method`
    # (or manually edited) survive re-scaffolding.
    functions = C.module_functions(cfg, module)
    ext_c_path = root / "native" / "src" / module / f"{module}_ext.c"
    existing_bodies: dict[str, str] = {}
    if ext_c_path.exists():
        existing_bodies = _extract_c_function_bodies(
            ext_c_path.read_text(encoding="utf-8")
        )
    ext_c = T.render_module_ext_c(module, comp_ctxs, functions)
    if existing_bodies:
        ext_c = _restore_c_function_bodies(ext_c, existing_bodies)
    _write(ext_c_path, ext_c, "update")

    # Module CMakeLists
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    # Collocated case: when an object shares the module name (e.g. module="fft",
    # object="fft"), CMAKE_LISTS_OBJECT_CORE is prepended and already defines
    # <mod>_core.  Non-collocated: we define <mod>_core separately so that
    # module-level functions in <mod>_core.c are compiled and linked in.
    has_collocated = module in object_names
    if has_collocated:
        # <mod>_core is the collocated object's OBJECT lib; it's already in
        # object_names so it will appear in object_core_libs below.
        module_core_lib_block = ""
        libs_parts = [f"{obj}_core" for obj in object_names]
    else:
        module_core_lib_block = (
            f"add_library({module}_core OBJECT {module}_core.c)\n"
            f"target_include_directories({module}_core PRIVATE"
            f" ${{CMAKE_SOURCE_DIR}}/native/inc)\n\n"
        )
        libs_parts = [f"{module}_core"] + [f"{obj}_core" for obj in object_names]
    object_core_libs = "\n    ".join(libs_parts)
    cmake_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "object_core_libs": object_core_libs,
        "module_core_lib_block": module_core_lib_block,
    }
    # Collocated objects share the same CMakeLists file as the module itself;
    # their OBJECT library cmake is prepended before CMAKE_LISTS_MODULE.
    # Migration: if a legacy _methods.c exists on disk, preserve it in the
    # CMakeLists so old projects don't break on regen.  New projects never
    # have _methods.c — stubs go in _core.c.
    collocated_cmake = ""
    for obj, ctx_ in zip(object_names, comp_ctxs):
        if obj == module:
            obj_cmake = T.render(T.CMAKE_LISTS_OBJECT_CORE, ctx_)
            methods_c = root / "native" / "src" / obj / f"{obj}_methods.c"
            if methods_c.exists():
                old_lib = f"add_library({obj}_core OBJECT {obj}_core.c)"
                new_lib = f"add_library({obj}_core OBJECT {obj}_core.c {obj}_methods.c)"
                obj_cmake = obj_cmake.replace(old_lib, new_lib)
            collocated_cmake += obj_cmake
    _write(
        root / "native" / "src" / module / "CMakeLists.txt",
        collocated_cmake + T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
        "update",
    )

    # Subpackage __init__.py — merge new exports into existing file so that
    # user-written wrapper classes and docstrings are not destroyed.
    Components = [ctx["Component"] for ctx in comp_ctxs]
    fn_names = [f["name"] for f in functions]
    all_exports = Components + fn_names
    pkg_module_dir = root / "src" / pkg / module
    init_path = pkg_module_dir / "__init__.py"
    if init_path.exists():
        merged = _merge_module_init(
            init_path.read_text(encoding="utf-8"), module, all_exports
        )
        _write(init_path, merged, "update")
    else:
        object_imports = ", ".join(all_exports)
        object_all = ", ".join(f'"{name}"' for name in all_exports)
        init_ctx = {
            "module": module,
            "Module": Module,
            "object_imports": object_imports,
            "object_all": object_all,
        }
        _write(init_path, T.render(T.MODULE_INIT_PY, init_ctx), "update")

    # Type stubs — regenerated in full every time the module changes.
    _write(pkg_module_dir / f"{module}.pyi", S.make_module_pyi(cfg, module), "update")


def run(
    root: Path,
    object_name: str,
    module: str | None,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    mutable: bool = False,
    impl_body: str | None = None,
    init_params: list[tuple[str, str, str]] = (),
    variable_output: bool = False,
    multi_output: list[str] = (),
    method_name: str = "run",
    class_name: str | None = None,
    _hint: bool = True,
) -> None:
    if not object_name.replace("_", "").isalnum() or object_name[0].isdigit():
        print(
            f"error: '{object_name}' is not a valid object name.\n"
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

    # No --module -> standalone object (own .so)
    if module is None:
        from . import _init

        _init.run(
            root,
            object_name,
            state_vars,
            perf=perf,
            arg_type=arg_type,
            return_type=return_type,
            array_args=array_args,
            no_state=no_state,
            no_step=no_step,
            mutable=mutable,
            impl_body=impl_body,
            init_params=init_params,
            class_name=class_name,
            _hint=_hint and not variable_output,
        )
        if variable_output:
            from . import _method as _M

            _rt = return_type or arg_type
            _M.run(
                root,
                object_name,
                method_name,
                module=None,
                arg_type="void",
                return_type=_rt,
                variable_output=True,
                multi_output=list(multi_output),
            )
        return

    # --module given -> in-module path
    mods = C.modules(cfg)
    if module not in mods:
        print(
            f"error: module '{module}' not found. "
            f"Run 'just-makeit module {module}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if object_name in C.module_objects(cfg, module):
        print(
            f"error: object '{object_name}' already exists in module '{module}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if object_name in C.components(cfg):
        print(
            f"error: '{object_name}' already exists as a standalone component.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)

    vars_ = [] if no_state else (state_vars or [("gain", "double", "0.0")])
    ctx = _make_object_ctx(
        object_name,
        module,
        pkg,
        version,
        vars_,
        arg_type,
        return_type,
        perf=perf,
        array_args=array_args,
        no_state=no_state,
        no_step=no_step,
        mutable=mutable,
        init_params=init_params,
        class_name=class_name,
    )
    ctx.update(
        T.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
        )
    )

    def r(tmpl):
        return T.render(tmpl, ctx)

    comp = ctx["component"]
    print(
        f"just-makeit: adding object '{comp}' to module '{module}' in project '{pkg}'"
    )
    print()

    # C library files (OBJECT lib only — no standalone Python module)
    _write(root / "native" / "inc" / comp / f"{comp}_core.h", r(T.COMPONENT_CORE_H))
    if impl_body is not None and not no_step:
        from . import _impl as I

        h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
        h_text = h_path.read_text(encoding="utf-8")
        h_text = I.patch_function_body(h_text, f"{comp}_step", impl_body)
        h_path.write_text(h_text, encoding="utf-8")
    _write(root / "native" / "src" / comp / f"{comp}_core.c", r(T.COMPONENT_CORE_C))
    obj_cmake_path = root / "native" / "src" / comp / "CMakeLists.txt"
    _write(obj_cmake_path, r(T.CMAKE_LISTS_OBJECT_CORE))
    # Propagate any external-library cmake blocks from sibling objects so the
    # new component picks up the same if(SOME_LIB) include/link wiring without
    # manual edits (e.g. if(DOPPLER_C_LIB) in doppler-based projects).
    _copy_external_cmake_blocks(root, comp, obj_cmake_path)
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(T.COMPONENT_TEST_C))
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(T.NO_STEP_BENCH_C if no_step else T.COMPONENT_BENCH_C),
    )
    jm_bench_h = root / "native" / "benchmarks" / "jm_bench.h"
    if not jm_bench_h.exists():
        _write(jm_bench_h, T.JM_BENCH_H)

    # Python tests and benchmarks for this module object
    pkg_mod_dir = root / "src" / pkg / module
    tests_init = pkg_mod_dir / "tests" / "__init__.py"
    if not tests_init.exists():
        _write(tests_init, T.TESTS_INIT_PY)
    test_py_tmpl = (
        T.MODULE_PYTEST_TEST_PURE if C.is_pytest(cfg) else T.MODULE_PYTEST_TEST
    )
    bench_py_tmpl = (
        T.MODULE_BENCH_PYTEST_BM if C.is_pytest_benchmark(cfg) else T.MODULE_BENCH_PY
    )
    _write(pkg_mod_dir / "tests" / f"test_{comp}.py", r(test_py_tmpl))
    benchmarks_init = pkg_mod_dir / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(pkg_mod_dir / "benchmarks" / f"bench_{comp}.py", r(bench_py_tmpl))

    # Update config before regenerating module (so module_objects is up-to-date)
    C.add_to_module(cfg, module, comp)
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
    )

    # Regenerate module ext.c + CMakeLists + subpackage __init__
    _regenerate_module(root, cfg, module, pkg)

    # Root CMakeLists: insert add_subdirectory into Components sentinel section,
    # then wire OBJECT library into both shared and static C library targets.
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        sub = f"add_subdirectory(native/src/{comp})\n"
        if sub not in cmake_text:
            sentinel = "# ── Components"
            obj_lines = (
                f"target_sources({pkg}_lib PRIVATE $<TARGET_OBJECTS:{comp}_core>)\n"
                f"target_sources({pkg}_lib_static PRIVATE $<TARGET_OBJECTS:{comp}_core>)\n"
            )
            if sentinel in cmake_text:
                # Insert after the sentinel comment line
                cmake_text = cmake_text.replace(
                    sentinel,
                    sentinel,
                    1,
                )
                idx = cmake_text.index(sentinel)
                idx = cmake_text.index("\n", idx) + 1
                cmake_text = cmake_text[:idx] + sub + obj_lines + cmake_text[idx:]
            else:
                cmake_text += sub + obj_lines
            cmake_path.write_text(cmake_text, encoding="utf-8")
            print(f"  update  {cmake_path}")

    # Umbrella header
    umbrella = root / "native" / "inc" / f"{pkg}.h"
    include_line = f'#include "{comp}/{comp}_core.h"\n'
    if umbrella.exists():
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

    # compile_commands.json
    all_comps = C.components(cfg)
    _write_compile_commands(root, all_comps, C.modules(cfg))

    # Save config
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    if variable_output:
        from . import _method as _M

        _rt = return_type or arg_type
        _M.run(
            root,
            object_name,
            method_name,
            module=module,
            arg_type="void",
            return_type=_rt,
            variable_output=True,
            multi_output=list(multi_output),
        )
    else:
        print()
        print(f"{Color.done('Done!')}  {Color.cmd('cmake --build build')}")
