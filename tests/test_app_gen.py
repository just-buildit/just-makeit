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
