"""
_app.py — `just-makeit app` command.

Scaffolds a shippable standalone application from an existing component:

    just-makeit app --target c       --object engine --name dsp_tool
    just-makeit app --target console --object engine --name dsp_tool
    just-makeit app --target pep723  --object engine --name dsp_tool

For a scalar ``step(x) -> y`` object, all three targets are generated as
*working* sample-stream tools: a real argument parser (one ``--flag`` per
ctor state var, plus ``--input``/``--output``) and a read -> step() -> write
loop over the object's sample type — no hand-editing required. Extra flags can
be declared with ``--flag name:type[:default[:help]]`` (persisted as
``[[app.flags]]``) and appear in both the C and Python parsers.

Four I/O shapes are generated: ``scalar`` (``x -> y``), ``blockwise``
(``x[] -> y[]``), ``consumer`` (``x -> void``), and ``generator``
(``void -> y``). ``no_step`` objects and any other shape fall back to an
``<<IMPLEMENT>>`` stub. See ``_app_shape``, which is the authority.

Targets
-------
c        Standalone C executable.  Generates native/src/app/<name>.c and
         appends an add_executable target to CMakeLists.txt.

console  Python console script.  Generates src/<pkg>/cli.py with argparse
         boilerplate and updates [project.scripts] in pyproject.toml.

pep723   PEP 723 inline-script.  Generates <name>.py in the project root
         with an embedded ``# /// script`` dependency block, runnable via
         ``uv run <name>.py`` without a full install.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _targets
from . import _render as R
from . import _report
from . import _types as T
from ._init import _to_title


# gh-1046: one definition, in the module that also has to recognise them
# when deciding which CMake targets are the PROJECT's rather than jm's.
from ._targets import APP_CMAKE_END as _APP_CMAKE_END
from ._targets import APP_CMAKE_SENTINEL as _APP_CMAKE_SENTINEL

_PYTYPE = {
    "float": "float",
    "double": "float",
    "float _Complex": "complex",
    "double _Complex": "complex",
    "int": "int",
    "int8_t": "int",
    "int16_t": "int",
    "int32_t": "int",
    "int64_t": "int",
    "uint8_t": "int",
    "uint16_t": "int",
    "uint32_t": "int",
    "uint64_t": "int",
    "size_t": "int",
}

# C types whose CLI value can be parsed from a string into a typed local.
# `{a}` is the argv token expression. Types absent here (complex, string) are
# not turned into CLI flags; their ctor default is used verbatim instead.
_C_PARSE = {
    "float": "strtof({a}, NULL)",
    "double": "strtod({a}, NULL)",
    "int": "(int)strtol({a}, NULL, 10)",
    "int8_t": "(int8_t)strtol({a}, NULL, 10)",
    "int16_t": "(int16_t)strtol({a}, NULL, 10)",
    "int32_t": "(int32_t)strtol({a}, NULL, 10)",
    "int64_t": "(int64_t)strtoll({a}, NULL, 10)",
    "uint8_t": "(uint8_t)strtoul({a}, NULL, 10)",
    "uint16_t": "(uint16_t)strtoul({a}, NULL, 10)",
    "uint32_t": "(uint32_t)strtoul({a}, NULL, 10)",
    "uint64_t": "(uint64_t)strtoull({a}, NULL, 10)",
    "size_t": "(size_t)strtoull({a}, NULL, 10)",
}


def _py_default(c_default: str) -> str:
    """Strip C suffixes from a default literal to get a Python literal.

    gh-1043: delegates to the one implementation in `_types`. This copy was
    the CORRECT one of three and the other two shipped `0U` into generated
    Python for months, so it is the copy that moved rather than the answer
    that changed.
    """
    return T.strip_c_literal_suffix(c_default)


def _np_dtype(ctype: str) -> str:
    """NumPy dtype string for a scalar C type (e.g. 'float' -> 'np.float32')."""
    return T._CTYPE_META.get(ctype, {}).get("py_type", "np.float32")


def _flag_help(name: str, supplied: str, default) -> str:
    return supplied if supplied else f"{name} (default: {default})"


# ── flag model ───────────────────────────────────────────────────────────────
# A "flag" is a dict {name, type, default, help, ctor}. `ctor=True` means it
# feeds the component constructor (derived from a ctor state var); `ctor=False`
# is an extra [[app.flags]] flag available for custom logic.
def _ctor_flags(cfg: dict, component: str) -> list[dict]:
    """Flags that feed the component constructor, in create() order.

    A constructor's arguments come from `init_params` when the object declares
    them (the awgn/ddc/no_state pattern), and otherwise from the `--state`
    ctor vars (the simple-object pattern) — mirroring how create() is generated
    (gh-184). A string-enum init param becomes a `choice` flag (its C arg is the
    enum index `int`); array init params have no scalar CLI form and are
    skipped (the body must supply them).
    """
    init = C.init_params(cfg, component)
    if init:
        out = []
        for p in init:
            name, ct, dflt = p[0], p[1], p[2]
            if T.is_array_param_type(ct):
                continue  # arrays aren't CLI scalars
            if T.is_string_enum_type(ct):
                out.append(
                    {
                        "name": name,
                        "type": "int",
                        "default": dflt,
                        "help": "",
                        "ctor": True,
                        "choices": T.string_enum_choices(ct),
                    }
                )
            else:
                out.append(
                    {
                        "name": name,
                        "type": ct,
                        "default": dflt,
                        "help": "",
                        "ctor": True,
                    }
                )
        return out
    state = cfg.get(component, {}).get("state", [])
    no_ctor = {s["name"] for s in state if s.get("no_ctor")}
    out = []
    for n, t, d in C.state_vars(cfg, component):
        if n in no_ctor:
            continue
        out.append(
            {"name": n, "type": t, "default": d, "help": "", "ctor": True}
        )
    return out


def _extra_flags(flags: list[dict] | None) -> list[dict]:
    return [
        {
            "name": f["name"],
            "type": f["type"],
            "default": f.get("default", ""),
            "help": f.get("help", ""),
            "ctor": False,
        }
        for f in flags or []
    ]


# ── Python argparse generation ───────────────────────────────────────────────
def _argparse_block(flags: list[dict]) -> str:
    """Build p.add_argument(...) lines for each flag, indented 4 sp."""
    lines = []
    for f in flags:
        chs = f.get("choices")
        if chs:
            # choice flag: argparse choices=[...], string-valued
            dflt = f["default"] if f["default"] in chs else chs[0]
            helptext = _flag_help(f["name"], f["help"], dflt)
            choices_lit = ", ".join(repr(c) for c in chs)
            lines.append(
                f"    p.add_argument(\n"
                f'        "--{f["name"]}", choices=[{choices_lit}],'
                f" default={dflt!r},\n"
                f'        help="{helptext}",\n'
                f"    )"
            )
            continue
        pytype = _PYTYPE.get(f["type"], "str")
        pydef = _py_default(f["default"]) if f["default"] else None
        helptext = _flag_help(f["name"], f["help"], pydef)
        if f.get("required"):
            spec = "required=True"
        elif pydef is None:
            spec = "default=None"
        elif pytype in ("float", "int", "complex"):
            spec = f"default={pydef}"  # bare numeric literal
        else:
            spec = f"default={pydef!r}"  # quoted string
        lines.append(
            f"    p.add_argument(\n"
            f'        "--{f["name"]}", type={pytype}, {spec},\n'
            f'        help="{helptext}",\n'
            f"    )"
        )
    return "\n".join(lines)


def _py_create_args(flags: list[dict]) -> str:
    """Keyword-args for the Python constructor from ctor flags."""
    return ", ".join(
        f"{f['name']}=args.{f['name']}" for f in flags if f["ctor"]
    )


def _np_dtype_of(t: str) -> str:
    """numpy dtype for a scalar or array (``T[]``) type."""
    elem = T.array_elem_ctype(t) if T.is_array_param_type(t) else t
    return _np_dtype(elem)


def _py_read(dtype: str) -> str:
    return (
        f"    if args.input:\n"
        f"        data = np.fromfile(args.input, dtype={dtype})\n"
        f"    else:\n"
        f"        data = np.frombuffer(sys.stdin.buffer.read(), dtype={dtype})"
    )


_PY_WRITE = (
    "    if args.output:\n"
    "        out.tofile(args.output)\n"
    "    else:\n"
    "        sys.stdout.buffer.write(out.tobytes())"
)


_PY_PACK_WRITE = (
    "    _st = args.sample_type\n"
    '    if args.file_type == "csv":\n'
    "        _lines = []\n"
    '        if _st == "cf32":\n'
    "            for _z in out:\n"
    '                _lines.append("%0.9f,%0.9f" % (_z.real, _z.imag))\n'
    '        elif _st == "cf64":\n'
    "            for _z in out:\n"
    '                _lines.append("%0.17g,%0.17g" % (_z.real, _z.imag))\n'
    "        else:\n"
    '            _sc = {"ci32": 2147483647.0, "ci16": 32767.0,\n'
    '                   "ci8": 127.0}[_st]\n'
    "            for _z in out:\n"
    '                _lines.append("%d,%d" % (\n'
    "                    int(min(max(_z.real, -1.0), 1.0) * _sc),\n"
    "                    int(min(max(_z.imag, -1.0), 1.0) * _sc)))\n"
    '        _buf = ("\\n".join(_lines) + "\\n").encode() if _lines else b""\n'
    "    else:\n"
    '        if _st == "cf32":\n'
    "            _a = out.astype(np.complex64)\n"
    '        elif _st == "cf64":\n'
    "            _a = out.astype(np.complex128)\n"
    "        else:\n"
    "            _iq = np.empty(out.size * 2, dtype=np.float64)\n"
    "            _iq[0::2] = out.real\n"
    "            _iq[1::2] = out.imag\n"
    "            _iq = np.clip(_iq, -1.0, 1.0)\n"
    '            _sc = {"ci32": 2147483647.0, "ci16": 32767.0,\n'
    '                   "ci8": 127.0}[_st]\n'
    '            _dt = {"ci32": np.int32, "ci16": np.int16,\n'
    '                   "ci8": np.int8}[_st]\n'
    "            _a = (_iq * _sc).astype(_dt)\n"
    '        if args.endian == "be":\n'
    "            _a = _a.byteswap()\n"
    "        _buf = _a.tobytes()\n"
    "    if args.output:\n"
    '        with open(args.output, "wb") as _f:\n'
    "            _f.write(_buf)\n"
    "    else:\n"
    "        sys.stdout.buffer.write(_buf)"
)


def _py_io_loop(
    shape: str,
    component: str,
    Component: str,
    arg_t: str,
    ret_t: str,
    sample_type: bool = False,
) -> str:
    """4-space-indented Python body for the given object shape."""
    create = f"    obj = {Component}(<<py_create_args>>)"
    if sample_type and shape == "generator":
        return "\n".join(
            [
                create,
                "    out = np.asarray(\n"
                f"        obj.steps(args.count), dtype={_np_dtype(ret_t)}\n"
                "    )",
                _PY_PACK_WRITE,
            ]
        )
    if sample_type and shape == "blockwise":
        return "\n".join(
            [
                _py_read(_np_dtype_of(arg_t)),
                create,
                "    out = np.asarray("
                f"obj.steps(data), dtype={_np_dtype_of(ret_t)})",
                _PY_PACK_WRITE,
            ]
        )
    if shape == "scalar":
        return "\n".join(
            [
                _py_read(_np_dtype(arg_t)),
                create,
                f"    out = np.array(\n"
                f"        [obj.step(x) for x in data], dtype={_np_dtype(ret_t)}\n"
                f"    )",
                _PY_WRITE,
            ]
        )
    if shape == "blockwise":
        return "\n".join(
            [
                _py_read(_np_dtype_of(arg_t)),
                create,
                f"    out = np.asarray(obj.steps(data), dtype={_np_dtype_of(ret_t)})",
                _PY_WRITE,
            ]
        )
    if shape == "consumer":
        return "\n".join(
            [_py_read(_np_dtype_of(arg_t)), create, "    obj.steps(data)"]
        )
    if shape == "generator":
        return "\n".join(
            [
                create,
                f"    out = np.asarray(\n"
                f"        obj.steps(args.count), dtype={_np_dtype(ret_t)}\n"
                f"    )",
                _PY_WRITE,
            ]
        )
    return ""


# ── C generation ─────────────────────────────────────────────────────────────
def _ctor_c_args(flags: list[dict], parsed: bool) -> str:
    """C constructor args. When `parsed`, a CLI-parseable ctor flag passes its
    parsed local by name; otherwise (and for non-parseable types) the default
    literal is passed inline (commented)."""
    parts = []
    for f in flags:
        if not f["ctor"]:
            continue
        if parsed and f["type"] in _C_PARSE:
            parts.append(f["name"])
        else:
            parts.append(f"/* {f['name']}= */{f['default']}")
    return ", ".join(parts)


def _c_argv_parser(
    name: str,
    flags: list[dict],
    *,
    want_in: bool = True,
    want_out: bool = True,
    want_record: bool = False,
) -> str:
    """Generate the C argv parsing block: typed decls + a strcmp loop, the
    requested --input/--output handling, and the file opens. 4-space indented.

    `want_in`/`want_out` follow the object shape: a consumer has no output, a
    generator has no input.
    """
    decls = []
    clauses = []
    usage_parts = []
    help_rows = []  # (flag display, help text) for the --help screen
    for f in flags:
        chs = f.get("choices")
        if chs:
            # choice flag: parse the string arg to its index via jm_parse_<name>
            didx = chs.index(f["default"]) if f.get("default") in chs else 0
            decls.append(f"    int {f['name']} = {didx};")
            clauses.append(
                f'if (!strcmp(argv[i], "--{f["name"]}") && i + 1 < argc) {{\n'
                f"            {f['name']} = jm_parse_{f['name']}(argv[++i]);\n"
                f"            if ({f['name']} < 0) {{\n"
                f'                fprintf(stderr, "error: --{f["name"]} must'
                f' be one of: {" ".join(chs)}\\n");\n'
                f"                return 2;\n"
                f"            }}\n"
                f"        }}"
            )
            usage_parts.append(f"[--{f['name']} {'|'.join(chs)}]")
            help_rows.append((f"--{f['name']} {'|'.join(chs)}", f["help"]))
            continue
        ct = f["type"]
        if ct not in _C_PARSE:
            continue  # not CLI-parseable; ctor uses its default literal
        decls.append(f"    {ct} {f['name']} = {f['default']};")
        parse = _C_PARSE[ct].format(a="argv[++i]")
        clauses.append(
            f'if (!strcmp(argv[i], "--{f["name"]}") && i + 1 < argc) {{\n'
            f"            {f['name']} = {parse};\n"
            f"        }}"
        )
        usage_parts.append(f"[--{f['name']} V]")
        help_rows.append((f"--{f['name']} V", f["help"]))

    if want_in:
        decls.append("    const char *in_path = NULL;")
        clauses.append(
            'if ((!strcmp(argv[i], "--input") || !strcmp(argv[i], "-i"))\n'
            "                   && i + 1 < argc) {\n"
            "            in_path = argv[++i];\n"
            "        }"
        )
        usage_parts.append("[--input FILE]")
        help_rows.append(("--input, -i FILE", "input file (default: stdin)"))
    if want_out:
        decls.append("    const char *out_path = NULL;")
        clauses.append(
            'if ((!strcmp(argv[i], "--output") || !strcmp(argv[i], "-o"))\n'
            "                   && i + 1 < argc) {\n"
            "            out_path = argv[++i];\n"
            "        }"
        )
        usage_parts.append("[--output FILE]")
        help_rows.append(
            ("--output, -o FILE", "output file (default: stdout)")
        )
    if want_record:
        decls.append("    const char *record_path = NULL;")
        clauses.append(
            'if (!strcmp(argv[i], "--record") && i + 1 < argc) {\n'
            "            record_path = argv[++i];\n"
            "        }"
        )
        usage_parts.append("[--record FILE]")
        help_rows.append(
            ("--record FILE", "write a JSON record of the resolved run")
        )

    usage = f"usage: {name} " + " ".join(usage_parts)
    # --help/-h: print usage + a per-flag description table, then exit 0.
    help_lines = [usage]
    for disp, htext in help_rows:
        if not htext:
            help_lines.append(f"  {disp}")
        elif len(disp) <= 24:
            help_lines.append(f"  {disp:<26}{htext}")
        else:
            help_lines.append(f"  {disp}  {htext}")
    help_body = "\\n".join(help_lines)
    if clauses:
        clauses.insert(
            0,
            'if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {\n'
            f'            fputs("{help_body}\\n", stdout);\n'
            "            return 0;\n"
            "        }",
        )
    if clauses:
        loop = (
            "    for (int i = 1; i < argc; i++) {\n"
            "        " + " else ".join(clauses) + " else {\n"
            f'            fprintf(stderr, "{usage}\\n");\n'
            "            return 2;\n"
            "        }\n"
            "    }"
        )
    else:
        loop = "    (void)argc;\n    (void)argv;"

    # Suppress unused-variable warnings for extra flags the generated loop
    # doesn't consume (non-ctor, non-"consumed" extras for custom logic).
    voids = [
        f"    (void){f['name']};"
        for f in flags
        if f["type"] in _C_PARSE and not f["ctor"] and not f.get("consumed")
    ]

    opens = []
    if want_in:
        opens.append('    FILE *in = in_path ? fopen(in_path, "rb") : stdin;')
    if want_out:
        opens.append(
            '    FILE *out = out_path ? fopen(out_path, "wb") : stdout;'
        )
    if opens:
        cond = (
            "!in || !out"
            if (want_in and want_out)
            else ("!in" if want_in else "!out")
        )
        # gh-944: close whichever stream DID open before bailing out. The
        # normal path already closes both (see the `tail` below); this branch
        # returned holding one of them, which is the only place
        # clang-analyzer-unix.Stream had to complain about. Guarded on
        # non-NULL and on not being the std stream, exactly as the tail is --
        # fclose(stdin) is not this program's to call.
        fails = []
        if want_in:
            fails.append("        if (in && in != stdin) fclose(in);")
        if want_out:
            fails.append("        if (out && out != stdout) fclose(out);")
        opens += [
            f"    if ({cond}) {{",
            '        fprintf(stderr, "error: cannot open input/output\\n");',
            *fails,
            "        return 1;",
            "    }",
        ]

    chunks = [*decls, "", loop]
    if voids:
        chunks += ["", *voids]
    if opens:
        chunks += ["", "\n".join(opens)]
    return "\n".join(chunks)


_APP_BLOCK = 4096

# ── output sample-type conversion (gh-184, Tier 2.2) ─────────────────────────
# For a complex-float (cf32) output stream, `jm app` offers a built-in
# `--sample_type` choice flag that converts each block to the chosen wire type
# on write. The choice index drives jm_convert_block directly (0=cf32 … 4=ci8),
# so no separate enum is needed; full-scale for the integer types is ±1.0.
_SAMPLE_TYPES = ["cf32", "cf64", "ci32", "ci16", "ci8"]

_SAMPLE_TYPE_C = """\
#include <complex.h>

