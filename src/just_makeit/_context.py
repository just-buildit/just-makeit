"""
_context.py — context-builder functions for just-makeit.

Each make_*_ctx() function assembles a rendering dict for a specific
code-generation task. Callers pass these dicts to _render.render().
"""

from ._types import (
    _CTYPE_META,
    _NP_ENUM,
    _ARRAY_DTYPE,
    _CTYPE_TO_NPY,
    _CTYPE_TO_DTYPE,
    _KIND_PY_ISINSTANCE,
    _KIND_PY_TEST_VAL,
    _PYBUILD_FMT,
    is_array_param_type,
    array_param_ndim,
    array_elem_ctype,
    is_string_enum_type,
    string_enum_choices,
    parse_array_type,
    is_valid_type,
    _ctype_display,
    SUPPORTED_TYPES,
)

def _build_ml_doc(lines: list[str]) -> str:
    """Render a list of logical doc lines into adjacent C string literals.

    Each item in *lines* becomes one C string literal ending with ``\\n``.
    Continuation lines are indented with 5 spaces so the result slots
    cleanly into the 4th element of a PyMethodDef entry::

        pmd = (
            f'    {{"step", (PyCFunction)Nco_step, METH_VARARGS,\\n'
            f'     {_build_ml_doc(lines)}}},\\n'
        )

    Special characters (backslashes, double-quotes) inside *lines* are
    escaped for the C string literal automatically.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    return "\n     ".join(f'"{_esc(ln)}\\n"' for ln in lines)


def _build_params_parse(
    params: list[dict],
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      Array types end with '[]', e.g. "float _Complex[]"; their element type
      must be in _CTYPE_TO_NPY.  Array params expand to two C args:
      (const elem_t *name, size_t name_len).

    Returns (parse_block, call_args_c, cleanup):
      parse_block  — indented C code: declarations, PyArg_ParseTuple, array
                     conversion and error-exit paths with partial cleanup
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

    for p in params:
        pname = p["name"]
        ptype = p["type"]

        if is_array_param_type(ptype):
            elem_ct = array_elem_ctype(ptype)
            npy_enum = _CTYPE_TO_NPY[elem_ct]
            elem_disp = _ctype_display(elem_ct)
            obj_var = f"{pname}_obj"
            arr_var = f"{pname}_arr"

            decl_lines.append(f"    PyObject *{obj_var} = NULL;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")

            # Build error path: decref all arrays acquired so far.
            prior_decrefs = "".join(f" Py_DECREF({a});" for a in arr_names)
            is_out = bool(p.get("out"))
            npy_flags = (
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE"
                if is_out
                else "NPY_ARRAY_C_CONTIGUOUS"
            )
            const_qual = "" if is_out else "const "
            arr_acq.append(
                f"    PyArrayObject *{arr_var} = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        {obj_var}, {npy_enum}, {npy_flags});\n"
                f"    if (!{arr_var}) {{{prior_decrefs} return NULL; }}"
            )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                f"    size_t {pname}_len = (size_t)PyArray_SIZE({arr_var});"
            )
            arr_names.append(arr_var)
            call_args.extend([pname, f"{pname}_len"])
        else:
            meta = _CTYPE_META[ptype]
            disp = _ctype_display(ptype)
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {meta['parse_zero']};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(f"    {disp} {pname} = {meta['to_c'](pname)};")
            else:
                decl_lines.append(f"    {disp} {pname} = {meta['zero']};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    fmt_str = "".join(fmt_chars)
    addr_str = ", ".join(addr_exprs)
    lines = (
        decl_lines
        + [
            f'    if (!PyArg_ParseTuple(args, "{fmt_str}", {addr_str}))',
            "        return NULL;",
        ]
        + conv_lines
        + arr_acq
    )
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names)
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _step_parse_block(sample_type: str, samp: dict) -> str:
    """4-space-indented parse block for step(); ends without trailing newline.

    Uses 'x_raw' as the intermediate parse variable so to_c("x") works
    (the to_c lambdas append '_raw' to the base name they receive).
    """
    disp = _ctype_display(sample_type)
    if "parse_type" in samp:
        parse_type = samp["parse_type"]
        parse_zero = samp["parse_zero"]
        fmt = samp["fmt"]
        to_c_expr = samp["to_c"]("x")  # to_c("x") -> "(type)x_raw..." using x_raw var
        return (
            f"    {parse_type} x_raw = {parse_zero};\n"
            f'    if (!PyArg_ParseTuple(args, "{fmt}", &x_raw))\n'
            f"        return NULL;\n"
            f"    {disp} x = {to_c_expr};"
        )
    else:
        fmt = samp["fmt"]
        return (
            f"    {disp} x;\n"
            f'    if (!PyArg_ParseTuple(args, "{fmt}", &x))\n'
            f"        return NULL;"
        )


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
        _msa64 = "" if is_void_return else "  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)"
        steps_py = (
            f"    x1k = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            '    dt = _bench("step 1k buf", obj.step, x1k,'
            " reps=max(1, REPS // 10))\n"
            f"    print(f\"  {{'step 1k buf':<22}} {{dt * 1e6:9.3f}} µs{_msa1}\")\n"
            f"    x64k = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            '    dt = _bench("step 64k buf", obj.step, x64k,'
            " reps=max(1, REPS // 100))\n"
            f"    print(f\"  {{'step 64k buf':<22}} {{dt * 1e3:9.3f}} ms{_msa64}\")\n"
        )
    else:
        _msa1 = "" if is_void_return else "  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)"
        _msa64 = "" if is_void_return else "  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)"
        steps_py = (
            f"    x1k = np.ones(BLOCK_1K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 1k", obj.steps, x1k,'
            " reps=max(1, REPS // 10))\n"
            f"    print(f\"  {{'steps 1k':<22}} {{dt * 1e6:9.3f}} µs{_msa1}\")\n"
            f"    x64k = np.ones(BLOCK_64K, dtype={in_np_dtype})\n"
            '    dt = _bench("steps 64k", obj.steps, x64k,'
            " reps=max(1, REPS // 100))\n"
            f"    print(f\"  {{'steps 64k':<22}} {{dt * 1e3:9.3f}} ms{_msa64}\")\n"
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
        bm_step = "\ndef test_bench_step(benchmark, obj):\n    benchmark(obj.step)\n"
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


def make_sample_ctx(
    arg_type: str = "float _Complex",
    return_type: str | None = None,
) -> dict[str, str]:
    """Return template context keys derived from step() arg/return types.

    arg_type    — C type for the step() input parameter x, or "void" for
                  generator objects that produce output from internal state only.
    return_type — C type for the step() return value (default: same as arg_type,
                  or "float _Complex" when arg_type is "void"). Pass "void" for
                  sink/processor objects whose step() performs side effects only.
    """
    if return_type is None:
        if arg_type.endswith("[]"):
            return_type = "void"  # array-input step() is void by default
        elif arg_type == "void":
            return_type = "float _Complex"
        else:
            return_type = arg_type

    is_void_return = return_type == "void"

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
    _bench_inner_loop_scalar = "        for (int i = 0; i < BENCH_N; i++)\n            "

    if arg_type == "void":
        # Generator (or void-in/void-out) object.
        # Keys that reference input type are set to safe fallbacks; the actual
        # step/steps C and Python bodies are pre-rendered by make_step_ctx().
        if is_void_return:
            _pyi_steps = (
                "\n    def steps(self, n: int = 1) -> None:\n"
                '        """Run n iterations."""\n'
            )
        else:
            _pyi_steps = (
                f"\n    def steps(self, n: int = 1) -> NDArray[{out_np_dtype}]:\n"
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
                    _bench_py_blocks("void", "1", out_np_dtype, is_void_return),
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
        # steps() is not generated — the primary operation already takes a buffer.
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
            # bench passes the pointer and length rather than a per-element index.
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
                        arg_type, f"np.zeros(4, dtype={in_np_dtype})", in_np_dtype
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
    # Use x_raw as intermediate so to_c("x") works (lambdas append _raw).
    samp_disp = _ctype_display(arg_type)
    if "parse_type" in samp:
        pure_x_local = f"    {samp['parse_type']} x_raw = {samp['parse_zero']};"
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
        "out_py_hint": ("None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]),
        "out_py_isinstance": (
            "None" if is_void_return else _KIND_PY_ISINSTANCE[ret["kind"]]
        ),
        "in_py_test_val": _KIND_PY_TEST_VAL[samp["kind"]],
        "step_parse_block": _step_parse_block(arg_type, samp),
        "step_return_expr": ("Py_RETURN_NONE" if is_void_return else ret["to_py"]("y")),
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
                    arg_type, _KIND_PY_TEST_VAL[samp["kind"]], in_np_dtype
                ),
            )
        ),
    }


_C_SET_VAL: dict[str, str] = {
    "float": "2.0f",
    "double": "2.0",
    "float _Complex": "2.0f + 0.0f * I",
    "double _Complex": "2.0 + 0.0 * I",
    "long double _Complex": "2.0L + 0.0L * I",
}


def _c_set_val(ctype: str) -> str:
    return _C_SET_VAL.get(ctype, "2")  # all integer types


def _py_default(ctype: str, default: str) -> str:
    """Convert a C default literal to a valid Python literal."""
    kind = _CTYPE_META[ctype]["kind"]
    if kind == "float":
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if kind == "complex":
        return "0j"
    return default


def _py_sample_val(meta: dict) -> str:
    """Return a Python test set-value for the given type metadata."""
    if meta["kind"] == "complex":
        return "1.0+0.0j"
    if meta["kind"] == "float":
        return "2.0"
    return "2"


# Maps scalar element type -> NumPy C-API enum constant (for array state).
_NP_DTYPE_ENUM: dict[str, str] = {
    "float": "NPY_FLOAT",
    "double": "NPY_DOUBLE",
    "int": "NPY_INT",
    "int8_t": "NPY_INT8",
    "int16_t": "NPY_INT16",
    "int32_t": "NPY_INT32",
    "int64_t": "NPY_INT64",
    "uint8_t": "NPY_UINT8",
    "uint16_t": "NPY_UINT16",
    "uint32_t": "NPY_UINT32",
    "uint64_t": "NPY_UINT64",
    "float _Complex": "NPY_COMPLEX64",
    "double _Complex": "NPY_COMPLEX128",
    "long double _Complex": "NPY_CLONGDOUBLE",
}


def _build_no_state_init_ctx(
    component: str,
    Component: str,
    params: list[tuple],
    array_args: list[tuple[str, str]] = (),
    init_post_parse_impl: str = "",
) -> dict[str, str]:
    """Build the init-parse context keys for a --no-state object.

    Handles three kinds of init-params in addition to --array-arg:

    * Array-typed  (``type = "float _Complex[]"`` or ``"float _Complex[][]"``)
      — required positional numpy arrays.  1-D params expand to
      ``(const T *name, size_t name_len)``; 2-D params expand to
      ``(const T *name, size_t name_dim0, size_t name_dim1)`` and add a
      ``PyArray_NDIM`` shape check in the generated binding.

    * String-enum  (``type = "string_enum:a,b,c,d"``)
      — optional keyword string parsed and mapped to an ``int`` index before
      being forwarded to the C constructor.  Covers any C enum the caller
      exposes as human-readable names.

    * Scalar       (any type in ``_CTYPE_META``)
      — optional keyword with a default value; existing behaviour.

    Ordering in kwlist / C signature:
      --array-arg first, then array init-params, then string-enum, then scalars.
    """
    # ── Classify params ───────────────────────────────────────────────────────

    # (name, elem_ctype, ndim, npy_enum)
    arr_ip: list[tuple[str, str, int, str]] = []
    # (name, choices, default_str)
    str_enum_ip: list[tuple[str, list[str], str]] = []
    # (name, ctype, default, default_raw)
    scalar_ip: list[tuple] = []
    # name → (real_elem_ct, real_npy_enum, real_create_fn) for dispatch params
    dispatch_meta: dict[str, tuple[str, str, str]] = {}
    # (name, elem_ctype, ndim, npy_enum, alt_create_fn) — optional array kwarg
    opt_arr_ip: list[tuple[str, str, int, str, str]] = []

    for param in params:
        name, ct, dflt = param[:3]
        dflt_raw = param[3] if len(param) > 3 else ""
        real_type = param[4] if len(param) > 4 else ""
        real_create_fn_p = param[5] if len(param) > 5 else ""
        optional_flag = param[6] if len(param) > 6 else False
        alt_create_fn = param[7] if len(param) > 7 else ""
        if is_array_param_type(ct):
            elem_ct = array_elem_ctype(ct)
            ndim = array_param_ndim(ct)
            if optional_flag:
                opt_arr_ip.append(
                    (name, elem_ct, ndim, _CTYPE_TO_NPY[elem_ct], alt_create_fn)
                )
            else:
                arr_ip.append((name, elem_ct, ndim, _CTYPE_TO_NPY[elem_ct]))
                if real_type and real_create_fn_p:
                    real_elem_ct = array_elem_ctype(real_type)
                    dispatch_meta[name] = (
                        real_elem_ct,
                        _CTYPE_TO_NPY[real_elem_ct],
                        real_create_fn_p,
                    )
        elif is_string_enum_type(ct):
            str_enum_ip.append((name, string_enum_choices(ct), dflt))
        else:
            scalar_ip.append((name, ct, dflt, dflt_raw))

    # --array-arg entries (dtype-string form)
    _aa = list(array_args)
    _aa_ctypes = [(_ARRAY_DTYPE[dt][0], _ARRAY_DTYPE[dt][1]) for _, dt in _aa]

    # ── C create() signature, Doxygen docs, and call args — TOML order ────────
    #
    # PyArg grouping (kwlist / parse format) still uses required-first order;
    # the C-facing outputs follow the TOML declaration order so that
    # hand-written *_core.c functions are called with the right argument layout.

    # Build per-param lookup dicts keyed by name.
    _arr_meta: dict[str, tuple] = {
        n: (act, andim) for n, act, andim, _ in arr_ip
    }
    _str_enum_meta: dict[str, tuple] = {
        sn: (choices, sdflt) for sn, choices, sdflt in str_enum_ip
    }
    _scalar_meta: dict[str, tuple] = {
        n: (ct, dflt) for n, ct, dflt, *_ in scalar_ip
    }
    # Optional array param names — excluded from sig/call building (handled separately).
    _opt_arr_names: frozenset[str] = frozenset(n for n, *_ in opt_arr_ip)

    sig_parts: list[str] = []
    doc_parts: list[str] = []
    call_parts: list[str] = []
    c_create_parts_ordered: list[str] = []

    # --array-arg entries always come first (they precede init_params).
    for (name, dt), (ct, __) in zip(_aa, _aa_ctypes):
        disp = _ctype_display(ct)
        sig_parts.append(f"const {disp} *{name}, size_t {name}_len")
        doc_parts.append(
            f" * @param {name}  Input {dt} array (length passed as {name}_len)."
        )
        call_parts.append(
            f"(const {disp} *)PyArray_DATA({name}_arr), {name}_len"
        )
        c_create_parts_ordered.append("NULL, 0")

    # Init params in TOML declaration order (optional array params excluded here).
    for param in params:
        pname = param[0]
        pct = param[1]
        pdflt = param[2] if len(param) > 2 else ""
        if pname in _opt_arr_names:
            continue
        if pname in _arr_meta:
            act, andim = _arr_meta[pname]
            adisp = _ctype_display(act)
            if andim == 2:
                sig_parts.append(
                    f"const {adisp} *{pname},"
                    f" size_t {pname}_dim0, size_t {pname}_dim1"
                )
                doc_parts.append(
                    f" * @param {pname}  Input {adisp} 2-D array"
                    f" (shape: {pname}_dim0 x {pname}_dim1)."
                )
                call_parts.append(
                    f"(const {adisp} *)PyArray_DATA({pname}_arr),"
                    f" {pname}_dim0, {pname}_dim1"
                )
                c_create_parts_ordered.append("NULL, 0, 0")
            else:
                sig_parts.append(
                    f"const {adisp} *{pname}, size_t {pname}_len"
                )
                doc_parts.append(
                    f" * @param {pname}  Input {adisp} array"
                    f" (length passed as {pname}_len)."
                )
                call_parts.append(
                    f"(const {adisp} *)PyArray_DATA({pname}_arr), {pname}_len"
                )
                c_create_parts_ordered.append("NULL, 0")
        elif pname in _str_enum_meta:
            choices, _ = _str_enum_meta[pname]
            sig_parts.append(f"int {pname}")
            doc_parts.append(
                f" * @param {pname}  Enum index; 0={choices[0]}"
                + (
                    f"…{len(choices)-1}={choices[-1]}."
                    if len(choices) > 1
                    else "."
                )
            )
            call_parts.append(pname)
            c_create_parts_ordered.append("0")
        else:
            ct_s, dflt_s = _scalar_meta[pname]
            sig_parts.append(f"{ct_s} {pname}")
            doc_parts.append(
                f" * @param {pname}  {pname} (default: {dflt_s})."
            )
            call_parts.append(pname)
            c_create_parts_ordered.append(dflt_s)

    create_params = ", ".join(sig_parts) or "void"
    create_param_docs = (
        "\n".join(doc_parts)
        or " * @param (none)  Caller is responsible for all state management."
    )

    # ── kwlist / locals / parse format ────────────────────────────────────────

    kwlist_items = (
        [f'"{name}"' for name, _ in _aa]
        + [f'"{name}"' for name, _, __, ___ in arr_ip]
        + [f'"{name}"' for name, _, __ in str_enum_ip]
        + [f'"{name}"' for name, *_ in opt_arr_ip]
        + [f'"{name}"' for name, *_ in scalar_ip]
        + ["NULL"]
    )
    init_kwlist = ", ".join(kwlist_items)

    local_lines: list[str] = (
        [f"    PyObject *{name}_obj = NULL;" for name, _ in _aa]
        + [f"    PyObject *{name}_obj = NULL;" for name, _, __, ___ in arr_ip]
        + [f"    PyObject *{name}_obj = NULL;" for name, *_ in opt_arr_ip]
    )
    parse_args: list[str] = (
        [f"&{name}_obj" for name, _ in _aa]
        + [f"&{name}_obj" for name, _, __, ___ in arr_ip]
    )
    post_lines: list[str] = []

    # String-enum: "s" format, optional (after |)
    for sname, choices, sdflt in str_enum_ip:
        local_lines.append(f'    const char *{sname}_str = "{sdflt}";')
        parse_args.append(f"&{sname}_str")
        # Post-parse: strcmp chain → int
        enum_lines = [f"    int {sname} = 0;"]
        for i, choice in enumerate(choices):
            kw = "if" if i == 0 else "else if"
            enum_lines.append(
                f'    {kw} (strcmp({sname}_str, "{choice}") == 0)'
                f" {sname} = {i};"
            )
        choices_str = ", ".join(f'\\"{c}\\"' for c in choices)
        enum_lines += [
            f"    else {{",
            f'        PyErr_Format(PyExc_ValueError, "{sname} must be one of'
            f' {choices_str}, got \'%s\'", {sname}_str);',
            f"        return -1;",
            f"    }}",
        ]
        post_lines.extend(enum_lines)

    # Optional array params: "O" format, optional (after |); NULL means absent.
    for oname, *_ in opt_arr_ip:
        parse_args.append(f"&{oname}_obj")

    # Scalar params: optional (after |)
    for name, ct, dflt, *_dflt_raw in scalar_ip:
        dflt_raw = _dflt_raw[0] if _dflt_raw else ""
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            raw_init = dflt_raw if dflt_raw else meta["parse_zero"]
            local_lines.append(
                f"    {meta['parse_type']} {name}_raw = {raw_init};"
            )
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            parse_args.append(f"&{name}_raw")
        else:
            local_lines.append(f"    {ct} {name} = {dflt};")
            parse_args.append(f"&{name}")

    # Caller-supplied post-parse code (e.g. sentinel → computed default).
    if init_post_parse_impl:
        post_lines.append(init_post_parse_impl.rstrip())

    init_locals = "\n".join(local_lines)
    init_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""

    n_required = len(_aa) + len(arr_ip)
    array_fmt = "O" * n_required
    optional_fmt = (
        "s" * len(str_enum_ip)
        + "O" * len(opt_arr_ip)
        + "".join(_CTYPE_META[ct]["fmt"] for _, ct, *_ in scalar_ip)
    )
    if str_enum_ip or opt_arr_ip or scalar_ip:
        init_parse_fmt = array_fmt + "|" + optional_fmt
    else:
        init_parse_fmt = array_fmt or "|"

    init_parse_args = ", ".join(parse_args)

    # ── create_call_args (TOML order, built above) ────────────────────────────

    create_call_args = ", ".join(call_parts)

    # ── init_parse_block ──────────────────────────────────────────────────────

    if _aa or arr_ip or str_enum_ip or opt_arr_ip or scalar_ip:
        init_parse_block = (
            f"    static char *kwlist[] = {{{init_kwlist}}};\n"
            f"{init_locals}\n"
            f"\n"
            f"    if (!PyArg_ParseTupleAndKeywords(args, kwds,"
            f' "{init_parse_fmt}", kwlist,\n'
            f"                                     {init_parse_args}))\n"
            f"        return -1;\n"
            f"{init_post_parse}"
        )
    else:
        init_parse_block = "    (void)args;\n    (void)kwds;\n"

    # ── array_args_parse_block (FROM_OTF) ─────────────────────────────────────

    aapb_lines: list[str] = []
    allocated: list[str] = []

    for (name, _), (ct, npy_enum) in zip(_aa, _aa_ctypes):
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in allocated)
        aapb_lines.append(
            f"    PyArrayObject *{name}_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        {name}_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!{name}_arr) {{{cleanup} return -1; }}\n"
            f"    size_t {name}_len = (size_t)PyArray_SIZE({name}_arr);\n"
        )
        allocated.append(name)

    for aname, act, andim, anpy in arr_ip:
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in allocated)
        if aname in dispatch_meta:
            # Dtype-dispatch: probe the incoming array's dtype and branch.
            real_ect, real_npy, d_create_fn = dispatch_meta[aname]
            real_adisp = _ctype_display(real_ect)
            complex_adisp = _ctype_display(act)
            # Replace the complex cast in create_call_args with the real cast.
            complex_cast = (
                f"(const {complex_adisp} *)PyArray_DATA({aname}_arr)"
            )
            real_cast = f"(const {real_adisp} *)PyArray_DATA({aname}_arr)"
            real_call_args = create_call_args.replace(complex_cast, real_cast, 1)
            aapb_lines.append(
                f"    /* dtype dispatch: {real_adisp} → {d_create_fn},"
                f" {complex_adisp} → {component}_create */\n"
                f"    {{\n"
                f"        PyArrayObject *_{aname}_probe ="
                f" (PyArrayObject *)PyArray_CheckFromAny(\n"
                f"            {aname}_obj, NULL, 1, 1,"
                f" NPY_ARRAY_C_CONTIGUOUS, NULL);\n"
                f"        int _{aname}_real = _{aname}_probe &&"
                f" (PyArray_TYPE(_{aname}_probe) == {real_npy});\n"
                f"        Py_XDECREF(_{aname}_probe);\n"
                f"        if (_{aname}_real) {{\n"
                f"            PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"                {aname}_obj, {real_npy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"            if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"            size_t {aname}_len ="
                f" (size_t)PyArray_SIZE({aname}_arr);\n"
                f"            self->handle = {d_create_fn}({real_call_args});\n"
                f"            Py_DECREF({aname}_arr);\n"
                f"        }} else {{\n"
                f"            PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"                {aname}_obj, {anpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"            if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"            size_t {aname}_len ="
                f" (size_t)PyArray_SIZE({aname}_arr);\n"
                f"            self->handle ="
                f" {component}_create({create_call_args});\n"
                f"            Py_DECREF({aname}_arr);\n"
                f"        }}\n"
                f"    }}\n"
            )
            # Do NOT add to allocated — decref happens inside the dispatch block.
        elif andim == 2:
            aapb_lines.append(
                f"    PyArrayObject *{aname}_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        {aname}_obj, {anpy}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"    if (PyArray_NDIM({aname}_arr) != 2) {{\n"
                f"        PyErr_SetString(PyExc_ValueError,\n"
                f'                        "{aname} must be a 2-D array");\n'
                f"        {cleanup} Py_DECREF({aname}_arr); return -1;\n"
                f"    }}\n"
                f"    size_t {aname}_dim0 = (size_t)PyArray_DIM({aname}_arr, 0);\n"
                f"    size_t {aname}_dim1 = (size_t)PyArray_DIM({aname}_arr, 1);\n"
            )
            allocated.append(aname)
        else:
            aapb_lines.append(
                f"    PyArrayObject *{aname}_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        {aname}_obj, {anpy}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"    size_t {aname}_len = (size_t)PyArray_SIZE({aname}_arr);\n"
            )
            allocated.append(aname)

    # Optional array params: if/else dispatch — self->handle assigned inside.
    scalar_call_str = create_call_args  # scalars-only at this point (opt_arr excluded)
    for oname, oact, ondim, onpy, oalt_fn in opt_arr_ip:
        odisp = _ctype_display(oact)
        if ondim == 2:
            aapb_lines.append(
                f"    if ({oname}_obj && {oname}_obj != Py_None) {{\n"
                f"        PyArrayObject *{oname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"            {oname}_obj, {onpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"        if (!{oname}_arr) {{ return -1; }}\n"
                f"        if (PyArray_NDIM({oname}_arr) != 2) {{\n"
                f"            PyErr_SetString(PyExc_ValueError,\n"
                f'                            "{oname} must be a 2-D array");\n'
                f"            Py_DECREF({oname}_arr); return -1;\n"
                f"        }}\n"
                f"        size_t {oname}_dim0 ="
                f" (size_t)PyArray_DIM({oname}_arr, 0);\n"
                f"        size_t {oname}_dim1 ="
                f" (size_t)PyArray_DIM({oname}_arr, 1);\n"
                f"        self->handle = {oalt_fn}(\n"
                f"            {oname}_dim0, {oname}_dim1,\n"
                f"            (const {odisp} *)PyArray_DATA({oname}_arr)"
                + (f",\n            {scalar_call_str}" if scalar_call_str else "")
                + f");\n"
                f"        Py_DECREF({oname}_arr);\n"
                f"    }} else {{\n"
                f"        self->handle ="
                f" {component}_create({scalar_call_str});\n"
                f"    }}\n"
            )
        else:
            aapb_lines.append(
                f"    if ({oname}_obj && {oname}_obj != Py_None) {{\n"
                f"        PyArrayObject *{oname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"            {oname}_obj, {onpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"        if (!{oname}_arr) {{ return -1; }}\n"
                f"        size_t {oname}_len ="
                f" (size_t)PyArray_SIZE({oname}_arr);\n"
                f"        self->handle = {oalt_fn}(\n"
                f"            {oname}_len,"
                f" (const {odisp} *)PyArray_DATA({oname}_arr)"
                + (f",\n            {scalar_call_str}" if scalar_call_str else "")
                + f");\n"
                f"        Py_DECREF({oname}_arr);\n"
                f"    }} else {{\n"
                f"        self->handle ="
                f" {component}_create({scalar_call_str});\n"
                f"    }}\n"
            )

    array_args_parse_block = "".join(aapb_lines)
    array_args_decref = "".join(f"    Py_DECREF({name}_arr);\n" for name in allocated)

    # create_line: the self->handle assignment.  Empty when a dispatch or
    # optional-array block already assigned self->handle inside its branches.
    if dispatch_meta or opt_arr_ip:
        create_line = ""
    else:
        create_line = (
            f"    self->handle = {component}_create({create_call_args});\n"
        )

    # ── pyi / test helpers ────────────────────────────────────────────────────

    _NP_PY_TYPE: dict[str, str] = {
        "float32": "np.float32",   "float64": "np.float64",
        "complex64": "np.complex64", "complex128": "np.complex128",
        "int8": "np.int8",   "int16": "np.int16",
        "int32": "np.int32", "int64": "np.int64",
        "uint8": "np.uint8", "uint16": "np.uint16",
        "uint32": "np.uint32", "uint64": "np.uint64",
        "uintp": "np.uintp", "intp": "np.intp",
    }

    pyi_parts: list[str] = (
        [f"{name}: npt.ArrayLike" for name, _ in _aa]
        + [f"{aname}: npt.ArrayLike" for aname, _, __, ___ in arr_ip]
        + [f'{sname}: str = "{sdflt}"' for sname, _, sdflt in str_enum_ip]
        + [f"{oname}: npt.ArrayLike | None = None" for oname, *_ in opt_arr_ip]
        + [
            f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
            for name, ct, dflt, *_ in scalar_ip
        ]
    )
    init_params_pyi = ", ".join(pyi_parts)

    pyi_doc_sections: list[str] = []
    if _aa:
        pyi_doc_sections.append("\n".join(
            f"    {name} : array-like\n        {dt} coefficients."
            for name, dt in _aa
        ))
    if arr_ip:
        pyi_doc_sections.append("\n".join(
            f"    {aname} : array-like"
            f"{', shape (rows, cols)' if andim == 2 else ''}\n"
            f"        {_ctype_display(act)} {'matrix' if andim == 2 else 'array'}."
            for aname, act, andim, _ in arr_ip
        ))
    if str_enum_ip:
        pyi_doc_sections.append("\n".join(
            f'    {sname} : str, default "{sdflt}"\n'
            f"        One of: {', '.join(choices)}."
            for sname, choices, sdflt in str_enum_ip
        ))
    if opt_arr_ip:
        pyi_doc_sections.append("\n".join(
            f"    {oname} : array-like or None, optional"
            f"{', shape (rows, cols)' if ondim == 2 else ''}\n"
            f"        {_ctype_display(oact)} array; when supplied {oalt_fn}"
            f" is called instead of the default constructor."
            for oname, oact, ondim, _, oalt_fn in opt_arr_ip
        ))
    if scalar_ip:
        pyi_doc_sections.append("\n".join(
            f"    {name} : {_CTYPE_META[ct]['py_type']},"
            f" default {_py_default(ct, dflt)}\n"
            f"        {name} constructor parameter."
            for name, ct, dflt, *_ in scalar_ip
        ))
    pyi_param_docs = "\n".join(pyi_doc_sections) or "    (none)"

    py_create_parts: list[str] = []
    for _, dt in _aa:
        py_create_parts.append(f"np.zeros(1, dtype={_NP_PY_TYPE.get(dt, 'np.float32')})")
    for aname, act, andim, _ in arr_ip:
        dt = _CTYPE_TO_DTYPE.get(act, "float32")
        npt = _NP_PY_TYPE.get(dt, "np.float32")
        py_create_parts.append(
            f"np.zeros((1, 1), dtype={npt})" if andim == 2
            else f"np.zeros(1, dtype={npt})"
        )
    py_create_parts += [f'"{sdflt}"' for _, _, sdflt in str_enum_ip]
    # Optional array params are omitted from py_create_args (they default to None).
    py_create_parts += [_py_default(ct, dflt) for _, ct, dflt, *_ in scalar_ip]
    py_create_args = ", ".join(py_create_parts)

    c_create_args = ", ".join(c_create_parts_ordered)

    test_obj = f"        obj = {Component}({py_create_args})"

    return {
        "create_params": create_params,
        "create_param_docs": create_param_docs,
        "init_kwlist": init_kwlist,
        "init_locals": init_locals,
        "init_post_parse": init_post_parse,
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
        "init_parse_block": init_parse_block,
        "array_args_parse_block": array_args_parse_block,
        "array_args_decref": array_args_decref,
        "create_line": create_line,
        "create_call_args": create_call_args,
        "init_params_pyi": init_params_pyi,
        "pyi_param_docs": pyi_param_docs,
        "py_create_args": py_create_args,
        "c_create_args": c_create_args,
        "bench_create_stmt": (
            f"    {component}_state_t *obj"
            f" = {component}_create({c_create_args});"
            if c_create_args
            else (
                f"    /* TODO: {component}_state_t *obj"
                f" = {component}_create(...); */"
            )
        ),
        "bench_destroy_stmt": f"    {component}_destroy(obj);",
        "getter_setter_test_py": (
            test_obj
            + "\n        pass  # no auto-state; add assertions for your fields"
        ),
        "reset_test_py": (
            test_obj
            + "\n        pass  # no auto-state; add assertions for your reset"
        ),
    }


def _doctest_safe_output(ctype: str, default: str) -> str | None:
    """Return the expected Python repr for a getter's default, or None if unsafe.

    Only returns a value when the default round-trips exactly through the C type
    so the doctest output is predictable without knowing float rounding details.
    """
    kind = _CTYPE_META[ctype]["kind"]
    if kind == "int":
        val = _py_default(ctype, default)
        try:
            int(val)  # reject "0L", "0U", etc.
            return val
        except ValueError:
            return None
    if kind == "float":
        s = default.rstrip("fF")
        try:
            v = float(s)
            if v == int(v):  # 0.0, 1.0, 2.0, … are exactly representable
                return repr(v)  # "0.0", "1.0", …
        except ValueError:
            pass
        return None
    if kind == "complex":
        return "0j"
    return None


def _pyi_examples_block(
    scalar_vars: list[tuple[str, str, str]],
    has_array_args: bool,
    import_line: str,
    py_create_args: str,
    Component: str,
) -> str:
    """Build an indented ``Examples`` section for a .pyi class docstring.

    Returns an empty string when no doctest-safe getter examples exist.
    The returned string ends with a trailing newline and is ready to embed
    directly before the closing ``\"\"\"`` in the class docstring.
    """
    getter_pairs: list[tuple[str, str]] = []
    for name, ct, dflt in scalar_vars:
        out = _doctest_safe_output(ct, dflt)
        if out is not None:
            getter_pairs.append((name, out))

    lines: list[str] = [
        "    Examples",
        "    --------",
        "    Create with defaults:",
        "",
    ]
    if has_array_args:
        lines.append("    >>> import numpy as np")
    lines.append(f"    >>> {import_line}")
    lines.append(f"    >>> obj = {Component}({py_create_args})")

    for name, out in getter_pairs[:3]:
        lines.append(f"    >>> obj.get_{name}()")
        lines.append(f"    {out}")

    if getter_pairs:
        first_name, first_out = getter_pairs[0]
        first_ct = next(ct for n, ct, _ in scalar_vars if n == first_name)
        kind = _CTYPE_META[first_ct]["kind"]
        set_val = (
            "0"
            if (kind == "int" and first_out != "0")
            else ("42" if kind == "int" else "0.0" if first_out != "0.0" else "1.0")
        )
        lines += [
            "",
            "    Reset restores defaults:",
            "",
            f"    >>> obj.set_{first_name}({set_val})",
            "    >>> obj.reset()",
            f"    >>> obj.get_{first_name}()",
            f"    {first_out}",
        ]

    lines.append("")
    return "\n".join(lines) + "\n"


def make_state_ctx(
    component: str,
    Component: str,
    state_vars: list[tuple[str, str, str]],
    array_args: list[tuple[str, str]] = (),
    roles: dict[str, str] | None = None,
    no_state: bool = False,
    init_params: list[tuple] = (),
    init_post_parse_impl: str = "",
) -> dict[str, str]:
    """Return template context keys derived from the state variable list.

    Each entry in state_vars is (name, ctype, default), where default is a
    C literal used for both reset and as the Python __init__ default value.
    Array types like 'float[64]' are always zero-initialised and do not appear
    as constructor parameters.

    array_args is a list of (name, dtype) pairs from --array-arg, e.g.
    [("h", "float32")].  Each becomes a required positional constructor
    argument: const <ctype> *name, size_t name_len.  Array args appear before
    scalar args in both the kwlist and the create() signature.

    roles is a dict mapping state-var name to "state" (default) or "config".
    Config fields are preserved on reset() — they represent construction-time
    parameters (e.g. filter coefficients, sample rate) that should survive a
    soft-reset of runtime state (e.g. phase accumulator, filter history).
    """
    if no_state:
        _ns_reset_fn = f"{Component}Obj_reset"
        base = {
            "ComponentW": f"{Component}Obj",
            "state_struct_fields": "    /* <<IMPLEMENT: add fields >> */",
            "create_params": "void",
            "create_param_docs": " * @param (none)  Caller is responsible for all state management.",
            "getter_setter_decls": "",
            "create_assignments": "    /* <<IMPLEMENT: initialise state >> */",
            "reset_assignments": "    /* <<IMPLEMENT: restore defaults >> */",
            "destroy_impl": "    /* <<IMPLEMENT: free resources >> */\n",
            "getter_setter_impls": "",
            "init_kwlist": "NULL",
            "init_locals": "",
            "init_post_parse": "",
            "init_parse_fmt": "|",
            "init_parse_args": "",
            "init_parse_block": "    (void)args;\n    (void)kwds;\n",
            "create_call_args": "",
            "getter_setter_methods_c": "",
            "getter_setter_pymethoddef": "",
            "init_params_pyi": "",
            "pyi_param_docs": "    (none)",
            "pyi_examples": "",
            "getter_setter_stubs_pyi": "",
            "py_create_args": "",
            "getter_setter_test_py": "        pass  # no auto-state; add assertions for your fields",
            "reset_test_py": "        pass  # no auto-state; add assertions for your reset",
            "getter_setter_test_py_pure": "    pass  # no auto-state; add assertions for your fields",
            "reset_test_py_pure": "    pass  # no auto-state; add assertions for your reset",
            "c_create_args": "",
            "bench_create_stmt": (
                f"    /* TODO: {component}_state_t *obj"
                f" = {component}_create(...); */"
            ),
            "bench_destroy_stmt": "",
            "getter_setter_test_c": "",
            "reset_test_c": f"    /* reset */\n    {component}_reset(obj);",
            "array_args_parse_block": "",
            "array_args_decref": "",
            "create_line": (
                f"    self->handle = {component}_create();\n"
            ),
            "method_decls": "",
            "extra_buf_fields": "",
            "extra_buf_free": "",
            "extra_buf_alloc": "",
            "extra_methods_c": "",
            "extra_methods_pymethoddef": "",
            "getset_def": "",
            "tp_getset_decl": "",
            "property_decls": "",
            "property_struct_fields": "",
            "builtin_reset_c": (
                f"static PyObject *\n"
                f"{_ns_reset_fn}({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {component}_reset(self->handle);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            ),
            "builtin_reset_pmd": (
                f'    {{"reset",    (PyCFunction){_ns_reset_fn},    METH_NOARGS,\n'
                f'     "Reset state to post-create defaults."}},\n'
            ),
            "builtin_reset_decl": (
                f"/**\n"
                f" * @brief Reset {Component} to its post-create state.\n"
                f" * @param state  Must be non-NULL.\n"
                f" */\n"
                f"void {component}_reset({component}_state_t *state);"
            ),
        }
        if init_params or array_args:
            base.update(
                _build_no_state_init_ctx(
                    component,
                    Component,
                    list(init_params),
                    list(array_args),
                    init_post_parse_impl=init_post_parse_impl,
                )
            )
        return base

    if roles is None:
        roles = {}
    for name, ct, _ in state_vars:
        if not is_valid_type(ct):
            supported = ", ".join(sorted(SUPPORTED_TYPES))
            raise ValueError(
                f"unsupported type '{ct}' for '{name}'. Supported: {supported}"
            )

    # Split into scalar vars (in _CTYPE_META) and array vars.
    scalar_vars = [(n, ct, dflt) for n, ct, dflt in state_vars if ct in _CTYPE_META]
    array_info: list[tuple[str, str, int]] = []  # (name, elem_ctype, size)
    for n, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            array_info.append((n, parsed[0], parsed[1]))

    # ── CORE_H: state_struct_fields ──────────────────────────────────────────

    struct_field_lines = []
    for name, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            struct_field_lines.append(f"    {parsed[0]} {name}[{parsed[1]}];")
        else:
            struct_field_lines.append(f"    {ct} {name};")
    state_struct_fields = "\n".join(struct_field_lines)

    # Array args (from --array-arg) go first in create() signature/kwlist.
    _aa = list(array_args)  # [(name, dtype), ...]
    _aa_ctypes = [(_ARRAY_DTYPE[dt][0], _ARRAY_DTYPE[dt][1]) for _, dt in _aa]

    arr_param_parts = [
        f"const {ct} *{name}, size_t {name}_len"
        for (name, _), (ct, __) in zip(_aa, _aa_ctypes)
    ]
    scalar_param_parts = [f"{ct} {name}" for name, ct, _ in scalar_vars]
    all_param_parts = arr_param_parts + scalar_param_parts
    create_params = ", ".join(all_param_parts) or "void"

    arr_doc_parts = [
        f" * @param {name}  Input {dt} array (length passed as {name}_len)."
        for (name, dt) in _aa
    ]
    scalar_doc_parts = [
        f" * @param {name}  Initial {name} (default: {dflt})."
        for name, _, dflt in scalar_vars
    ]
    all_docs = arr_doc_parts + scalar_doc_parts
    create_param_docs = (
        "\n".join(all_docs) or " * @param (none)  All array fields initialise to zero."
    )

    # ── CORE_H: getter_setter_decls ──────────────────────────────────────────

    decl_parts = []
    for name, ct, _ in scalar_vars:
        decl_parts.append(
            f"/**\n"
            f" * @brief Get current {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" */\n"
            f"{ct} {component}_get_{name}(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param val    New value.\n"
            f" */\n"
            f"void {component}_set_{name}({component}_state_t *state, {ct} val);"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param dest   Output buffer of length {size}.\n"
            f" */\n"
            f"void {component}_get_{name}(const {component}_state_t *state, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Get a read-only pointer to {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @return Pointer valid until {component}_destroy() is called.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param src    Source buffer of length {size}.\n"
            f" */\n"
            f"void {component}_set_{name}({component}_state_t *state, const {elem_ct} *src);"
        )
    getter_setter_decls = "\n\n".join(decl_parts)

    # ── CORE_C: assignments ──────────────────────────────────────────────────

    create_assign_lines = [f"    obj->{n} = {n};" for n, _, _ in scalar_vars]
    for name, _, size in array_info:
        create_assign_lines.append(
            f"    memset(obj->{name}, 0, sizeof(obj->{name}));"
        )
    create_assignments = "\n".join(create_assign_lines)

    reset_assign_lines = []
    for n, _, dflt in scalar_vars:
        if roles.get(n, "state") == "config":
            reset_assign_lines.append(
                f"    /* {n}: config field — preserved on reset */"
            )
        else:
            reset_assign_lines.append(f"    state->{n} = {dflt};")
    for name, _, size in array_info:
        if roles.get(name, "state") == "config":
            reset_assign_lines.append(
                f"    /* {name}[]: config field — preserved on reset */"
            )
        else:
            reset_assign_lines.append(
                f"    memset(state->{name}, 0, sizeof(state->{name}));"
            )
    reset_assignments = "\n".join(reset_assign_lines)

    # ── CORE_C: getter_setter_impls ──────────────────────────────────────────

    impl_parts = []
    for name, ct, _ in scalar_vars:
        impl_parts.append(
            f"{ct}\n"
            f"{component}_get_{name}(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({component}_state_t *state, {ct} val)\n"
            f"{{\n"
            f"    state->{name} = val;\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}(const {component}_state_t *state, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, state->{name}, {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({component}_state_t *state, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy(state->{name}, src, {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    getter_setter_impls = "\n\n".join(impl_parts)

    # ── EXT_C: init parse block (array args first, then scalars) ────────────

    # kwlist: array arg names first (required), then scalar names (optional)
    kwlist_items = (
        [f'"{name}"' for name, _ in _aa]
        + [f'"{name}"' for name, _, __ in scalar_vars]
        + ["NULL"]
    )
    init_kwlist = ", ".join(kwlist_items)

    # Locals: PyObject* for each array arg, then scalar locals
    local_lines = [f"    PyObject *{name}_obj = NULL;" for name, _ in _aa]
    post_lines = []
    parse_args = [f"&{name}_obj" for name, _ in _aa]
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            local_lines.append(
                f"    {meta['parse_type']} {name}_raw = {meta['parse_zero']};"
            )
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            parse_args.append(f"&{name}_raw")
        else:
            local_lines.append(f"    {ct} {name} = {dflt};")
            parse_args.append(f"&{name}")
    init_locals = "\n".join(local_lines)
    init_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""

    # Format: required O per array arg, then optional scalars after |
    array_fmt = "O" * len(_aa)
    scalar_fmt_str = "".join(_CTYPE_META[ct]["fmt"] for _, ct, __ in scalar_vars)
    if scalar_vars:
        init_parse_fmt = array_fmt + "|" + scalar_fmt_str
    else:
        init_parse_fmt = array_fmt or "|"  # "|" means no args required

    init_parse_args = ", ".join(parse_args)

    # create_call_args: array (ptr, len) pairs first, then scalars
    arr_call_parts = [
        f"(const {ct} *)PyArray_DATA({name}_arr), {name}_len"
        for (name, _), (ct, __) in zip(_aa, _aa_ctypes)
    ]
    scalar_call_parts = [name for name, _, __ in scalar_vars]
    create_call_args = ", ".join(arr_call_parts + scalar_call_parts)

    if _aa or scalar_vars:
        post_str = ("\n".join(post_lines) + "\n") if post_lines else ""
        init_parse_block = (
            f"    static char *kwlist[] = {{{init_kwlist}}};\n"
            f"{init_locals}\n"
            f"\n"
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{init_parse_fmt}", kwlist,\n'
            f"                                     {init_parse_args}))\n"
            f"        return -1;\n"
            f"{post_str}"
        )
    else:
        init_parse_block = "    (void)args;\n    (void)kwds;\n"

    # ── EXT_C: array-arg post-parse (FROM_OTF + size, after kwarg parse) ────

    aapb_lines: list[str] = []
    already_allocated: list[str] = []
    for (name, _), (ct, npy_enum) in zip(_aa, _aa_ctypes):
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in already_allocated)
        aapb_lines.append(
            f"    PyArrayObject *{name}_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        {name}_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!{name}_arr) {{{cleanup} return -1; }}\n"
            f"    size_t {name}_len = (size_t)PyArray_SIZE({name}_arr);\n"
        )
        already_allocated.append(name)
    array_args_parse_block = "".join(aapb_lines)

    array_args_decref = "".join(f"    Py_DECREF({name}_arr);\n" for name, _ in _aa)

    # c_create_args: for C test templates — pass NULL, 0 per array arg
    c_arr_call_parts = ["NULL, 0" for _ in _aa]
    c_create_args = ", ".join(c_arr_call_parts + [dflt for _, _, dflt in scalar_vars])

    # py_create_args: for Python test/bench/pyi templates
    _NP_PY_TYPE: dict[str, str] = {
        "float32": "np.float32",
        "float64": "np.float64",
        "complex64": "np.complex64",
        "complex128": "np.complex128",
        "int8": "np.int8",
        "int16": "np.int16",
        "int32": "np.int32",
        "int64": "np.int64",
        "uint8": "np.uint8",
        "uint16": "np.uint16",
        "uint32": "np.uint32",
        "uint64": "np.uint64",
    }
    py_arr_args = [f"np.zeros(1, dtype={_NP_PY_TYPE[dt]})" for _, dt in _aa]

    # ── EXT_C: getter/setter methods (scalars + arrays) ──────────────────────

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )
    method_parts = []
    for name, ct, _ in scalar_vars:
        meta = _CTYPE_META[ct]
        to_py = meta["to_py"](f"{component}_get_{name}(self->handle)")
        getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    return {to_py};\n"
            f"}}"
        )
        if meta.get("parse_type"):
            setter = (
                f"static PyObject *\n"
                f"{Component}_set_{name}(\n"
                f"    {Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"    {meta['parse_type']} v_raw = {meta['parse_zero']};\n"
                f'    if (!PyArg_ParseTuple(args, "{meta["fmt"]}", &v_raw))\n'
                f"        return NULL;\n"
                f"    {ct} v = {meta['to_c']('v')};\n"
                f"    {component}_set_{name}(self->handle, v);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        else:
            setter = (
                f"static PyObject *\n"
                f"{Component}_set_{name}(\n"
                f"    {Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"    {ct} v = {meta['zero']};\n"
                f'    if (!PyArg_ParseTuple(args, "{meta["fmt"]}", &v))\n'
                f"        return NULL;\n"
                f"    {component}_set_{name}(self->handle, v);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        method_parts.append(getter + "\n\n" + setter)

    for name, elem_ct, size in array_info:
        npy_enum = _NP_DTYPE_ENUM[elem_ct]
        ptr_cast = f"({elem_ct} *)"
        const_ptr_cast = f"(const {elem_ct} *)"

        copy_getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    npy_intp dims[] = {{{size}}};\n"
            f"    PyObject *arr = PyArray_SimpleNew(1, dims, {npy_enum});\n"
            f"    if (!arr) return NULL;\n"
            f"    {component}_get_{name}(self->handle,\n"
            f"        {ptr_cast}PyArray_DATA((PyArrayObject *)arr));\n"
            f"    return arr;\n"
            f"}}"
        )
        view_getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}_view(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    npy_intp dims[] = {{{size}}};\n"
            f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
            f"        1, dims, {npy_enum},\n"
            f"        (void *){component}_get_{name}_view(self->handle));\n"
            f"    if (!arr) return NULL;\n"
            f"    PyArray_CLEARFLAGS((PyArrayObject *)arr, NPY_ARRAY_WRITEABLE);\n"
            f"    return arr;\n"
            f"}}"
        )
        array_setter = (
            f"static PyObject *\n"
            f"{Component}_set_{name}(\n"
            f"    {Component}Object *self, PyObject *args)\n"
            f"{{\n"
            f"{guard}"
            f"    PyObject *in_obj = NULL;\n"
            f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
            f"        return NULL;\n"
            f"    PyArrayObject *arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        in_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!arr) return NULL;\n"
            f"    if (PyArray_SIZE(arr) != {size}) {{\n"
            f"        PyErr_Format(PyExc_ValueError,\n"
            f'            "{name} requires exactly {size} elements, got %zd",\n'
            f"            (Py_ssize_t)PyArray_SIZE(arr));\n"
            f"        Py_DECREF(arr);\n"
            f"        return NULL;\n"
            f"    }}\n"
            f"    {component}_set_{name}(self->handle,\n"
            f"        {const_ptr_cast}PyArray_DATA(arr));\n"
            f"    Py_DECREF(arr);\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        )
        method_parts.append(copy_getter + "\n\n" + view_getter + "\n\n" + array_setter)

    getter_setter_methods_c = "\n\n".join(method_parts)

    # ── EXT_C: PyMethodDef ───────────────────────────────────────────────────

    pmd_lines = []
    for name, _, __ in scalar_vars:
        pmd_lines += [
            f'    {{"get_{name}",',
            f"     (PyCFunction){Component}_get_{name}, METH_NOARGS,",
            f'     "Get {name}."}},',
            f'    {{"set_{name}",',
            f"     (PyCFunction){Component}_set_{name}, METH_VARARGS,",
            f'     "Set {name}."}},',
        ]
    for name, elem_ct, size in array_info:
        py_type = _CTYPE_META[elem_ct]["py_type"]
        pmd_lines += [
            f'    {{"get_{name}",',
            f"     (PyCFunction){Component}_get_{name}, METH_NOARGS,",
            f'     "Return a copy of {name} as {py_type} ndarray (length {size})."}},',
            f'    {{"get_{name}_view",',
            f"     (PyCFunction){Component}_get_{name}_view, METH_NOARGS,",
            f'     "Return read-only view of {name}. Valid until destroy()."}},',
            f'    {{"set_{name}",',
            f"     (PyCFunction){Component}_set_{name}, METH_VARARGS,",
            f'     "Set {name} from {py_type} array of length {size}."}},',
        ]
    getter_setter_pymethoddef = "\n".join(pmd_lines)
    if pmd_lines:
        getter_setter_pymethoddef += "\n"

    # ── PYI ──────────────────────────────────────────────────────────────────

    init_params_pyi = ", ".join(
        f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
        for name, ct, dflt in scalar_vars
    )

    pyi_param_docs = "\n".join(
        f"    {name} : {_CTYPE_META[ct]['py_type']}, default {_py_default(ct, dflt)}\n"
        f"        {name} state variable."
        for name, ct, dflt in scalar_vars
    )

    stub_groups: list[str] = []
    for name, ct, _ in scalar_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        stub_groups.append(
            "\n".join(
                [
                    f"    def get_{name}(self) -> {py_type}:",
                    f'        """Return current {name}."""',
                    "",
                    f"    def set_{name}(self, value: {py_type}) -> None:",
                    f'        """Set {name}."""',
                ]
            )
        )
    for name, elem_ct, size in array_info:
        py_type = _CTYPE_META[elem_ct]["py_type"]
        stub_groups.append(
            "\n".join(
                [
                    f"    def get_{name}(self) -> NDArray[{py_type}]:",
                    f'        """Return a copy of {name} (length {size}, dtype {py_type})."""',
                    "",
                    f"    def get_{name}_view(self) -> NDArray[{py_type}]:",
                    f'        """Return a read-only view of {name}.',
                    "",
                    "        Backed by the component's internal state buffer.",
                    "        **Do not use after destroy().**",
                    '        """',
                    "",
                    f"    def set_{name}(self, value: NDArray[{py_type}]) -> None:",
                    f'        """Set {name} from a {py_type} array of length {size}."""',
                ]
            )
        )
    getter_setter_stubs_pyi = (
        "\n" + "\n\n".join(stub_groups) + "\n" if stub_groups else ""
    )

    # ── Shared: create args ───────────────────────────────────────────────────

    # Array args appear first; scalar args follow.
    py_create_args = ", ".join(
        py_arr_args + [_py_default(ct, dflt) for _, ct, dflt in scalar_vars]
    )
    # c_create_args already computed above (NULL, 0 per array arg + scalar defaults)

    # ── PYI Examples ─────────────────────────────────────────────────────────
    pyi_examples = (
        _pyi_examples_block(
            scalar_vars,
            bool(py_arr_args),
            "from <<package>> import <<Component>>",
            py_create_args,
            Component,
        )
        if scalar_vars
        else ""
    )

    # ── PYTEST: getter_setter_test_py ─────────────────────────────────────────

    gs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        iv = _py_default(ct, dflt)
        sv = _py_sample_val(meta)
        if meta["kind"] == "float":
            gs_lines += [
                f"        assert obj.get_{name}() == _approx({iv})",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == _approx({sv})",
            ]
        else:
            gs_lines += [
                f"        assert obj.get_{name}() == {iv}",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == {sv}",
            ]
    for name, elem_ct, size in array_info:
        np_dtype = _CTYPE_META[elem_ct]["py_type"].replace("np.", "")
        gs_lines += [
            f"        _arr = np.zeros({size}, dtype=np.{np_dtype})",
            "        _arr[0] = 1",
            f"        obj.set_{name}(_arr)",
            f"        _got = obj.get_{name}()",
            "        assert _got[0] == _approx(1)",
            f"        _view = obj.get_{name}_view()",
            "        assert not _view.flags['WRITEABLE']",
            "        assert _view[0] == _approx(1)",
        ]
    getter_setter_test_py = "\n".join(gs_lines)

    # ── PYTEST: reset_test_py ─────────────────────────────────────────────────

    rs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, _ in scalar_vars:
        rs_lines.append(f"        obj.set_{name}({_py_sample_val(_CTYPE_META[ct])})")
    for name, elem_ct, size in array_info:
        np_dtype = _CTYPE_META[elem_ct]["py_type"].replace("np.", "")
        rs_lines.append(f"        obj.set_{name}(np.ones({size}, dtype=np.{np_dtype}))")
    rs_lines.append("        obj.reset()")
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        iv = _py_default(ct, dflt)
        if meta["kind"] == "float":
            rs_lines.append(f"        assert obj.get_{name}() == _approx({iv})")
        else:
            rs_lines.append(f"        assert obj.get_{name}() == {iv}")
    for name, elem_ct, _ in array_info:
        rs_lines.append(f"        assert obj.get_{name}()[0] == _approx(0)")
    reset_test_py = "\n".join(rs_lines)

    # ── CTEST: getter_setter_test_c ───────────────────────────────────────────

    cgs_lines: list[str] = []
    for name, ct, dflt in scalar_vars:
        sv = _c_set_val(ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            f"    CHECK({component}_get_{name}(obj) == {dflt});",
            f"    {component}_set_{name}(obj, {sv});",
            f"    CHECK({component}_get_{name}(obj) == {sv});",
            "",
        ]
    for name, elem_ct, size in array_info:
        sv = _c_set_val(elem_ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            "    {",
            f"        {elem_ct} src[{size}], dst[{size}];",
            f"        src[0] = {sv};",
            f"        {component}_set_{name}(obj, src);",
            f"        {component}_get_{name}(obj, dst);",
            f"        CHECK(dst[0] == {sv});",
            "    }",
            "",
        ]
    getter_setter_test_c = "\n".join(cgs_lines).rstrip()

    # ── CTEST: reset_test_c ───────────────────────────────────────────────────

    rst_lines = ["    /* reset restores defaults */"]
    for name, ct, _ in scalar_vars:
        rst_lines.append(f"    {component}_set_{name}(obj, {_c_set_val(ct)});")
    for name, elem_ct, size in array_info:
        sv = _c_set_val(elem_ct)
        rst_lines += [
            "    {",
            f"        {elem_ct} ones[{size}];",
            f"        size_t i_; for (i_ = 0; i_ < {size}; i_++) ones[i_] = {sv};",
            f"        {component}_set_{name}(obj, ones);",
            "    }",
        ]
    rst_lines.append(f"    {component}_reset(obj);")
    for name, _, dflt in scalar_vars:
        rst_lines.append(f"    CHECK({component}_get_{name}(obj) == {dflt});")
    for name, elem_ct, size in array_info:
        zero = _CTYPE_META[elem_ct]["zero"]
        rst_lines += [
            "    {",
            f"        {elem_ct} buf[{size}];",
            f"        {component}_get_{name}(obj, buf);",
            f"        CHECK(buf[0] == {zero});",
            "    }",
        ]
    reset_test_c = "\n".join(rst_lines)

    return {
        "state_struct_fields": state_struct_fields,
        "create_params": create_params,
        "create_param_docs": create_param_docs,
        "getter_setter_decls": getter_setter_decls,
        "create_assignments": create_assignments,
        "reset_assignments": reset_assignments,
        "destroy_impl": "",
        "getter_setter_impls": getter_setter_impls,
        "init_kwlist": init_kwlist,
        "init_locals": init_locals,
        "init_post_parse": init_post_parse,
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
        "init_parse_block": init_parse_block,
        "create_call_args": create_call_args,
        "create_line": (
            f"    self->handle = {component}_create({create_call_args});\n"
        ),
        "getter_setter_methods_c": getter_setter_methods_c,
        "getter_setter_pymethoddef": getter_setter_pymethoddef,
        "init_params_pyi": init_params_pyi,
        "pyi_param_docs": pyi_param_docs,
        "pyi_examples": pyi_examples,
        "getter_setter_stubs_pyi": getter_setter_stubs_pyi,
        "py_create_args": py_create_args,
        "getter_setter_test_py": getter_setter_test_py,
        "reset_test_py": reset_test_py,
        "getter_setter_test_py_pure": (
            getter_setter_test_py.replace("        ", "    ").replace(
                "_approx(", "pytest.approx("
            )
        ),
        "reset_test_py_pure": (
            reset_test_py.replace("        ", "    ").replace(
                "_approx(", "pytest.approx("
            )
        ),
        "c_create_args": c_create_args,
        "bench_create_stmt": (
            f"    {component}_state_t *obj"
            f" = {component}_create({c_create_args});"
            if c_create_args
            else (
                f"    /* TODO: {component}_state_t *obj"
                f" = {component}_create(...); */"
            )
        ),
        "bench_destroy_stmt": f"    {component}_destroy(obj);",
        "getter_setter_test_c": getter_setter_test_c,
        "reset_test_c": reset_test_c,
        # ComponentW is the wrapper-function prefix. Equal to Component for
        # normal components; for no_state=True the no_state branch sets it
        # to "{Component}Obj" to avoid clashing with user-supplied C API
        # names (e.g. Resampler_destroy vs. Resampler_destroy(state_t *)).
        "ComponentW": Component,
        # Array-arg placeholders (empty when no --array-arg used).
        "array_args_parse_block": array_args_parse_block,
        "array_args_decref": array_args_decref,
        # Extra-method placeholders: callers override via make_methods_ctx().
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        # Property placeholders: callers override via make_properties_ctx().
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
        "builtin_reset_c": (
            f"static PyObject *\n"
            f"{Component}_reset({Component}Object *self,"
            f" PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"    if (!self->handle) {{\n"
            f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    {component}_reset(self->handle);\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        ),
        "builtin_reset_pmd": (
            f'    {{"reset",    (PyCFunction){Component}_reset,    METH_NOARGS,\n'
            f'     "Reset state to post-create defaults."}},\n'
        ),
        # Callers may zero this out via make_methods_ctx() when user defines
        # a custom "reset" method — suppresses the no-arg declaration from
        # the header so the user's parameterised declaration is the only one.
        "builtin_reset_decl": (
            f"/**\n"
            f" * @brief Reset {Component} to its post-create state.\n"
            f" * @param state  Must be non-NULL.\n"
            f" */\n"
            f"void {component}_reset({component}_state_t *state);"
        ),
    }


def _make_gs_decls_impls(
    component: str,
    scalar_vars: list[tuple[str, str, str]],
    array_info: list[tuple[str, str, int]],
    type_suffix: str,
    ptr_name: str,
) -> tuple[str, str]:
    """Generate getter/setter C declarations and implementations.

    type_suffix: 'state' or 'params'  (produces <comp>_<type_suffix>_t)
    ptr_name:    'state' or 'params'  (the C variable name)
    """
    full_type = f"{component}_{type_suffix}_t"
    decl_parts = []
    for name, ct, _ in scalar_vars:
        decl_parts.append(
            f"/**\n"
            f" * @brief Get current {name}.\n"
            f" */\n"
            f"{ct} {component}_get_{name}(const {full_type} *{ptr_name});\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name}.\n"
            f" */\n"
            f"void {component}_set_{name}({full_type} *{ptr_name}, {ct} {name});"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" */\n"
            f"void {component}_get_{name}(const {full_type} *{ptr_name}, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Return a read-only pointer to {name}.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view(const {full_type} *{ptr_name});\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" */\n"
            f"void {component}_set_{name}({full_type} *{ptr_name}, const {elem_ct} *src);"
        )
    decls = "\n\n".join(decl_parts)

    impl_parts = []
    for name, ct, _ in scalar_vars:
        impl_parts.append(
            f"{ct}\n"
            f"{component}_get_{name}(const {full_type} *{ptr_name})\n"
            f"{{\n"
            f"    return {ptr_name}->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({full_type} *{ptr_name}, {ct} {name})\n"
            f"{{\n"
            f"    {ptr_name}->{name} = {name};\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}(const {full_type} *{ptr_name}, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, {ptr_name}->{name}, {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view(const {full_type} *{ptr_name})\n"
            f"{{\n"
            f"    return {ptr_name}->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({full_type} *{ptr_name}, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy({ptr_name}->{name}, src, {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    impls = "\n\n".join(impl_parts)
    return decls, impls


def make_perf_ctx(perf: bool) -> dict[str, str]:
    if perf:
        return {
            "perf_include": '#include "jm_perf.h"',
            "step_qualifier": "JM_FORCEINLINE JM_HOT",
            "omp_simd_hint": "    /* #pragma omp simd */\n",
        }
    return {
        "perf_include": "",
        "step_qualifier": "static inline",
        "omp_simd_hint": "",
    }


def _bench_method_block(component: str, m: dict) -> str:
    """Return a self-contained C bench timing block for method *m*.

    Returns an empty string when the method should not be benchmarked
    (``bench == False`` in the method dict, or ``variable_output`` methods
    whose output size is indeterminate at bench time).

    The generated block is wrapped in ``{}`` for scope isolation so that
    per-method locals (buffers, sink variables) do not conflict with each
    other or with the surrounding ``step()``/``steps()`` bench variables.

    Parameters
    ----------
    component : str
        Snake-case component name (e.g. ``"fir"``).
    m : dict
        Method dict with keys: name, arg_type, return_type, variable_output,
        batch, params.  Same shape as the dicts in ``[[comp.methods]]``.

    Returns
    -------
    str
        C source fragment (indented 4 spaces, blank line before/after) ready
        to paste into ``main()`` of the bench executable, or ``""`` to skip.
    """
    if m.get("bench") is False or m.get("variable_output"):
        return ""

    name: str = m["name"]
    arg_type: str = m.get("arg_type", "void")
    return_type: str = m.get("return_type", "float _Complex")
    batch: bool = m.get("batch", False)
    params: list[dict] = m.get("params", [])

    has_arg = arg_type != "void"
    has_ret = return_type != "void"
    is_array_arg = arg_type.endswith("[]")

    if has_arg:
        arg_elem = arg_type[:-2] if is_array_arg else arg_type
        arg_meta = _CTYPE_META[arg_elem]
        arg_disp = _ctype_display(arg_type)
        arg_elem_disp = _ctype_display(arg_elem)
        arg_zero = arg_meta["zero"]

    if has_ret:
        ret_meta = _CTYPE_META.get(return_type)
        ret_disp = _ctype_display(return_type) if ret_meta else return_type

    # Build zero-value extra-param args for scalar methods.
    param_args = ""
    for p in params:
        pt = p["type"]
        if is_array_param_type(pt):
            param_args += ", NULL, 0"
        else:
            pm = _CTYPE_META.get(pt, {})
            param_args += f", {pm.get('zero', '0')}"

    lines: list[str] = [f"    /* bench: {name}() */", "    {"]
    # Per-round times array (ITERATIONS is a compile-time macro constant).
    lines.append(f"        double _times_{name}[ITERATIONS];")

    if batch:
        # Buffer bench: one call per outer iteration, measures throughput.
        # Signature: void comp_name(state, [const arg_t *in,] size_t n, ret_t *out)
        if has_arg:
            lines += [
                f"        {arg_disp} *{name}_in ="
                f" ({arg_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_disp}));",
            ]
        ret_disp_b = _ctype_display(return_type)
        lines += [
            f"        {ret_disp_b} *{name}_out ="
            f" ({ret_disp_b} *)malloc("
            f"BENCH_N * sizeof({ret_disp_b}));",
        ]
        chk_vars = f"{name}_in && {name}_out" if has_arg else f"{name}_out"
        lines += [
            f"        if (!({chk_vars}))"
            f" {{ fprintf(stderr, \"OOM\\n\"); return 1; }}",
        ]
        in_arg = f" {name}_in," if has_arg else ""
        call = (
            f"{component}_{name}(obj,{in_arg} BENCH_N, {name}_out)"
        )
        # Warmup then per-round timing.
        lines += [
            f"        for (int i = 0; i < 4; i++)",
            f"            {call};",
            f"        for (int r = 0; r < ITERATIONS; r++) {{",
            f"            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {call};",
            f"            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            f"        }}",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
        lines.append(f"        free({name}_out);")

    elif is_array_arg:
        # Non-batch array-arg method: alloc input buffer, single call/iter.
        # Signature: ret_t comp_name(state, const elem_t *x, size_t x_len, ...)
        lines += [
            f"        {arg_elem_disp} *{name}_in ="
            f" ({arg_elem_disp} *)calloc("
            f"BENCH_N, sizeof({arg_elem_disp}));",
            f"        if (!{name}_in)"
            f" {{ fprintf(stderr, \"OOM\\n\"); return 1; }}",
        ]
        if has_ret:
            lines.append(
                f"        volatile {ret_disp} {name}_sink;"
            )
        sink = f"{name}_sink = " if has_ret else ""
        call = (
            f"{component}_{name}(obj, {name}_in, BENCH_N{param_args})"
        )
        lines += [
            f"        for (int i = 0; i < 4; i++)",
            f"            {sink}{call};",
            f"        for (int r = 0; r < ITERATIONS; r++) {{",
            f"            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {sink}{call};",
            f"            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            f"        }}",
            f"        free({name}_in);",
        ]

    else:
        # Scalar method: inner loop × BENCH_N per outer iteration.
        # Signature: ret_t comp_name(state, [arg_t x,] [param_t p, ...])
        if has_ret:
            lines.append(f"        volatile {ret_disp} {name}_sink;")
        sink = f"{name}_sink = " if has_ret else ""
        in_arg = f", {arg_zero}" if has_arg else ""
        call = f"{component}_{name}(obj{in_arg}{param_args})"
        lines += [
            f"        for (int i = 0; i < 16; i++) {sink}{call};",
            f"        for (int r = 0; r < ITERATIONS; r++) {{",
            f"            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            for (int i = 0; i < BENCH_N; i++)",
            f"                {sink}{call};",
            f"            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            f"        }}",
        ]

    # Register with bench harness and print mean throughput.
    add_line = (
        f"        jm_bench_add(&_bench, \"{name}\","
        f" _times_{name}, ITERATIONS, BENCH_N);"
    )
    lines += [
        add_line,
        f"        {{",
        f"            double _s = 0.0;",
        f"            for (int r = 0; r < ITERATIONS; r++)"
        f" _s += _times_{name}[r];",
        f'            printf("  {name}()  %8.1f MSa/s\\n",',
        f"                   (double)BENCH_N / (_s / ITERATIONS) / 1e6);",
        f"        }}",
    ]

    lines.append("    }")
    return "\n".join(lines)


