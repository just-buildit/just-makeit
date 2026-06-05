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
    cli = (proj / "src" / "proj" / "cli.py").read_text()
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