/* Clamp v to [-1, 1] and scale to a signed integer of full-scale fs_val. */
static long
jm_q(float v, double fs_val)
{
    if (v > 1.0f) v = 1.0f;
    if (v < -1.0f) v = -1.0f;
    return (long)(v * fs_val);
}

/* Convert a cf32 block to the selected wire type into `bytes` (interleaved
   I/Q); returns bytes written.  0=cf32 1=cf64 2=ci32 3=ci16 4=ci8. */
static size_t
jm_convert_block(const float _Complex *in, size_t n, int st,
                 unsigned char *bytes)
{
    switch (st) {
    case 1: {
        double _Complex *o = (double _Complex *)bytes;
        for (size_t i = 0; i < n; i++)
            o[i] = (double)crealf(in[i]) + (double)cimagf(in[i]) * I;
        return n * sizeof(double _Complex);
    }
    case 2: {
        int32_t *o = (int32_t *)bytes;
        for (size_t i = 0; i < n; i++) {
            o[2 * i] = (int32_t)jm_q(crealf(in[i]), 2147483647.0);
            o[2 * i + 1] = (int32_t)jm_q(cimagf(in[i]), 2147483647.0);
        }
        return n * 2 * sizeof(int32_t);
    }
    case 3: {
        int16_t *o = (int16_t *)bytes;
        for (size_t i = 0; i < n; i++) {
            o[2 * i] = (int16_t)jm_q(crealf(in[i]), 32767.0);
            o[2 * i + 1] = (int16_t)jm_q(cimagf(in[i]), 32767.0);
        }
        return n * 2 * sizeof(int16_t);
    }
    case 4: {
        int8_t *o = (int8_t *)bytes;
        for (size_t i = 0; i < n; i++) {
            o[2 * i] = (int8_t)jm_q(crealf(in[i]), 127.0);
            o[2 * i + 1] = (int8_t)jm_q(cimagf(in[i]), 127.0);
        }
        return n * 2 * sizeof(int8_t);
    }
    default: /* 0 = cf32: raw passthrough */
        memcpy(bytes, in, n * sizeof(float _Complex));
        return n * sizeof(float _Complex);
    }
}
"""

# ── output container + byte order (gh-193, 0.17.0) ───────────────────────────
# `--file-type raw|csv` and `--endian le|be` ride on the same cf32 output stream
# as `--sample_type`. raw = interleaved I/Q (byte-swapped per element when big-
# endian); csv = one "I,Q" line per sample (text, endian-agnostic). The host is
# assumed little-endian (jm's targets), so big-endian output reverses each
# element on the way out.
_WRITE_BLOCK_C = """\
/* Bytes per I or Q element for sample type st (for big-endian swapping). */
static size_t
jm_elem_size(int st)
{
    switch (st) {
    case 1: return sizeof(double);
    case 2: return sizeof(int32_t);
    case 3: return sizeof(int16_t);
    case 4: return sizeof(int8_t);
    default: return sizeof(float);
    }
}

