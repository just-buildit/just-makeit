"""
_render.py — template loading and rendering for just-makeit.

Templates live in src/just_makeit/templates/ as real files. This module
loads them at import time and exposes them as module-level constants with
the same names callers already use (COMPONENT_CORE_H, CMAKE_LISTS_TOP, etc.).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from ._types import (
    c_param_list,
    _CTYPE_META,
    _CTYPE_TO_NPY,
    record_tuple_build,
    _join_fmt_with_optional,
    array_elem_ctype,
    is_array_param_type,
    parse_out_type,
)
from . import _coerce
from . import _config as C
from . import _record

_TMPL_DIR = Path(__file__).parent / "templates"


def _load(relpath: str) -> str:
    return (_TMPL_DIR / relpath).read_text(encoding="utf-8")


# ── C headers ────────────────────────────────────────────────────────────────
CLIB_COMMON_H = _load("c/inc/clib_common.h")
PYEX_COMMON_H = _load("c/inc/pyex_common.h")
JM_SIMD_H = _load("c/inc/jm_simd.h")
JM_PERF_H = _load("c/inc/jm_perf.h")
JM_BENCH_H = _load("c/inc/jm_bench.h")
JM_TEST_H = _load("c/inc/jm_test.h")
COMPONENT_CORE_H = _load("c/inc/component_core.h")
MODULE_CORE_H = _load("c/inc/module_core.h")
UMBRELLA_H = _load("c/inc/umbrella.h")
# Seeded into a new project only when `--c-style clang-format` is requested
# (gh-265), so `clang-format --style=file` has the house style to format to.
CLANG_FORMAT = _load("c/.clang-format")
# ── C source ─────────────────────────────────────────────────────────────────
COMPONENT_CORE_C = _load("c/src/component_core.c")
COMPONENT_EXT_C = _load("c/src/component_ext.c")
COMPONENT_TEST_C = _load("c/src/component_test.c")
COMPONENT_BENCH_C = _load("c/src/component_bench.c")
NO_STEP_BENCH_C = _load("c/src/no_step_bench.c")
MODULE_CORE_C = _load("c/src/module_core.c")
LIB_STUB_C = _load("c/src/lib_stub.c")
# ── CMake ────────────────────────────────────────────────────────────────────
CMAKE_LISTS_TOP = _load("cmake/CMakeLists_top.cmake")
CMAKE_LISTS_MODULE = _load("cmake/CMakeLists_module.cmake")
# gh-1034: the C test and benchmark a function-only module now gets, the
# same pair an object has always had.
MODULE_TEST_C = _load("c/src/module_test.c")
MODULE_BENCH_C = _load("c/src/module_bench.c")


def module_targets_block(
    cname: str,
    has_functions: bool,
    taken: "frozenset[str]" = frozenset(),
    extra_libs: Sequence[str] = (),
) -> str:
    """CMake `test_`/`bench_<cname>_core` targets for a module (gh-1034).

    Empty unless the module declares at least one free function, so a module
    that is purely a container for objects renders byte-identically to before
    — each of its objects already carries this pair.

    Keyed on "declares a function" rather than on "declares no objects". A
    function-only predicate would mean adding an object to a module silently
    DELETED its test and benchmark targets, which is the kind of foot-gun
    `jm apply` would then dutifully reconcile away.

    gh-1061: `extra_libs` is everything the module's own `.so` links beyond
    `<cname>_core` and Python — its `extra_link_libs`, each member object's
    core, and each member's `depends_on ... link = true` closure. The pair
    used to link `<cname>_core m` and nothing else, built from the name
    alone, so a declared dependency had no path by which it could reach them
    and a module function calling a sibling core did not link.

    This is gh-254's lesson one target pair later. That issue made
    `link = true` ADDITIVE for a collocated object's own test/bench rather
    than a move onto the `.so`; gh-1034 then introduced this pair without
    inheriting it. The rule is now one sentence covering both kinds of test
    target: they link what the `.so` links, minus Python.
    """
    if not has_functions:
        return ""
    # gh-1046: a CMake target name is global, so emitting one the project
    # already declares in its own CMakeLists is a hard configure error rather
    # than an override. Every consumer hand-registered this pair *because* jm
    # did not generate it (the gh-1023 workaround), which means the projects
    # this feature was written for are exactly the ones it collided with.
    # Skipped per target, not per pair: a project that hand-registered only
    # the benchmark still gets the test.
    libs = _module_link_libs(cname, extra_libs)
    blocks = []
    if f"test_{cname}_core" not in taken:
        blocks.append(_module_test_target(cname, libs))
    if f"bench_{cname}_core" not in taken:
        blocks.append(_module_bench_target(cname, libs))
    return "".join(blocks)


def _module_link_libs(cname: str, extra_libs: Sequence[str]) -> str:
    """The link-library argument for a module's test/bench pair (gh-1061).

    `<cname>_core` first, then each declared dependency in the order the
    module's `.so` link line carries it, then `m`. With nothing declared this
    is the one-line form the pair has always rendered, so a module that
    depends on nothing stays byte-identical and no project churns for free.

    Returns the text that follows `PRIVATE`, its own leading space included,
    so the multi-line form does not leave `PRIVATE ` with a trailing space on
    it — which cmake-lint rejects (C0303) and `make lint` gates on.
    """
    parts = [f"{cname}_core", *extra_libs, "m"]
    if len(parts) == 2:
        return " " + " ".join(parts)
    return "\n    " + "\n    ".join(parts)


def _module_test_target(cname: str, libs: str) -> str:
    """The `test_<cname>_core` CMake target for a module (gh-1034)."""
    return (
        f"\nadd_executable(test_{cname}_core\n"
        f"    ${{CMAKE_SOURCE_DIR}}/native/tests/test_{cname}_core.c)\n"
        f"target_link_libraries(test_{cname}_core PRIVATE{libs})\n"
        f"target_include_directories(test_{cname}_core\n"
        f"    PRIVATE ${{CMAKE_SOURCE_DIR}}/native/inc)\n"
        f"add_test(NAME test_{cname}_core COMMAND test_{cname}_core)\n"
    )


def _module_bench_target(cname: str, libs: str) -> str:
    """The `bench_<cname>_core` CMake target for a module (gh-1034)."""
    return (
        f"\nadd_executable(bench_{cname}_core\n"
        f"    ${{CMAKE_SOURCE_DIR}}/native/benchmarks/bench_{cname}_core.c)\n"
        f"target_link_libraries(bench_{cname}_core PRIVATE{libs})\n"
        f"target_include_directories(bench_{cname}_core\n"
        f"    PRIVATE ${{CMAKE_SOURCE_DIR}}/native/inc\n"
        f"            ${{CMAKE_SOURCE_DIR}}/native/benchmarks)\n"
    )


def module_fn_smoke_calls(functions: list[dict]) -> "tuple[str, int]":
    """Body of a module's C smoke test, and its scaffold-check count.

    Mirrors an object's `step_c_smoke_test`, which is a `(void)`-cast call
    with the comment "verify it runs without crashing" and no assertion — the
    scaffold proves the symbol links and the call returns, and gh-806's
    "no assertions beyond the scaffold" note nags until a human adds a real
    one.

    A function whose parameters are not all scalars is emitted as a COMMENTED
    candidate rather than a call: synthesising a buffer for an array or a
    handle for a capsule is guesswork, and a scaffold that does not compile
    is worse than one that does not measure.

    gh-1060: an ``out_type`` out-parameter gets that same treatment, and for
    the same reason. jm injects it into the prototype it writes but it never
    enters ``params``, so it was invisible to both the argument list and the
    guard above — the scalar check passed and the call was emitted one
    argument short of the signature jm had just generated. Sizing the buffer
    is not an option either: ``out_size`` is an expression over the
    parameters (``"2 * span * sps + 1"``), so with every scalar zeroed it
    evaluates to 1, or 0, or divides by zero, and a scaffold that allocates
    from that is worse than one that does not call.
    """
    from ._types import _CTYPE_META

    lines: list[str] = []
    for fn in functions:
        params = fn.get("params") or []
        zeros = [
            _CTYPE_META.get(p.get("type", ""), {}).get("zero") for p in params
        ]
        name = fn["name"]
        if fn.get("out_type"):
            lines.append(
                f"    /* TODO: {name}(...) writes into a caller-sized output"
                f" buffer jm cannot synthesise; call it here. */"
            )
        elif all(z is not None for z in zeros):
            lines.append(f"    /* {name}: verify it runs without crashing */")
            lines.append(f"    (void){name}({', '.join(zeros)});")
        else:
            lines.append(
                f"    /* TODO: {name}(...) takes a non-scalar argument jm"
                f" cannot synthesise; call it here. */"
            )
    if not lines:
        lines = ["    /* no functions declared yet */"]
    return "\n".join(lines), 0


CMAKE_LISTS_OBJECT_CORE = _load("cmake/CMakeLists_object_core.cmake")
CMAKE_LISTS_COMPONENT = _load("cmake/CMakeLists_component.cmake")
CMAKE_PC_IN = _load("cmake/package.pc.in")
CMAKE_CONFIG_IN = _load("cmake/packageConfig.cmake.in")
# ── CI workflows ───────────────────────────────────────────────────────────────
CI_GITHUB = _load("ci/github.yml")
CI_WOODPECKER = _load("ci/woodpecker.yml")
# ── Make ─────────────────────────────────────────────────────────────────────
MAKEFILE = _load("make/Makefile")
MAKEFILE_SIMPLE = _load("make/Makefile_simple")
MAKEFILE_SIMPLE_COMPONENT = _load("make/Makefile_simple_component")
# ── Doc ──────────────────────────────────────────────────────────────────────
DOXYFILE = _load("doc/Doxyfile")
DOCS_INDEX_MD = _load("doc/docs_index.md")
DOCS_API_MD = _load("doc/docs_api.md")
README_MD = _load("doc/README.md")
# ── TOML / config ────────────────────────────────────────────────────────────
ZENSICAL_TOML = _load("toml/zensical.toml")
PYPROJECT_TOML = _load("toml/pyproject.toml")
BOOTSTRAP_TOML = _load("toml/bootstrap.toml")
# ── Misc ─────────────────────────────────────────────────────────────────────
GITIGNORE = _load("misc/.gitignore")
CLANG_TIDY = _load("misc/.clang-tidy")
# ── Python ───────────────────────────────────────────────────────────────────
MODULE_INIT_PY = _load("py/module_init.py")
MODULE_INIT_PY_EMPTY = _load("py/module_init_empty.py")
SUBPACKAGE_INIT_PY = _load("py/subpackage_init.py")
PACKAGE_INIT_PY = _load("py/package_init.py")
PACKAGE_INIT_PY_MINIMAL = _load("py/package_init_minimal.py")
COMPONENT_PYI = _load("py/component.pyi")
PYTEST_TEST = _load("py/pytest_test.py")
MODULE_PYTEST_TEST = _load("py/module_pytest_test.py")
PYTEST_TEST_PURE = _load("py/pytest_test_pure.py")
MODULE_PYTEST_TEST_PURE = _load("py/module_pytest_test_pure.py")
COMPONENT_BENCH_PY = _load("py/component_bench.py")
COMPONENT_BENCH_PYTEST_BM = _load("py/component_bench_pytest_bm.py")
MODULE_BENCH_PY = _load("py/module_bench.py")
MODULE_BENCH_PYTEST_BM = _load("py/module_bench_pytest_bm.py")
# ── App scaffolds ────────────────────────────────────────────────────────────
APP_MAIN_C = _load("c/app_main.c")
APP_CONSOLE_CLI = _load("py/app_console_cli.py")
APP_PEP723 = _load("py/app_pep723.py")
APP_MAIN_FN_C = _load("c/app_main_fn.c")
APP_CONSOLE_CLI_FN = _load("py/app_console_cli_fn.py")
APP_PEP723_FN = _load("py/app_pep723_fn.py")
APP_MAIN_CMD_C = _load("c/app_main_cmd.c")
APP_CONSOLE_CLI_CMD = _load("py/app_console_cli_cmd.py")
APP_PEP723_CMD = _load("py/app_pep723_cmd.py")
# Empty tests package init — written as a blank __init__.py.
TESTS_INIT_PY = ""


#: The `<<…>>` forms that are DELIBERATE output, not an unfilled slot.
#:
#: `<<IMPLEMENT: name>>` marks where the author writes a body and
#: `<<MANUAL_STUB>>` a stub member jm does not own. Both survive into the
#: generated file on purpose. Matched by SHAPE rather than listed, since
#: `IMPLEMENT` carries the member's name with it.
DELIBERATE_MARKER = re.compile(r"<<(?:IMPLEMENT\b[^>]*|MANUAL_STUB)>>")

#: Anything still looking like a slot once those are set aside.
UNFILLED_SLOT = re.compile(r"<<([A-Za-z_][A-Za-z_0-9]*)>>")


def unfilled_slots(text: str) -> "set[str]":
    """The `<<slot>>` names *text* still carries, deliberate markers aside.

    gh-1199. NOT checked inside :func:`render` — rendering is layered, and a
    slot filled by a later pass is normal there. Measured when it was tried:
    the suite reported 1,557 failures over just two slots,
    `<<scaffold_checks>>` and `<<property_struct_fields>>`, both filled a pass
    later. So the question "did anything fill this" is only answerable at the
    moment a string becomes a FILE, which is where this is asked from.
    """
    # BOTH forms count, including the C-comment-wrapped `/*<<k>>*/`.
    #
    # gh-1199 set the wrapped form aside, for two reasons. One was that a
    # leftover there lands inside a comment — untidy rather than broken, since
    # the bare form is what reached live code (a cmake source filename). The
    # other was the price: counting it turned the suite red at 92 failures.
    #
    # Both reasons are gone. The 92 were one product shape — a `--no-state`
    # object whose header kept `/*<<property_struct_fields>>*/` — and gh-1200
    # fixed it at the source by making `render` sweep to a fixed point instead
    # of depending on ctx insertion order. Re-measured after that fix: 4
    # failures, all of them this module's own tests asserting the carve-out,
    # and no product path at all.
    #
    # "Untidy" was never a reason to ship an internal placeholder in a header
    # jm publishes; it was a reason not to fail the build over one while the
    # cause was unfixed. With the cause fixed, the carve-out only protects the
    # next regression, so it goes.
    return {
        m.group(1)
        for m in UNFILLED_SLOT.finditer(DELIBERATE_MARKER.sub("", text))
    }


#: How many times :func:`render` may re-sweep its own output. A nested slot
#: needs one extra sweep per level of nesting, and jm has one level
#: (`state_struct_decl` carries `/*<<property_struct_fields>>*/`). The bound
#: exists so a ctx value that contains its own key -- `{"x": "<<x>>"}` -- stops
#: rather than spins; it is not a limit anything real is near.
_RENDER_SWEEPS = 8


def render(template: str, ctx: dict) -> str:
    """Substitute every ``<<key>>`` in *template* from *ctx*, to a fixed point.

    Sweeps until the text stops changing rather than once, because a slot's
    VALUE may itself carry a slot: ``state_struct_decl`` is built with
    ``/*<<property_struct_fields>>*/`` nested inside it so the later properties
    pass can land its fields inside the braces.

    A single sweep made that work only when the dict happened to be ordered
    right. Substitution ran in ctx insertion order, so the nested token was
    filled if `state_struct_decl` was inserted first and left in the file if
    it was inserted second -- and those are exactly the two orders
    `make_state_ctx` uses: stateful objects insert the decl first and rendered
    correctly, `--no-state` objects insert the slot first and shipped a header
    reading `/*<<property_struct_fields>>*/` (gh-1200).

    Ordering a dict is not a property anything can see, which is why this went
    unnoticed through both branches of one function. Sweeping to a fixed point
    removes the question instead of answering it once.
    """
    result = template
    for _ in range(_RENDER_SWEEPS):
        before = result
        for k, v in ctx.items():
            if not isinstance(v, str):
                continue
            result = result.replace(f"/*<<{k}>>*/", v)
            result = result.replace(f"<<{k}>>", v)
        if result == before:
            break
    return result


def render_component_pyi(ctx: dict) -> str:
    """Render a standalone component's ``.pyi``, reflowed to 79 columns.

    gh-744. **The** way to render ``COMPONENT_PYI`` — every caller goes
    through here rather than ``render(COMPONENT_PYI, ctx)``, so the reflow
    cannot be applied on the write path and skipped on the compare path.
    That asymmetry is not hypothetical: it is exactly the bug gh-635 records
    for the C side, where ``apply`` formats the aggregator and
    ``status --check`` compares against the unformatted text, leaving a
    project permanently stale. ``tests/test_gh744_stub_width.py`` greps for
    direct ``render(COMPONENT_PYI, …)`` calls so a seventh call site added
    later cannot quietly reintroduce it.
    """
    from ._pyfmt import reflow_pyi

    return reflow_pyi(render(COMPONENT_PYI, ctx))


#: An assertion **statement** in the rendered C test. Anchored to the start of
#: the line on purpose: every emitter in `_context._state` writes the call as a
#: statement of its own, and anchoring is what keeps any prose in a comment
#: that happens to mention ``CHECK()`` out of the count. The unanchored form
#: got that wrong on its first run — and, back when the macro was stamped into
#: this template, its own ``#define`` too. gh-934 moved the definitions to
#: jm_test.h, so that second hazard is gone; the anchor stays for the first.
#:
#: ``REQUIRE`` counts as well, and must: it is an assertion like any other, and
#: ``obj_null_check`` emits one. Counting only ``CHECK`` would understate
#: JM_SCAFFOLD_CHECKS by one and make the gh-806 note claim an author had
#: written a test when they had not.
_CHECK_CALL = re.compile(r"^\s*(?:CHECK|REQUIRE)\s*\(", re.M)


def render_component_test_c(ctx: dict) -> str:
    """Render the C test scaffold and stamp how many checks it contains.

    gh-806. **The** way to render ``COMPONENT_TEST_C``. The count cannot come
    from the context dict: the checks are contributed by four independent
    slots (``obj_null_check``, ``getter_setter_test_c``, ``step_c_smoke_test``,
    ``reset_test_c``), each built by a different ``make_*_ctx``, and any
    arithmetic over them is a second implementation that drifts the first time
    a fifth slot is added. Counting the rendered text is a measurement of the
    file being written, so it cannot disagree with it.

    A call site that skipped this would leave ``JM_SCAFFOLD_CHECKS`` defined
    as nothing and the generated C would not compile — deliberately loud,
    since the alternative is a silent banner that never fires.
    """
    text = render(COMPONENT_TEST_C, ctx)
    checks = len(_CHECK_CALL.findall(text))
    return text.replace("/*<<scaffold_checks>>*/", str(checks))


# ── Multi-object module support ──────────────────────────────────────────────
#
# A "module" is a single .so that hosts multiple Python types ("objects").
# COMPONENT_TYPE_SECTION is the per-object block (struct + methods +
# PyTypeObject) without file headers or PyMODINIT_FUNC.
# MODULE_EXT_C is the full file: header + <<type_sections>> + PyMODINIT_FUNC.
# render_module_ext_c() assembles the two from a list of component contexts.
#
# <<module>> must be in the ctx passed to COMPONENT_TYPE_SECTION; it equals
# the component name for standalone components, or the module name otherwise.

COMPONENT_TYPE_SECTION = """\
/* ======================================================== */
/* <<Component>>Object — wraps <<component>>_state_t *       */
/* ======================================================== */

