"""Unit tests for `jm app` CLI-parser/I-O-loop generation (no build).

Scaffolding-level checks: the generated C and Python faces contain a real
argument parser and step() loop (not an <<IMPLEMENT>> stub) for scalar objects,
extra --flag specs reach both parsers and round-trip through the manifest, the
CMake block is idempotent, and non-scalar shapes fall back to a stub.
"""

from __future__ import annotations

from pathlib import Path

from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object
from just_makeit._function import run as jm_function
from just_makeit._app import run as jm_app
from just_makeit import _config as C


def _scaffold(root: Path, **obj_kw) -> Path:
    proj = root / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "gain",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type=obj_kw.pop("arg_type", "float"),
        return_type=obj_kw.pop("return_type", "float"),
        impl_body="return state->gain * x;",
        **obj_kw,
    )
    return proj


def test_scalar_object_generates_working_faces(tmp_path: Path):
    proj = _scaffold(tmp_path)
    jm_app(proj, target="c", name="tool", object_="gain")
    jm_app(proj, target="console", name="tool", object_="gain")

    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()

    # No stub left behind.
    assert "<<IMPLEMENT" not in c
    assert "<<IMPLEMENT" not in cli
    # ctor state var -> flag, wired into create(), with a real step loop.
    assert '"--gain"' in c
    assert "float gain = 1.0f;" in c
    assert "gain_create(gain)" in c
    assert "gain_step(state, x)" in c
    assert "fread(&x" in c and "fwrite(&y" in c
    # Python parity.
    assert '"--gain"' in cli
    assert "Gain(gain=args.gain)" in cli
    assert "obj.step(x)" in cli
    assert "np.float32" in cli


def test_extra_flag_reaches_both_parsers_and_roundtrips(tmp_path: Path):
    proj = _scaffold(tmp_path)
    jm_app(
        proj,
        target="c",
        name="tool",
        object_="gain",
        flags=[
            {"name": "thr", "type": "float", "default": "0.5", "help": "t"}
        ],
    )
    # Second run without --flag must preserve the stored flag.
    jm_app(proj, target="console", name="tool", object_="gain")

    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()

    assert "float thr = 0.5;" in c and '"--thr"' in c
    assert "(void)thr;" in c  # extra flag unused by the loop -> warning-safe
    assert '"--thr"' in cli  # persisted across the 2nd run
    # Round-trips through the manifest.
    cfg = C.load(proj)
    flags = C.app_flags(cfg)
    assert flags == [
        {"name": "thr", "type": "float", "default": "0.5", "help": "t"}
    ]
    assert "[[app.flags]]" in (proj / "just-makeit.toml").read_text()


def test_cmake_block_is_idempotent(tmp_path: Path):
    proj = _scaffold(tmp_path)
    jm_app(proj, target="c", name="tool", object_="gain")
    jm_app(proj, target="c", name="tool", object_="gain")
    cmake = (proj / "CMakeLists.txt").read_text()
    assert cmake.count("add_executable(tool") == 1


def test_pep723_target_is_generated(tmp_path: Path):
    proj = _scaffold(tmp_path)
    jm_app(proj, target="pep723", name="tool", object_="gain")
    script = (proj / "tool.py").read_text()
    assert "# /// script" in script
    assert "from proj import Gain" in script
    assert "obj.step(x)" in script and "<<IMPLEMENT" not in script


def test_int_flag_uses_strtol_and_int_argparse(tmp_path: Path):
    proj = _scaffold(tmp_path)
    jm_app(
        proj,
        target="c",
        name="tool",
        object_="gain",
        flags=[{"name": "taps", "type": "int32_t", "default": "8"}],
    )
    jm_app(proj, target="console", name="tool", object_="gain")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    assert "int32_t taps = 8;" in c and "strtol(argv[++i]" in c
    assert "--taps" in cli and "type=int" in cli


