"""_context/_sample.py — benchmark helpers and make_sample_ctx().

Builds the sample-type portion of the template rendering dict.
"""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
    _NP_ENUM,
    _ctype_display,
    _KIND_PY_ISINSTANCE,
    _KIND_PY_TEST_VAL,
)
from ._types import _py_default  # noqa: F401 – kept for any future callers
from ._parse import _step_parse_block


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------


def _bench_in_init(sample_type: str, samp: dict) -> str:
    if samp["kind"] == "complex":
        base = sample_type.replace(" _Complex", "")
        suffix = samp["zero"][samp["zero"].index("+") :]
        return f"({base})(i){suffix}"
    return f"({_ctype_display(sample_type)})(i)"


def _bench_warmup(samp: dict) -> str:
    z = samp["zero"]
    if samp["kind"] == "complex":
        return (
            z.replace("0.0f +", "1.0f +")
            .replace("0.0 +", "1.0 +")
            .replace("0.0L +", "1.0L +")
        )
    if samp["kind"] == "float":
        return z.replace("0.0f", "1.0f").replace("0.0", "1.0")
    return "1"


def _test_arr_4_init(sample_type: str, samp: dict) -> str:
    if samp["kind"] == "complex":
        base = sample_type.replace(" _Complex", "")
        if "long double" in base:
            return "{1.0L, 2.0L, 3.0L, 4.0L}"
        elif base == "double":
            return "{1.0, 2.0, 3.0, 4.0}"
        return "{1.0f, 2.0f, 3.0f, 4.0f}"
    if samp["kind"] == "float":
        return (
            "{1.0, 2.0, 3.0, 4.0}"
            if sample_type == "double"
            else "{1.0f, 2.0f, 3.0f, 4.0f}"
        )
    return "{1, 2, 3, 4}"


def _bench_py_blocks(
    arg_type: str,
    in_py_test_val: str,
    in_np_dtype: str,
    is_void_return: bool,
) -> tuple[str, str]:
    """Return (bench_step_py, bench_steps_py) indented blocks for BENCH_PY.

    bench_step_py   — lines that time a single step() call
    bench_steps_py  — lines that time steps() at 1k and 64k (may be empty)
    Both blocks are already indented with 4 spaces.
    """
    # step() timing block
    if arg_type == "void":
        step_py = (
            '    dt = _bench("step", obj.step)\n'
            "    print(f\"  {'step':<22} {dt * 1e9:9.1f} ns/call\")\n"
        )
    elif arg_type.endswith("[]"):
        step_py = (
            f"    x_step = np.zeros(4, dtype={in_np_dtype})\n"
            '    dt = _bench("step", obj.step, x_step)\n'
            "    print(f\"  {'step':<22} {dt * 1e9:9.1f} ns/call\")\n"
        )
    else:
        step_py = (
            f'    dt = _bench("step", obj.step, {in_py_test_val})\n'
            "    print(f\"  {'step':<22} {dt * 1e9:9.1f} ns/call\")\n"
        )

    # steps() timing block
    if arg_type == "void":
        steps_py = (
            '    dt = _bench("steps 1k", obj.steps, BLOCK_1K,'
            " reps=max(1, REPS // 10))\n"
            "    print(f\"  {'steps 1k':<22} {dt * 1e6:9.3f} µs/call\")\n"
            '    dt = _bench("steps 64k", obj.steps, BLOCK_64K,'
            " reps=max(1, REPS // 100))\n"
            "    print(f\"  {'steps 64k':<22} {dt * 1e3:9.3f} ms/call\")\n"
        )
    elif arg_type.endswith("[]"):
        # No steps(); bench buffer-arg step() with larger arrays instead
        _msa1 = "" if is_void_return else "  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)"
        _msa64 = (
            "" if is_void_return else "  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)"
        )
        steps_py = (
            f"    x1k = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            '    dt = _bench("step 1k buf", obj.step, x1k,'
            " reps=max(1, REPS // 10))\n"
            f"    print(f\"  {{'step 1k buf':<22}} {{dt * 1e6:9.3f}}"
            f' µs{_msa1}")\n'
            f"    x64k = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            '    dt = _bench("step 64k buf", obj.step, x64k,'
            " reps=max(1, REPS // 100))\n"
            f"    print(f\"  {{'step 64k buf':<22}} {{dt * 1e3:9.3f}}"
            f' ms{_msa64}")\n'
        )
    else:
        _msa1 = "" if is_void_return else "  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)"
        _msa64 = (
            "" if is_void_return else "  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)"
        )
        steps_py = (
            f"    x1k = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 1k", obj.steps, x1k,'
            " reps=max(1, REPS // 10))\n"
            f"    print(f\"  {{'steps 1k':<22}} {{dt * 1e6:9.3f}}"
            f' µs{_msa1}")\n'
            f"    x64k = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 64k", obj.steps, x64k,'
            " reps=max(1, REPS // 100))\n"
            f"    print(f\"  {{'steps 64k':<22}} {{dt * 1e3:9.3f}}"
            f' ms{_msa64}")\n'
        )

    return step_py, steps_py