#include "<<component>>/<<component>>_core.h"

typedef struct {
    PyObject_HEAD
    <<component>>_state_t *handle;
<<extra_buf_fields>><<capsule_owner_fields>>} <<Component>>Object;

static void
<<ComponentW>>_dealloc(<<Component>>Object *self)
{
<<destroy_dealloc_call>><<extra_buf_free>><<capsule_owner_free>>    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
<<ComponentW>>_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    <<Component>>Object *self = (<<Component>>Object *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
<<ComponentW>>_init(<<Component>>Object *self, PyObject *args, PyObject *kwds)
{
<<init_parse_block>><<array_args_parse_block>><<create_line>><<array_args_decref>><<create_fail_block>><<extra_buf_alloc>><<init_warn_block>>    return 0;
}

<<builtin_reset_c>>

<<step_ext_fn>>

<<steps_ext_fn>>

<<enum_tables>>
<<getter_setter_methods_c>>
<<extra_methods_c>>
<<getset_def>>
static PyObject *
<<ComponentW>>_destroy(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
<<destroy_method_body>>}

static PyObject *
<<ComponentW>>_enter(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
<<ComponentW>>_exit(<<Component>>Object *self, PyObject *args)
{
    (void)args;
<<destroy_exit_body>>}

<<stream_iter_block>>static PyMethodDef <<ComponentW>>_methods[] = {
<<builtin_reset_pmd>><<step_pymethoddef_entry>><<steps_def_entry>>
<<getter_setter_pymethoddef>><<extra_methods_pymethoddef>><<stream_def_entry>><<destroy_pymethoddef>>    {"__enter__", (PyCFunction)<<ComponentW>>_enter,   METH_NOARGS,
     <<cm_enter_doc>>},
    {"__exit__",  (PyCFunction)<<ComponentW>>_exit,    METH_VARARGS,
     <<cm_exit_doc>>},
    {NULL}
};

static PyTypeObject <<ComponentW>>Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "<<module_tp>>.<<Component>>",
    .tp_basicsize = sizeof(<<Component>>Object),
    .tp_dealloc   = (destructor)<<ComponentW>>_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = <<tp_doc>>,
    .tp_methods   = <<ComponentW>>_methods,<<tp_getset_decl>><<stream_tp_iter>><<stream_tp_async>>
    .tp_new       = <<ComponentW>>_new,
    .tp_init      = (initproc)<<ComponentW>>_init,
};
"""

MODULE_EXT_C_HEADER = """\
/*
 * <<module>>_ext.c — Python extension module <<module>>
 *
 * Objects: <<object_list>>
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>
<<module_extra_includes>>
<<module_core_include>>"""

MODULE_EXT_C_FOOTER = """\

/* ======================================================== */
/* Module                                                    */
/* ======================================================== */

<<module_methods_def>>static PyModuleDef <<module>>_moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "<<module_leaf>>",
    .m_doc     = <<module_doc_c>>,
    .m_size    = -1,
    .m_methods = <<module_m_methods>>,
};

PyMODINIT_FUNC
PyInit_<<module_leaf>>(void)
{
    import_array();
<<type_ready_checks>>
    PyObject *m = PyModule_Create(&<<module>>_moduledef);
    if (!m) return NULL;
<<add_object_calls>><<procglobal>>
    return m;
}
"""


def _fn_c_params(
    params: list[tuple],
) -> tuple[list[str], str]:
    """Return (c_parts, suppress_lines) for a list of param tuples.

    Each param is either ``(name, type)`` or ``(name, type, out)`` where the
    optional third element is a bool. Array params ("type[]") expand to
    ``(const elem_t *name, size_t name_len)`` by default; when ``out=True``
    the ``const`` is dropped so the function can write through the pointer
    (gh-72).

    Returns the parts as a **list**, deliberately (gh-1072). It used to
    return them already joined, with `void` substituted for the empty case —
    and two callers then appended the parameters jm generates, producing
    ``f(void, row_t *result, size_t max_results)``, which is not C. A list
    has nothing to append to after the placeholder decision, because the
    decision has not been made yet: `c_param_list` makes it, last, from the
    complete list.
    """
    c_parts: list[str] = []
    suppress_parts: list[str] = []
    for p in params:
        n, t = p[0], p[1]
        is_out = bool(p[2]) if len(p) > 2 else False
        if t == "path":
            # gh-353: a path arg crosses as a borrowed PyBytes (the binding
            # coerces with PyUnicode_FSConverter); the C function receives a
            # plain `const char *` it copies during the call (gh-219 UAF).
            c_parts.append(f"{_coerce.PATH_C_TYPE}{n}")
            suppress_parts.append(f"(void){n};")
        elif is_array_param_type(t):
            elem_disp = array_elem_ctype(t)
            qual = "" if is_out else "const "
            c_parts.append(f"{qual}{elem_disp} *{n}")
            c_parts.append(f"size_t {n}_len")
            suppress_parts.append(f"(void){n};")
            suppress_parts.append(f"(void){n}_len;")
        else:
            c_parts.append(f"{t} {n}")
            suppress_parts.append(f"(void){n};")
    suppress = "    " + " ".join(suppress_parts) if suppress_parts else ""
    return c_parts, suppress


def _scalar_c_param(p: tuple) -> str:
    """C declaration for one non-array param (out_type / result_fields paths).

    gh-353: a ``path`` arg crosses as a borrowed ``const char *`` (the binding
    coerces it with ``PyUnicode_FSConverter``); an enum arg is already typed
    ``int`` in the manifest, so it needs no special case here.
    """
    n, t = p[0], p[1]
    if t == "path":
        return f"{_coerce.PATH_C_TYPE}{n}"
    return f"{t} {n}"


def fn_c_decl(
    fn_name: str,
    params: list[tuple],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    variable_output: bool = False,
) -> str:
    """One-line C declaration: 'return_type fn_name(c_params);'

    out_type: if set, inserts '{out_type} *out' after array params and
    forces the return type to void (output is returned via the pointer).

    variable_output: when set (a #318 self-sizing output), '{out_type} *out'
    is appended LAST instead of after the array params, so the C signature
    matches the binding's call (which appends the self-allocated buffer last).

    result_fields: if set, forces return type to size_t (count) and
    appends '{return_type} *result' (plus 'size_t max_results' when
    max_results_param is empty, meaning the cap is not already a named
    param).
    """
    result_fields = result_fields or []
    if result_fields:
        rt_disp = return_type
        c_parts, _ = _fn_c_params(params)
        # gh-1072: the result buffer joins the list BEFORE it is joined into
        # text. Appending it to an already-rendered list is what produced
        # `f(void, row_t *result, size_t max_results)`.
        c_parts = list(c_parts) + [f"{rt_disp} *result"]
        if not max_results_param:
            c_parts.append("size_t max_results")
        return f"size_t {fn_name}({c_param_list(c_parts)});\n"
    if out_type:
        arr_p = [p for p in params if is_array_param_type(p[1])]
        scl_p = [p for p in params if not is_array_param_type(p[1])]
        # gh-128: out_type may carry a [param_name] size annotation
        # (e.g. "float64[M]").  Resolve to the underlying C type so the
        # declaration emits "double *out", not the invalid "float64[M] *out".
        # gh-1180: `str` is a PYTHON-side shape, not a C type — the C the
        # author writes takes a `char *`, and it is jm that turns the written
        # bytes into a `str`.
        if out_type == "str":
            _out_ctype, out_disp = "char", "char"
        else:
            _out_ctype, _ = parse_out_type(out_type)
            out_disp = _out_ctype
        c_parts: list[str] = []
        for p in arr_p:
            n, t = p[0], p[1]
            qual = "" if (len(p) > 2 and p[2]) else "const "
            c_parts.append(f"{qual}{array_elem_ctype(t)} *{n}")
            c_parts.append(f"size_t {n}_len")
        if not variable_output:
            c_parts.append(f"{out_disp} *out")
        for p in scl_p:
            c_parts.append(_scalar_c_param(p))
        if variable_output:
            c_parts.append(f"{out_disp} *out")
        _decl_ret = _out_fn_return_disp(return_type, variable_output)
        return f"{_decl_ret} {fn_name}({c_param_list(c_parts)});\n"
    ret_disp = return_type
    c_parts, _ = _fn_c_params(params)
    return f"{ret_disp} {fn_name}({c_param_list(c_parts)});\n"


#: The C return type an `out_type` function is DECLARED with.
#:
#: gh-1180. Both emitters hardcoded `void`, while the binding for a
#: `variable_output` function reads an integer return as the written length
#: (`size_t _n = (size_t)fn(...)`). A project declaring `return_type =
#: "size_t"` alongside `out_type` therefore got a header saying `void` and a
#: binding assigning from it — generated C that does not compile. Reproduced
#: on main before this change with a plain `out_type = "double"`, so it is not
#: something the `str` shape introduced; that shape only made it unavoidable,
#: because a string output has no length without one.
#:
#: `void` stays the answer for every function that declares no return, which
#: is the default and the overwhelming majority.
def _out_fn_return_disp(return_type: str, variable_output: bool) -> str:
    if not variable_output:
        return "void"
    meta = _CTYPE_META.get(return_type)
    if meta and meta.get("kind") == "int":
        return return_type
    return "void"


def fn_c_inline_stub(
    fn_name: str,
    params: list[tuple],
    return_type: str,
) -> str:
    """C body stub for embedding in ``_core.h`` as ``static inline``.

    Emits the full ``static inline`` definition so callers see the body at
    compile time.  No entry is written to ``_core.c``.  Intended for pure,
    stateless functions that benefit from inlining at every call site.

    Parameters
    ----------
    fn_name : str
        C function name (without module prefix).
    params : list of (name, type)
        Scalar parameters only — array params and out_type are not supported
        for inline functions.
    return_type : str
        C return type string (e.g. ``"int16_t"``, ``"float"``).

    Returns
    -------
    str
        ``static inline`` C source ready to splice into ``_core.h``.

    Examples
    --------
    >>> print(fn_c_inline_stub("clip_f32", [("x", "float"), ("lo", "float")], "float"))
    /* <<IMPLEMENT: clip_f32>> */
    static inline float
    clip_f32(float x, float lo)
    {
        (void)x; (void)lo;
        return (float)0.0f; /* placeholder */
    }
    <BLANKLINE>
    """
    ret_disp = return_type
    ret_meta = _CTYPE_META.get(return_type)
    c_parts, suppress = _fn_c_params(params)
    c_ret_line = (
        f"    return ({ret_disp}){ret_meta['zero']}; /* placeholder */"
        if ret_meta
        else ""
    )
    return (
        f"/* <<IMPLEMENT: {fn_name}>> */\n"
        f"static inline {ret_disp}\n"
        f"{fn_name}({c_param_list(c_parts)})\n"
        f"{{\n"
        + (suppress + "\n" if suppress else "")
        + (c_ret_line + "\n" if c_ret_line else "")
        + "}\n"
    )


def fn_c_stub(
    fn_name: str,
    params: list[tuple],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    variable_output: bool = False,
) -> str:
    """C implementation stub for <module>_core.c (public, no _impl suffix).

    out_type, variable_output, and result_fields extend the signature in the
    same way as fn_c_decl; see that function's docstring for the semantics.
    """
    result_fields = result_fields or []
    if result_fields:
        rt_disp = return_type
        c_parts, suppress = _fn_c_params(params)
        # gh-1072: same order as `fn_c_decl` above, and it has to be — the
        # stub's signature must match the prototype character for character.
        c_parts = list(c_parts) + [f"{rt_disp} *result"]
        if not max_results_param:
            c_parts.append("size_t max_results")
        suppress_extra = " (void)result;"
        if not max_results_param:
            suppress_extra += " (void)max_results;"
        suppress_line = (
            (suppress + suppress_extra)
            if suppress
            else ("    " + suppress_extra.strip())
        )
        return (
            f"/* <<IMPLEMENT: {fn_name}>> */\n"
            f"size_t\n"
            f"{fn_name}({c_param_list(c_parts)})\n"
            f"{{\n"
            + suppress_line
            + "\n"
            + "    return 0; /* placeholder */\n"
            + "}\n"
        )
    if out_type:
        arr_p = [p for p in params if is_array_param_type(p[1])]
        scl_p = [p for p in params if not is_array_param_type(p[1])]
        # gh-128: resolve numpy dtype + size annotation → C type.
        # gh-1180: see fn_c_decl — `str` names the Python shape, and the C
        # the author implements takes a `char *`.
        if out_type == "str":
            _out_ctype, out_disp = "char", "char"
        else:
            _out_ctype, _ = parse_out_type(out_type)
            out_disp = _out_ctype
        c_parts: list[str] = []
        suppress_parts: list[str] = []
        for p in arr_p:
            n, t = p[0], p[1]
            qual = "" if (len(p) > 2 and p[2]) else "const "
            c_parts.append(f"{qual}{array_elem_ctype(t)} *{n}")
            c_parts.append(f"size_t {n}_len")
            suppress_parts += [f"(void){n};", f"(void){n}_len;"]
        if not variable_output:
            c_parts.append(f"{out_disp} *out")
            suppress_parts.append("(void)out;")
        for p in scl_p:
            n = p[0]
            c_parts.append(_scalar_c_param(p))
            suppress_parts.append(f"(void){n};")
        if variable_output:
            c_parts.append(f"{out_disp} *out")
            suppress_parts.append("(void)out;")
        suppress = "    " + " ".join(suppress_parts) if suppress_parts else ""
        _stub_ret = _out_fn_return_disp(return_type, variable_output)
        _stub_zero = _CTYPE_META.get(return_type, {}).get("zero")
        _stub_ret_line = (
            f"    return ({_stub_ret}){_stub_zero}; /* placeholder */"
            if _stub_ret != "void"
            else ""
        )
        return (
            f"/* <<IMPLEMENT: {fn_name}>> */\n"
            f"{_stub_ret}\n"
            f"{fn_name}({c_param_list(c_parts)})\n"
            f"{{\n"
            + (suppress + "\n" if suppress else "")
            + (_stub_ret_line + "\n" if _stub_ret_line else "")
            + "}\n"
        )
    ret_disp = return_type
    ret_meta = _CTYPE_META.get(return_type)
    c_parts, suppress = _fn_c_params(params)
    c_ret_line = (
        f"    return ({ret_disp}){ret_meta['zero']}; /* placeholder */"
        if ret_meta
        else ""
    )
    return (
        f"/* <<IMPLEMENT: {fn_name}>> */\n"
        f"{ret_disp}\n"
        f"{fn_name}({c_param_list(c_parts)})\n"
        f"{{\n"
        + (suppress + "\n" if suppress else "")
        + (c_ret_line + "\n" if c_ret_line else "")
        + "}\n"
    )


def _build_params_parse(
    params: list[dict],
    enums: "dict[str, list[str]] | None" = None,
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      Array types end with '[]', e.g. "float _Complex[]"; their element type
      must be in _CTYPE_TO_NPY.  Array params expand to two C args:
      (const elem_t *name, size_t name_len).

    Returns (parse_block, call_args_c, cleanup):
      parse_block  — indented C code: a kwlist, declarations,
                     PyArg_ParseTupleAndKeywords (positional-or-keyword), array
                     conversion and error-exit paths with partial cleanup. The
                     caller's wrapper must take a ``PyObject *kwds`` parameter.
      call_args_c  — comma-sep C variables/expressions for the downstream call
      cleanup      — Py_DECREF lines for all acquired numpy arrays (empty string
                     when no array params); caller must emit before every return
    """
    decl_lines: list[str] = []  # before PyArg_ParseTuple
    addr_exprs: list[str] = []  # &name args for PyArg_ParseTuple
    fmt_chars: list[str] = []  # format characters
    conv_lines: list[str] = []  # after PyArg_ParseTuple (scalars needing to_c)
    arr_acq: list[str] = []  # array acquisition lines (after ParseTuple)
    call_args: list[str] = []  # final C args to pass
    arr_names: list[str] = []  # arr variable names for Py_DECREF cleanup
    # gh-353: borrowed path PyBytes (from PyUnicode_FSConverter). DECREF'd only
    # AFTER the C call (the C side copies the string during the call — gh-219
    # UAF), and on every pre-call error path before returning NULL.
    path_names: list[str] = []

    for p in params:
        pname = p["name"]
        ptype = p["type"]

        if ptype == "path":
            # gh-353: the shared file-handler pattern (_coerce) — a
            # str | os.PathLike coerces with O& + PyUnicode_FSConverter into a
            # borrowed PyBytes; pass PyBytes_AS_STRING to the const char * arg.
            # Same primitives the handle generator uses for a `path` create-arg.
            decl_lines.append("    " + _coerce.path_decl(pname))
            fmt_chars.append(_coerce.path_fmt())
            addr_exprs.append(_coerce.path_addr(pname))
            path_names.append(pname)
            call_args.append(_coerce.path_call_expr(pname))
        elif p.get("enum"):
            # gh-353 (mirrors _handle's enum-validate in render_tp_init): parse
            # the choice string with `s`, validate to its SSOT int via
            # _enum_index; a `< 0` raises ValueError after cleaning up any
            # arrays / path objects acquired so far, then pass the validated int.
            ename = p["enum"]
            fmt_chars.append("s")
            # gh-240/gh-353: a defaulted enum is optional — its C local seeds to
            # the default choice string so an omitted arg validates to that
            # choice's index. A required enum seeds to "" (an invalid choice,
            # but PyArg fills it before _enum_index runs).
            _edflt = p.get("default") or ""
            decl_lines.append(f'    const char *{pname} = "{_edflt}";')
            addr_exprs.append(f"&{pname}")
            prior = "".join(f" Py_DECREF({a});" for a in arr_names) + "".join(
                f" {_coerce.path_release(n)}" for n in path_names
            )
            # gh-1026: one emitter, so the refusal a caller meets does not
            # depend on which surface the enum was declared on. This site is
            # the one the issue names: it said only `invalid sample_type
            # '%s'` while a method parameter for the SAME enum in the SAME
            # manifest named the choices, because gh-1021 fixed one copy.
            from . import _enumc

            conv_lines.append(
                _enumc.validate_c(pname, ename, enums, cleanup=prior)
            )
            call_args.append(f"_arg_{pname}")
        elif is_array_param_type(ptype):
            elem_ct = array_elem_ctype(ptype)
            npy_enum = _CTYPE_TO_NPY[elem_ct]
            elem_disp = elem_ct
            obj_var = f"{pname}_obj"
            arr_var = f"{pname}_arr"

            decl_lines.append(f"    PyObject *{obj_var} = NULL;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")

            # Build error path: decref all arrays + path objects (gh-353)
            # acquired so far.
            prior_decrefs = "".join(
                f" Py_DECREF({a});" for a in arr_names
            ) + "".join(f" {_coerce.path_release(n)}" for n in path_names)
            is_out = bool(p.get("out"))
            npy_flags = (
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE"
                if is_out
                else "NPY_ARRAY_C_CONTIGUOUS"
            )
            const_qual = "" if is_out else "const "
            # gh-581: an `out` param names the caller's own buffer, so require
            # the exact dtype before FROM_OTF gets a chance to cast it into a
            # temp the callee fills and we then discard. Mirrors the same guard
            # in _context/_parse.py — the two builders must not drift.
            if is_out:
                arr_acq.append(
                    _coerce.out_buffer_guard(
                        obj_var,
                        npy_enum,
                        label=pname,
                        decrefs=prior_decrefs.strip(),
                    ).rstrip("\n")
                )
            arr_acq.append(
                f"    PyArrayObject *{arr_var} = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        {obj_var}, {npy_enum}, {npy_flags});\n"
                f"    if (!{arr_var}) {{{prior_decrefs} return NULL; }}"
            )
            # gh-805 §C: the module-function copy of _context/_parse.py's
            # branch. Both call the one emitter rather than each spelling the
            # length out — this pair is already the documented peer shape, and
            # an interleave factor applied to only one of them is exactly the
            # drift that costs a buffer overrun in whichever face was missed.
            _rank = p.get("rank")
            if _rank:
                arr_acq.append(
                    _coerce.array_rank_guard(
                        pname, arr_var, int(_rank), prior_decrefs.strip()
                    ).rstrip("\n")
                )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                + _coerce.array_len_c(
                    pname, arr_var, int(p.get("elements_per_sample", 1) or 1)
                )
            )
            arr_names.append(arr_var)
            call_args.extend([pname, f"{pname}_len"])
        elif p.get("capsule"):
            # gh-432: a foreign C pointer crossing as a named PyCapsule —
            # mirrors _context/_parse.py's branch (this builder is the
            # module-function copy). None -> NULL; a non-capsule object is
            # unwrapped via its `_capsule` attribute. Unwrap errors happen
            # in conv_lines, before any array/path acquisition, so the
            # early returns need no cleanup.
            cname = p["capsule"]
            disp = ptype
            if not disp.endswith("*"):
                disp += " "
            obj_var = f"{pname}_obj"
            decl_lines.append(f"    PyObject *{obj_var} = Py_None;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")
            # Error paths release any O&-converted path objects (mirrors the
            # enum branch): ParseTuple has already run when conv executes.
            _cap_prior = "".join(
                f" {_coerce.path_release(n)}" for n in path_names
            )
            conv_lines.append(
                f"    {disp}{pname} = NULL;\n"
                f"    if ({obj_var} != Py_None) {{\n"
                f"        PyObject *{pname}_cap = {obj_var};\n"
                f"        Py_INCREF({pname}_cap);\n"
                f"        if (!PyCapsule_CheckExact({pname}_cap)) {{\n"
                f"            Py_DECREF({pname}_cap);\n"
                f"            {pname}_cap = PyObject_GetAttrString(\n"
                f'                {obj_var}, "_capsule");\n'
                f"            if (!{pname}_cap) {{{_cap_prior} return NULL; }}\n"
                f"        }}\n"
                f"        {pname} = ({disp})PyCapsule_GetPointer(\n"
                f'            {pname}_cap, "{cname}");\n'
                f"        Py_DECREF({pname}_cap);\n"
                f"        if (!{pname}) {{{_cap_prior} return NULL; }}\n"
                f"    }}"
            )
            call_args.append(pname)
        else:
            meta = _CTYPE_META[ptype]
            disp = ptype
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                # gh-432 drive-by: seed the raw local with the gh-240
                # default (not parse_zero) so an omitted defaulted arg
                # yields the default — previously only the non-parse_type
                # branch honoured `default` (mirrors _context/_parse.py).
                _raw_init = p.get("default") or meta["parse_zero"]
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {_raw_init};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(
                    f"    {disp} {pname} = {meta['to_c'](pname)};"
                )
            else:
                # gh-240: a scalar with a `default` is optional — its C local is
                # initialised to the default literal so an omitted arg yields it
                # (PyArg leaves it untouched). Required scalars init to zero.
                init = p.get("default") or meta["zero"]
                decl_lines.append(f"    {disp} {pname} = {init};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    # gh-240: split required vs optional. A param with a `default` is optional;
    # the `|` in the PyArg format goes before the first optional param. Optional
    # params must follow all required ones (the PyArg `|` rule == Python's
    # "no required parameter after a defaulted one"); validate and error clearly.
    fmt_str = _join_fmt_with_optional(fmt_chars, params)
    addr_str = ", ".join(addr_exprs)
    # gh-238: module functions are positional-OR-keyword. Each param name is a
    # kwarg (an array param's kwarg is its object), and the kwlist order matches
    # the fmt/addr order. Keyword *capability* is ~free when callers still pass
    # positionally; the keyword-match cost is paid only when keywords are used —
    # the right trade for the (often multi-param) function call site, which is
    # rarely the innermost loop. The per-sample hot path (step/steps) stays
    # positional-only.
    kwnames = "".join(f'"{p["name"]}", ' for p in params)
    # gh-353: a parse failure may have already converted some O& path args (and
    # FSConverter sets the target on success), so XDECREF every path object on
    # the parse-fail path before returning NULL. The multi-statement cleanup is
    # braced (the no-path form keeps the bare `return NULL;` — zero churn).
    parse_fail = (
        "        return NULL;"
        if not path_names
        else "    {\n"
        + "".join(f"        {_coerce.path_release(n)}\n" for n in path_names)
        + "        return NULL;\n    }"
    )
    lines = (
        [f"    static char *_kwlist[] = {{{kwnames}NULL}};"]
        + decl_lines
        + [
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt_str}",',
            f"            _kwlist, {addr_str}))",
            parse_fail,
        ]
        + conv_lines
        + arr_acq
    )
    # The final cleanup (emitted by callers AFTER the C call): array DECREFs +
    # path XDECREFs (gh-353 — the C side has copied the string by now).
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names) + "".join(
        f"    {_coerce.path_release(n)}\n" for n in path_names
    )
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _py_wrapper_for_function(
    fn_name: str,
    params: list[dict],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    max_results: int = 64,
    variable_output: bool = False,
    out_size: str = "",
    check_return: bool = False,
    # gh-1026: the `[[enum]]` registry, so a bad choice can be refused by
    # NAMING the choices — the wording a method parameter for the same enum
    # has had since gh-1021, and this face had not.
    enums: "dict[str, list[str]] | None" = None,
) -> str:
    """Generate a _bind_<fn_name> Python wrapper for a module-level C function.

    The C function is assumed to be declared in <module>_core.h and named
    exactly fn_name (public, no prefix).

    out_type: if set, allocates a 1-D ndarray of this type (length = first
    array param's length) and passes it after the array args, before scalars.

    result_fields: if set, calls C with a stack-allocated array of structs,
    builds and returns list[tuple] from the fields. max_results_param names
    an existing param that already carries the capacity (already embedded in
    call_args, so it is not passed again); when empty (the common case),
    fn_c_decl appended a bare trailing `size_t max_results` param instead, so
    the wrapper passes the literal `max_results` value for it.
    """
    result_fields = result_fields or []
    ret_meta = _CTYPE_META.get(return_type)

    if params:
        parse_block, call_args, cleanup = _build_params_parse(params, enums)
        # Positional-or-keyword (gh-238): the binding takes kwds and parses with
        # PyArg_ParseTupleAndKeywords. A no-param function stays METH_NOARGS.
        py_args = "PyObject *args, PyObject *kwds"
    else:
        parse_block = ""
        call_args = ""
        cleanup = ""
        py_args = "PyObject *Py_UNUSED(args)"

    if result_fields:
        # Build list-of-tuples from struct array. gh-598: fields convert
        # through _CTYPE_META's to_py (the shared record_tuple_build), not a
        # second format-char table that fell back to a cast-less "i".
        _bv = record_tuple_build(result_fields, "_results[_i]")
        _rt_disp = return_type
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        # max_results_param names an existing param already in call_args (the
        # C signature has no extra trailing param for it); otherwise fn_c_decl
        # appended a bare `size_t max_results`, so the call passes the literal.
        if max_results_param:
            _max_expr = f"(size_t){max_results_param}"
            _call = f"{fn_name}({call_args}, _results)"
        else:
            _max_expr = str(max_results)
            _call = (
                f"{fn_name}({call_args}, _results, _max)"
                if call_args
                else f"{fn_name}(_results, _max)"
            )
        ret_line = (
            f"    size_t _max = {_max_expr};\n"
            f"    {_rt_disp} *_results ="
            f" ({_rt_disp} *)malloc(_max * sizeof({_rt_disp}));\n"
            f"    if (!_results) {{{_cleanup_inline} return PyErr_NoMemory(); }}\n"
            f"    size_t _n = {_call};\n"
            f"{cleanup}"
            f"    PyObject *_lst = PyList_New((Py_ssize_t)_n);\n"
            f"    if (!_lst) {{ free(_results); return NULL; }}\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"        PyObject *_tup = Py_BuildValue({_bv});\n"
            f"        if (!_tup) {{ free(_results); Py_DECREF(_lst); return NULL; }}\n"
            f"        PyList_SET_ITEM(_lst, (Py_ssize_t)_i, _tup);\n"
            f"    }}\n"
            f"    free(_results);\n"
            f"    return _lst;"
        )
    elif variable_output and out_type == "str":
        # gh-1180: the text sibling of the branch below. A module function
        # could TAKE a string (`const char *`) and had no way to give one
        # back, so a pair of inverse conversions read asymmetrically — the
        # parse direction natural, the render direction handing back a uint8
        # buffer the caller decodes itself.
        #
        # Same allocate-call-trim shape as the ndarray case, and deliberately
        # so: jm sizes the buffer from `out_size`, the C function writes into
        # `char *out` and returns the used length, and the wrapper builds the
        # `str`. The caller allocates nothing, which is what makes this the
        # issue's option 2 rather than its option 1 — a `char[]` out-param
        # would have made the caller pass a buffer it cannot read as text.
        #
        # `char` is deliberately NOT added to `_CTYPE_META`: that would make
        # it a legal scalar type everywhere and silently retire the hint that
        # steers `char` to `int8_t` for its platform-dependent signedness.
        # This is one output shape, not a new type.
        if out_size:
            len_expr = out_size
        else:
            first_arr = next(
                (p["name"] for p in params if is_array_param_type(p["type"])),
                None,
            )
            len_expr = f"{first_arr}_len" if first_arr else "1"
        _call_with_out = f"{call_args}, _buf" if call_args else "_buf"
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        # A `void` function cannot say how much it wrote, and NUL-hunting a
        # buffer the callee may not have terminated is a read past the end
        # waiting to happen. Refuse rather than guess.
        if not (ret_meta and ret_meta.get("kind") == "int"):
            raise ValueError(
                f"function '{fn_name}' declares out_type = \"str\" but "
                f"returns '{return_type}'.\n"
                "  A string output needs its LENGTH back, so the function "
                "must return a size_t\n"
                "  (or another integer) giving the number of characters "
                "written. Without it\n"
                "  jm would have to hunt for a NUL the callee may never have "
                "written."
            )
        ret_line = (
            f"    size_t _cap = (size_t)({len_expr});\n"
            f"    char *_buf = (char *)malloc(_cap + 1);\n"
            f"    if (!_buf) {{{_cleanup_inline} return PyErr_NoMemory(); }}\n"
            f"    size_t _n = (size_t){fn_name}({_call_with_out});\n"
            f"{cleanup}"
            f"    if (_n > _cap) _n = _cap;\n"
            f"    PyObject *_s = PyUnicode_FromStringAndSize(_buf, "
            f"(Py_ssize_t)_n);\n"
            f"    free(_buf);\n"
            f"    return _s;"
        )
    elif variable_output and out_type:
        # #318: stateless self-sizing output — the function allocates its own
        # 1-D output (no caller buffer, no cached instance buffer). Its length
        # is `out_size` (a verbatim-C expr over the args + array `<name>_len`s,
        # e.g. "wfm_rrc_ntaps(sps, span)" or "x_len * factor"), or the first
        # array param's length. `out` is appended LAST to the call. If the fn
        # reports a size_t count, trim to it (the runtime-sized shape); a void
        # fn returns the full allocation. Keeps a `rrc_taps(...) -> ndarray`
        # zero-Python (the helper used to allocate internally in hand-Python).
        _base_ctype, _ = parse_out_type(out_type)
        out_npy = _CTYPE_TO_NPY[_base_ctype]
        out_disp = _base_ctype
        if out_size:
            len_expr = out_size
        else:
            first_arr = next(
                (p["name"] for p in params if is_array_param_type(p["type"])),
                None,
            )
            len_expr = f"{first_arr}_len" if first_arr else "1"
        _out_ptr = f"({out_disp} *)PyArray_DATA((PyArrayObject *)_out)"
        _call_with_out = f"{call_args}, {_out_ptr}" if call_args else _out_ptr
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        _trim = bool(ret_meta) and ret_meta.get("kind") == "int"
        _alloc = (
            f"    npy_intp _dim = (npy_intp)({len_expr});\n"
            f"    PyObject *_out ="
            f" PyArray_EMPTY(1, &_dim, {out_npy}, 0);\n"
            f"    if (!_out) {{{_cleanup_inline} return NULL; }}\n"
        )
        if _trim:
            ret_line = (
                _alloc
                + f"    size_t _n = (size_t){fn_name}({_call_with_out});\n"
                + cleanup
                + "    PyArray_DIMS((PyArrayObject *)_out)[0] ="
                " (npy_intp)_n;\n"
                "    return _out;"
            )
        else:
            ret_line = (
                _alloc
                + f"    {fn_name}({_call_with_out});\n"
                + cleanup
                + "    return _out;"
            )
    elif out_type:
        # Allocate output array, insert after array args, before scalars.
        # out_type may carry a [param_name] suffix naming the scalar that
        # holds the output length (e.g. "float64[M]").
        _base_ctype, _scalar_len_param = parse_out_type(out_type)
        out_npy = _CTYPE_TO_NPY[_base_ctype]
        out_disp = _base_ctype
        if _scalar_len_param:
            len_expr = _scalar_len_param
        else:
            first_arr = next(
                (p["name"] for p in params if is_array_param_type(p["type"])),
                None,
            )
            len_expr = f"{first_arr}_len" if first_arr else "1"
        # call_args is: arr_ptr, arr_len, [more_arr_ptr, arr_len,] scalar1, ...
        # Insert `out` after the last (ptr, len) pair.
        _arr_count = sum(1 for p in params if is_array_param_type(p["type"]))
        _arr_args = call_args.split(", ")
        # Each array expands to 2 args; scalars are single.
        _insert_idx = _arr_count * 2
        _parts_before = ", ".join(_arr_args[:_insert_idx])
        _parts_after = ", ".join(_arr_args[_insert_idx:])
        _sep_before = ", " if _parts_before else ""
        _sep_after = ", " if _parts_after else ""
        _call_with_out = (
            f"{_parts_before}{_sep_before}"
            f"({out_disp} *)PyArray_DATA"
            f"((PyArrayObject *)_out){_sep_after}{_parts_after}"
        )
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        ret_line = (
            f"    npy_intp _dim = (npy_intp){len_expr};\n"
            f"    PyObject *_out ="
            f" PyArray_EMPTY(1, &_dim, {out_npy}, 0);\n"
            f"    if (!_out) {{{_cleanup_inline} return NULL; }}\n"
            f"    {fn_name}({_call_with_out});\n"
            f"{cleanup}"
            f"    return _out;"
        )
    elif check_return:
        # gh-363: the C function reports an int status (0 = success); raise on a
        # non-zero result instead of returning it, so the Python surface is a
        # "succeeds or raises" None — the module-function analog of the handle
        # generator's `close_returns`. Covers both a failed primitive and a
        # NULL/neg sentinel the C fn returns on an open/alloc failure. Capture
        # the rc first, run any array/path cleanup, then check + raise.
        _rt_disp = return_type
        ret_line = (
            f"    {_rt_disp} _rc = {fn_name}({call_args});\n"
            f"{cleanup}"
            f"    if (_rc != 0) {{\n"
            f"        PyErr_Format(PyExc_RuntimeError,\n"
            f'            "{fn_name} failed (rc=%d)", (int)_rc);\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    Py_RETURN_NONE;"
        )
    elif ret_meta:
        # gh-353: a path arg borrows a PyBytes that the C call copies, so the
        # path XDECREF in `cleanup` must run AFTER the call, not before. Capture
        # the C result into a temp, clean up, then convert + return. (Without a
        # path arg `cleanup` is array-only and order is immaterial — the legacy
        # one-line form is kept so enum-free output is byte-identical.)
        _has_path = any(p["type"] == "path" for p in params)
        if _has_path and cleanup:
            _rt_disp = return_type
            ret_line = (
                f"    {_rt_disp} _r = {fn_name}({call_args});\n"
                f"{cleanup}"
                f"    return {ret_meta['to_py']('_r')};"
            )
        else:
            ret_expr = ret_meta["to_py"](f"{fn_name}({call_args})")
            ret_line = f"{cleanup}    return {ret_expr};"
    else:
        call_line = (
            f"    {fn_name}({call_args});" if params else f"    {fn_name}();"
        )
        ret_line = call_line + f"\n{cleanup}    Py_RETURN_NONE;"

    return (
        f"static PyObject *\n"
        f"_bind_{fn_name}(PyObject *self, {py_args})\n"
        f"{{\n"
        f"    (void)self;\n" + parse_block + f"{ret_line}\n" + "}\n"
    )


