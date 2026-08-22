"""End-to-end test: the four channels a component reports trouble on.

Exercises `just-makeit error` (gh-482), `just-makeit warning` (gh-481), and
the two method-level translations `--status-return` and `--error-negative` --
none of which had example coverage.

  1. Scaffold an allocator whose ctor args are init-params, so the three
     derived state fields stay out of the constructor signature.
  2. Declare all four channels. None of them touches a sacred file.
  3. Assert each one reached BOTH faces -- the generated glue and the .pyi --
     since a declaration that reaches only one is the recurring defect class
     here (gh-1060, gh-1064, gh-1066).
  4. Implement create(), take() and peek(): C sets a flag or returns a code,
     and never mentions Python.
  5. cmake configure + build + CTest.
  6. Drive all four from Python and check what each one actually raises.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/errors_warnings/test.py
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
    from just_makeit._error import run as jm_error
    from just_makeit._method import run as jm_method
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._warning import run as jm_warning

    # ── 1. Scaffold ──────────────────────────────────────────────────────
    jm_new("budget", root / "budget")
    proj = root / "budget"
    jm_object(
        proj,
        "allocator",
        None,
        init_params=[
            # REQUIRED, and that is what makes the generated smoke tests
            # correct rather than merely stricter. jm seeds an optional
            # scalar with the type's ZERO; this constructor validates and
            # rejects `slots == 0`, so the generated `Allocator(capacity=0,
            # slots=0)` raised the very ValueError declared below and all
            # eight generated tests failed — inside the image build, which
            # runs them and which `make test` does not.
            #
            # `required` is jm's existing answer: `_unseedable_required`
            # names this exact case ("a required scalar carrying no default —
            # a validating constructor would reject the type's zero") and
            # suppresses the generated construction instead of emitting one
            # that cannot work. Declaring it is the fix; giving the params
            # defaults would have been the other route and is wrong here,
            # because the point of the example is a ctor that refuses.
            # ...and gh-1105's `example_value` (the trailing field) is what
            # lets the generated tests RUN rather than skip. `required`
            # stopped them constructing `Allocator(capacity=0, slots=0)`,
            # which this ctor refuses; it did not give jm anything valid to
            # construct WITH, so all eight tests skipped and the example
            # shipped a project whose own suite asserted nothing.
            #
            # These are seeds for the generated tests and doctests only. The
            # params stay REQUIRED and the Python signature is unchanged —
            # a default would have made `Allocator()` legal, which is exactly
            # what this example exists to refuse.
            #
            # 1024 / 4 divides evenly, so `remaining` is the full capacity and
            # the degraded-warning channel stays quiet for the smoke tests.
            # The example drives the degraded path explicitly further down.
            (
                "capacity",
                "size_t",
                "",
                "",
                "",
                "",
                False,
                "",
                True,
                "",
                "",
                "",
                "",
                "",
                "1024",
            ),
            (
                "slots",
                "size_t",
                "",
                "",
                "",
                "",
                False,
                "",
                True,
                "",
                "",
                "",
                "",
                "",
                "4",
            ),
        ],
        state_vars=[
            ("n_slots", "size_t", "0"),
            ("remaining", "size_t", "0"),
            ("degraded", "bool", "false"),
        ],
        arg_type="size_t",
        return_type="size_t",
    )

    # Declaring init-params is what keeps the derived state out of the
    # constructor. (`no_ctor` on the state fields is the other route and is
    # broken today -- it reaches the prototype and not the binding, gh-1066.)
    header = (
        proj / "native" / "inc" / "allocator" / "allocator_core.h"
    ).read_text(encoding="utf-8")
    assert (
        "allocator_state_t *allocator_create(size_t capacity, size_t slots);"
        in header
    ), "the derived state fields must not be constructor parameters"

    # ── 2. Declare the four channels ─────────────────────────────────────
    jm_error(
        proj,
        "allocator",
        "ValueError",
        "capacity must cover at least one unit per slot",
    )
    jm_warning(
        proj,
        "allocator",
        "degraded",
        "capacity is not divisible by slots; the remainder is unusable",
        category="RuntimeWarning",
    )
    jm_method(
        proj,
        object_name="allocator",
        method_name="take",
        module=None,
        arg_type="size_t",
        return_type="int",
        variable_output=False,
        multi_output=[],
        status_return=True,
        error="ValueError",
        error_message="requested more than remains",
    )
    jm_method(
        proj,
        object_name="allocator",
        method_name="peek",
        module=None,
        arg_type="size_t",
        return_type="int",
        variable_output=False,
        multi_output=[],
        error_negative=True,
        error="IndexError",
        error_message="no such slot",
    )

    # ── 3. Each channel reached BOTH faces ───────────────────────────────
    ext = (
        proj / "native" / "src" / "allocator" / "allocator_ext.c"
    ).read_text(encoding="utf-8")
    assert "PyErr_SetString(PyExc_ValueError," in ext, "create-error missing"
    assert "PyErr_WarnEx(PyExc_RuntimeWarning," in ext, "warning missing"
    assert "self->handle->degraded" in ext, (
        "the warning must read the bool field named by --condition"
    )
    assert "PyErr_Format(PyExc_IndexError," in ext, "error-negative missing"

    pyi = (proj / "src" / "budget" / "allocator.pyi").read_text(
        encoding="utf-8"
    )
    # --status-return erases the int; --error-negative keeps it. Same C
    # signature, and the declared flag is the only difference.
    assert "def take(self, x: int) -> None:" in pyi, (
        "status_return must erase the int from the Python signature"
    )
    assert "def peek(self, x: int) -> int:" in pyi, (
        "error_negative must KEEP the int -- it is a value, not a status"
    )
    for token in ("ValueError", "RuntimeWarning", "IndexError"):
        assert token in pyi, f"{token} is not documented in the stub"

    # No sacred file was touched by any of the four declarations.
    core_c = (
        proj / "native" / "src" / "allocator" / "allocator_core.c"
    ).read_text(encoding="utf-8")
    assert "Python.h" not in core_c and "PyErr" not in core_c, (
        "declaring an error or warning must stay in the glue"
    )

    # ── 4. Implement ─────────────────────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "03_patch.py")], cwd=proj)

    # ── 5. Build + CTest ─────────────────────────────────────────────────
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

    # ── 6. Drive all four from Python ────────────────────────────────────
    _cmd([sys.executable, str(STEPS / "05_demo.py")], cwd=proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("errors_warnings: PASSED")