def make_methods_ctx(
    component: str,
    Component: str,
    methods: list[dict],
    pkg: str = "",
    py_create_args: str = "",
    no_state: bool = False,
) -> dict[str, str]:
    """Generate template context keys for extra named methods.

    Each method dict has: name, arg_type ("void" or a _CTYPE_META key),
    return_type (a _CTYPE_META key), variable_output (bool),
    batch (bool), and optionally multi_output (list of additional return ctypes).

    batch=True generates a 1:1-rate array method:
      C: void comp_name(state_t *, [const arg_t *in,] size_t n, ret_t *out)
      Python: allocates output array each call with PyArray_SimpleNew.

    pkg and py_create_args are used in the generated PyMethodDef docstrings
    to produce working doctests; omitting them produces functional but
    package-anonymous examples.
    """
    _KIND_TO_PY: dict[str, str] = {
        "float": "float",
        "int": "int",
        "complex": "complex",
        "str": "str",
    }

    def _pyi_scalar(ctype: str) -> str:
        if ctype == "void":
            return "None"
        meta = _CTYPE_META.get(ctype)
        return _KIND_TO_PY.get(meta["kind"], "Any") if meta else "Any"

    def _pyi_ndarray(ctype: str) -> str:
        elem = ctype[:-2] if ctype.endswith("[]") else ctype
        meta = _CTYPE_META.get(elem)
        return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"

    _EMPTY: dict[str, str] = {
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        "pyi_extra_methods": "",
        "bench_methods_timing_block": "",
    }
    if not methods:
        return _EMPTY

    # For no_state objects, Python wrapper names must not collide with the
    # C API (e.g. Resampler_reset conflicts with Resampler_reset in core.h
    # when the component name starts with a capital letter).  Use the
    # {Component}Obj_ prefix so there is never a match.
    wrapper_prefix = f"{Component}Obj" if no_state else Component

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    decl_lines: list[str] = []
    buf_fields: list[str] = []
    buf_free: list[str] = []
    buf_alloc: list[str] = []
    method_c_parts: list[str] = []
    pmd_lines: list[str] = []
    pyi_lines: list[str] = []
    user_has_reset: bool = any(m["name"] == "reset" for m in methods)

    for m in methods:
        name: str = m["name"]
        arg_type: str = m.get("arg_type", "void")
        return_type: str = m.get("return_type", "float _Complex")
        variable_output: bool = m.get("variable_output", False)
        batch: bool = m.get("batch", False)
        multi_output: list[str] = m.get("multi_output", [])
        params: list[dict] = m.get("params", [])  # [{name, type}, ...]
        result_fields: list[dict] = m.get("result_fields", [])
        max_results: int = int(m.get("max_results", 64))
        none_on_empty: bool = m.get("none_on_empty", False)

        ret_disp = _ctype_display(return_type)
        _ret_elem = return_type[:-2] if return_type.endswith("[]") else return_type
        ret_meta = _CTYPE_META.get(_ret_elem)
        ret_np = _NP_ENUM.get(ret_meta["py_type"]) if ret_meta else "NPY_FLOAT"

        out_type: str | None = m.get("out_type")
        out_divisor: int = int(m.get("out_divisor", 1))
        # For variable_output: out_type (when set) specifies the output buffer
        # element type, overriding return_type.  Non-variable paths use
        # out_type differently (per-call allocated ndarray with out_divisor).
        _vo_out_elem = out_type if (variable_output and out_type) else return_type
        _vo_out_disp = _ctype_display(_vo_out_elem)
        _vo_out_meta = _CTYPE_META.get(_vo_out_elem)
        _vo_out_np = (
            _NP_ENUM.get(_vo_out_meta["py_type"]) if _vo_out_meta else "NPY_FLOAT"
        )
        has_params = bool(params)
        has_arg = arg_type != "void"
        if has_arg:
            arg_disp = _ctype_display(arg_type)
            _arg_elem = arg_type[:-2] if arg_type.endswith("[]") else arg_type
            arg_meta = _CTYPE_META[_arg_elem]
            arg_np = _NP_ENUM[arg_meta["py_type"]]

        # Doxygen for this method's _core.h declaration(s).
        _param_docs = " * @param state  Must be non-NULL.\n"
        if has_arg:
            _param_docs += f" * @param x      Input ({_ctype_display(arg_type)}).\n"
        for _p in params:
            _pdisp = _ctype_display(_p["type"])
            _param_docs += f" * @param {_p['name']}  {_pdisp} parameter.\n"
        _doc_ret_disp = _vo_out_disp if variable_output else ret_disp
        _ret_doc = (
            f" * @return Result ({_doc_ret_disp}).\n"
            if return_type != "void"
            else ""
        )
        _method_doc = f"/**\n * @brief {name}.\n *\n{_param_docs}{_ret_doc} */"
        _ndecl = len(decl_lines)  # index before this method adds declarations

        # Example input value for doctest — determined by arg_type.
        if not has_arg:
            _in_example = ""
            _in_dtype_str = ""
        elif arg_type.endswith("[]"):
            _elem = arg_type[:-2]
            _in_dtype_str = (
                _CTYPE_META[_elem]["py_type"] if _elem in _CTYPE_META else "np.float32"
            )
            _in_example = f"np.zeros(4, dtype={_in_dtype_str})"
        elif arg_type in _CTYPE_META:
            _in_dtype_str = _CTYPE_META[arg_type]["py_type"]
            _kind = _CTYPE_META[arg_type]["kind"]
            _in_example = _KIND_PY_TEST_VAL.get(_kind, "1")
        else:
            _in_dtype_str = "np.float32"
            _in_example = "x"
        _from_line = [f"    >>> from {pkg} import {Component}"] if pkg else []
        _obj_line = f"    >>> obj = {Component}({py_create_args})"

        # ── batch method (1:1-rate array transform, no pre-alloc buffer) ─────
        if batch:
            if has_arg:
                decl_lines.append(
                    f"void {component}_{name}({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n, {ret_disp} *out);"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out = PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"    {component}_{name}(self->handle,\n"
                    f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA((PyArrayObject *)out));\n"
                    f"    Py_DECREF(in_arr);\n"
                    f"    return out;\n"
                    f"}}"
                )
            else:
                decl_lines.append(
                    f"void {component}_{name}({component}_state_t *state,"
                    f" size_t n, {ret_disp} *out);"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"    Py_ssize_t n = 1;\n"
                    f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                    f"        return NULL;\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out = PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) return NULL;\n"
                    f"    {component}_{name}(self->handle,\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA((PyArrayObject *)out));\n"
                    f"    return out;\n"
                    f"}}"
                )
            method_c_parts.append(wrapper)
            _ret_np_str = _CTYPE_META[return_type]["py_type"].replace("np.", "")
            _batch_sig = f"{name}({'x' if has_arg else 'n'}) -> ndarray"
            _batch_doc_lines = [
                _batch_sig,
                "",
                f"1:1-rate batch transform. Returns an ndarray of dtype {_ret_np_str}.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            if has_arg:
                _batch_doc_lines += [
                    f"    >>> x = np.zeros(4, dtype={_in_dtype_str})",
                    f"    >>> y = obj.{name}(x)",
                ]
            else:
                _batch_doc_lines.append(f"    >>> y = obj.{name}(4)")
            _batch_doc_lines += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_ret_np_str}')",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name}, METH_VARARGS,\n'
                f"     {_build_ml_doc(_batch_doc_lines)}}},\n"
            )
            for _j in range(_ndecl, len(decl_lines)):
                decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]
            continue

        # ── declarations for _core.h ─────────────────────────────────────────
        if result_fields:
            # struct-list return: size_t comp_push(state, in, n_in, T *out, n)
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {ret_disp} *result, size_t max_results);"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}({component}_state_t *state,"
                    f" {ret_disp} *result, size_t max_results);"
                )
        elif variable_output:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out({component}_state_t *state);\n"
                    f"size_t {component}_{name}({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {_vo_out_disp} *out{extra_params});"
                )
            elif has_params:
                _vp_parts: list[str] = []
                for _p in params:
                    if is_array_param_type(_p["type"]):
                        _e = _ctype_display(array_elem_ctype(_p["type"]))
                        _vp_parts.append(f"const {_e} *{_p['name']}")
                        _vp_parts.append(f"size_t {_p['name']}_len")
                    else:
                        _vp_parts.append(
                            f"{_ctype_display(_p['type'])} {_p['name']}"
                        )
                decl_lines.append(
                    f"size_t {component}_{name}_max_out({component}_state_t *state);\n"
                    f"size_t {component}_{name}({component}_state_t *state,"
                    f" {', '.join(_vp_parts)},"
                    f" {_vo_out_disp} *out{extra_params});"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out({component}_state_t *state);\n"
                    f"size_t {component}_{name}({component}_state_t *state, size_t n,"
                    f" {_vo_out_disp} *out{extra_params});"
                )
        else:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            out_type_param = f", {_ctype_display(out_type)} *out" if out_type else ""
            if has_params:
                # Expand primary arg + extra params to C declarations.
                p_parts: list[str] = []
                if has_arg:
                    if is_array_param_type(arg_type):
                        _e_disp = _ctype_display(array_elem_ctype(arg_type))
                        p_parts.append(f"const {_e_disp} *x")
                        p_parts.append(f"size_t x_len")
                    else:
                        p_parts.append(f"{arg_disp} x")
                for p in params:
                    if is_array_param_type(p["type"]):
                        e_disp = _ctype_display(array_elem_ctype(p["type"]))
                        p_parts.append(f"const {e_disp} *{p['name']}")
                        p_parts.append(f"size_t {p['name']}_len")
                    else:
                        p_parts.append(f"{_ctype_display(p['type'])} {p['name']}")
                c_param_str = ", ".join(p_parts)
                decl_lines.append(
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {c_param_str}{extra_params}{out_type_param});"
                )
            elif has_arg:
                if is_array_param_type(arg_type):
                    _e_disp = _ctype_display(array_elem_ctype(arg_type))
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" const {_e_disp} *x, size_t x_len"
                        f"{extra_params}{out_type_param});"
                    )
                else:
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" {arg_disp} x{extra_params}{out_type_param});"
                    )
            else:
                decl_lines.append(
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state{extra_params}{out_type_param});"
                )

        # Prefix any newly added declarations with Doxygen.
        for _j in range(_ndecl, len(decl_lines)):
            decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]

        # ── pre-allocated buffer fields + alloc + free ────────────────────────
        if variable_output:
            all_return_types = [_vo_out_elem] + list(multi_output)
            _malloc_lines: list[str] = []
            for i, rt in enumerate(all_return_types):
                suffix = f"_{i}" if i > 0 else ""
                rt_disp = _ctype_display(rt)
                field_name = f"_{name}_buf{suffix}"
                buf_fields.append(
                    f"    {rt_disp} *{field_name};"
                    f"  /* pre-allocated output for {name} */\n"
                )
                buf_free.append(f"    free(self->{field_name});\n")
                _malloc_lines.append(
                    f"        self->{field_name} = malloc("
                    f"_max * sizeof({rt_disp}));\n"
                    f"        if (!self->{field_name}) {{"
                    f" PyErr_NoMemory(); return -1; }}\n"
                )
            # Cap field tracks current allocation size; used by the runtime
            # grow logic to detect when a realloc is needed.
            buf_fields.append(
                f"    size_t _{name}_buf_cap;"
                f"  /* allocated capacity for {name} */\n"
            )
            # Guard against max_out() returning 0 at construction time
            # (output size is input-dependent; lazy alloc in the wrapper
            # handles grow/realloc transparently at each call).
            buf_alloc.append(
                f"    {{\n"
                f"        size_t _max ="
                f" {component}_{name}_max_out(self->handle);\n"
                f"        if (_max) {{\n"
                + "".join(_malloc_lines)
                + f"            self->_{name}_buf_cap = _max;\n"
                + f"        }}\n"
                f"    }}\n"
            )

        # ── Python wrapper in ext.c ───────────────────────────────────────────
        if variable_output:
            if has_arg:
                parse_block = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                )
                call_data = (
                    f"self->handle,"
                    f" (const {arg_disp} *)PyArray_DATA(in_arr),"
                    f" (size_t)n, self->_{name}_buf"
                )
                decref_in = "    Py_DECREF(in_arr);\n"
                _lazy_fallback = "(size_t)n"
            elif has_params:
                # params drive the output length; parse each array param as
                # a numpy array and use the first array's length as n.
                _pb_lines: list[str] = []
                _cd_parts: list[str] = ["self->handle"]
                _dr_lines: list[str] = []
                _fmt = ""
                _fmt_args: list[str] = []
                _first_arr: str | None = None
                _first_scalar: str | None = None
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _pe_disp = _ctype_display(_pe)
                        _pb_lines += [
                            f"    PyObject *{_pn}_obj = NULL;",
                        ]
                        _fmt += "O"
                        _fmt_args.append(f"&{_pn}_obj")
                        _pb_lines += [
                            f"    PyArrayObject *{_pn}_arr = NULL;",
                        ]
                        _cd_parts.append(
                            f"(const {_pe_disp} *)PyArray_DATA({_pn}_arr)"
                        )
                        _cd_parts.append(f"(size_t)PyArray_SIZE({_pn}_arr)")
                        _dr_lines.append(f"    Py_DECREF({_pn}_arr);")
                        if _first_arr is None:
                            _first_arr = _pn
                    else:
                        _pt_meta = _CTYPE_META.get(_pt, {})
                        _fmt_char = _pt_meta.get("fmt", "d")
                        _has_parse = "parse_type" in _pt_meta
                        _parse_t = _pt_meta.get(
                            "parse_type", _ctype_display(_pt)
                        )
                        _parse_zero = _pt_meta.get("parse_zero", "0")
                        if _has_parse:
                            _raw = f"{_pn}_raw"
                            _pb_lines.append(
                                f"    {_parse_t} {_raw} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_raw}")
                        else:
                            _pb_lines.append(
                                f"    {_parse_t} {_pn} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_pn}")
                        _cd_parts.append(_pn)
                        if _first_scalar is None:
                            _first_scalar = _pn
                _cd_parts.append(f"self->_{name}_buf")
                _fmt_str = '", "'.join([f'"{_fmt}', ", ".join(_fmt_args) + ")"])
                parse_block = (
                    "\n".join(_pb_lines) + "\n"
                    f'    if (!PyArg_ParseTuple(args, "{_fmt}", '
                    + ", ".join(_fmt_args) + "))\n"
                    "        return NULL;\n"
                )
                # Convert obj pointers to arrays and Py_complex to C complex
                # after PyArg_ParseTuple.
                _conv_lines: list[str] = []
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _conv_lines += [
                            f"    {_pn}_arr = (PyArrayObject *)PyArray_FROM_OTF(",
                            f"        {_pn}_obj, {_pe_np}, NPY_ARRAY_C_CONTIGUOUS);",
                            f"    if (!{_pn}_arr) return NULL;",
                        ]
                    elif "parse_type" in _CTYPE_META.get(_pt, {}):
                        _pm = _CTYPE_META[_pt]
                        _pt_disp = _ctype_display(_pt)
                        _conv_lines.append(
                            f"    {_pt_disp} {_pn} = {_pm['to_c'](_pn)};"
                        )
                parse_block += "\n".join(_conv_lines) + "\n" if _conv_lines else ""
                call_data = ", ".join(_cd_parts)
                decref_in = "\n".join(_dr_lines) + "\n" if _dr_lines else ""
                # Lazy-alloc / grow fallback: prefer the first array param's
                # length, then the first scalar param (e.g. steps(uint32_t n)),
                # then 1 as a last resort.
                _lazy_fallback = (
                    f"(size_t)PyArray_SIZE({_first_arr}_arr)"
                    if _first_arr is not None
                    else f"(size_t){_first_scalar}"
                    if _first_scalar is not None
                    else "1"
                )
            else:
                parse_block = (
                    "    Py_ssize_t n = 1;\n"
                    '    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                    "        return NULL;\n"
                )
                call_data = f"self->handle, (size_t)n, self->_{name}_buf"
                decref_in = ""
                _lazy_fallback = "(size_t)n"

            if multi_output:
                # Multi-output: call C function, return tuple of views
                all_rts = [return_type] + list(multi_output)
                call_extra = "".join(
                    f", self->_{name}_buf_{i}" for i in range(1, len(all_rts))
                )
                np_enums = [
                    _NP_ENUM[_CTYPE_META[rt[:-2] if rt.endswith("[]") else rt]["py_type"]]
                    for rt in all_rts
                ]
                arr_decls = "\n".join(
                    f"    PyObject *arr{i} = PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {np_enums[i]}, self->_{name}_buf"
                    f"{'_' + str(i) if i > 0 else ''});"
                    for i in range(len(all_rts))
                )
                incref_lines = "\n".join(
                    f"    PyArray_SetBaseObject((PyArrayObject *)arr{i},"
                    f" (PyObject *)self); Py_INCREF(self);"
                    for i in range(len(all_rts))
                )
                null_checks = " || ".join(f"!arr{i}" for i in range(len(all_rts)))
                decref_cleanup = " ".join(
                    f"Py_XDECREF(arr{i});" for i in range(len(all_rts))
                )
                pack_args = ", ".join(f"arr{i}" for i in range(len(all_rts)))
                decref_after = "\n".join(
                    f"    Py_DECREF(arr{i});" for i in range(len(all_rts))
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"    size_t n_out = {component}_{name}({call_data}{call_extra});\n"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"{arr_decls}\n"
                    f"    if ({null_checks}) {{\n"
                    f"        {decref_cleanup} return NULL;\n"
                    f"    }}\n"
                    f"{incref_lines}\n"
                    f"    PyObject *result = PyTuple_Pack({len(all_rts)}, {pack_args});\n"
                    f"{decref_after}\n"
                    f"{decref_in}"
                    f"    return result;\n"
                    f"}}"
                )
            else:
                _none_on_empty_line = (
                    "    if (!n_out) Py_RETURN_NONE;\n"
                    if none_on_empty else ""
                )
                # Grow the buffer when it is NULL (max_out() returned 0 at
                # construction) or when the caller requests more output than
                # the current allocation can hold.  _need is the minimum
                # number of elements required for this call.
                _decref_early_vo = (
                    " ".join(
                        l.strip()
                        for l in decref_in.splitlines()
                        if l.strip()
                    ) + " "
                    if decref_in.strip() else ""
                )
                _lazy_alloc_vo = (
                    f"    size_t _need = {_lazy_fallback};\n"
                    f"    if (!self->_{name}_buf"
                    f" || self->_{name}_buf_cap < _need) {{\n"
                    f"        size_t _max ="
                    f" {component}_{name}_max_out(self->handle);\n"
                    f"        if (!_max || _max < _need) _max = _need;\n"
                    f"        {_vo_out_disp} *_tmp = realloc("
                    f"self->_{name}_buf,"
                    f" _max * sizeof({_vo_out_disp}));\n"
                    f"        if (!_tmp) {{"
                    f" {_decref_early_vo}PyErr_NoMemory();"
                    f" return NULL; }}\n"
                    f"        self->_{name}_buf = _tmp;\n"
                    f"        self->_{name}_buf_cap = _max;\n"
                    f"    }}\n"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"{_lazy_alloc_vo}"
                    f"    size_t n_out = {component}_{name}({call_data});\n"
                    f"{_none_on_empty_line}"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {_vo_out_np}, self->_{name}_buf);\n"
                    f"    if (!arr) return NULL;\n"
                    f"    PyArray_SetBaseObject((PyArrayObject *)arr, (PyObject *)self);\n"
                    f"    Py_INCREF(self);\n"
                    f"{decref_in}"
                    f"    return arr;\n"
                    f"}}"
                )
            _all_rts_vo = [_vo_out_elem] + list(multi_output)
            _dtype_strs_vo = [
                _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt]["py_type"].replace("np.", "")
                for rt in _all_rts_vo
            ]
            _ret_hint_vo = (
                f"tuple[{', '.join('ndarray' for _ in _all_rts_vo)}]"
                if len(_all_rts_vo) > 1
                else "ndarray"
            )
            if has_arg:
                _vo_sig_arg = "x"
                _vo_call_example = f"obj.{name}({_in_example})"
            elif has_params:
                _first_ap = next(
                    (p for p in params if is_array_param_type(p["type"])), None
                )
                _vo_sig_arg = _first_ap["name"] if _first_ap else "n=1"
                _vo_call_example = f"obj.{name}(np.zeros(4))"
            else:
                _vo_sig_arg = "n=1"
                _vo_call_example = f"obj.{name}(4)"
            _vo_doc_lines = [
                f"{name}({_vo_sig_arg}) -> {_ret_hint_vo}",
                "",
                "Zero-copy view into pre-allocated output buffer.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            _vo_doc_lines.append(f"    >>> y = {_vo_call_example}")
            _vo_doc_lines += [
                f"    >>> y{'[0]' if len(_all_rts_vo) > 1 else ''}.dtype",
                f"    dtype('{_dtype_strs_vo[0]}')",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name}, METH_VARARGS,\n'
                f"     {_build_ml_doc(_vo_doc_lines)}}},\n"
            )
        elif result_fields:
            # struct-list return: stack-alloc array, call C, build list[tuple]
            _rf_fmt_parts: list[str] = []
            _rf_arg_parts: list[str] = []
            for _rf in result_fields:
                _rft = _rf["type"]
                _rfn = _rf["name"]
                _fmt_c, _cast = _PYBUILD_FMT.get(_rft, ("i", ""))
                _rf_fmt_parts.append(_fmt_c)
                _rft_val = f"results[i].{_rfn}"
                if _cast:
                    _rft_val = f"({_cast}){_rft_val}"
                _rf_arg_parts.append(_rft_val)
            _bvfmt = '"(' + "".join(_rf_fmt_parts) + ')"'
            _bvargs = ", ".join(_rf_arg_parts)
            if has_arg:
                _rf_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr"
                    f" = (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    size_t n_in = (size_t)PyArray_SIZE(in_arr);\n"
                )
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    f"    size_t n_out = {component}_{name}(self->handle,\n"
                    f"        (const {arg_disp} *)PyArray_DATA(in_arr),"
                    f" n_in,\n"
                    f"        results, {max_results});\n"
                    f"    Py_DECREF(in_arr);\n"
                )
            else:
                _rf_parse = ""
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    f"    size_t n_out = {component}_{name}(self->handle,\n"
                    f"        results, {max_results});\n"
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"{_rf_parse}"
                f"{_rf_call}"
                f"    PyObject *lst = PyList_New((Py_ssize_t)n_out);\n"
                f"    if (!lst) return NULL;\n"
                f"    for (size_t i = 0; i < n_out; i++) {{\n"
                f"        PyObject *tup = Py_BuildValue({_bvfmt}, {_bvargs});\n"
                f"        if (!tup) {{ Py_DECREF(lst); return NULL; }}\n"
                f"        PyList_SET_ITEM(lst, (Py_ssize_t)i, tup);\n"
                f"    }}\n"
                f"    return lst;\n"
                f"}}"
            )
            _rf_field_names = ", ".join(f["name"] for f in result_fields)
            _rf_call_arg = (
                f"np.zeros(4, dtype={_in_dtype_str})" if has_arg else ""
            )
            _rf_doc_lines = [
                f"{name}({'x' if has_arg else ''}) -> list[tuple]",
                "",
                f"Returns list of ({_rf_field_names},) tuples.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
                f"    >>> results = obj.{name}({_rf_call_arg})",
                "    >>> isinstance(results, list)",
                "    True",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS,\n"
                f"     {_build_ml_doc(_rf_doc_lines)}}},\n"
            )
        else:
            # Fixed-output wrapper
            _p_cleanup = ""
            if has_params and has_arg:
                # Primary array/scalar arg + extra params — unify via
                # _build_params_parse so array args get PyArray_FROM_OTF.
                _x_param = {"name": "x", "type": arg_type}
                _combined = [_x_param] + list(params)
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    _combined
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_params:
                parse_block, _p_call, _p_cleanup = _build_params_parse(params)
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_arg and arg_type.endswith("[]"):
                # Array primary arg, no extra params.
                _x_param = {"name": "x", "type": arg_type}
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    [_x_param]
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_arg:
                # Scalar primary arg, no extra params.
                parse_block = _step_parse_block(arg_type, arg_meta) + "\n"
                call_args_c = "self->handle, x"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            else:
                parse_block = ""
                call_args_c = "self->handle"
                fn_sig = (
                    f"{Component}Object *self, PyObject *Py_UNUSED(ignored)"
                )
                meth_flags = "METH_NOARGS"

            if multi_output:
                extra_decls = "".join(
                    f"    {_ctype_display(rt)} out{i + 1}"
                    f" = {_CTYPE_META[rt]['zero']};\n"
                    for i, rt in enumerate(multi_output)
                )
                extra_call = "".join(f", &out{i + 1}" for i in range(len(multi_output)))
                if ret_meta:
                    call_line = (
                        f"    {ret_disp} y ="
                        f" {component}_{name}({call_args_c}{extra_call});\n"
                    )
                    py_primary = ret_meta["to_py"]("y")
                else:
                    call_line = f"    {component}_{name}({call_args_c}{extra_call});\n"
                    py_primary = "Py_None"
                pack_parts = [py_primary] + [
                    _CTYPE_META[rt]["to_py"](f"out{i + 1}")
                    if rt in _CTYPE_META
                    else f"PyLong_FromLong(out{i + 1})"
                    for i, rt in enumerate(multi_output)
                ]
                n = len(multi_output) + 1
                ret_body = (
                    f"{extra_decls}"
                    f"{call_line}"
                    f"{_p_cleanup}"
                    f"    return PyTuple_Pack({n},"
                    f" {', '.join(pack_parts)});\n"
                )
            elif out_type:
                # Per-call output allocation: allocate ndarray of out_type
                # sized to first_array_param_len / out_divisor, pass *out to C.
                out_disp = _ctype_display(out_type)
                out_npy = _CTYPE_TO_NPY[out_type]
                first_arr = next(
                    (p["name"] for p in params if is_array_param_type(p["type"])), None
                )
                raw_len = f"{first_arr}_len" if first_arr else "0"
                if out_divisor > 1:
                    len_expr = f"({raw_len} / {out_divisor})"
                else:
                    len_expr = raw_len
                cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                ret_body = (
                    f"    npy_intp _dims[] ="
                    f" {{(npy_intp){len_expr}}};\n"
                    f"    PyObject *_out ="
                    f" PyArray_EMPTY(1, _dims, {out_npy}, 0);\n"
                    f"    if (!_out) {{{cleanup_inline} return NULL; }}\n"
                    f"    {component}_{name}({call_args_c},"
                    f" ({out_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)_out));\n"
                    f"{_p_cleanup}"
                    f"    return _out;\n"
                )
            elif ret_meta:
                ret_expr = ret_meta["to_py"]("y")
                ret_body = (
                    f"    {ret_disp} y = {component}_{name}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    return {ret_expr};\n"
                )
            else:
                ret_body = (
                    f"    {component}_{name}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    Py_RETURN_NONE;\n"
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}({fn_sig})\n"
                f"{{\n"
                f"{guard}"
                f"{parse_block}"
                f"{ret_body}"
                f"}}"
            )
            # Fixed-output: build doctest example based on params/arg/return.
            _fix_sig_in = (
                f"{'x' if has_arg else ''}"
                + (", " if has_arg and has_params else "")
                + ", ".join(p["name"] for p in params)
            )
            _fix_ret_hint = (
                "ndarray" if out_type or multi_output else _pyi_scalar(return_type)
            )
            _fix_doc_lines = [
                f"{name}({_fix_sig_in}) -> {_fix_ret_hint}".rstrip(),
                "",
                f"{name}.",
            ]
            if has_arg or has_params:
                _fix_doc_lines += ["", "    >>> import numpy as np"]
            else:
                _fix_doc_lines.append("")
            _fix_doc_lines += [*_from_line, _obj_line]
            # Call line with representative args
            _call_parts: list[str] = []
            if has_arg:
                _call_parts.append(_in_example if _in_example else "x")
            for _p in params:
                _pt = _p["type"]
                if _pt.endswith("[]"):
                    _pe = _pt[:-2]
                    _pe_str = (
                        _CTYPE_META[_pe]["py_type"]
                        if _pe in _CTYPE_META
                        else "np.float32"
                    )
                    _call_parts.append(f"np.zeros(4, dtype={_pe_str})")
                elif _pt in _CTYPE_META:
                    _call_parts.append(_CTYPE_META[_pt].get("py_zero", "0"))
                else:
                    _call_parts.append("0")
            _call_str = ", ".join(_call_parts)
            if out_type or multi_output:
                _fix_doc_lines.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_doc_lines.append("    >>> y.ndim")
                _fix_doc_lines.append("    1")
            elif return_type != "void" and return_type in _CTYPE_META:
                _py_z = _CTYPE_META[return_type].get("py_zero", "0")
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
                _fix_doc_lines.append(f"    {_py_z}")
            else:
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name}, {meth_flags},\n'
                f"     {_build_ml_doc(_fix_doc_lines)}}},\n"
            )

        method_c_parts.append(wrapper)

        # pyi stub for this method
        m_var = variable_output
        m_multi = multi_output
        param_parts: list[str] = []
        if arg_type != "void":
            if arg_type.endswith("[]"):
                elem = arg_type[:-2]
                param_parts.append(f"x: {_pyi_ndarray(elem)}")
            else:
                param_parts.append(f"x: {_pyi_scalar(arg_type)}")
        for p in params:
            pt = p["type"]
            if pt.endswith("[]"):
                param_parts.append(f"{p['name']}: {_pyi_ndarray(pt[:-2])}")
            else:
                param_parts.append(f"{p['name']}: {_pyi_scalar(pt)}")
        if result_fields:
            ret_ann = "list[tuple]"
        elif m_var:
            all_rts = [return_type] + list(m_multi)
            ndarrays = [_pyi_ndarray(rt) for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]" if len(ndarrays) > 1 else ndarrays[0]
            )
        else:
            ret_ann = _pyi_scalar(return_type)
        sig = ", ".join(param_parts)
        _pyi_ret_desc = (
            f"Returns\n        -------\n        {ret_ann}\n            Output.\n        "
            if ret_ann != "None"
            else ""
        )
        _pyi_param_desc = ""
        for _pp in (["x"] if has_arg else []) + [p["name"] for p in params]:
            _pyi_param_desc += f"        {_pp}\n            Input.\n"
        _pyi_params_section = (
            f"        Parameters\n        ----------\n{_pyi_param_desc}        "
            if _pyi_param_desc
            else "        "
        )
        _pyi_doc = (
            f'        """{name}.\n\n'
            f"{_pyi_params_section}\n"
            f"        {_pyi_ret_desc}\n"
            f'        """\n'
            if (sig or ret_ann != "None")
            else f'        """{name}."""\n'
        )
        stub = (
            f"    def {name}(self, {sig}) -> {ret_ann}:\n{_pyi_doc}"
            if sig
            else f"    def {name}(self) -> {ret_ann}:\n{_pyi_doc}"
        )
        pyi_lines.append(stub)

    method_decls = "\n\n".join(decl_lines) + "\n" if decl_lines else ""

    _method_bench_blocks = [
        _bench_method_block(component, m) for m in methods
    ]
    _filled = [b for b in _method_bench_blocks if b]
    bench_methods_timing_block = (
        "\n" + "\n\n".join(_filled) if _filled else ""
    )
    return {
        "method_decls": method_decls,
        "extra_buf_fields": "".join(buf_fields),
        "extra_buf_free": "".join(buf_free),
        "extra_buf_alloc": "".join(buf_alloc),
        "extra_methods_c": "\n\n".join(method_c_parts),
        "extra_methods_pymethoddef": "".join(pmd_lines),
        "pyi_extra_methods": "\n" + "\n\n".join(pyi_lines) + "\n" if pyi_lines else "",
        "bench_methods_timing_block": bench_methods_timing_block,
        # When user defines a "reset" method suppress the template's built-in
        # one so it is not emitted twice (bug #10).
        **({
            "builtin_reset_c": "",
            "builtin_reset_pmd": "",
            "builtin_reset_decl": "",
        } if user_has_reset else {}),
    }