def _functions_enums_used(
    functions: list[dict],
) -> list[str]:
    """Ordered, de-duplicated enum names referenced by any function param.

    gh-353: a module function's enum arg carries ``{enum: "<name>"}`` (mirrors
    the handle convention). The ``_ext.c`` emits the per-enum tables + the
    shared ``_enum_index`` helper only for the enums actually referenced, so an
    enum-free module renders byte-identical output (no helper, no tables).
    """
    seen: list[str] = []
    for fn in functions:
        for p in fn.get("params", []):
            e = p.get("enum")
            if e and e not in seen:
                seen.append(e)
    return seen


def _render_function_enum_tables(
    functions: list[dict], enums: dict[str, list[str]]
) -> str:
    """Emit the per-enum ``_enum_<name>[]`` tables + the shared ``_enum_index``
    for the enums a module's functions reference (gh-353).

    One emitter with every other face (gh-1026): the same lookup body and the
    same "order is the C int" table layout, over the enums this module's
    functions reference."""
    from . import _enumc

    return _enumc.render_tables(_functions_enums_used(functions), enums)


def make_functions_ctx(
    module: str,
    Module: str,
    functions: list[dict],
    enums: "dict[str, list[str]] | None" = None,
    doc_blocks: "dict | None" = None,
) -> dict:
    """Return template context keys for module-level Python wrapper functions.

    Returns keys consumed by render_module_ext_c:
      function_wrappers  — static _bind_<fn> functions (inserted after header)
      module_methods_def — static PyMethodDef array block, or ''
      module_m_methods   — '{module}_module_methods' or 'NULL'
      function_enum_tables — the _enum_index helper + per-enum tables (gh-353),
                             or '' when no function param uses an enum
      function_uses_enum — True when any function param references an enum (the
                           ext.c then also #includes <string.h> for strcmp)

    The module-level table is named ``{module}_module_methods`` (not
    ``{Module}_methods``) so it never collides with an object's own
    ``{Component}_methods`` table when the module shares a name with one of
    its objects (the collocated case, e.g. ``jm module fft`` +
    ``jm object fft --module fft``): both end up in the same translation unit
    via the aggregator's ``#include``.
    """
    if not functions:
        return {
            "function_wrappers": "",
            "module_methods_def": "",
            "module_m_methods": "NULL",
            "function_enum_tables": "",
            "function_uses_enum": False,
        }
    # gh-643: the runtime doc derives from the same module-header block the
    # .pyi derives from, through the same renderer. Local imports: _stubs and
    # _docstring are leaves relative to this module, but importing _stubs at
    # module scope would put _render into the _object/_stubs import cycle.
    from ._context._parse import _build_ml_doc
    from ._docstring import render_runtime_doc
    from ._stubs import fn_py_surface

    wrappers: list[str] = []
    entries: list[str] = []
    for fn in functions:
        name = fn["name"]
        params = list(fn.get("params", []))
        return_type = fn.get("return_type", "void")
        # gh-643: was `fn.get("doc", f"{name}.")` — the manifest override or a
        # name stub, so `help(kaiser_window)` never saw the C @brief, let alone
        # params/returns/examples, while the .pyi beside it carried all of it
        # (gh-384). The manifest `doc` stays the summary override; it is passed
        # to the renderer rather than replacing it.
        _blk = (doc_blocks or {}).get(name)
        if _blk is None:
            # No header block: keep the historical one-liner. `_fn_stub`
            # collapses to a one-liner here too, so rendering the section
            # skeleton would *introduce* a divergence rather than close one —
            # the runtime would carry `Parameters`/`Input.` placeholders the
            # stub beside it does not. Undocumented functions are unchanged.
            doc = _build_ml_doc([fn.get("doc", "") or f"{name}."])
        else:
            _ret_ann, _py_params, _ = fn_py_surface(fn)
            doc = _build_ml_doc(
                render_runtime_doc(
                    _blk, name, _py_params, _ret_ann, fn.get("doc", "")
                )
            )
        # gh-238: a function with params is positional-or-keyword
        # (METH_VARARGS | METH_KEYWORDS); a no-param function stays METH_NOARGS.
        # The kw-capable binding has the 3-arg PyCFunctionWithKeywords signature,
        # so cast it through `(void *)` in the table (jm's convention; silences
        # -Wcast-function-type).
        if params:
            flags = "METH_VARARGS | METH_KEYWORDS"
            fn_ref = f"(PyCFunction)(void *)_bind_{name}"
        else:
            flags = "METH_NOARGS"
            fn_ref = f"_bind_{name}"
        wrappers.append(
            _py_wrapper_for_function(
                name,
                params,
                return_type,
                out_type=fn.get("out_type", ""),
                result_fields=fn.get("result_fields", []),
                max_results_param=fn.get("max_results_param", ""),
                max_results=int(fn.get("max_results", 64)),
                variable_output=bool(fn.get("variable_output")),
                out_size=fn.get("out_size", ""),
                check_return=bool(fn.get("check_return")),
                enums=enums,
            )
        )
        # `doc` is already a C string literal (escaped, possibly multi-line) —
        # it used to be interpolated bare into `"{doc}"`, so a quote or a
        # newline in a manifest `doc` produced a module that did not compile.
        # That is gh-633's class of bug, on the one surface it had not reached.
        entries.append(f'    {{"{name}", {fn_ref}, {flags},\n     {doc}}},')
    entries.append("    {NULL, NULL, 0, NULL}")
    array_body = "\n".join(entries)
    methods_def = (
        f"static PyMethodDef {module}_module_methods[] = "
        f"{{\n{array_body}\n}};\n\n"
    )
    # gh-353: when a function param references a [[enum]], emit the SSOT
    # _enum_index helper + per-enum tables BEFORE the _bind_ wrappers (which
    # call _enum_index). Enum-free modules get an empty string (no churn).
    enums_used = _functions_enums_used(functions)
    enum_tables = (
        _render_function_enum_tables(functions, enums or {})
        if enums_used
        else ""
    )
    return {
        "function_wrappers": "\n".join(wrappers),
        "module_methods_def": methods_def,
        "module_m_methods": f"{module}_module_methods",
        "function_enum_tables": enum_tables,
        "function_uses_enum": bool(enums_used),
    }


