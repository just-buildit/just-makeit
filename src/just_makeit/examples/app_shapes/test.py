"""End-to-end test: `jm app` generates working C binaries for non-scalar
object shapes — blockwise (steps buffer→buffer) and generator (--count → N
samples). Companion to `three_face` (scalar). Each shape is built in its own
project (jm app keeps one app per project) and the binary is run on real bytes.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/app_shapes/test.py
"""

import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def _cmd(args, cwd):
    r = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def _build(proj: Path) -> None:
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)


def _exe(proj: Path, name: str) -> Path:
    return proj / "build" / name


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._function import run as jm_function
    from just_makeit._app import run as jm_app

    # ── blockwise: scale a float32 stream by --g (steps buffer→buffer) ───
    bw = root / "bw"
    jm_new("bw", bw)
    jm_object(
        bw,
        "scale",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float[]",
        return_type="float[]",
    )
    # blockwise transform lives in steps()'s <<IMPLEMENT>> (not the inline
    # step()), so fill it directly — the point here is the generated app, not
    # the kernel.
    core = bw / "native" / "src" / "scale" / "scale_core.c"
    core.write_text(
        core.read_text().replace(
            "out[i] = (float)in[i];", "out[i] = state->g * in[i];"
        ),
        encoding="utf-8",
    )
    jm_app(bw, target="c", name="scaletool", object_="scale")
    app_c = (bw / "native" / "src" / "app" / "scaletool.c").read_text()
    assert "scale_steps(state, inbuf, k, outbuf)" in app_c
    _build(bw)
    r = subprocess.run(
        [str(_exe(bw, "scaletool")), "--g", "3"],
        input=struct.pack("<4f", 1, 2, 3, 4),
        capture_output=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stderr
    assert list(struct.unpack("<4f", r.stdout)) == [3.0, 6.0, 9.0, 12.0]

    # ── generator: produce N ramp samples (void→float, --count) ──────────
    gen = root / "gen"
    jm_new("gen", gen)
    jm_object(
        gen,
        "ramp",
        None,
        state_vars=[("acc", "float", "0.0f"), ("inc", "float", "1.0f")],
        arg_type="void",
        return_type="float",
        mutable=True,
        impl_body="float v = state->acc; state->acc += state->inc; return v;",
    )
    jm_app(gen, target="c", name="ramptool", object_="ramp")
    app_c = (gen / "native" / "src" / "app" / "ramptool.c").read_text()
    assert (
        "ramp_steps(state, outbuf, k)" in app_c and "produced < count" in app_c
    )
    _build(gen)
    r = subprocess.run(
        [str(_exe(gen, "ramptool")), "--inc", "2", "--count", "5"],
        capture_output=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stderr
    assert list(struct.unpack("<5f", r.stdout)) == [0.0, 2.0, 4.0, 6.0, 8.0]

    # ── module function: `jm app --function` → parse flags, call, print ──
    fn = root / "fn"
    jm_new("fn", fn, modules=["mathx"])
    jm_function(
        fn,
        "addn",
        module="mathx",
        params=[("a", "float"), ("b", "float")],
        return_type="float",
        impl_body="return a + b;",
    )
    jm_app(fn, target="c", name="addtool", function_="addn")
    app_c = (fn / "native" / "src" / "app" / "addtool.c").read_text()
    assert "float result = addn(a, b);" in app_c
    _build(fn)
    r = subprocess.run(
        [str(_exe(fn, "addtool")), "--a", "2", "--b", "3"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "5"

    # ── subcommands: `jm app` with [[app.commands]] → dispatch scaffold ──
    # A realistic multi-command tool wraps a real component (so it also builds
    # a Python extension); the command bodies are stubs the user wires up.
    # (project name avoids stdlib module names like `cmd` that a `src/<pkg>/`
    # package would shadow under pytest.)
    multi = root / "multi"
    jm_new("multi", multi)
    jm_object(
        multi,
        "engine",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        impl_body="return state->gain * x;",
    )
    jm_app(
        multi,
        target="c",
        name="cmdtool",
        commands=[
            {
                "name": "encode",
                "help": "encode input",
                "flags": [
                    {"name": "rate", "type": "int32_t", "default": "48000"}
                ],
            },
            {"name": "info", "help": "print info"},
        ],
    )
    app_c = (multi / "native" / "src" / "app" / "cmdtool.c").read_text()
    assert 'if (!strcmp(argv[1], "encode"))' in app_c
    _build(multi)
    exe = _exe(multi, "cmdtool")
    # A declared subcommand exits 0 (stub body); no command prints usage (2).
    assert (
        subprocess.run(
            [str(exe), "encode", "--rate", "44100"], timeout=600
        ).returncode
        == 0
    )
    assert (
        subprocess.run([str(exe)], capture_output=True, timeout=600).returncode
        == 2
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("app_shapes: PASSED")