def _pytest_bm_blocks(
    arg_type: str,
    in_py_test_val: str,
    in_np_dtype: str,
) -> tuple[str, str]:
    """Return (bm_step_py, bm_steps_py) top-level function defs for pytest-bm.

    bm_step_py  — benchmark function(s) for a single step() call
    bm_steps_py — benchmark function(s) for steps() or larger buffers
    """
    if arg_type == "void":
        bm_step = (
            "\ndef test_bench_step(benchmark, obj):\n    benchmark(obj.step)\n"
        )
        bm_steps = (
            "\n"
            "def test_bench_steps_1k(benchmark, obj):\n"
            "    benchmark(obj.steps, BLOCK_1K)\n"
            "\n"
            "def test_bench_steps_64k(benchmark, obj):\n"
            "    benchmark(obj.steps, BLOCK_64K)\n"
        )
    elif arg_type.endswith("[]"):
        bm_step = (
            "\n"
            "def test_bench_step_1k(benchmark, obj):\n"
            f"    x = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            "    benchmark(obj.step, x)\n"
            "\n"
            "def test_bench_step_64k(benchmark, obj):\n"
            f"    x = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            "    benchmark(obj.step, x)\n"
        )
        bm_steps = ""
    else:
        bm_step = (
            "\n"
            "def test_bench_step(benchmark, obj):\n"
            f"    benchmark(obj.step, {in_py_test_val})\n"
        )
        bm_steps = (
            "\n"
            "def test_bench_steps_1k(benchmark, obj):\n"
            f"    x = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            "    benchmark(obj.steps, x)\n"
            "\n"
            "def test_bench_steps_64k(benchmark, obj):\n"
            f"    x = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            "    benchmark(obj.steps, x)\n"
        )
    return bm_step, bm_steps


# ---------------------------------------------------------------------------
# resolve_return_type — single source of truth for the return-type default
# ---------------------------------------------------------------------------


def resolve_return_type(arg_type: str, return_type: str | None) -> str:
    """Apply the canonical default when ``return_type`` is None.

    The renderer has three callsites that need to know what the step()
    return type is when the user didn't pass --return-type — this
    function (plus its single import) is the only place where that
    default lives. Keeping the rule in one spot avoids the gh-92 class
    of bug where ``make_sample_ctx`` and ``make_step_ctx`` would
    silently disagree on the default.

    Rules (matching the existing per-shape conventions):

    - Array-input (``T[]``)  → ``void`` return (block transforms write
      into a caller-provided buffer).
    - Void-input (``void``)  → ``float _Complex`` return (generators
      emit complex samples by default; user can override).
    - Otherwise              → same as ``arg_type`` (processor: scalar
      in, scalar out, same width).
    """
    if return_type is not None:
        return return_type
    if arg_type.endswith("[]"):
        return "void"
    if arg_type == "void":
        return "float _Complex"
    return arg_type


# ---------------------------------------------------------------------------
# make_sample_ctx
# ---------------------------------------------------------------------------