def record_registration_c(
    registrations: "list[_record.RecordReg]",
    seen: "dict[str, _record.RecordReg] | None" = None,
) -> "tuple[list[str], list[str]]":
    """``(type_ready_lines, add_object_lines)`` for a component's records.

    gh-1264. *registrations* comes from :func:`_record.registrations` — one
    entry per ``single = true`` method's structseq. Shared by every
    ``PyInit_`` assembly site (the standalone template's ctx and both
    module-init assemblers below) so the create-then-register shape is
    written once rather than three times drifting apart.

    The type-ready half runs BEFORE the module object exists (creating a
    type needs no module); the add-object half runs after, so a caller
    splices the first into its `PyType_Ready` block and the second into its
    `PyModule_AddObject` block, the same two-phase split every other jm-owned
    type already goes through. `PyStructSequence_NewType` returns an owned
    new reference (unlike the static `PyTypeObject`s beside it, which need an
    explicit `Py_INCREF` before `AddObject` "steals" one) — passed straight
    to `PyModule_AddObject`, and `Py_DECREF`'d only if THAT call fails, since
    on success ownership has already transferred to the module.

    *seen* is the public-name namespace of the extension module being
    assembled (gh-1268). A module aggregator threads ONE dict through every
    component so a name claimed by an object is not claimed again by its
    view — the second claim aliases the first type object instead, since
    ``PyModule_AddObject`` steals its reference and a second call under the
    same key frees the first type out from under the wrapper still pointing
    at it. Passing ``None`` starts a fresh namespace, which is what a
    standalone object's own ``PyInit_`` wants: its `.so` is its own module.

    Raises
    ------
    ValueError
        Via :func:`_record.resolve`, when two records claim one name with
        different shapes.
    """
    ns = {} if seen is None else seen
    ready: list[str] = []
    add: list[str] = []
    for reg in registrations:
        sid, name = reg.sid, reg.name
        alias = _record.resolve(reg, ns)
        if alias is not None:
            # gh-1268: the module already publishes this record. Point this
            # wrapper's static at that one type rather than registering a
            # second under the same key. Both statics are borrowed aliases
            # of the reference the module dict owns, so this adds no
            # refcount and the lazy in-wrapper fallback (a NULL check) never
            # fires again.
            ready.append(
                f"    {sid}_type = {alias.sid}_type;"
                f"  /* {name}: one public name, one type */"
            )
            continue
        ready.append(
            f"    if (!{sid}_type) {{\n"
            f"        {sid}_type = PyStructSequence_NewType(&{sid}_desc);\n"
            f"        if (!{sid}_type) return NULL;\n"
            f"    }}"
        )
        add.append(
            f'    if (PyModule_AddObject(m, "{name}",'
            f" (PyObject *){sid}_type) < 0) {{\n"
            f"        Py_DECREF({sid}_type);\n"
            f"        Py_DECREF(m);\n"
            f"        return NULL;\n"
            f"    }}"
        )
    return ready, add


