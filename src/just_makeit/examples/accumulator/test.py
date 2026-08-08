"""End-to-end test: accumulator scaffold -> implement -> build -> verify.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/accumulator/test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _make_env():
    return {**os.environ, "PYTHON": Path(sys.executable).as_posix()}


def _cmd(args, cwd, **kw):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600, **kw
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._apply import run as apply_run
    from just_makeit._method import run as method_run
    from just_makeit._module import run as module_run
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    dest = root / "my_acc"

    # ── 1. Scaffold project + accumulator module ──────────────────────────────
    new_run("my_acc", dest)
    module_run(dest, "accumulator")

    toml = (dest / "just-makeit.toml").read_text()
    assert "[module.accumulator]" in toml, "module entry missing"
    assert (dest / "src" / "my_acc" / "accumulator" / "__init__.py").exists()

    # ── 2. Add AccF32 object ──────────────────────────────────────────────────
    object_run(
        dest,
        "acc_f32",
        module="accumulator",
        arg_type="float",
        return_type="void",
        state_vars=[("acc", "float", "0.0f")],
        mutable=True,
    )

    frag_f32 = (
        dest / "native" / "src" / "accumulator" / "accumulator_ext_acc_f32.c"
    ).read_text()
    assert "AccF32Object" in frag_f32
    assert "AccCf64Object" not in frag_f32

    # ── 3. Add AccCf64 object ─────────────────────────────────────────────────
    object_run(
        dest,
        "acc_cf64",
        module="accumulator",
        arg_type="double _Complex",
        return_type="void",
        state_vars=[("acc", "double _Complex", "0.0 + 0.0 * I")],
        mutable=True,
    )

    frag_f32 = (
        dest / "native" / "src" / "accumulator" / "accumulator_ext_acc_f32.c"
    ).read_text()
    frag_cf64 = (
        dest / "native" / "src" / "accumulator" / "accumulator_ext_acc_cf64.c"
    ).read_text()
    assert "AccF32Object" in frag_f32
    assert "AccCf64Object" in frag_cf64

    init_py = (
        dest / "src" / "my_acc" / "accumulator" / "__init__.py"
    ).read_text()
    assert "AccF32" in init_py
    assert "AccCf64" in init_py

    toml = (dest / "just-makeit.toml").read_text()
    assert '"acc_f32"' in toml
    assert '"acc_cf64"' in toml

    # ── 4. Add named methods ──────────────────────────────────────────────────
    # All array-input methods use arg_type="void" + params to correctly expand
    # "type[]" to "const type *name, size_t name_len" in the C signature.
    for obj, elem_type, scalar_rt in [
        ("acc_f32", "float[]", "float"),
        ("acc_cf64", "double _Complex[]", "double _Complex"),
    ]:
        method_run(
            dest,
            obj,
            "get",
            module="accumulator",
            arg_type="void",
            return_type=scalar_rt,
            variable_output=False,
            multi_output=[],
        )
        method_run(
            dest,
            obj,
            "dump",
            module="accumulator",
            arg_type="void",
            return_type=scalar_rt,
            variable_output=False,
            multi_output=[],
        )
        method_run(
            dest,
            obj,
            "madd",
            module="accumulator",
            arg_type="void",
            return_type="void",
            variable_output=False,
            multi_output=[],
            params=[("x", elem_type), ("h", "float[]")],
        )
        method_run(
            dest,
            obj,
            "add2d",
            module="accumulator",
            arg_type="void",
            return_type="void",
            variable_output=False,
            multi_output=[],
            params=[("x", elem_type)],
        )
        method_run(
            dest,
            obj,
            "madd2d",
            module="accumulator",
            arg_type="void",
            return_type="void",
            variable_output=False,
            multi_output=[],
            params=[("x", elem_type), ("h", "float[]")],
        )

    # Verify stubs were appended to both core.c files
    core_f32 = (
        dest / "native" / "src" / "acc_f32" / "acc_f32_core.c"
    ).read_text()
    for name in ("get", "dump", "madd", "add2d", "madd2d"):
        assert f"<<IMPLEMENT: {name} >>" in core_f32, (
            f"stub for {name} missing from acc_f32_core.c"
        )

    core_cf64 = (
        dest / "native" / "src" / "acc_cf64" / "acc_cf64_core.c"
    ).read_text()
    for name in ("get", "dump", "madd", "add2d", "madd2d"):
        assert f"<<IMPLEMENT: {name} >>" in core_cf64, (
            f"stub for {name} missing from acc_cf64_core.c"
        )

    # ── 5. Patch stubs with real implementations ──────────────────────────────
    _cmd([sys.executable, str(STEPS / "04_patch_f32.py")], cwd=dest)
    _cmd([sys.executable, str(STEPS / "04_patch_cf64.py")], cwd=dest)

    # Verify the step stubs were replaced
    h_f32 = (
        dest / "native" / "inc" / "acc_f32" / "acc_f32_core.h"
    ).read_text()
    assert "state->acc += x;" in h_f32, "acc_f32_step not patched"

    h_cf64 = (
        dest / "native" / "inc" / "acc_cf64" / "acc_cf64_core.h"
    ).read_text()
    assert "state->acc += x;" in h_cf64, "acc_cf64_step not patched"

    # ── 5b. Enrich the headers with Doxygen, regenerate the stubs ─────────────
    # The sacred header is the single source of truth for docs: hand-written
    # @brief/@param/@return/@code comments on create() and the named methods
    # become rich numpy-style .pyi docstrings, and a method's @code block
    # becomes a runnable doctest. `jm apply` re-derives the glue (.pyi included)
    # from the edited headers.
    _cmd([sys.executable, str(STEPS / "04b_doxygen.py")], cwd=dest)
    apply_run(dest)

    # ── 6. Build ──────────────────────────────────────────────────────────────
    _cmd(["make"], cwd=dest, env=_make_env())

    # ── 7. C tests ────────────────────────────────────────────────────────────
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=dest)

    # ── 8. Python correctness checks ──────────────────────────────────────────
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys; sys.path.insert(0, 'src')
import numpy as np
from my_acc.accumulator import AccF32, AccCf64

# AccF32: step == push
f = AccF32()
f.step(np.float32(1.0))
f.step(np.float32(2.0))
f.step(np.float32(3.0))
assert abs(f.get() - 6.0) < 1e-5, f"get after 3 pushes: {f.get()}"

# steps == batch add
f.reset()
f.steps(np.ones(100, dtype=np.float32))
assert abs(f.get() - 100.0) < 1e-5, f"steps(ones*100): {f.get()}"

# dump: atomic get + reset
f.reset()
f.step(np.float32(42.0))
v = f.dump()
assert abs(v - 42.0) < 1e-5, f"dump value: {v}"
assert abs(f.get()) < 1e-5, f"get after dump: {f.get()}"

# madd: weighted sum
f.reset()
x = np.array([1, 2, 3, 4], dtype=np.float32)
h = np.array([0.25, 0.25, 0.25, 0.25], dtype=np.float32)
f.madd(x, h)
assert abs(f.get() - 2.5) < 1e-5, f"madd result: {f.get()}"

# add2d: accumulate a flat array
f.reset()
f.add2d(np.ones(50, dtype=np.float32))
assert abs(f.get() - 50.0) < 1e-5, f"add2d result: {f.get()}"

# madd2d: weighted accumulate
f.reset()
x2 = np.array([2.0, 4.0], dtype=np.float32)
h2 = np.array([0.5, 0.25], dtype=np.float32)
f.madd2d(x2, h2)
assert abs(f.get() - 2.0) < 1e-5, f"madd2d result: {f.get()}"

# AccCf64: step with complex
c = AccCf64()
c.step(1 + 2j)
c.step(3 + 4j)
g = c.get()
assert abs(g.real - 4.0) < 1e-10, f"cf64 real: {g.real}"
assert abs(g.imag - 6.0) < 1e-10, f"cf64 imag: {g.imag}"

# AccCf64 dump: reset, push one sample, dump returns it and zeroes
c.reset()
c.step(5 + 6j)
dv = c.dump()
assert abs(dv.real - 5.0) < 1e-10, f"cf64 dump real: {dv.real}"
assert abs(dv.imag - 6.0) < 1e-10, f"cf64 dump imag: {dv.imag}"
assert abs(c.get().real) < 1e-10, f"cf64 after dump: {c.get()}"

# AccCf64 madd: complex signal, real weights
c.reset()
sig = np.array([1 + 1j, 2 + 2j, 3 + 3j], dtype=np.complex128)
w = np.array([1.0, 0.5, 0.25], dtype=np.float32)
c.madd(sig, w)
# (1+1j)*1.0 + (2+2j)*0.5 + (3+3j)*0.25 = (2.75+2.75j)
g2 = c.get()
assert abs(g2.real - 2.75) < 1e-10, f"cf64 madd real: {g2.real}"
assert abs(g2.imag - 2.75) < 1e-10, f"cf64 madd imag: {g2.imag}"

# AccCf64 steps == batch add
c.reset()
batch = np.array([1+0j, 0+1j, 1+1j], dtype=np.complex128)
c.steps(batch)
g3 = c.get()
assert abs(g3.real - 2.0) < 1e-10, f"cf64 steps real: {g3.real}"
assert abs(g3.imag - 2.0) < 1e-10, f"cf64 steps imag: {g3.imag}"

print("accumulator: all checks passed")
""",
        ],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Python correctness checks failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    print(result.stdout.strip())

    # ── 9. Type stub checks ───────────────────────────────────────────────────
    pyi = (
        dest / "src" / "my_acc" / "accumulator" / "accumulator.pyi"
    ).read_text()
    assert "class AccF32:" in pyi
    assert "class AccCf64:" in pyi
    assert "def step(self" in pyi
    assert "def steps(self" in pyi
    assert "def get(self" in pyi
    assert "def dump(self" in pyi

    # The Doxygen enrichment (step 5b) reached the stub: class summaries from
    # create()'s @brief, method prose from @brief/@param/@return, and a @code
    # block on get()/madd() rendered as a runnable Examples doctest.
    assert "Create a 32-bit float accumulator" in pyi, "class @brief missing"
    assert "Return the current accumulated sum." in pyi, (
        "method @brief missing"
    )
    assert ">>> a.get()" in pyi and "6.0" in pyi, "get() @code doctest missing"
    assert "(4+6j)" in pyi, "AccCf64 get() @code doctest missing"

    # ── 10. The header-authored doctests actually run against the built .so ────
    # This is the whole point: `pytest --doctest-glob='*.pyi'` imports the
    # compiled extension and executes every `>>>` in the enriched stub. If a
    # kernel drifts from its documented example, CI fails here.
    doctest_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--doctest-glob=*.pyi",
            "-q",
            str(Path("src") / "my_acc" / "accumulator" / "accumulator.pyi"),
        ],
        cwd=dest,
        env={**_make_env(), "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
    )
    # An ABSENT pytest also exits non-zero, and saying "doctests failed" for
    # that is a wrong answer, not a vague one: it sends the reader to the
    # header's `@code` blocks when the real fault is an uninstalled runner.
    # It reported exactly that for 15 days, because artifact.yml ran this
    # step before its `pip install pytest`. Separate the two answers.
    if "No module named pytest" in doctest_res.stderr:
        raise AssertionError(
            "pytest is not installed, so the header-authored .pyi doctests "
            "NEVER RAN -- this is an environment problem, not a doctest "
            "failure. Install it (`pip install pytest`) and re-run."
        )
    assert doctest_res.returncode == 0, (
        "header-authored .pyi doctests failed:\n"
        f"{doctest_res.stdout}\n{doctest_res.stderr}"
    )
    print(doctest_res.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("accumulator: PASSED")
