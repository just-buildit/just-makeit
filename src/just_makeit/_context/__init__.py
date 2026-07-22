"""_context — context-builder functions for just-makeit.

Each make_*_ctx() function assembles a rendering dict for a specific
code-generation task. Callers pass these dicts to _render.render().

Public API:
  make_sample_ctx    — step() arg/return type metadata
  make_state_ctx     — struct fields, getters/setters, init parse
  make_methods_ctx   — extra named execute methods
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
    _pyi_examples_block,
    _build_no_state_init_ctx,
    _unseedable_required,
    _reset_wrapper_slots,
)
from ._methods import make_methods_ctx, make_properties_ctx
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

__all__ = [
    "make_sample_ctx",
    "resolve_return_type",
    "make_state_ctx",
    "make_methods_ctx",
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
