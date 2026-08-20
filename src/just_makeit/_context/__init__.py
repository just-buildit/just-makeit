"""_context — context-builder functions for just-makeit.

Each make_*_ctx() function assembles a rendering dict for a specific
code-generation task. Callers pass these dicts to _render.render().

Public API:
  make_sample_ctx    — step() arg/return type metadata
  make_state_ctx     — struct fields, getters/setters, init parse
  make_methods_ctx   — extra named execute methods
  make_enum_tables_ctx — the [[enum]] tables a type's methods/properties index
  make_properties_ctx — Python properties backed by C getters/setters
  make_warnings_ctx  — post-construction PyErr_WarnEx (gh-481)
  make_errors_ctx    — create() failure -> Python exception (gh-482)
  make_destroy_ctx   — destructor name/aliases/fallibility (gh-541/544)
  make_perf_ctx      — JM_HOT/JM_FORCEINLINE vs static inline
  make_step_ctx      — full step()/steps() C and Python bodies
"""

from ._sample import make_sample_ctx, resolve_return_type
from ._state import (
    make_state_ctx,
    state_accessor_stubs,
    _pyi_examples_block,
    _build_no_state_init_ctx,
    _unseedable_required,
    _reset_wrapper_slots,
)
from ._methods import (
    _bench_todo as _bench_todo_impl,
    make_enum_tables_ctx,
    make_methods_ctx,
    make_properties_ctx,
)
from ._diagnostics import make_warnings_ctx, make_errors_ctx
from ._destroy import (
    make_destroy_ctx,
    destroy_py_names,
    validate_destroy_spec,
)
from ._step import make_perf_ctx, make_step_ctx
from ._stream import make_stream_ctx
from ._platform import make_platform_ctx
from ._modpath import make_module_ctx


def bench_todo_for_functions(component: str, functions: "list[str]") -> str:
    """gh-1034: the scaffolded-benchmark TODO for a module's free functions.

    A thin alias so `_object` reaches the one block in `_methods` rather than
    growing a second. The worked ``jm_bench_add`` example inside it is
    paste-and-run and carries its own ``elapsed_sec`` declarations for that
    reason; a second copy is a copy that stops running.
    """
    return _bench_todo_impl(component, [], functions=functions)


__all__ = [
    "make_sample_ctx",
    "resolve_return_type",
    "make_state_ctx",
    "state_accessor_stubs",
    "make_methods_ctx",
    "bench_todo_for_functions",
    "make_enum_tables_ctx",
    "make_properties_ctx",
    "make_warnings_ctx",
    "make_errors_ctx",
    "make_destroy_ctx",
    "destroy_py_names",
    "validate_destroy_spec",
    "make_perf_ctx",
    "make_step_ctx",
    "make_stream_ctx",
    "make_platform_ctx",
    "make_module_ctx",
    "_pyi_examples_block",
    "_build_no_state_init_ctx",
    "_unseedable_required",
    "_reset_wrapper_slots",
]