/* Write n cf32 samples in the chosen sample_type/endian/file_type.
   ftype: 0=raw 1=csv.  endian: 0=little 1=big (raw only). */
static void
jm_write_block(FILE *out, const float _Complex *in, size_t n, int st,
               int endian, int ftype, unsigned char *bytes)
{
    if (ftype == 1) { /* csv: one I,Q line per sample */
        for (size_t i = 0; i < n; i++) {
            float re = crealf(in[i]), im = cimagf(in[i]);
            if (st == 0)
                fprintf(out, "%0.9f,%0.9f\\n", (double)re, (double)im);
            else if (st == 1)
                fprintf(out, "%0.17g,%0.17g\\n", (double)re, (double)im);
            else {
                double sc = (st == 2) ? 2147483647.0
                            : (st == 3) ? 32767.0
                                        : 127.0;
                fprintf(out, "%ld,%ld\\n", jm_q(re, sc), jm_q(im, sc));
            }
        }
        return;
    }
    size_t nb = jm_convert_block(in, n, st, bytes);
    if (endian == 1) { /* big-endian: reverse each element's bytes */
        size_t es = jm_elem_size(st);
        if (es > 1)
            for (size_t off = 0; off + es <= nb; off += es)
                for (size_t a = 0, b = es - 1; a < b; a++, b--) {
                    unsigned char t = bytes[off + a];
                    bytes[off + a] = bytes[off + b];
                    bytes[off + b] = t;
                }
    }
    fwrite(bytes, 1, nb, out);
}
"""

# printf format + cast for each numeric C type, used by the --record JSON dump.
_C_REC_FMT = {
    "float": ("%g", "(double)"),
    "double": ("%g", "(double)"),
    "int": ("%d", "(int)"),
    "int8_t": ("%d", "(int)"),
    "int16_t": ("%d", "(int)"),
    "int32_t": ("%d", "(int)"),
    "int64_t": ("%lld", "(long long)"),
    "uint8_t": ("%u", "(unsigned)"),
    "uint16_t": ("%u", "(unsigned)"),
    "uint32_t": ("%lu", "(unsigned long)"),
    "uint64_t": ("%llu", "(unsigned long long)"),
    "size_t": ("%zu", ""),
}


def _c_choice_arrays(flags: list[dict]) -> str:
    """Static name tables for choice flags, so --record can print the chosen
    string (the parser stores the index)."""
    out = []
    for f in flags:
        chs = f.get("choices")
        if not chs:
            continue
        lit = ", ".join(f'"{c}"' for c in chs)
        out.append(
            f"static const char *const jm_choices_{f['name']}[] = {{{lit}}};"
        )
    return "\n".join(out)


def _c_record_block(flags: list[dict], name: str, version: str) -> str:
    """Emit a `--record` JSON dump of the resolved run from the parsed locals."""
    lines = [
        "    if (record_path) {",
        '        FILE *rec = fopen(record_path, "w");',
        "        if (rec) {",
        f'            fprintf(rec, "{{\\"tool\\":\\"{name}\\",'
        f'\\"version\\":\\"{version}\\"");',
    ]
    for f in flags:
        nm = f["name"]
        if f.get("choices"):
            lines.append(
                f'            fprintf(rec, ",\\"{nm}\\":\\"%s\\"", '
                f"jm_choices_{nm}[{nm}]);"
            )
        elif f["type"] in _C_REC_FMT:
            fmt, cast = _C_REC_FMT[f["type"]]
            lines.append(
                f'            fprintf(rec, ",\\"{nm}\\":{fmt}", {cast}{nm});'
            )
    lines += [
        '            fprintf(rec, "}\\n");',
        "            fclose(rec);",
        "        }",
        "    }",
    ]
    return "\n".join(lines)


def _py_record_block(flags: list[dict], name: str, version: str) -> str:
    """Emit a `--record` JSON dump in Python from the parsed args."""
    fields = [f'"tool": "{name}"', f'"version": "{version}"']
    for f in flags:
        if f.get("choices") or f["type"] in _C_REC_FMT:
            fields.append(f'"{f["name"]}": args.{f["name"]}')
    body = ", ".join(fields)
    return (
        "    if args.record:\n"
        "        import json\n"
        '        with open(args.record, "w") as _rf:\n'
        f"            json.dump({{{body}}}, _rf, indent=2)"
    )


def _is_cf32_out(ret_t: str) -> bool:
    """True if the output element type is single-precision complex (cf32)."""
    elem = T.array_elem_ctype(ret_t).replace("complex", "_Complex")
    return " ".join(elem.split()) == "float _Complex"


def _c_choice_parsers(flags: list[dict]) -> str:
    """Generate a `jm_parse_<name>` string→index helper per choice flag."""
    out = []
    for f in flags:
        chs = f.get("choices")
        if not chs:
            continue
        body = "".join(
            f'    if (!strcmp(s, "{c}")) return {i};\n'
            for i, c in enumerate(chs)
        )
        out.append(
            f"static int\njm_parse_{f['name']}(const char *s)\n{{\n"
            f"{body}    return -1;\n}}"
        )
    return "\n\n".join(out)


def _c_io_loop(
    shape: str,
    component: str,
    arg_t: str,
    ret_t: str,
    sample_type: bool = False,
) -> str:
    """4-space-indented C body for the given object shape.

    When `sample_type` is set (a cf32 output stream with `--sample_type`), the
    block is converted to the chosen wire type via jm_convert_block before the
    write, sized for the widest type (cf64).
    """
    n = _APP_BLOCK
    if shape == "blockwise" and sample_type:
        ie = T.array_elem_ctype(arg_t)
        return (
            f"    {ie} inbuf[{n}];\n"
            f"    float _Complex outbuf[{n}];\n"
            f"    unsigned char jm_bytes[{n} * sizeof(double _Complex)];\n"
            f"    size_t k;\n"
            f"    while ((k = fread(inbuf, sizeof inbuf[0], {n}, in)) > 0) {{\n"
            f"        {component}_steps(state, inbuf, k, outbuf);\n"
            f"        jm_write_block(out, outbuf, k, sample_type, endian,"
            f" file_type, jm_bytes);\n"
            f"    }}"
        )
    if shape == "generator" and sample_type:
        return (
            f"    float _Complex outbuf[{n}];\n"
            f"    unsigned char jm_bytes[{n} * sizeof(double _Complex)];\n"
            f"    size_t produced = 0;\n"
            f"    while (produced < count) {{\n"
            f"        size_t k = (count - produced) < {n}\n"
            f"                       ? (count - produced) : (size_t){n};\n"
            f"        {component}_steps(state, outbuf, k);\n"
            f"        jm_write_block(out, outbuf, k, sample_type, endian,"
            f" file_type, jm_bytes);\n"
            f"        produced += k;\n"
            f"    }}"
        )
    if shape == "scalar":
        return (
            f"    {arg_t} x;\n"
            f"    while (fread(&x, sizeof x, 1, in) == 1) {{\n"
            f"        {ret_t} y = {component}_step(state, x);\n"
            f"        fwrite(&y, sizeof y, 1, out);\n"
            f"    }}"
        )
    if shape == "blockwise":
        ie = T.array_elem_ctype(arg_t)
        oe = T.array_elem_ctype(ret_t)
        return (
            f"    {ie} inbuf[{n}];\n"
            f"    {oe} outbuf[{n}];\n"
            f"    size_t k;\n"
            f"    while ((k = fread(inbuf, sizeof inbuf[0], {n}, in)) > 0) {{\n"
            f"        {component}_steps(state, inbuf, k, outbuf);\n"
            f"        fwrite(outbuf, sizeof outbuf[0], k, out);\n"
            f"    }}"
        )
    if shape == "consumer":
        return (
            f"    {arg_t} inbuf[{n}];\n"
            f"    size_t k;\n"
            f"    while ((k = fread(inbuf, sizeof inbuf[0], {n}, in)) > 0) {{\n"
            f"        {component}_steps(state, inbuf, k);\n"
            f"    }}"
        )
    if shape == "generator":
        return (
            f"    {ret_t} outbuf[{n}];\n"
            f"    size_t produced = 0;\n"
            f"    while (produced < count) {{\n"
            f"        size_t k = (count - produced) < {n}\n"
            f"                       ? (count - produced) : (size_t){n};\n"
            f"        {component}_steps(state, outbuf, k);\n"
            f"        fwrite(outbuf, sizeof outbuf[0], k, out);\n"
            f"        produced += k;\n"
            f"    }}"
        )
    return ""


# libm only off-Windows — MinGW/MSVC link the math runtime implicitly (gh-187).
_LIBM = "$<$<NOT:$<PLATFORM_ID:Windows>>:m>"


def _app_object_link(cfg: dict, object_: str) -> str:
    """Libraries an object app links: the object's own core, its `depends_on`
    cores, and libm (gh-187).

    OBJECT libraries don't propagate their PUBLIC link deps' objects to a
    consuming executable, so an object's `depends_on` cores must be named on the
    app's own link line — otherwise create()/step() reach undefined symbols
    (e.g. lo_create) at link time.
    """
    libs = [f"{object_}_core"]
    for d in cfg.get(object_, {}).get("depends_on", []):
        core = d if d.endswith("_core") else f"{d}_core"
        if core not in libs:
            libs.append(core)
    return " ".join(libs) + " " + _LIBM


def _reserved_targets(cfg: dict, root: "Path | None" = None) -> frozenset:
    """CMake target names already claimed by the project.

    Delegates to :func:`_targets.claimed`, which answers both halves: what jm
    will emit for *cfg*, and what the project declares in its own root
    ``CMakeLists.txt``. This used to be the first half alone, hand-maintained
    here — so a `add_executable(myapp ...)` the project wrote was invisible and
    `jm app --name myapp` emitted a second target beside it, which CMake
    refuses to configure (gh-1046). The same list had also gone stale in its
    own half, omitting `bench_<comp>_core` while jm emitted it.
    """
    return _targets.claimed(cfg, root)


def _exe_target(name: str, cfg: dict, root: "Path | None" = None) -> str:
    """Return a collision-free CMake target id for the app executable.

    Normally the target id equals the app ``name`` (so ``cmake --build
    --target <name>`` is intuitive and the binary is ``<name>``).  When
    ``name`` collides with an existing target — e.g. ``jm app --name wfmgen``
    over a ``wfmgen`` module ext target — suffix the *target id* (``<name>_app``)
    while keeping the binary name ``<name>`` via ``OUTPUT_NAME`` (gh-184).

    Detection runs on the user-facing ``name`` (not a prior suffixed id), so
    re-running ``jm app`` / ``jm apply`` yields the same id — never
    ``<name>_app_app``."""
    reserved = _reserved_targets(cfg, root)
    if name not in reserved:
        return name
    target = f"{name}_app"
    while target in reserved:
        target += "_app"
    return target


def _cmake_app_block(
    name: str, link_target: str, exe_target: str | None = None
) -> str:
    tgt = exe_target or name
    # When the target id differs from the app name (collision avoidance),
    # keep the built binary named <name> via OUTPUT_NAME.
    output_name_line = (
        f"set_target_properties({tgt} PROPERTIES OUTPUT_NAME {name})\n"
        if tgt != name
        else ""
    )
    return (
        f"{_APP_CMAKE_SENTINEL}"
        "─────────────────────────────────────────────────────────\n"
        f"add_executable({tgt} native/src/app/{name}.c)\n"
        f"{output_name_line}"
        f"target_link_libraries({tgt} PRIVATE {link_target})\n"
        f"install(TARGETS {tgt} DESTINATION bin)\n"
        f"{_APP_CMAKE_END}"
        "─────────────────────────────────────────────────────────\n"
    )


def _splice_cmake(
    cmake: Path, name: str, link_target: str, exe_target: str | None = None
) -> None:
    """Insert or replace the App block in CMakeLists.txt."""
    text = cmake.read_text(encoding="utf-8")
    block = _cmake_app_block(name, link_target, exe_target)
    start = text.find(_APP_CMAKE_SENTINEL)
    end = text.find(_APP_CMAKE_END)
    if start != -1 and end != -1:
        end = text.index("\n", end) + 1
        text = text[:start] + block + text[end:]
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    cmake.write_text(text, encoding="utf-8")


def _update_pyproject_scripts(
    root: Path, name: str, pkg: str, module: str | None = None
) -> bool:
    """Add/update [project.scripts] in pyproject.toml using tomlkit.

    Returns True on success, False if tomlkit is absent or pyproject.toml
    does not exist (caller should print manual instructions instead)."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        import tomlkit as _tk
    except ModuleNotFoundError:
        return False

    doc = _tk.loads(pyproject.read_text(encoding="utf-8"))
    if "project" not in doc:
        doc.add("project", _tk.table())
    if "scripts" not in doc["project"]:
        doc["project"].add("scripts", _tk.table())
    dotted = f"{pkg}.{module}.cli" if module else f"{pkg}.cli"
    doc["project"]["scripts"][name] = f"{dotted}:main"
    pyproject.write_text(_tk.dumps(doc), encoding="utf-8")
    return True