def make_sample_ctx(
    arg_type: str = "float _Complex",
    return_type: str | None = None,
) -> dict[str, str]:
    """Return template context keys derived from step() arg/return types.

    arg_type    — C type for the step() input parameter x, or "void" for
                  generator objects that produce output from internal state
                  only.
    return_type — C type for the step() return value (default: same as
                  arg_type, or "float _Complex" when arg_type is "void").
                  Pass "void" for sink/processor objects whose step()
                  performs side effects only.
    """
    return_type = resolve_return_type(arg_type, return_type)
    is_void_return = return_type == "void"

    # Blockwise: array-in / array-out  (T[] → U[])
    # Both arg_type and return_type are array types. Build and return a
    # blockwise-specific context dict that populates the steps() interface
    # (no inline step(); the user writes steps() directly in _core.c).
    if return_type.endswith("[]"):
        if not arg_type.endswith("[]"):
            raise ValueError(
                f"array return type '{return_type}' requires an array "
                f"arg type (--arg-type 'T[]'). Blockwise transforms take "
                f"an input array and write to an output array of the same "
                f"length. Use a scalar return type for reductions."
            )
        in_elem = arg_type[:-2]
        out_elem = return_type[:-2]
        if in_elem not in _CTYPE_META:
            raise ValueError(
                f"unsupported array element type '{in_elem}' in "
                f"--arg-type '{arg_type}'."
            )
        if out_elem not in _CTYPE_META:
            raise ValueError(
                f"unsupported array element type '{out_elem}' in "
                f"--return-type '{return_type}'."
            )
        in_samp = _CTYPE_META[in_elem]
        out_samp = _CTYPE_META[out_elem]
        in_disp = _ctype_display(in_elem)
        out_disp = _ctype_display(out_elem)
        in_np_dtype = in_samp["py_type"]
        out_np_dtype = out_samp["py_type"]
        in_np_enum = _NP_ENUM[in_np_dtype]
        out_np_enum = _NP_ENUM[out_np_dtype]
        # Python bench blocks (steps(x) — same API as scalar steps())
        _bw_steps_py = (
            f"    x1k = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 1k", obj.steps, x1k,'
            " reps=max(1, REPS // 10))\n"
            "    print(f\"  {'steps 1k':<22} {dt * 1e6:9.3f} µs"
            '  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)")\n'
            f"    x64k = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 64k", obj.steps, x64k,'
            " reps=max(1, REPS // 100))\n"
            "    print(f\"  {'steps 64k':<22} {dt * 1e3:9.3f} ms"
            '  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)")\n'
        )
        _bw_step_py = (
            f"    x_step = np.zeros(4, dtype={in_np_dtype})\n"
            '    dt = _bench("steps (4)", obj.steps, x_step)\n'
            "    print(f\"  {'steps (4)':<22} {dt * 1e9:9.1f} ns/call\")\n"
        )
        # pytest-benchmark blocks
        _bw_bm_steps = (
            f"\ndef test_bm_steps(benchmark, obj_fixture):\n"
            f"    obj = obj_fixture\n"
            f"    x = np.ones(1024, dtype={in_np_dtype})\n"
            f"    benchmark(obj.steps, x)\n"
        )
        return {
            "arg_ctype": in_disp,
            "return_ctype": out_disp,
            "arg_zero": "",
            "step_example_suffix": ", NULL, 0, NULL",
            "step_example_lhs": "",
            "in_np_dtype": in_np_dtype,
            "out_np_dtype": out_np_dtype,
            "in_np_enum": in_np_enum,
            "out_np_enum": out_np_enum,
            "in_py_hint": f"NDArray[{in_np_dtype}]",
            "out_py_hint": f"NDArray[{out_np_dtype}]",
            "out_py_isinstance": f"NDArray[{out_np_dtype}]",
            "in_py_test_val": f"np.zeros(4, dtype={in_np_dtype})",
            "step_parse_block": "",
            "step_return_expr": "Py_RETURN_NONE",
            # C bench: allocate both in and out
            "bench_in_init": _bench_in_init(in_elem, in_samp),
            "bench_warmup": _bench_warmup(in_samp),
            "bench_in_decl": (
                f"    {in_disp} *in  = "
                f"malloc(BENCH_N * sizeof({in_disp}));\n"
                f'    if (!in) {{ fprintf(stderr, "OOM\\n"); return 1; }}'
            ),
            "bench_in_loop": (
                f"    for (int i = 0; i < BENCH_N; i++) "
                f"in[i] = {_bench_in_init(in_elem, in_samp)};"
            ),
            # For blockwise, step() IS steps(); bench passes in, n, out.
            "bench_step_input_arg": "in, BENCH_N, out",
            "bench_step_input_sep": ", ",
            "bench_step_inner_loop": "        ",  # no per-element inner loop
            "bench_steps_in_arg": " in, BENCH_N,",
            "bench_free_in": "    free(in);",
            "bench_out_decl": (
                f"    {out_disp} *out = "
                f"malloc(BENCH_N * sizeof({out_disp}));\n"
                f'    if (!out) {{ fprintf(stderr, "OOM\\n"); return 1; }}'
            ),
            "bench_volatile_sink": "",
            "bench_sink_assign": "",
            "bench_steps_out_arg": " out",
            "bench_free_out": "    free(out);",
            "test_arr_4_init": "{0}",
            "pure_x_local": "",
            "pure_x_fmt_char": "",
            "pure_x_parse_arg": "",
            "pure_x_to_c": "",
            # steps() returns NDArray — proper type stub
            "pyi_steps_stub": (
                f"\n    def steps(\n"
                f"        self,\n"
                f"        x: NDArray[{in_np_dtype}],\n"
                f"        out: NDArray[{out_np_dtype}] | None = None,\n"
                f"    ) -> NDArray[{out_np_dtype}]: ...\n"
            ),
            "bench_step_py": _bw_step_py,
            "bench_steps_py": _bw_steps_py,
            "bm_step_py": "",
            "bm_steps_py": _bw_bm_steps,
        }

    # Skip scalar validation for array arg — the [] path handles return type
    # separately below; the only invalid case is a non-scalar, non-void
    # return type on a scalar-input object.
    if (
        not is_void_return
        and not arg_type.endswith("[]")
        and return_type not in _CTYPE_META
    ):
        supported = ", ".join(sorted(_CTYPE_META))
        raise ValueError(
            f"unsupported --return-type value '{return_type}'."
            f" Supported scalar types: void, {supported}"
        )

    # Return-type-derived values (fallbacks used when return_type == "void").
    if is_void_return:
        ret_disp = "void"
        out_np_dtype = "np.complex64"  # unused for void return; safe fallback
    else:
        ret = _CTYPE_META[return_type]
        ret_disp = _ctype_display(return_type)
        out_np_dtype = ret["py_type"]

    # Bench keys that depend on the return type.
    if is_void_return:
        step_example_lhs = ""
        bench_out_decl = ""
        bench_volatile_sink = ""
        bench_sink_assign = ""
        bench_steps_out_arg = " BENCH_N"
        bench_free_out = ""
    else:
        step_example_lhs = f"{ret_disp} y = "
        bench_out_decl = (
            f"    {ret_disp} *out = "
            f"malloc(BENCH_N * sizeof({ret_disp}));\n"
            f'    if (!out) {{ fprintf(stderr, "OOM\\n"); return 1; }}'
        )
        bench_volatile_sink = (
            f"    /* volatile sink prevents DCE of the step() loop */\n"
            f"    volatile {ret_disp} _sink;"
        )
        bench_sink_assign = "_sink = "
        bench_steps_out_arg = " out, BENCH_N"
        bench_free_out = "    free(out);"

    # Bench inner-loop key: the indented for-loop that wraps the step() call.
    # Scalar/void: iterate BENCH_N times per outer iteration.
    # Array: no inner loop — one step() call processes the whole buffer.
    _bench_inner_loop_scalar = (
        "        for (int i = 0; i < BENCH_N; i++)\n            "
    )

    if arg_type == "void":
        # Generator (or void-in/void-out) object.
        if is_void_return:
            _pyi_steps = (
                "\n    def steps(self, n: int = 1) -> None:\n"
                '        """Run n iterations."""\n'
            )
        else:
            _pyi_steps = (
                f"\n    def steps(self, n: int = 1)"
                f" -> NDArray[{out_np_dtype}]:\n"
                '        """Generate n output samples."""\n'
            )
        return {
            "arg_ctype": "void",
            "return_ctype": ret_disp,
            "arg_zero": "",
            "step_example_suffix": "",
            "step_example_lhs": step_example_lhs,
            "in_np_dtype": out_np_dtype,
            "out_np_dtype": out_np_dtype,
            "in_np_enum": _NP_ENUM[out_np_dtype],
            "out_np_enum": _NP_ENUM[out_np_dtype],
            "in_py_hint": "int",
            "out_py_hint": (
                "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
            ),
            "out_py_isinstance": (
                "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
            ),
            "in_py_test_val": "1",
            "step_parse_block": "",
            "step_return_expr": (
                "Py_RETURN_NONE" if is_void_return else ret["to_py"]("y")
            ),
            "bench_in_init": "0",
            "bench_warmup": "1",
            "bench_in_decl": "",
            "bench_in_loop": "",
            "bench_step_input_arg": "",
            "bench_step_input_sep": "",
            "bench_step_inner_loop": _bench_inner_loop_scalar,
            "bench_steps_in_arg": "",
            "bench_free_in": "",
            "bench_out_decl": bench_out_decl,
            "bench_volatile_sink": bench_volatile_sink,
            "bench_sink_assign": bench_sink_assign,
            "bench_steps_out_arg": bench_steps_out_arg,
            "bench_free_out": bench_free_out,
            "test_arr_4_init": "{0}",
            # pure_x_* not used with void arg; provide empty fallbacks
            "pure_x_local": "",
            "pure_x_fmt_char": "",
            "pure_x_parse_arg": "",
            "pure_x_to_c": "",
            "pyi_steps_stub": _pyi_steps,
            **dict(
                zip(
                    ("bench_step_py", "bench_steps_py"),
                    _bench_py_blocks(
                        "void", "1", out_np_dtype, is_void_return
                    ),
                )
            ),
            **dict(
                zip(
                    ("bm_step_py", "bm_steps_py"),
                    _pytest_bm_blocks("void", "1", out_np_dtype),
                )
            ),
        }

    if arg_type.endswith("[]"):
        # Array-buffer object: step(state, const elem_t *x, size_t x_len).
        # steps() is not generated — the primary op already takes a buffer.
        elem_type = arg_type[:-2]
        if elem_type not in _CTYPE_META:
            supported = ", ".join(sorted(_CTYPE_META))
            raise ValueError(
                f"unsupported array element type '{elem_type}' in "
                f"--arg-type '{arg_type}'."
                f" Supported element types: void, {supported}"
            )
        samp = _CTYPE_META[elem_type]
        elem_disp = _ctype_display(elem_type)
        in_np_dtype = samp["py_type"]
        in_np_enum = _NP_ENUM[in_np_dtype]
        return {
            "arg_ctype": elem_disp,
            "return_ctype": ret_disp,
            "arg_zero": "",
            "step_example_suffix": ", NULL, 0",
            "step_example_lhs": step_example_lhs,
            "in_np_dtype": in_np_dtype,
            "out_np_dtype": out_np_dtype,
            "in_np_enum": in_np_enum,
            "out_np_enum": _NP_ENUM[out_np_dtype],
            "in_py_hint": f"NDArray[{in_np_dtype}]",
            "out_py_hint": (
                "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
            ),
            "out_py_isinstance": (
                "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
            ),
            "in_py_test_val": f"np.zeros(4, dtype={in_np_dtype})",
            "step_parse_block": "",  # pre-rendered in make_step_ctx
            "step_return_expr": (
                "Py_RETURN_NONE" if is_void_return else ret["to_py"]("y")
            ),
            "bench_in_init": _bench_in_init(elem_type, samp),
            "bench_warmup": _bench_warmup(samp),
            "bench_in_decl": (
                f"    {elem_disp} *in  = "
                f"malloc(BENCH_N * sizeof({elem_disp}));\n"
                f'    if (!in) {{ fprintf(stderr, "OOM\\n"); return 1; }}'
            ),
            "bench_in_loop": (
                f"    for (int i = 0; i < BENCH_N; i++) "
                f"in[i] = {_bench_in_init(elem_type, samp)};"
            ),
            # For array arg the step() call already processes the whole buffer;
            # bench passes the pointer and length rather than per-element index.
            "bench_step_input_arg": "in, BENCH_N",
            "bench_step_input_sep": ", ",
            "bench_step_inner_loop": "        ",  # no inner loop
            "bench_steps_in_arg": "",  # no steps() for array arg
            "bench_free_in": "    free(in);",
            "bench_out_decl": bench_out_decl,
            "bench_volatile_sink": bench_volatile_sink,
            "bench_sink_assign": bench_sink_assign,
            "bench_steps_out_arg": bench_steps_out_arg,
            "bench_free_out": bench_free_out,
            "test_arr_4_init": "{0}",
            "pure_x_local": "",
            "pure_x_fmt_char": "",
            "pure_x_parse_arg": "",
            "pure_x_to_c": "",
            "pyi_steps_stub": "",  # no steps() for array arg
            **dict(
                zip(
                    ("bench_step_py", "bench_steps_py"),
                    _bench_py_blocks(
                        arg_type,
                        f"np.zeros(4, dtype={in_np_dtype})",
                        in_np_dtype,
                        is_void_return,
                    ),
                )
            ),
            **dict(
                zip(
                    ("bm_step_py", "bm_steps_py"),
                    _pytest_bm_blocks(
                        arg_type,
                        f"np.zeros(4, dtype={in_np_dtype})",
                        in_np_dtype,
                    ),
                )
            ),
        }

    if arg_type not in _CTYPE_META:
        supported = ", ".join(sorted(_CTYPE_META))
        raise ValueError(
            f"unsupported --arg-type value '{arg_type}'."
            f" Supported scalar types: void, {supported}"
        )

    samp = _CTYPE_META[arg_type]
    in_np_dtype = samp["py_type"]

    # pure_x_* keys: used inside pure-scalar fn() to parse the x argument.
    samp_disp = _ctype_display(arg_type)
    if "parse_type" in samp:
        pure_x_local = (
            f"    {samp['parse_type']} x_raw = {samp['parse_zero']};"
        )
        pure_x_parse_arg = "&x_raw"
        pure_x_to_c = f"    {samp_disp} x = {samp['to_c']('x')};\n"
    else:
        pure_x_local = f"    {samp_disp} x;"
        pure_x_parse_arg = "&x"
        pure_x_to_c = ""

    return {
        "arg_ctype": _ctype_display(arg_type),
        "return_ctype": ret_disp,
        "arg_zero": samp["zero"],
        "step_example_suffix": f", {samp['zero']}",
        "step_example_lhs": step_example_lhs,
        "in_np_dtype": in_np_dtype,
        "out_np_dtype": out_np_dtype,
        "in_np_enum": _NP_ENUM[in_np_dtype],
        "out_np_enum": _NP_ENUM[out_np_dtype],
        "in_py_hint": _KIND_PY_ISINSTANCE[samp["kind"]],
        "out_py_hint": (
            "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
        ),
        "out_py_isinstance": (
            "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
        ),
        "in_py_test_val": _KIND_PY_TEST_VAL[samp["kind"]],
        "step_parse_block": _step_parse_block(arg_type, samp),
        "step_return_expr": (
            "Py_RETURN_NONE" if is_void_return else ret["to_py"]("y")
        ),
        "bench_in_init": _bench_in_init(arg_type, samp),
        "bench_warmup": _bench_warmup(samp),
        "bench_in_decl": (
            f"    {samp_disp} *in  = "
            f"malloc(BENCH_N * sizeof({samp_disp}));\n"
            f'    if (!in) {{ fprintf(stderr, "OOM\\n"); return 1; }}'
        ),
        "bench_in_loop": (
            f"    for (int i = 0; i < BENCH_N; i++) "
            f"in[i] = {_bench_in_init(arg_type, samp)};"
        ),
        "bench_step_input_arg": "in[i]",
        "bench_step_input_sep": ", ",
        "bench_step_inner_loop": _bench_inner_loop_scalar,
        "bench_steps_in_arg": " in,",
        "bench_free_in": "    free(in);",
        "bench_out_decl": bench_out_decl,
        "bench_volatile_sink": bench_volatile_sink,
        "bench_sink_assign": bench_sink_assign,
        "bench_steps_out_arg": bench_steps_out_arg,
        "bench_free_out": bench_free_out,
        "test_arr_4_init": _test_arr_4_init(arg_type, samp),
        "pure_x_local": pure_x_local,
        "pure_x_fmt_char": samp["fmt"],
        "pure_x_parse_arg": pure_x_parse_arg,
        "pure_x_to_c": pure_x_to_c,
        "pyi_steps_stub": (
            f"\n    def steps(self, x: NDArray[{in_np_dtype}], "
            f"out: NDArray[{out_np_dtype}] | None = None) "
            f"-> NDArray[{out_np_dtype}]:\n"
            '        """Process a samples array. Returns ndarray, '
            'or fills out= if supplied."""\n'
        )
        if not is_void_return
        else (
            f"\n    def steps(self, x: NDArray[{in_np_dtype}]) -> None:\n"
            '        """Process a block of input samples."""\n'
        ),
        **dict(
            zip(
                ("bench_step_py", "bench_steps_py"),
                _bench_py_blocks(
                    arg_type,
                    _KIND_PY_TEST_VAL[samp["kind"]],
                    in_np_dtype,
                    is_void_return,
                ),
            )
        ),
        **dict(
            zip(
                ("bm_step_py", "bm_steps_py"),
                _pytest_bm_blocks(
                    arg_type,
                    _KIND_PY_TEST_VAL[samp["kind"]],
                    in_np_dtype,
                ),
            )
        ),
    }