def make_properties_ctx(
    component: str,
    Component: str,
    properties: list[dict],
    state_var_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Generate getset_def and tp_getset_decl context keys for Python properties.

    state_var_names: names already declared by make_state_ctx(); those are
    excluded from property_decls to avoid duplicate C declarations.

    Each property dict has: name, type (a _CTYPE_META key), writable (bool).
    """
    _EMPTY: dict[str, str] = {
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
    }
    if not properties:
        return _EMPTY

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    getter_parts: list[str] = []
    getset_entries: list[str] = []
    decl_lines: list[str] = []
    struct_field_lines: list[str] = []

    for p in properties:
        pname: str = p["name"]
        ctype: str = p.get("type") or p.get("ctype", "size_t")
        writable: bool = p.get("writable", False)
        field: bool = p.get("field", False)
        buf_field: str = p.get("buf_field", "")
        len_field: str = p.get("len_field", "n")
        valid_field: str = p.get("valid_field", "")

        meta = _CTYPE_META.get(ctype, _CTYPE_META["size_t"])
        disp = _ctype_display(ctype)

        if buf_field:
            # Buffer-view property: returns a zero-copy numpy array backed by
            # an internal state-struct pointer, optionally gated by a validity
            # flag.  Never generates a C getter declaration.
            _elem_ct = ctype[:-2] if ctype.endswith("[]") else ctype
            _elem_meta = _CTYPE_META.get(_elem_ct, _CTYPE_META["float _Complex"])
            _np_enum = _NP_ENUM.get(_elem_meta["py_type"], "NPY_CFLOAT")
            _valid_check = (
                f"    if (!self->handle->{valid_field}) Py_RETURN_NONE;\n"
                if valid_field else ""
            )
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_valid_check}"
                f"    npy_intp dim = (npy_intp)self->handle->{len_field};\n"
                f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                f"        1, &dim, {_np_enum},"
                f" self->handle->{buf_field});\n"
                f"    if (!arr) return NULL;\n"
                f"    PyArray_SetBaseObject("
                f"(PyArrayObject *)arr, (PyObject *)self);\n"
                f"    Py_INCREF(self);\n"
                f"    return arr;\n"
                f"}}"
            )
        elif p.get("expr"):
            # Inline-expression property: getter evaluates a custom C
            # expression — no extern declaration, no struct field.
            _expr = p["expr"]
            to_py = meta["to_py"](_expr)
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"    return {to_py};\n"
                f"}}"
            )
        elif field:
            # Struct-backed property: direct struct field access,
            # no extern C function declaration needed.
            struct_field_lines.append(f"    {disp} {pname};")
            to_py = meta["to_py"](f"self->handle->{pname}")
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"    return {to_py};\n"
                f"}}"
            )
        else:
            # Computed property: if not backed by a state var, emit a
            # placeholder comment to remind the caller to implement the
            # getter; otherwise the getter calls the auto-generated
            # state-var accessor and the comment would be misleading.
            to_py = meta["to_py"](f"{component}_get_{pname}(self->handle)")
            implement_cmt = (
                "    /* <<IMPLEMENT: return the computed or stored value>> */\n"
                if pname not in state_var_names
                else ""
            )
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{implement_cmt}"
                f"    return {to_py};\n"
                f"}}"
            )
            if pname not in state_var_names:
                decl_lines.append(
                    f"/**\n"
                    f" * @brief Get {pname}.\n"
                    f" * @param state  Must be non-NULL.\n"
                    f" * @return Current {pname} value ({disp}).\n"
                    f" */\n"
                    f"{disp} {component}_get_{pname}"
                    f"(const {component}_state_t *state);"
                )

        getter_parts.append(getter)

        setter_name = "NULL"
        if writable:
            setter_name = f"(setter){Component}_setprop_{pname}"
            if "parse_type" in meta:
                parse_block = (
                    f"    {meta['parse_type']} v_raw = {meta['parse_zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v_raw))'
                    f" return -1;\n"
                    f"    {disp} v = {meta['to_c']('v')};\n"
                )
            else:
                parse_block = (
                    f"    {disp} v = {meta['zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v))'
                    f" return -1;\n"
                )
            if field:
                assign_line = f"    self->handle->{pname} = v;\n"
            else:
                assign_line = f"    {component}_set_{pname}(self->handle, v);\n"
                if pname not in state_var_names:
                    decl_lines.append(
                        f"/**\n"
                        f" * @brief Set {pname}.\n"
                        f" * @param state  Must be non-NULL.\n"
                        f" * @param val    New value ({disp}).\n"
                        f" */\n"
                        f"void {component}_set_{pname}"
                        f"({component}_state_t *state, {disp} val);"
                    )
            setter = (
                f"static int\n"
                f"{Component}_setprop_{pname}({Component}Object *self,"
                f" PyObject *value, void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return -1;\n"
                f"    }}\n"
                f"{parse_block}"
                f"{assign_line}"
                f"    return 0;\n"
                f"}}"
            )
            getter_parts.append(setter)

        getset_entries.append(
            f'    {{ "{pname}", (getter){Component}_getprop_{pname},'
            f" {setter_name}, NULL, NULL }},"
        )

    getset_body = "\n".join(getter_parts)
    entries_str = "\n".join(getset_entries)
    getset_def = (
        f"{getset_body}\n\n"
        f"static PyGetSetDef {Component}_getset[] = {{\n"
        f"{entries_str}\n"
        f"    {{ NULL }}\n"
        f"}};\n"
    )
    tp_getset_decl = f"\n    .tp_getset    = {Component}_getset,"
    property_decls = "\n".join(decl_lines) + "\n" if decl_lines else ""
    # Leading \n so it appends cleanly after state_struct_fields in the template.
    property_struct_fields = (
        "\n" + "\n".join(struct_field_lines) if struct_field_lines else ""
    )

    return {
        "getset_def": getset_def,
        "tp_getset_decl": tp_getset_decl,
        "property_decls": property_decls,
        "property_struct_fields": property_struct_fields,
    }


def make_step_ctx(
    ctx: dict,
    arg_type: str,
    return_type: str,
    no_step: bool = False,
    mutable: bool = False,
) -> dict[str, str]:
    """Pre-render step() and steps() C and Python bodies for stateful objects.

    Must be called AFTER make_sample_ctx() and make_perf_ctx() so that
    ctx already contains: component, Component, return_ctype, out_np_enum,
    step_qualifier, omp_simd_hint, step_parse_block, step_return_expr.

    Returns seven keys:
      step_header_decl — non-inline step() declaration for _core.h
      step_impl_def    — inline step() definition for _core.h (after struct)
      steps_c_decl     — steps() declaration for _core.h
      steps_c_impl     — steps() implementation for _core.c
      step_ext_fn      — Component_step() C ext function
      steps_ext_fn     — Component_steps() C ext function
      step_py_flags    — METH_NOARGS or METH_VARARGS for PyMethodDef

    The inline step() definition is placed in _core.h after the struct body
    so every consumer gets the inlined version from a single header.
    """
    component = ctx["component"]
    Component = ctx["Component"]
    ret_disp = ctx["return_ctype"]
    out_np_enum = ctx["out_np_enum"]
    step_qualifier = ctx.get("step_qualifier", "static inline")
    omp_simd_hint = ctx.get("omp_simd_hint", "")
    step_return = ctx.get("step_return_expr", "PyFloat_FromDouble((double)y)")
    is_void_return = return_type == "void"

    if no_step:
        # --no-step: suppress step() and steps() entirely.
        py_create_args = ctx.get("py_create_args", "")
        _lifecycle = (
            f"\n"
            f"    def test_context_manager(self):\n"
            f"        with {Component}({py_create_args}) as obj:\n"
            f"            pass\n"
            f"\n"
            f"    def test_destroy(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        obj.destroy()\n"
        )
        _lifecycle_pure = (
            f"\n"
            f"def test_context_manager():\n"
            f"    with {Component}({py_create_args}) as obj:\n"
            f"        pass\n"
            f"\n"
            f"def test_destroy():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    obj.destroy()\n"
        )
        return {
            "step_header_decl": "",
            "step_impl_def": "",
            "steps_c_decl": "",
            "steps_c_impl": "",
            "step_ext_fn": "",
            "steps_ext_fn": "",
            "step_py_flags": "METH_VARARGS",
            "bench_step_timing_block": "",
            "bench_steps_timing_block": "",
            "steps_def_entry": "",
            "step_pymethoddef_entry": "",
            "step_c_smoke_test": "    /* no step() generated (--no-step) */",
            "pyi_step_methods": "",
            "step_pytest_methods": "",
            "lifecycle_pytest_methods": _lifecycle,
            "step_pytest_methods_pure": "",
            "lifecycle_pytest_methods_pure": _lifecycle_pure,
        }

    if arg_type == "void":
        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use {component}_steps() declared below. */"
        )
        if is_void_return:
            # Void-in, void-out: sink/processor with no scalar I/O.
            step_impl_def = (
                f"/**\n"
                f" * @brief Advance state by one tick (no I/O).\n"
                f" * @param state  Must be non-NULL; state is mutated.\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step({component}_state_t *state)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement */\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process n iterations (no scalar output).\n"
                f" *\n"
                f" * @param state  Component state (mutated).\n"
                f" * @param n     Number of iterations.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        {component}_step(state);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {component}_step(self->handle);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    Py_ssize_t n = 1;\n"
                f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                f"        return NULL;\n"
                f"    {component}_steps(self->handle, (size_t)n);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            step_py_flags = "METH_NOARGS"
        else:
            # Generator object: step(state) -> sample.
            _state_qual = "" if mutable else "const "
            step_impl_def = (
                f"/**\n"
                f" * @brief Generate one output sample from internal state.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @return Next output sample ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step({_state_qual}{component}_state_t *state)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement */\n"
                f"    return ({ret_disp})0;\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Generate a block of output samples.\n"
                f" *\n"
                f" * @param state   Component state (mutated).\n"
                f" * @param output  Output array (length >= n).\n"
                f" * @param n       Number of samples to generate.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        output[i] = {component}_step(state);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {ret_disp} y = {component}_step(self->handle);\n"
                f"    return {step_return};\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    Py_ssize_t n = 1;\n"
                f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                f"        return NULL;\n"
                f"\n"
                f"    npy_intp dims[] = {{n}};\n"
                f"    PyObject *out_arr = PyArray_SimpleNew(1, dims, {out_np_enum});\n"
                f"    if (!out_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        ({ret_disp} *)PyArray_DATA((PyArrayObject *)out_arr),\n"
                f"        (size_t)n);\n"
                f"\n"
                f"    return out_arr;\n"
                f"}}"
            )
            step_py_flags = "METH_NOARGS"
    elif arg_type.endswith("[]"):
        # Array-buffer object: step(state, const elem_t *x, size_t x_len).
        # No steps() — the primary operation already operates on a buffer.
        elem_type = arg_type[:-2]
        elem_disp = _ctype_display(elem_type)
        in_np_enum = ctx.get("in_np_enum", "NPY_COMPLEX64")
        step_return = ctx.get("step_return_expr", "Py_RETURN_NONE")

        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use {component}_steps() declared below. */"
        )
        if is_void_return:
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input buffer (no scalar output).\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input array ({elem_disp}).\n"
                f" * @param x_len  Number of elements in @p x.\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step(\n"
                f"    {component}_state_t *state,\n"
                f"    const {elem_disp} *x, size_t x_len)\n"
                f"{{\n"
                f"    (void)state; (void)x; (void)x_len; /* TODO: implement */\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *x_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &x_obj))\n'
                f"        return NULL;\n"
                f"    PyArrayObject *x_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        x_obj, {in_np_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!x_arr)\n"
                f"        return NULL;\n"
                f"    const {elem_disp} *x = "
                f"(const {elem_disp} *)PyArray_DATA(x_arr);\n"
                f"    size_t x_len = (size_t)PyArray_SIZE(x_arr);\n"
                f"    {component}_step(self->handle, x, x_len);\n"
                f"    Py_DECREF(x_arr);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        else:
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input buffer and return a result.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input array ({elem_disp}).\n"
                f" * @param x_len  Number of elements in @p x.\n"
                f" * @return Result ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step(\n"
                f"    {component}_state_t *state,\n"
                f"    const {elem_disp} *x, size_t x_len)\n"
                f"{{\n"
                f"    (void)state; (void)x; (void)x_len; /* TODO: implement */\n"
                f"    return ({ret_disp})0;\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *x_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &x_obj))\n'
                f"        return NULL;\n"
                f"    PyArrayObject *x_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        x_obj, {in_np_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!x_arr)\n"
                f"        return NULL;\n"
                f"    const {elem_disp} *x = "
                f"(const {elem_disp} *)PyArray_DATA(x_arr);\n"
                f"    size_t x_len = (size_t)PyArray_SIZE(x_arr);\n"
                f"    {ret_disp} y = {component}_step(self->handle, x, x_len);\n"
                f"    Py_DECREF(x_arr);\n"
                f"    return {step_return};\n"
                f"}}"
            )

        steps_c_decl = ""
        steps_c_impl = ""
        steps_ext_fn = ""
        step_py_flags = "METH_VARARGS"
    else:
        arg_disp = ctx["arg_ctype"]
        in_np_enum = ctx.get("in_np_enum", "NPY_COMPLEX64")
        step_parse = ctx.get("step_parse_block", "")

        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use {component}_steps() declared below. */"
        )
        if is_void_return:
            # Sink object: step(state, x) -> void.
            step_impl_def = (
                f"/**\n"
                f" * @brief Consume one input sample (sink; no output).\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input sample ({arg_disp}).\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step({component}_state_t *state, {arg_disp} x)\n"
                f"{{\n"
                f"    (void)state; (void)x; /* TODO: implement */\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process a block of input samples (no output).\n"
                f" *\n"
                f" * @param state  Component state (mutated).\n"
                f" * @param input  Input array (length >= n).\n"
                f" * @param n     Number of samples.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        {component}_step(state, input[i]);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"{step_parse}\n"
                f"    {component}_step(self->handle, x);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *in_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                f"        return NULL;\n"
                f"\n"
                f"    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        in_obj, {in_np_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!in_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"        (size_t)PyArray_SIZE(in_arr));\n"
                f"    Py_DECREF(in_arr);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            step_py_flags = "METH_VARARGS"
        else:
            _state_qual = "" if mutable else "const "
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input sample.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input sample ({arg_disp}).\n"
                f" * @return Output sample ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step({_state_qual}{component}_state_t *state, {arg_disp} x)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement using state variables */\n"
                f"    return ({ret_disp})x;\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process a block of samples.\n"
                f" *\n"
                f" * @param state   Component state (mutated).\n"
                f" * @param input   Input array (length >= n).\n"
                f" * @param output  Output array (length >= n; may alias input for"
                f" in-place).\n"
                f" * @param n       Number of samples.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        output[i] = {component}_step(state, input[i]);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"{step_parse}\n"
                f"    {ret_disp} y = {component}_step(self->handle, x);\n"
                f"    return {step_return};\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *in_obj  = NULL;\n"
                f"    PyObject *out_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O|O", &in_obj, &out_obj))\n'
                f"        return NULL;\n"
                f"\n"
                f"    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        in_obj, {in_np_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!in_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                f"\n"
                f"    if (out_obj && out_obj != Py_None) {{\n"
                f"        PyArrayObject *out_arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"            out_obj, {out_np_enum},\n"
                f"            NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);\n"
                f"        if (!out_arr) {{ Py_DECREF(in_arr); return NULL; }}\n"
                f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                f"            PyErr_Format(PyExc_ValueError,\n"
                f'                "out length %zd != input length %zd",\n'
                f"                (Py_ssize_t)PyArray_SIZE(out_arr), (Py_ssize_t)n);\n"
                f"            Py_DECREF(out_arr);\n"
                f"            Py_DECREF(in_arr);\n"
                f"            return NULL;\n"
                f"        }}\n"
                f"        {component}_steps(\n"
                f"            self->handle,\n"
                f"            (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"            ({ret_disp} *)PyArray_DATA(out_arr),\n"
                f"            (size_t)n);\n"
                f"        Py_DECREF(in_arr);\n"
                f"        return (PyObject *)out_arr;\n"
                f"    }}\n"
                f"\n"
                f"    npy_intp dims[] = {{n}};\n"
                f"    PyObject *out_arr = PyArray_SimpleNew(1, dims, {out_np_enum});\n"
                f"    if (!out_arr) {{\n"
                f"        Py_DECREF(in_arr);\n"
                f"        return NULL;\n"
                f"    }}\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"        ({ret_disp} *)PyArray_DATA((PyArrayObject *)out_arr),\n"
                f"        (size_t)n);\n"
                f"\n"
                f"    Py_DECREF(in_arr);\n"
                f"    return out_arr;\n"
                f"}}"
            )
            step_py_flags = "METH_VARARGS"

    # bench_step_timing_block: per-round step() timing for bench_core.c.
    # Each round is measured individually so jm_bench_write_json() can compute
    # full statistics compatible with the pytest-benchmark JSON format.
    _bsink = ctx.get("bench_sink_assign", "")
    _bsep  = ctx.get("bench_step_input_sep", "")
    _barg  = ctx.get("bench_step_input_arg", "")
    _is_arr = arg_type.endswith("[]")
    if _is_arr:
        # One call per round processes BENCH_N samples.
        _inner = (
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        {_bsink}{component}_step(obj{_bsep}{_barg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
        )
    else:
        # Inner loop of BENCH_N scalar calls per round.
        _inner = (
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        for (int i = 0; i < BENCH_N; i++)\n"
            f"            {_bsink}{component}_step(obj{_bsep}{_barg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
        )
    bench_step_timing_block = (
        f"    double _times_step[ITERATIONS];\n"
        f"    for (int r = 0; r < ITERATIONS; r++) {{\n"
        f"{_inner}"
        f"        _times_step[r] = elapsed_sec(&t0, &t1);\n"
        f"    }}\n"
        f"    jm_bench_add(&_bench, \"step\","
        f" _times_step, ITERATIONS, BENCH_N);\n"
        f"    {{\n"
        f"        double _s = 0.0;\n"
        f"        for (int r = 0; r < ITERATIONS; r++) _s += _times_step[r];\n"
        f'        printf("  step()   %8.1f MSa/s\\n",\n'
        f"               (double)BENCH_N / (_s / ITERATIONS) / 1e6);\n"
        f"    }}"
    )

    # bench_steps_timing_block: the complete steps() timing section in bench_core.c,
    # or "" when steps() is not generated (array arg objects).
    # Each round is timed individually so jm_bench_write_json() can compute
    # min/max/stddev/quartiles compatible with pytest-benchmark JSON.
    si_arg = ctx.get("bench_steps_in_arg", "")
    so_arg = ctx.get("bench_steps_out_arg", " BENCH_N")
    if steps_ext_fn:
        bench_steps_timing_block = (
            f"    double _times_steps[ITERATIONS];\n"
            f"    for (int r = 0; r < ITERATIONS; r++) {{\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        {component}_steps(obj,{si_arg}{so_arg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
            f"        _times_steps[r] = elapsed_sec(&t0, &t1);\n"
            f"    }}\n"
            f"    jm_bench_add(&_bench, \"steps\","
            f" _times_steps, ITERATIONS, BENCH_N);\n"
            f"    {{\n"
            f"        double _s = 0.0;\n"
            f"        for (int r = 0; r < ITERATIONS; r++)"
            f" _s += _times_steps[r];\n"
            f'        printf("  steps()  %8.1f MSa/s\\n",\n'
            f"               (double)BENCH_N / (_s / ITERATIONS) / 1e6);\n"
            f"    }}"
        )
    else:
        bench_steps_timing_block = ""

    # Build ml_doc lines for step() and steps() using context values.
    _pkg = ctx.get("package", "")
    _create = ctx.get("py_create_args", "")
    _in_val = ctx.get("in_py_test_val", "1")
    _out_np_str = ctx.get("out_np_dtype", "np.complex64").replace("np.", "")
    _in_np_str = ctx.get("in_np_dtype", "np.complex64")
    _is_void_arg = arg_type == "void"
    _is_arr_arg = arg_type.endswith("[]")
    _from_pkg = [f"    >>> from {_pkg} import {Component}"] if _pkg else []
    _obj_create = f"    >>> obj = {Component}({_create})"

    # Signature and description for step().
    _ret_hint_step = "None" if is_void_return else ret_disp
    if _is_void_arg and is_void_return:
        _step_sig = "step() -> None"
        _step_desc = "Advance state by one tick (no I/O)."
    elif _is_void_arg:
        _step_sig = f"step() -> {_ret_hint_step}"
        _step_desc = "Generate one output sample from internal state."
    elif _is_arr_arg and is_void_return:
        _step_sig = "step(x) -> None"
        _step_desc = "Process an input buffer (no scalar output)."
    elif _is_arr_arg:
        _step_sig = f"step(x) -> {_ret_hint_step}"
        _step_desc = "Process an input buffer and return a result."
    elif is_void_return:
        _step_sig = "step(x) -> None"
        _step_desc = "Consume one input sample (sink; no output)."
    else:
        _step_sig = f"step(x) -> {_ret_hint_step}"
        _step_desc = "Process one input sample."

    _step_doc_lines: list[str] = [_step_sig, "", _step_desc, ""]
    if _is_arr_arg:
        _step_doc_lines.append("    >>> import numpy as np")
    _step_doc_lines += [*_from_pkg, _obj_create]
    _step_call = "obj.step()" if _is_void_arg else f"obj.step({_in_val})"
    _step_doc_lines.append(f"    >>> {_step_call}")
    if not is_void_return and return_type in _CTYPE_META:
        _step_doc_lines.append(f"    {_CTYPE_META[return_type].get('py_zero', '0')}")

    # steps_def_entry: PyMethodDef entry for steps(), or "" when absent.
    if steps_ext_fn:
        if _is_void_arg:
            _steps_sig = "steps(n=1) -> ndarray" if not is_void_return else "steps(n=1)"
            _steps_desc = (
                "Generate n output samples."
                if not is_void_return
                else "Run n iterations."
            )
            _steps_call = "    >>> y = obj.steps(4)"
        else:
            _steps_sig = "steps(x[, out]) -> ndarray"
            _steps_desc = "Process a block of samples in batch."
            _steps_call = f"    >>> y = obj.steps(np.zeros(4, dtype={_in_np_str}))"
        _steps_doc_lines: list[str] = [_steps_sig, "", _steps_desc, ""]
        _steps_doc_lines.append("    >>> import numpy as np")
        _steps_doc_lines += [*_from_pkg, _obj_create, _steps_call]
        if not is_void_return:
            _steps_doc_lines += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_out_np_str}')",
            ]
        steps_def_entry = (
            f'    {{"steps",    (PyCFunction){Component}_steps,    METH_VARARGS,\n'
            f"     {_build_ml_doc(_steps_doc_lines)}}},\n"
        )
    else:
        steps_def_entry = ""

    # step_pymethoddef_entry: PyMethodDef entry for step().
    step_pymethoddef_entry = (
        f'    {{"step",     (PyCFunction){Component}_step,     {step_py_flags},\n'
        f"     {_build_ml_doc(_step_doc_lines)}}},\n"
    )

    # step_c_smoke_test: C test smoke-test line.
    _suffix = ctx.get("step_example_suffix", "")
    step_c_smoke_test = (
        f"    /* step: verify it runs without crashing */\n"
        f"    (void){component}_step(obj{_suffix});"
    )

    # pyi_step_methods: def step / def steps stubs for .pyi.
    in_py_hint = ctx.get("in_py_hint", "float")
    out_py_hint = ctx.get("out_py_hint", "float")
    pyi_steps = ctx.get("pyi_steps_stub", "")

    # Build a Parameters/Returns docstring for the .pyi step stub.
    if _is_void_arg and is_void_return:
        _pyi_step_doc = '        """Advance state by one tick (no I/O)."""\n'
        _pyi_step_self = "self"
    elif _is_void_arg:
        _pyi_step_doc = (
            f'        """Generate one output sample from internal state.\n\n'
            f"        Returns\n"
            f"        -------\n"
            f"        {out_py_hint}\n"
            f"            Output sample.\n"
            f'        """\n'
        )
        _pyi_step_self = "self"
    elif is_void_return:
        _pyi_step_doc = (
            f'        """Consume one input sample (no output).\n\n'
            f"        Parameters\n"
            f"        ----------\n"
            f"        x : {in_py_hint}\n"
            f"            Input sample.\n"
            f'        """\n'
        )
        _pyi_step_self = f"self, x: {in_py_hint}"
    else:
        _pyi_step_doc = (
            f'        """Process one input sample.\n\n'
            f"        Parameters\n"
            f"        ----------\n"
            f"        x : {in_py_hint}\n"
            f"            Input sample.\n\n"
            f"        Returns\n"
            f"        -------\n"
            f"        {out_py_hint}\n"
            f"            Output sample.\n"
            f'        """\n'
        )
        _pyi_step_self = f"self, x: {in_py_hint}"
    pyi_step_methods = (
        f"\n    def step({_pyi_step_self}) -> {out_py_hint}:\n"
        f"{_pyi_step_doc}" + pyi_steps
    )

    # step_pytest_methods + lifecycle_pytest_methods: Python test methods.
    py_create_args = ctx.get("py_create_args", "")
    in_py_test_val = ctx.get("in_py_test_val", "1")
    out_py_isinstance = ctx.get("out_py_isinstance", "float")
    in_np_dtype = ctx.get("in_np_dtype", "np.float32")
    out_np_dtype = ctx.get("out_np_dtype", "np.float32")
    # step() call for tests: void-input generators take no argument.
    _step_call_test = "obj.step()" if _is_void_arg else f"obj.step({in_py_test_val})"
    # isinstance(y, None) is invalid; use `y is None` for void return types.
    _assert_y = (
        "assert y is None"
        if is_void_return
        else f"assert isinstance(y, {out_py_isinstance})"
    )
    if steps_ext_fn:
        if _is_void_arg:
            # Generator: steps(n) takes an integer count, no input array,
            # no out-buffer variant.
            if is_void_return:
                step_pytest_methods = (
                    f"\n"
                    f"    def test_step_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.step()\n"
                    f"        {_assert_y}\n"
                    f"\n"
                    f"    def test_steps_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        assert obj.steps(64) is None\n"
                )
            else:
                step_pytest_methods = (
                    f"\n"
                    f"    def test_step_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.step()\n"
                    f"        {_assert_y}\n"
                    f"\n"
                    f"    def test_steps_shape_dtype(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.steps(64)\n"
                    f"        self.assertEqual(y.shape, (64,))\n"
                    f"        self.assertEqual(y.dtype, {out_np_dtype})\n"
                )
        elif is_void_return:
            # Scalar input, void return (mutable sink): steps(x) returns None,
            # no output buffer argument.
            step_pytest_methods = (
                f"\n"
                f"    def test_step_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        y = obj.step({in_py_test_val})\n"
                f"        {_assert_y}\n"
                f"\n"
                f"    def test_steps_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        x = np.ones(64, dtype={in_np_dtype})\n"
                f"        assert obj.steps(x) is None\n"
            )
        else:
            step_pytest_methods = (
                f"\n"
                f"    def test_step_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        y = obj.step({in_py_test_val})\n"
                f"        {_assert_y}\n"
                f"\n"
                f"    def test_steps_shape_dtype(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        x = np.ones(64, dtype={in_np_dtype})\n"
                f"        y = obj.steps(x)\n"
                f"        self.assertEqual(y.shape, (64,))\n"
                f"        self.assertEqual(y.dtype, {out_np_dtype})\n"
                f"\n"
                f"    def test_steps_out_param(self):\n"
                f"        x   = np.ones(64, dtype={in_np_dtype})\n"
                f"        buf = np.zeros(64, dtype={out_np_dtype})\n"
                f"        obj1 = {Component}({py_create_args})\n"
                f"        ret = obj1.steps(x, buf)\n"
                f"        self.assertIs(ret, buf)\n"
                f"        obj2 = {Component}({py_create_args})\n"
                f"        np.testing.assert_array_equal(ret, obj2.steps(x))\n"
            )
    else:
        step_pytest_methods = (
            f"\n"
            f"    def test_step_runs(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        y = {_step_call_test}\n"
            f"        {_assert_y}\n"
        )
    lifecycle_pytest_methods = (
        f"\n"
        f"    def test_context_manager(self):\n"
        f"        with {Component}({py_create_args}) as obj:\n"
        f"            y = {_step_call_test}\n"
        f"        {_assert_y}\n"
        f"\n"
        f"    def test_destroy(self):\n"
        f"        obj = {Component}({py_create_args})\n"
        f"        obj.destroy()\n"
        f'        with _raises(RuntimeError, match="destroyed"):\n'
        f"            {_step_call_test}\n"
    )

    # Pure-pytest variants: top-level functions, no self, plain assertions.
    if steps_ext_fn:
        if _is_void_arg:
            if is_void_return:
                step_pytest_methods_pure = (
                    f"\n"
                    f"def test_step_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.step()\n"
                    f"    {_assert_y}\n"
                    f"\n"
                    f"def test_steps_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    assert obj.steps(64) is None\n"
                )
            else:
                step_pytest_methods_pure = (
                    f"\n"
                    f"def test_step_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.step()\n"
                    f"    {_assert_y}\n"
                    f"\n"
                    f"def test_steps_shape_dtype():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.steps(64)\n"
                    f"    assert y.shape == (64,)\n"
                    f"    assert y.dtype == {out_np_dtype}\n"
                )
        elif is_void_return:
            step_pytest_methods_pure = (
                f"\n"
                f"def test_step_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    y = obj.step({in_py_test_val})\n"
                f"    {_assert_y}\n"
                f"\n"
                f"def test_steps_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    x = np.ones(64, dtype={in_np_dtype})\n"
                f"    assert obj.steps(x) is None\n"
            )
        else:
            step_pytest_methods_pure = (
                f"\n"
                f"def test_step_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    y = obj.step({in_py_test_val})\n"
                f"    {_assert_y}\n"
                f"\n"
                f"def test_steps_shape_dtype():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    x = np.ones(64, dtype={in_np_dtype})\n"
                f"    y = obj.steps(x)\n"
                f"    assert y.shape == (64,)\n"
                f"    assert y.dtype == {out_np_dtype}\n"
                f"\n"
                f"def test_steps_out_param():\n"
                f"    x   = np.ones(64, dtype={in_np_dtype})\n"
                f"    buf = np.zeros(64, dtype={out_np_dtype})\n"
                f"    obj1 = {Component}({py_create_args})\n"
                f"    ret = obj1.steps(x, buf)\n"
                f"    assert ret is buf\n"
                f"    obj2 = {Component}({py_create_args})\n"
                f"    np.testing.assert_array_equal(ret, obj2.steps(x))\n"
            )
    else:
        step_pytest_methods_pure = (
            f"\n"
            f"def test_step_runs():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    y = {_step_call_test}\n"
            f"    {_assert_y}\n"
        )
    lifecycle_pytest_methods_pure = (
        f"\n"
        f"def test_context_manager():\n"
        f"    with {Component}({py_create_args}) as obj:\n"
        f"        y = {_step_call_test}\n"
        f"    {_assert_y}\n"
        f"\n"
        f"def test_destroy():\n"
        f"    obj = {Component}({py_create_args})\n"
        f"    obj.destroy()\n"
        f'    with pytest.raises(RuntimeError, match="destroyed"):\n'
        f"        {_step_call_test}\n"
    )

    return {
        "step_header_decl": step_header_decl,
        "step_impl_def": step_impl_def,
        "steps_c_decl": steps_c_decl,
        "steps_c_impl": steps_c_impl,
        "step_ext_fn": step_ext_fn,
        "steps_ext_fn": steps_ext_fn,
        "step_py_flags": step_py_flags,
        "bench_step_timing_block": bench_step_timing_block,
        "bench_steps_timing_block": bench_steps_timing_block,
        "steps_def_entry": steps_def_entry,
        "step_pymethoddef_entry": step_pymethoddef_entry,
        "step_c_smoke_test": step_c_smoke_test,
        "pyi_step_methods": pyi_step_methods,
        "step_pytest_methods": step_pytest_methods,
        "lifecycle_pytest_methods": lifecycle_pytest_methods,
        "step_pytest_methods_pure": step_pytest_methods_pure,
        "lifecycle_pytest_methods_pure": lifecycle_pytest_methods_pure,
    }