def _is_scalar(t: str) -> bool:
    return t in T._CTYPE_META and t != "const char *"


def _is_scalar_array(t: str) -> bool:
    if not T.is_array_param_type(t):
        return False
    return _is_scalar(T.array_elem_ctype(t))


def _app_shape(cfg: dict, component: str) -> str | None:
    """Classify the object's I/O shape so the right parser + loop can be
    generated: 'scalar', 'blockwise', 'consumer', 'generator', or None (an
    unsupported shape that falls back to an <<IMPLEMENT>> stub)."""
    if cfg.get(component, {}).get("no_step") in (True, "true"):
        return None
    arg_t = C.arg_type(cfg, component)
    ret_t = C.return_type(cfg, component)
    if _is_scalar(arg_t) and _is_scalar(ret_t):
        return "scalar"
    if _is_scalar_array(arg_t) and _is_scalar_array(ret_t):
        return "blockwise"
    if _is_scalar(arg_t) and ret_t == "void":
        return "consumer"
    if arg_t == "void" and _is_scalar(ret_t):
        return "generator"
    return None


# ── module-function apps ─────────────────────────────────────────────────────
# printf format + cast per scalar return type.
_C_PRINTF = {
    "float": ("%g", "(double)"),
    "double": ("%g", "(double)"),
    "int": ("%d", ""),
    "int8_t": ("%d", "(int)"),
    "int16_t": ("%d", "(int)"),
    "int32_t": ("%ld", "(long)"),
    "int64_t": ("%lld", "(long long)"),
    "uint8_t": ("%u", "(unsigned)"),
    "uint16_t": ("%u", "(unsigned)"),
    "uint32_t": ("%lu", "(unsigned long)"),
    "uint64_t": ("%llu", "(unsigned long long)"),
    "size_t": ("%zu", ""),
}