def test_non_parseable_ctor_uses_default_literal(tmp_path: Path):
    # A complex ctor state var can't be a CLI flag → C create passes its
    # default literal inline (commented), not an undeclared variable.
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "mix",
        None,
        state_vars=[("g", "float", "1.0f"), ("c0", "float _Complex", "0")],
        arg_type="float",
        return_type="float",
        impl_body="return state->g * x;",
    )
    jm_app(proj, target="c", name="tool", object_="mix")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    assert "/* c0= */0" in c  # complex ctor flag falls back to default literal
    assert "float g = 1.0f;" in c  # float ctor flag still parsed


def test_no_step_console_falls_back_to_stub(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "sink",
        None,
        state_vars=[("count", "uint32_t", "0")],
        no_step=True,
    )
    jm_app(proj, target="console", name="tool", object_="sink")
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    assert "<<IMPLEMENT" in cli and "sys.exit(0)" in cli


def test_blockwise_shape_generates_steps_loop(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "bw",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float[]",
        return_type="float[]",
    )
    jm_app(proj, target="c", name="tool", object_="bw")
    jm_app(proj, target="console", name="tool", object_="bw")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    assert "<<IMPLEMENT" not in c
    assert "bw_steps(state, inbuf, k, outbuf)" in c
    assert "fread(inbuf" in c and "fwrite(outbuf" in c
    assert "obj.steps(data)" in cli and "out.tofile" in cli


def test_consumer_shape_no_output(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "cons",
        None,
        state_vars=[("sum", "float", "0.0f")],
        arg_type="float",
        return_type="void",
        mutable=True,
    )
    jm_app(proj, target="c", name="tool", object_="cons")
    jm_app(proj, target="console", name="tool", object_="cons")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    assert "cons_steps(state, inbuf, k)" in c
    assert "fwrite" not in c and '"--output"' not in c  # no output side
    assert "obj.steps(data)" in cli and "out.tofile" not in cli


