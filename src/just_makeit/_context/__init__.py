"""_context — context-builder functions for just-makeit.

Each make_*_ctx() function assembles a rendering dict for a specific
code-generation task. Callers pass these dicts to _render.render().

Public API (six functions):
  make_sample_ctx    — step() arg/return type metadata
  make_state_ctx     — struct fields, getters/setters, init parse
  make_methods_ctx   — extra named execute methods
  make_properties_ctx — Python properties backed by C getters/setters
  make_perf_ctx      — JM_HOT/JM_FORCEINLINE vs static inline
  make_step_ctx      — full step()/steps() C and Python bodies
"""

from ._sample import make_sample_ctx, resolve_return_type
from ._state import (
    make_state_ctx,
    _pyi_examples_block,
    _build_no_state_init_ctx,
)
from ._methods import make_methods_ctx, make_properties_ctx
from ._step import make_perf_ctx, make_step_ctx
from ._stream import make_stream_ctx

__all__ = [
    "make_sample_ctx",
    "resolve_return_type",
    "make_state_ctx",
    "make_methods_ctx",
    "make_properties_ctx",
    "make_perf_ctx",
    "make_step_ctx",
    "make_stream_ctx",
    "_pyi_examples_block",
    "_build_no_state_init_ctx",
]