def _find_fn(cfg: dict, function: str, module: str | None):
    """Return (module, fn_dict) for the named function, or (None, None)."""
    mods = [module] if module else C.modules(cfg)
    for m in mods:
        for fn in C.module_functions(cfg, m):
            if fn["name"] == function:
                return m, fn
    return None, None


def _fn_generatable(fn: dict) -> bool:
    """A function app is generatable when every param is a CLI-parseable scalar
    and the return is a scalar (or void)."""
    for p in fn.get("params", []):
        if p["type"] not in _C_PARSE:
            return False
    ret = fn.get("return_type", "void")
    return ret == "void" or ret in _C_PRINTF


def _fn_flags(params: list[dict]) -> list[dict]:
    return [
        {
            "name": p["name"],
            "type": p["type"],
            "default": T._CTYPE_META.get(p["type"], {}).get("zero", "0"),
            "help": p["name"],
            "ctor": False,
            "consumed": True,
            "required": True,
        }
        for p in params
    ]


def _c_call_print(function: str, ret_t: str, param_names: list[str]) -> str:
    args = ", ".join(param_names)
    if ret_t == "void" or ret_t not in _C_PRINTF:
        return f"    {function}({args});"
    fmt, cast = _C_PRINTF[ret_t]
    return (
        f"    {ret_t} result = {function}({args});\n"
        f'    printf("{fmt}\\n", {cast}result);'
    )


