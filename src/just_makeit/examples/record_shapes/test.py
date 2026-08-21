"""End-to-end test: the three record shapes a method can declare.

Exercises, on ONE object, the three results `result_fields` can produce:

  1. Scaffold a collector (double step, ring-buffer array state).
  2. Add the two record structs to the sacred header -- they are the author's.
  3. Declare summary() `--single`, read() `--record-dtype`, peaks() neither.
  4. Assert the three C prototypes and the three .pyi return types differ in
     exactly the documented way.
  5. Implement step() and the four kernels.
  6. cmake configure + build + CTest.
  7. Python smoke test: a Summary record, a structured ndarray with real
     field names, and a list of tuples -- read back from one ring.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/record_shapes/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._method import run as jm_method
    from just_makeit._new import run as jm_new

    # ── 1. Scaffold ──────────────────────────────────────────────────────
    jm_new(
        "evlog",
        root / "evlog",
        object_names=["collector"],
        state_vars=[
            ("count", "uint64_t", "0"),
            ("t", "uint64_t[64]", ""),
            ("v", "double[64]", ""),
        ],
        arg_type="double",
        return_type="void",
    )
    proj = root / "evlog"

    # ── 2. The record structs go in FIRST ────────────────────────────────
    # Both method declarations below name a type by string; it has to exist.
    _cmd([sys.executable, str(STEPS / "02_structs.py")], cwd=proj)

    # ── 3. The same data, declared three ways ────────────────────────────
    # shape 1 -- ONE record, returned by value.
    jm_method(
        proj,
        object_name="collector",
        method_name="summary",
        module=None,
        arg_type="void",
        return_type="evlog_summary_t",
        variable_output=False,
        multi_output=[],
        single=True,
        result_fields=[
            {"name": "n", "type": "uint64_t"},
            {"name": "mean", "type": "double"},
        ],
        record_name="Summary",
        record_doc="Count and mean of everything recorded so far.",
    )
    # shape 2 -- an ARRAY of records, as a structured ndarray.
    jm_method(
        proj,
        object_name="collector",
        method_name="read",
        module=None,
        arg_type="void",
        return_type="double",
        variable_output=True,
        multi_output=[],
        record_dtype="evlog_rec_t",
        result_fields=[
            {"name": "t", "type": "uint64_t"},
            {"name": "v", "type": "double"},
        ],
    )
    # shape 3 -- a list of tuples. NOT variable_output: the count is the
    # kernel's return value, which is what max_results is paired with.
    jm_method(
        proj,
        object_name="collector",
        method_name="peaks",
        module=None,
        arg_type="void",
        return_type="evlog_peak_t",
        variable_output=False,
        multi_output=[],
        result_fields=[
            {"name": "index", "type": "size_t"},
            {"name": "value", "type": "double"},
        ],
    )

    # ── 4. The three shapes are actually different ───────────────────────
    # This is the whole point of the example, so it is asserted rather than
    # narrated: one key changes, and the C signature and the Python return
    # type both change with it.
    header = (
        proj / "native" / "inc" / "collector" / "collector_core.h"
    ).read_text(encoding="utf-8")
    assert (
        "evlog_summary_t collector_summary(collector_state_t *state);"
        in header
    ), "single: the kernel must return the record BY VALUE"
    assert (
        "size_t collector_read(collector_state_t *state, size_t n,"
        " evlog_rec_t *out);" in header
    ), "record_dtype: the kernel must fill a caller-sized <struct> *out"
    assert (
        "size_t collector_peaks(collector_state_t *state,"
        " evlog_peak_t *result, size_t max_results);" in header
    ), "neither: the kernel must fill <row> *result with a max_results cap"

    pyi = (proj / "src" / "evlog" / "collector.pyi").read_text(
        encoding="utf-8"
    )
    assert "def summary(self) -> Summary:" in pyi
    assert "class Summary(" in pyi, "the --single record type must be named"
    assert "Count and mean of everything recorded so far." in pyi, (
        "--record-doc must reach the record type in the stub"
    )
    assert ") -> NDArray[Any]:" in pyi, "record_dtype must return an ndarray"
    assert ") -> list[tuple]:" in pyi, "neither must return a list of tuples"

    # ── 5. Implement ─────────────────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "04_patch.py")], cwd=proj)

    # ── 6. Build + CTest ─────────────────────────────────────────────────
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
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # ── 7. Read the same ring back three ways ────────────────────────────
    _cmd([sys.executable, str(STEPS / "06_demo.py")], cwd=proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("record_shapes: PASSED")