def render_module_ext_c(
    module: str,
    comp_ctxs: list[dict],
    functions: list[dict] = (),
    enums: "dict[str, list[str]] | None" = None,
    module_doc_c: str = "",
    fn_doc_blocks: "dict | None" = None,
    procglobal: str = "",
) -> str:
    """Render a multi-object module _ext.c from a list of component contexts.

    Each ctx must contain 'module' = module_name and 'Component' = the type name.
    Pass functions (from config module_functions()) to wire up module-level
    PyMethodDef entries; Python wrappers are emitted inline (not via #include).
    Pass enums (from ``C.enums(cfg)``) so a function's ``enum`` param emits the
    SSOT ``_enum_index`` helper + per-enum tables (gh-353).

    ``module`` may be a dotted id (``dsp.filters``); the C identifiers /
    file-name prefixes use the cname form (``dsp_filters``) while the
    ``PyInit_``/``.m_name`` use the leaf (``filters``). For a dotless id all
    three coincide, so flat modules render unchanged.
    """
    mp = C.module_paths(module)
    leaf = mp.leaf
    module = mp.cname
    Module = "".join(w.title() for w in module.split("_"))
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)

    fn_ctx = make_functions_ctx(
        module, Module, list(functions), enums, fn_doc_blocks
    )
    # Only include the module-level core header when there are module functions
    # that use it.  Objects have their own per-component includes in
    # COMPONENT_TYPE_SECTION; the module_core.h is only needed when module-
    # level C functions (declared in module_core.h) are wired into the ext.c.
    has_module_fns = bool(functions)
    module_core_include = (
        f'#include "{module}/{module}_core.h"\n' if has_module_fns else ""
    )
    header_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "module_core_include": module_core_include,
        # gh-353: an enum param's _enum_index uses strcmp.
        "module_extra_includes": (
            "#include <string.h>\n" if fn_ctx.get("function_uses_enum") else ""
        ),
    }
    parts = [render(MODULE_EXT_C_HEADER, header_ctx)]

    if fn_ctx.get("function_enum_tables"):
        parts.append(fn_ctx["function_enum_tables"] + "\n")
    if fn_ctx["function_wrappers"]:
        parts.append(fn_ctx["function_wrappers"] + "\n")

    for ctx in comp_ctxs:
        parts.append(render(COMPONENT_TYPE_SECTION, ctx))

    type_ready_lines: list[str] = []
    add_object_calls_lines: list[str] = []
    # gh-1268: ONE public-name namespace for the whole module, so a view and
    # its parent sharing a record_name publish one type object rather than
    # two, the second of which frees the first.
    _rec_seen: dict[str, _record.RecordReg] = {}
    for ctx in comp_ctxs:
        type_ready_lines.append(
            f"    if (PyType_Ready(&{ctx['ComponentW']}Type) < 0) return NULL;"
        )
        # gh-203: a streamable object also readies its iterator type.
        if ctx.get("stream_module_ready"):
            type_ready_lines.append(ctx["stream_module_ready"])
        # gh-1264: a single=true method's structseq, created and registered
        # here instead of lazily inside the method (which never told the
        # module it existed).
        _rec_ready, _rec_add = record_registration_c(
            ctx.get("record_registrations") or [], _rec_seen
        )
        type_ready_lines += _rec_ready
        C_ = ctx["Component"]
        CW_ = ctx["ComponentW"]
        add_object_calls_lines += [
            f"    Py_INCREF(&{CW_}Type);",
            f'    if (PyModule_AddObject(m, "{C_}", (PyObject *)&{CW_}Type) < 0) {{',
            f"        Py_DECREF(&{CW_}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
        add_object_calls_lines += _rec_add
    type_ready_checks = "\n".join(type_ready_lines)
    add_object_calls = "\n".join(add_object_calls_lines)

    footer_ctx = {
        "module": module,
        "module_leaf": leaf,
        "Module": Module,
        "type_ready_checks": type_ready_checks,
        "add_object_calls": add_object_calls,
        # gh-645: m_doc. Defaulted here so no render path can leak a literal
        # <<module_doc_c>> into generated C; the caller passes the manifest
        # string when the module declares one.
        "module_doc_c": module_doc_c or f'"{Module} module."',
        # gh-1117: empty unless a linked core declares `process_global`, so
        # every existing project renders byte-identically.
        "procglobal": procglobal,
        **fn_ctx,
    }
    parts.append(render(MODULE_EXT_C_FOOTER, footer_ctx))
    return "".join(parts)


_FRAGMENT_FILE_HEADER = """\
/*
 * <<module>>_ext_<<frag_id>>.c — <<Component>> type for the <<module>> module.
 *
 * Included by <<module>>_ext.c (the module aggregator).
 * Hand-patches to this file are preserved across jm commands.
 * Do NOT compile this file directly — only <<module>>_ext.c is compiled.
 */
"""


def render_module_ext_fragment(comp_ctx: dict) -> str:
    """Render the per-object section for one fragment file.

    The returned text is the content of ``<module>_ext_<frag_id>.c``: a brief
    warning header followed by the full ``COMPONENT_TYPE_SECTION`` for the
    object.  It contains no Python.h include (the aggregator provides it).
    ``frag_id`` is the object's component name, or a view's lowercased
    class_name (gh-504); it defaults to ``component`` for any caller that
    predates views.
    """
    ctx = {
        "frag_id": comp_ctx.get("frag_id", comp_ctx["component"]),
        **comp_ctx,
    }
    header = render(_FRAGMENT_FILE_HEADER, ctx)
    return header + render(COMPONENT_TYPE_SECTION, ctx)


def render_module_ext_aggregator(
    module: str,
    comp_ctxs: list[dict],
    functions: list[dict] = (),
    extra_files: "set[str] | frozenset[str]" = frozenset(),
    extra_types: "list[str] | None" = None,
    enums: "dict[str, list[str]] | None" = None,
    module_doc_c: str = "",
    fn_doc_blocks: "dict | None" = None,
    procglobal: str = "",
) -> str:
    """Render the thin aggregator ``<module>_ext.c``.

    The aggregator #includes each per-object fragment in order, then defines
    the module-level PyModuleDef and PyInit_.  It is always overwritten on
    regeneration; hand-written code belongs in the fragment files or in
    never-touched ``*_extra.c`` files.

    Parameters
    ----------
    extra_files : set of str
        Basenames of ``*_extra.c`` files that exist on disk (e.g.
        ``{"filter_ext_fir_extra.c", "filter_ext_extra.c"}``).
        Per-object extras are included immediately after their fragment;
        the per-module extra is included after all fragments.
        jm never creates or modifies these files.
    extra_types : list of str, optional
        Names of hand-written CPython types declared in ``*_extra.c`` files
        that should be registered in ``PyInit_<module>``.  For each name
        ``T``, jm emits ``PyType_Ready(&TType)`` and
        ``PyModule_AddObject(m, "T", (PyObject *)&TType)``.
    """
    mp = C.module_paths(module)
    leaf = mp.leaf
    module = mp.cname
    Module = "".join(w.title() for w in module.split("_"))
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    fn_ctx = make_functions_ctx(
        module, Module, list(functions), enums, fn_doc_blocks
    )
    has_module_fns = bool(functions)
    module_core_include = (
        f'#include "{module}/{module}_core.h"\n' if has_module_fns else ""
    )
    header_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "module_core_include": module_core_include,
        # gh-353: an enum param's _enum_index uses strcmp.
        "module_extra_includes": (
            "#include <string.h>\n" if fn_ctx.get("function_uses_enum") else ""
        ),
    }
    parts = [render(MODULE_EXT_C_HEADER, header_ctx)]
    # Replace the "Objects: ..." comment to clarify this is the aggregator.
    parts[0] = parts[0].replace(
        f" * Objects: {object_list}",
        f" * Objects: {object_list}\n"
        f" * GENERATED — do not hand-edit. Patches belong in the _ext_<obj>.c fragments.",
    )
    # gh-862: the prologue comes FIRST, before any fragment, because that is
    # the whole point of it — a declaration included after its callers is not
    # available to them. It is the only hook here that is not "extra"; the
    # other two exist so hand-written types survive regeneration, while this
    # one exists so two fragments in the same module can share a helper
    # without one of them having to include the other.
    include_parts: list[str] = []
    prologue = f"{module}_ext_prologue.c"
    if prologue in extra_files:
        include_parts.append(
            f'#include "{prologue}"  /* hand-written — jm never modifies */'
        )
    # Include each per-object fragment, then its per-object extra if present.
    for ctx in comp_ctxs:
        # gh-504: a view's fragment is keyed on its frag_id, not the shared
        # parent component; real objects have frag_id == component.
        comp = ctx.get("frag_id", ctx["component"])
        include_parts.append(f'#include "{module}_ext_{comp}.c"')
        obj_extra = f"{module}_ext_{comp}_extra.c"
        if obj_extra in extra_files:
            include_parts.append(
                f'#include "{obj_extra}"  /* hand-written — jm never modifies */'
            )
    # Per-module extra goes after all fragments.
    mod_extra = f"{module}_ext_extra.c"
    if mod_extra in extra_files:
        include_parts.append(
            f'#include "{mod_extra}"  /* hand-written — jm never modifies */'
        )
    parts.append("\n" + "\n".join(include_parts) + "\n")
    if fn_ctx.get("function_enum_tables"):
        parts.append("\n" + fn_ctx["function_enum_tables"] + "\n")
    if fn_ctx["function_wrappers"]:
        parts.append("\n" + fn_ctx["function_wrappers"] + "\n")
    _extra_types = extra_types or []
    type_ready_lines: list[str] = []
    add_object_calls_lines: list[str] = []
    # gh-1268: see the peer loop in render_module_ext_c — one namespace per
    # module, not per component.
    _rec_seen: dict[str, _record.RecordReg] = {}
    for ctx in comp_ctxs:
        type_ready_lines.append(
            f"    if (PyType_Ready(&{ctx['ComponentW']}Type) < 0) return NULL;"
        )
        # gh-203: a streamable object also readies its iterator type.
        if ctx.get("stream_module_ready"):
            type_ready_lines.append(ctx["stream_module_ready"])
        # gh-1264: a single=true method's structseq, created and registered
        # here instead of lazily inside the method (which never told the
        # module it existed).
        _rec_ready, _rec_add = record_registration_c(
            ctx.get("record_registrations") or [], _rec_seen
        )
        type_ready_lines += _rec_ready
        C_ = ctx["Component"]
        CW_ = ctx["ComponentW"]
        add_object_calls_lines += [
            f"    Py_INCREF(&{CW_}Type);",
            f'    if (PyModule_AddObject(m, "{C_}", (PyObject *)&{CW_}Type) < 0) {{',
            f"        Py_DECREF(&{CW_}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
        add_object_calls_lines += _rec_add
    type_ready_lines += [
        f"    if (PyType_Ready(&{et}Type) < 0) return NULL;"
        for et in _extra_types
    ]
    type_ready_checks = "\n".join(type_ready_lines)
    for et in _extra_types:
        add_object_calls_lines += [
            f"    Py_INCREF(&{et}Type);",
            f'    if (PyModule_AddObject(m, "{et}", (PyObject *)&{et}Type) < 0) {{',
            f"        Py_DECREF(&{et}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
    add_object_calls = "\n".join(add_object_calls_lines)
    footer_ctx = {
        "module": module,
        "module_leaf": leaf,
        "Module": Module,
        "type_ready_checks": type_ready_checks,
        "add_object_calls": add_object_calls,
        # gh-645: m_doc. Defaulted here so no render path can leak a literal
        # <<module_doc_c>> into generated C; the caller passes the manifest
        # string when the module declares one.
        "module_doc_c": module_doc_c or f'"{Module} module."',
        # gh-1117: empty unless a linked core declares `process_global`, so
        # every existing project renders byte-identically.
        "procglobal": procglobal,
        **fn_ctx,
    }
    parts.append(render(MODULE_EXT_C_FOOTER, footer_ctx))
    return "".join(parts)