def _build_fn_ctx(
    cfg: dict, module: str, function: str, name: str, fn: dict
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    params = fn.get("params", [])
    ret_t = fn.get("return_type", "void")
    fn_flags = _fn_flags(params)
    pnames = [p["name"] for p in params]
    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": C.project_version(cfg),
        "module": module,
        "function": function,
        "argparse_state_args": _argparse_block(fn_flags),
        "arg_parse_block": _c_argv_parser(
            name, fn_flags, want_in=False, want_out=False
        ),
        "call_and_print": _c_call_print(function, ret_t, pnames),
        "py_call_args": ", ".join(f"args.{n}" for n in pnames),
    }


# ── subcommand apps ──────────────────────────────────────────────────────────
def _cmd_flag_dicts(flags: list[dict]) -> list[dict]:
    out = []
    for f in flags:
        t = f["type"]
        out.append(
            {
                "name": f["name"],
                "type": t,
                "default": f.get("default")
                or T._CTYPE_META.get(t, {}).get("zero", "0"),
                "help": f.get("help", ""),
                "ctor": False,
            }
        )
    return out


def _c_command_handlers(commands: list[dict]) -> str:
    parts = []
    for c in commands:
        flags = _cmd_flag_dicts(c.get("flags", []))
        parse = _c_argv_parser(c["name"], flags, want_in=False, want_out=False)
        parts.append(
            f"static int\ncmd_{c['name']}(int argc, char *argv[])\n{{\n"
            f"{parse}\n"
            f"    /* <<IMPLEMENT: {c['name']}>> */\n"
            f"    return 0;\n}}"
        )
    return "\n\n".join(parts)


def _c_dispatch(commands: list[dict]) -> str:
    return "\n".join(
        f'    if (!strcmp(argv[1], "{c["name"]}")) {{\n'
        f"        return cmd_{c['name']}(argc - 1, argv + 1);\n"
        f"    }}"
        for c in commands
    )


def _cmd_usage(name: str, commands: list[dict]) -> str:
    names = ", ".join(c["name"] for c in commands)
    return f"usage: {name} <command> [options]  (commands: {names})"


def _py_command_fns(commands: list[dict]) -> str:
    return "\n\n".join(
        f"def _cmd_{c['name']}(args: argparse.Namespace) -> None:\n"
        f"    # <<IMPLEMENT: {c['name']}>>\n"
        f"    _ = args"
        for c in commands
    )


def _py_subparsers(commands: list[dict]) -> str:
    lines = []
    for c in commands:
        var = f"p_{c['name']}"
        lines.append(
            f'    {var} = sub.add_parser("{c["name"]}", '
            f'help="{c.get("help", "")}")'
        )
        for f in _cmd_flag_dicts(c.get("flags", [])):
            pytype = _PYTYPE.get(f["type"], "str")
            pydef = _py_default(f["default"]) if f["default"] else None
            if pydef is None:
                dr = "None"
            elif pytype in ("float", "int", "complex"):
                dr = pydef
            else:
                dr = repr(pydef)
            lines.append(
                f'    {var}.add_argument("--{f["name"]}", type={pytype}, '
                f'default={dr}, help="{f["help"] or f["name"]}")'
            )
        lines.append(f"    {var}.set_defaults(_fn=_cmd_{c['name']})")
    return "\n".join(lines)


def _build_cmd_ctx(
    cfg: dict, name: str, commands: list[dict]
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "version": C.project_version(cfg),
        "helpers": "",
        "command_handlers": _c_command_handlers(commands),
        "dispatch": _c_dispatch(commands),
        "usage": _cmd_usage(name, commands),
        "command_fns": _py_command_fns(commands),
        "subparsers": _py_subparsers(commands),
    }