def test_generator_shape_uses_count(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "gen",
        None,
        state_vars=[("inc", "float", "1.0f")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    jm_app(proj, target="c", name="tool", object_="gen")
    jm_app(proj, target="console", name="tool", object_="gen")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    assert "gen_steps(state, outbuf, k)" in c
    assert "produced < count" in c and '"--count"' in c
    assert "fread" not in c and '"--input"' not in c  # no input side
    assert "obj.steps(args.count)" in cli
    assert "--count" in cli


def test_function_app_generates_call_and_print(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj, modules=["mathx"])
    jm_function(
        proj,
        "addn",
        module="mathx",
        params=[("a", "float"), ("b", "float")],
        return_type="float",
        impl_body="return a + b;",
    )
    jm_app(proj, target="c", name="addtool", function_="addn")
    jm_app(proj, target="console", name="addtool", function_="addn")
    c = (proj / "native" / "src" / "app" / "addtool.c").read_text()
    # gh-187: a module-function console face lives under its module subpackage.
    cli = (proj / "src" / "proj" / "mathx" / "cli.py").read_text()
    # C: includes the module core, parses each param, calls + prints result.
    assert "mathx/mathx_core.h" in c
    assert "float a = " in c and '"--a"' in c
    assert "float result = addn(a, b);" in c
    assert "printf(" in c
    # Python: imports from the module subpackage, required args, prints result.
    assert "from .mathx import addn" in cli
    assert "required=True" in cli
    assert "print(addn(args.a, args.b))" in cli
    # CMake links the module core; manifest records the function source.
    cmake = (proj / "CMakeLists.txt").read_text()
    assert "add_executable(addtool" in cmake and "mathx_core" in cmake
    app = C.app_config(C.load(proj))
    assert app["function"] == "addn" and app["module"] == "mathx"


def test_command_app_generates_dispatch(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    cmds = [
        {
            "name": "encode",
            "help": "encode input",
            "flags": [{"name": "rate", "type": "int32_t", "default": "48000"}],
        },
        {"name": "info", "help": "print info"},
    ]
    jm_app(proj, target="c", name="tool", commands=cmds)
    jm_app(proj, target="console", name="tool", commands=[])  # persist
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    cli = (proj / "src" / "proj" / "cli.py").read_text()
    # C: per-command handlers + flag parse + dispatch + usage.
    assert "static int\ncmd_encode(" in c and "static int\ncmd_info(" in c
    assert "int32_t rate = 48000;" in c and '"--rate"' in c
    assert 'if (!strcmp(argv[1], "encode"))' in c
    assert "commands: encode, info" in c
    assert "<<IMPLEMENT: encode>>" in c  # body is a stub (user logic)
    # Python: subparsers + per-command fns + set_defaults dispatch.
    assert 'add_subparsers(dest="command", required=True)' in cli
    assert 'sub.add_parser("encode"' in cli
    assert 'p_encode.add_argument("--rate"' in cli
    assert "set_defaults(_fn=_cmd_encode)" in cli
    # Manifest round-trips the commands.
    got = C.app_commands(C.load(proj))
    assert [c["name"] for c in got] == ["encode", "info"]
    assert "[[app.commands]]" in (proj / "just-makeit.toml").read_text()


def test_function_app_rejects_unknown_function(tmp_path: Path):
    import pytest

    proj = tmp_path / "proj"
    jm_new("proj", proj, modules=["mathx"])
    with pytest.raises(SystemExit):
        jm_app(proj, target="c", name="x", function_="nope")


def test_no_step_object_falls_back_to_stub(tmp_path: Path):
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "sink",
        None,
        state_vars=[("count", "uint32_t", "0")],
        no_step=True,
    )
    jm_app(proj, target="c", name="tool", object_="sink")
    c = (proj / "native" / "src" / "app" / "tool.c").read_text()
    # Fallback: stub body, and create() uses the default literal (no undeclared
    # variable), so the file still compiles.
    assert "<<IMPLEMENT" in c
    assert "(void)argc;" in c
    assert "sink_step" not in c


# ── gh-184: ctor flags from init_params + [app] round-trips through apply ─────
def test_init_param_ctor_flags(tmp_path: Path):
    """A generator whose ctor args are init_params (no_state) gets a flag per
    init param, and create() is called with them — not create() with no args."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "gen",
        None,
        no_state=True,
        init_params=[("type", "int", "0"), ("fs", "double", "1e6")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="c", name="gentool", object_="gen")
    app_c = (proj / "native/src/app/gentool.c").read_text()
    assert "gen_create(type, fs)" in app_c
    assert "--type" in app_c and "--fs" in app_c


def test_app_record_survives_apply(tmp_path: Path):
    """`jm apply` re-materialises the recorded [app], not a default one."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "a",
        None,
        state_vars=[("g", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    jm_object(
        proj,
        "gen",
        None,
        no_state=True,
        init_params=[("type", "int", "0")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="c", name="gentool", object_="gen")
    jm_apply(proj)  # used to clobber [app] -> a/<project>
    rec = C.app_config(C.load(proj))
    assert rec.get("name") == "gentool" and rec.get("object") == "gen"
    assert (proj / "native/src/app/gentool.c").exists()
    # no stray default app for the first object / project name
    assert not (proj / "native/src/app/proj.c").exists()


# ── gh-184 Tier 2: dtype output (--sample_type), choice flags, --help ─────────
def _gen_proj(tmp_path: Path):
    """A cf32 generator object (the wavegen shape)."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "gen",
        None,
        no_state=True,
        init_params=[("freq", "double", "0.0")],
        arg_type="void",
        return_type="float _Complex",
        mutable=True,
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    return proj


def test_sample_type_dtype_output_c(tmp_path: Path):
    proj = _gen_proj(tmp_path)
    jm_app(proj, target="c", name="tool", object_="gen")
    c = (proj / "native/src/app/tool.c").read_text()
    assert "jm_convert_block(outbuf, k, sample_type" in c
    assert "jm_parse_sample_type" in c
    assert "[--sample_type cf32|cf64|ci32|ci16|ci8]" in c
    # the convert helper + clamp are emitted before main
    assert "static size_t\njm_convert_block(" in c
    assert c.index("jm_convert_block(const") < c.index("int\nmain(")


def test_sample_type_dtype_output_python(tmp_path: Path):
    proj = _gen_proj(tmp_path)
    jm_app(proj, target="console", name="tool", object_="gen")
    jm_app(proj, target="pep723", name="tool", object_="gen")
    cli = (proj / "src/proj/cli.py").read_text()
    assert "choices=['cf32', 'cf64', 'ci32', 'ci16', 'ci8']" in cli
    assert "_buf = (_iq * _scale).astype(_dt).tobytes()" in cli
    import py_compile

    py_compile.compile(str(proj / "src/proj/cli.py"), doraise=True)
    py_compile.compile(str(proj / "tool.py"), doraise=True)


def test_c_app_has_help(tmp_path: Path):
    proj = _gen_proj(tmp_path)
    jm_app(proj, target="c", name="tool", object_="gen")
    c = (proj / "native/src/app/tool.c").read_text()
    assert '!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")' in c
    assert "fputs(" in c and "return 0;" in c


def test_sample_type_blockwise(tmp_path: Path):
    """A cf32 block (blockwise) app also gets --sample_type convert-on-write."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "flt",
        None,
        state_vars=[("g", "float", "1.0")],
        arg_type="float _Complex[]",
        return_type="float _Complex[]",
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="c", name="t", object_="flt")
    c = (proj / "native/src/app/t.c").read_text()
    assert "jm_convert_block(outbuf, k, sample_type" in c


def test_no_sample_type_for_real_output(tmp_path: Path):
    """A non-cf32 (real float) generator gets no --sample_type machinery."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "g",
        None,
        no_state=True,
        init_params=[("f", "double", "1.0")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="c", name="t", object_="g")
    c = (proj / "native/src/app/t.c").read_text()
    assert "jm_convert_block" not in c and "sample_type" not in c


def test_ctor_flags_string_enum_and_array(tmp_path: Path):
    """_ctor_flags: string-enum init param → choice flag; array → skipped."""
    from just_makeit import _app

    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_object(
        proj,
        "se",
        None,
        state_vars=[("g", "float", "1.0")],
        init_params=[
            ("mode", "string_enum:tone,noise", "tone"),
            ("taps", "float[]", ""),
            ("n", "int", "8"),
        ],
        arg_type="float",
        return_type="float",
    )
    cfg = C.load(proj)
    flags = _app._ctor_flags(cfg, "se")
    names = [f["name"] for f in flags]
    assert "mode" in names and "n" in names
    assert "taps" not in names  # array init params have no scalar CLI form
    mode = next(f for f in flags if f["name"] == "mode")
    assert mode.get("choices") == ["tone", "noise"]


# ── gh-187: object apps link depends_on cores + libm; module console scoping ──
def test_object_app_links_depends_on_cores(tmp_path: Path):
    """An object with depends_on gets those cores + libm on the app link line
    (OBJECT libs don't propagate their deps to the exe)."""
    proj = tmp_path / "proj"
    jm_new("proj", proj)
    # a leaf dependency core...
    jm_object(
        proj,
        "lo",
        None,
        state_vars=[("f", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    # ...and an object that depends on it
    jm_object(
        proj,
        "eng",
        None,
        state_vars=[("g", "float", "1.0")],
        depends_on=["lo"],
        arg_type="float",
        return_type="float",
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="c", name="tool", object_="eng")
    cmake = (proj / "CMakeLists.txt").read_text()
    line = next(
        ln for ln in cmake.splitlines() if "target_link_libraries(tool" in ln
    )
    assert "eng_core" in line and "lo_core" in line
    assert "PLATFORM_ID:Windows" in line and ">:m>" in line  # conditional libm


def test_module_object_console_scoped_to_module(tmp_path: Path):
    """A module object's console face lives under src/<pkg>/<module>/ and its
    entry point is <pkg>.<module>.cli:main (gh-187 — avoids a cli collision)."""
    from just_makeit._module import run as jm_module

    proj = tmp_path / "proj"
    jm_new("proj", proj)
    jm_module(proj, "wfm")
    jm_object(
        proj,
        "gen",
        "wfm",
        state_vars=[("g", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)
    jm_app(proj, target="console", name="tool", object_="gen", module="wfm")
    assert (proj / "src" / "proj" / "wfm" / "cli.py").exists()
    assert not (proj / "src" / "proj" / "cli.py").exists()
    pyproject = (proj / "pyproject.toml").read_text()
    assert "proj.wfm.cli:main" in pyproject