def _build_ctx(
    cfg: dict,
    component: str,
    name: str,
    target: str,
    flags: list[dict] | None = None,
    argc_argv: bool = False,
    module: str | None = None,
) -> dict[str, str]:
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    Component = _to_title(component)
    # gh-187: the pep723 face imports the class by absolute path; for a module
    # object that's `<pkg>.<module>`, not `<pkg>`. `package` stays the pip
    # distribution name (dependency line); `import_pkg` is the import path.
    import_pkg = f"{pkg}.{module}" if module else pkg

    all_flags = _ctor_flags(cfg, component) + _extra_flags(flags)
    arg_t = C.arg_type(cfg, component)
    ret_t = C.return_type(cfg, component)

    def _create_call(parsed: bool) -> str:
        a = _ctor_c_args(all_flags, parsed)
        return f"{component}_create({a})" if a else f"{component}_create()"

    shape = _app_shape(cfg, component)
    if shape is not None:
        # A generator produces N samples from internal state with no input,
        # driven by a synthetic --count flag; a consumer has no output.
        want_in = shape != "generator"
        want_out = shape != "consumer"
        parse_flags = list(all_flags)
        if shape == "generator":
            parse_flags.append(
                {
                    "name": "count",
                    "type": "size_t",
                    "default": "1024",
                    "help": "number of samples to generate",
                    "ctor": False,
                    "consumed": True,
                }
            )
        argparse_flags = list(parse_flags)
        # gh-184 Tier 2.2: a cf32 output stream gets a built-in --sample_type
        # choice flag + convert-on-write, in every face.
        sample_type = _is_cf32_out(ret_t) and shape in (
            "generator",
            "blockwise",
        )
        if sample_type:
            st_flag = {
                "name": "sample_type",
                "type": "choice",
                "default": "cf32",
                "choices": _SAMPLE_TYPES,
                "help": "output wire sample type",
                "ctor": False,
                "consumed": True,
            }
            # gh-193 (0.17.0): output container + byte order, on the same stream.
            ft_flag = {
                "name": "file_type",
                "type": "choice",
                "default": "raw",
                "choices": ["raw", "csv"],
                "help": "output container",
                "ctor": False,
                "consumed": True,
            }
            en_flag = {
                "name": "endian",
                "type": "choice",
                "default": "le",
                "choices": ["le", "be"],
                "help": "byte order (raw only)",
                "ctor": False,
                "consumed": True,
            }
            for fl in (st_flag, ft_flag, en_flag):
                parse_flags.append(fl)
                argparse_flags.append(fl)
            # --record is a path flag: parsed in C via want_record, surfaced in
            # Python argparse here (str/default None, not CLI-numeric/choice).
            argparse_flags.append(
                {
                    "name": "record",
                    "type": "const char *",
                    "default": "",
                    "help": "write a JSON record of the resolved run",
                    "ctor": False,
                }
            )
        arg_parse_block = _c_argv_parser(
            name,
            parse_flags,
            want_in=want_in,
            want_out=want_out,
            want_record=sample_type,
        )
        create_call = _create_call(parsed=True)
        io_loop = _c_io_loop(
            shape, component, arg_t, ret_t, sample_type=sample_type
        )
        helpers = _c_choice_parsers(parse_flags)
        if sample_type:
            helpers = "\n\n".join(
                x
                for x in (
                    helpers,
                    _c_choice_arrays(parse_flags),
                    _SAMPLE_TYPE_C + _WRITE_BLOCK_C,
                )
                if x
            )
            io_loop = (
                _c_record_block(parse_flags, name, version) + "\n\n" + io_loop
            )
        py_io_loop = R.render(
            _py_io_loop(
                shape,
                component,
                Component,
                arg_t,
                ret_t,
                sample_type=sample_type,
            ),
            {"py_create_args": _py_create_args(all_flags)},
        )
        if sample_type:
            py_io_loop = (
                _py_record_block(parse_flags, name, version)
                + "\n"
                + py_io_loop
            )
        tail = []
        if want_in:
            tail.append("    if (in != stdin) fclose(in);")
        if want_out:
            tail.append("    if (out != stdout) fclose(out);")
        cleanup_tail = "\n".join(tail)
    else:
        # Fall back to a stub for shapes we don't generate a loop for. The
        # --argc-argv opt-in still controls whether an argv-parsing skeleton or
        # a plain (void) suppression is emitted here.
        argparse_flags = all_flags
        arg_parse_block = (
            "    if (argc > 1) {\n"
            "        /* <<IMPLEMENT: parse argv>> */\n"
            "    }"
            if argc_argv
            else "    (void)argc;\n    (void)argv;"
        )
        create_call = _create_call(parsed=False)
        io_loop = (
            "    /* <<IMPLEMENT: read stdin, call step()/steps(), "
            "write stdout>> */"
        )
        py_io_loop = R.render(
            f"    obj = {Component}(<<py_create_args>>)\n"
            "    # <<IMPLEMENT: open input/output, call obj.step(), write>>\n"
            "    _ = obj\n"
            "    sys.exit(0)",
            {"py_create_args": _py_create_args(all_flags)},
        )
        cleanup_tail = ""
        helpers = ""

    return {
        "name": name,
        "project": pkg,
        "package": pkg,
        "import_pkg": import_pkg,
        "version": version,
        "component": component,
        "Component": Component,
        "argparse_state_args": _argparse_block(argparse_flags),
        "py_io_loop": py_io_loop,
        "arg_parse_block": arg_parse_block,
        "io_loop": io_loop,
        "helpers": helpers,
        "app_create_line": f"    {component}_state_t *state = {create_call};",
        "cleanup_tail": cleanup_tail,
        # gh-944: the same closes, one block deeper, for the `create() failed`
        # early return. Derived from cleanup_tail rather than written twice --
        # the two ran out of step once already, which is how a bail-out that
        # held both streams open shipped in the first place.
        "cleanup_tail_deep": "\n".join(
            "    " + ln if ln.strip() else ln
            for ln in cleanup_tail.splitlines()
        ),
    }


def run(
    root: Path,
    cfg: dict | None = None,
    *,
    target: str = "c",
    name: str | None = None,
    object_: str | None = None,
    function_: str | None = None,
    module: str | None = None,
    flags: list[dict] | None = None,
    commands: list[dict] | None = None,
    argc_argv: bool = False,
) -> None:
    if cfg is None:
        cfg_path = root / C.FILENAME
        if not cfg_path.exists():
            print(
                f"error: no {C.FILENAME} found in {root}.\n"
                "Run 'just-makeit new' first.",
                file=sys.stderr,
            )
            sys.exit(1)
        cfg = C.load(root)

    pkg = C.project_name(cfg)
    if not pkg:
        print(
            "error: [project].name missing from just-makeit.toml.",
            file=sys.stderr,
        )
        sys.exit(1)

    if target not in ("c", "console", "pep723"):
        print(
            f"error: unknown target '{target}'. Use c, console, or pep723.",
            file=sys.stderr,
        )
        sys.exit(1)

    if function_ is not None:
        # ── module-function app ──────────────────────────────────────────
        mod, fn = _find_fn(cfg, function_, module)
        if fn is None:
            where = f" in module '{module}'" if module else ""
            print(
                f"error: function '{function_}' not found{where}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not _fn_generatable(fn):
            print(
                f"error: function '{function_}' has non-scalar params or "
                "return; `jm app --function` supports scalar signatures only.",
                file=sys.stderr,
            )
            sys.exit(1)
        if name is None:
            name = function_
        ctx = _build_fn_ctx(cfg, mod, function_, name, fn)
        C.set_app(cfg, target, name, function=function_, module=mod)
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_FN_C,
            R.APP_CONSOLE_CLI_FN,
            R.APP_PEP723_FN,
        )
        link_target = f"{mod}_core " + _LIBM
    elif commands or (object_ is None and C.app_commands(cfg)):
        # ── multi-command app ────────────────────────────────────────────
        if name is None:
            name = pkg
        C.set_app(cfg, target, name)
        for c in commands or []:
            C.add_app_command(cfg, c)
        eff_cmds = C.app_commands(cfg)
        if not eff_cmds:
            print("error: no commands declared.", file=sys.stderr)
            sys.exit(1)
        ctx = _build_cmd_ctx(cfg, name, eff_cmds)
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_CMD_C,
            R.APP_CONSOLE_CLI_CMD,
            R.APP_PEP723_CMD,
        )
        # Stub command bodies link the project's aggregate static lib so any
        # component/function symbol is reachable once the user fills them in.
        link_target = f"{pkg.replace('-', '_')}_lib_static"
    else:
        # ── object app ───────────────────────────────────────────────────
        comps = C.components(cfg)
        if object_ is None:
            if not comps:
                print(
                    "error: no components found — run "
                    "'just-makeit object' first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            object_ = comps[0]
        elif object_ not in comps:
            print(f"error: object '{object_}' not found.", file=sys.stderr)
            sys.exit(1)
        if name is None:
            name = pkg
        # Persist + merge flags before codegen so stored [[app.flags]] from
        # prior runs are reflected in the generated parsers (reproducible).
        C.set_app(cfg, target, name, object_=object_, module=module)
        for f in flags or []:
            C.add_app_flag(cfg, f)
        ctx = _build_ctx(
            cfg,
            object_,
            name,
            target,
            flags=C.app_flags(cfg),
            argc_argv=argc_argv,
            module=module,
        )
        main_tmpl, console_tmpl, pep_tmpl = (
            R.APP_MAIN_C,
            R.APP_CONSOLE_CLI,
            R.APP_PEP723,
        )
        link_target = _app_object_link(cfg, object_)

    print(f"just-makeit: scaffolding app '{name}' (target={target})")
    print()

    if target == "c":
        # Only the C target adds an add_executable() that can clash with a
        # module/component CMake target; console/pep723 faces don't (gh-184).
        _run_c(
            root,
            ctx,
            name,
            link_target,
            main_tmpl,
            _exe_target(name, cfg, root),
        )
    elif target == "console":
        _run_console(
            root,
            ctx,
            name,
            pkg,
            console_tmpl,
            module=C.app_config(cfg).get("module") or None,
        )
    else:
        _run_pep723(root, ctx, name, pep_tmpl)

    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")
    print()
    _print_summary(target, root, name, pkg)


def _run_c(
    root: Path,
    ctx: dict,
    name: str,
    link_target: str,
    tmpl: str = R.APP_MAIN_C,
    exe_target: str | None = None,
) -> None:
    app_dir = root / "native" / "src" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    main_c = app_dir / f"{name}.c"
    rendered = R.render(tmpl, ctx)
    # gh-962: this file is regenerated wholesale, by `jm app` AND by every
    # `jm apply` (the replay re-runs the verb from `[app]`). Nothing preserves
    # a body here the way `_restore_c_function_bodies` preserves `_core.c`, and
    # there is no `_extra.c` escape hatch for an app — so an edit is simply
    # lost. Say so at the moment it happens, naming the file.
    #
    # Reported rather than refused: refusing would leave a stale app no command
    # could refresh, and `apply`'s whole contract is to reconcile. The author
    # is told what was discarded and where the logic belongs instead.
    #
    # Only when the bytes actually differ, so a re-run over an untouched
    # scaffold — which is most of them — stays quiet.
    _existed = main_c.exists()
    if _existed and main_c.read_text(encoding="utf-8") != rendered:
        _report.warn(
            f"{main_c}: this app is regenerated from `[app]` in the manifest,"
            " by `jm app` and by every `jm apply`, and your edits to it have"
            " just been discarded. Nothing preserves a body here — put custom"
            " logic in a component (`jm method`) and call it from the"
            " generated main(), or keep your own copy outside native/src/app/."
        )
    main_c.write_text(rendered, encoding="utf-8")
    # gh-962: computed BEFORE the write. It was after, so `exists()` was
    # trivially true and a first-time scaffold announced itself as `update`.
    print(f"  {'update' if _existed else 'create'}  {main_c}")

    tgt = exe_target or name
    if tgt != name:
        print(
            f"  note: target name '{name}' is already used by another target; "
            f"using exe target '{tgt}' (binary stays '{name}')."
        )
    cmake = root / "CMakeLists.txt"
    if cmake.exists():
        _splice_cmake(cmake, name, link_target, exe_target)
        print(f"  update  {cmake}")
    else:
        out_name = (
            f"\n    set_target_properties({tgt} PROPERTIES OUTPUT_NAME {name})"
            if tgt != name
            else ""
        )
        print(
            f"  note: CMakeLists.txt not found — add this manually:\n"
            f"    add_executable({tgt} native/src/app/{name}.c){out_name}\n"
            f"    target_link_libraries({tgt} PRIVATE {link_target})"
        )


def _run_console(
    root: Path,
    ctx: dict,
    name: str,
    pkg: str,
    tmpl: str = R.APP_CONSOLE_CLI,
    module: str | None = None,
) -> None:
    # gh-187: scope the console module under its owning subpackage when the app
    # is built from a module object/function, so it never collides with a
    # `src/<pkg>/cli.py` already used by a `cli` subpackage.
    cli_dir = root / "src" / pkg
    if module:
        cli_dir = cli_dir / module
    cli_py = cli_dir / "cli.py"
    dotted = f"{pkg}.{module}.cli" if module else f"{pkg}.cli"
    cli_py.parent.mkdir(parents=True, exist_ok=True)
    cli_py.write_text(R.render(tmpl, ctx), encoding="utf-8")
    verb = "update" if cli_py.exists() else "create"
    print(f"  {verb}  {cli_py}")

    updated = _update_pyproject_scripts(root, name, pkg, module)
    if updated:
        print(f"  update  {root / 'pyproject.toml'}")
    else:
        print(
            f"  note: add to pyproject.toml manually:\n"
            f"    [project.scripts]\n"
            f'    {name} = "{dotted}:main"'
        )


def _run_pep723(
    root: Path, ctx: dict, name: str, tmpl: str = R.APP_PEP723
) -> None:
    script = root / f"{name}.py"
    script.write_text(R.render(tmpl, ctx), encoding="utf-8")
    verb = "update" if script.exists() else "create"
    print(f"  {verb}  {script}")


def _print_summary(target: str, root: Path, name: str, pkg: str) -> None:
    if target == "c":
        print("Done!  C executable scaffold created.")
        print(f"  Build:  make && ./build/{name}")
    elif target == "console":
        print("Done!  Console script scaffold created.")
        print("  Install:  pip install -e .")
        print(f"  Run:      {name} --help")
    else:
        print(f"Done!  PEP 723 script created: {name}.py")
        print(f"  Run:      uv run {name}.py --help")
        print(f"  Share:    distribute {name}.py — no install needed")
        print(f"  Note:     requires {pkg} on PyPI")
