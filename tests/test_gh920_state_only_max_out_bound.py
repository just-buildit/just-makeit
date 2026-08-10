"""gh-920: `pass_capacity` trusted a `max_out()` that cannot see the call.

gh-607 shipped two halves. One was the exact allocation: with
``pass_capacity`` the kernel is handed its capacity and enforces the bound
itself, so the binding allocates ``max_out(state, n)`` with no clamp instead
of the defensive ``max(max_out, n)``. The other was the count parameter that
makes an exact answer *possible* — ``max_out`` was given the same count the
binding is about to pass to the kernel.

A project can be in the seam between them: opted into ``pass_capacity`` while
its header still declares the pre-gh-607 ``max_out(state)``. jm then extended
the exactness to a value that provably cannot depend on ``n`` — and a
call-independent cap read as a per-call bound is a **silent truncation**:

    >>> nco.steps_u32(393_216)      # doppler, jm 0.55.2
    array([...], dtype=uint32)      # 65536 elements, no error

The kernel was not wrong and the allocation did not overflow; the binding
simply asked a question the signature could not answer, and believed the
reply. jm already knows the arity — gh-761 reads it off the sacred header for
exactly this wrapper — so the fix is to withhold a trust the prototype cannot
carry and fall back to the clamp. The kernel is still handed the true
allocation, so ``pass_capacity``'s contract is untouched: this decides how
large the buffer is, never what the kernel is told.

``exact_max_out`` (gh-805 §D) is deliberately *not* gated the same way. It is
the author asserting the bound holds for any call — precisely the claim a
state-only prototype cannot make on its own — so it remains the way a project
with a genuinely call-independent bound keeps the exact allocation.

The `TestRuntime` case is the one that would have caught this: the generated
text reads as correct on both sides of the fix, and only a compiled call with
``n`` above the cap tells them apart.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._methods import make_methods_ctx  # noqa: E402
from just_makeit._docstring import max_out_arity_key  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

GENERATOR = {
    "name": "steps_u32",
    "arg_type": "void",
    "return_type": "uint32_t",
    "variable_output": True,
    "pass_capacity": True,
}

CLAMP = "if (!_cap || _cap < _need) _cap = _need;"
NO_CLAMP = "(void)_need;"


def _ctx(state_only: bool, **over):
    """Render the wrapper for a header of the given `max_out` arity."""
    blocks = (
        {max_out_arity_key(): frozenset({"nco_steps_u32_max_out"})}
        if state_only
        else {}
    )
    return make_methods_ctx(
        "nco", "Nco", [dict(GENERATOR, **over)], doc_blocks=blocks
    )


class TestTheAllocationFollowsThePrototype:
    def test_a_state_only_max_out_keeps_the_clamp(self):
        src = _ctx(state_only=True)["extra_methods_c"]
        assert CLAMP in src
        assert NO_CLAMP not in src

    def test_the_count_bearing_form_still_allocates_exactly(self):
        """gh-607's point, and the half this fix must not revert."""
        src = _ctx(state_only=False)["extra_methods_c"]
        assert NO_CLAMP in src
        assert CLAMP not in src

    def test_out_validation_agrees_with_the_allocation(self):
        """The two faces come from one emitter and must not diverge.

        `out=` under-validating relative to what the binding would have
        allocated itself is the failure this coupling exists to prevent.
        """
        state_only = _ctx(state_only=True)["extra_methods_c"]
        counted = _ctx(state_only=False)["extra_methods_c"]
        assert "size_t _min_cap = _omax > (size_t)n ? _omax" in state_only
        assert "size_t _min_cap = _omax;" in counted

    def test_exact_max_out_still_opts_out_of_the_clamp(self):
        """The author's explicit assertion outranks the prototype's arity.

        Gating this the same way would leave a project with a genuinely
        call-independent bound no way to ask for the exact allocation.
        """
        src = _ctx(state_only=True, exact_max_out=True)["extra_methods_c"]
        assert "if (!_cap) _cap = _need;" in src
        assert CLAMP not in src

    def test_no_clamp_without_pass_capacity_either_way(self):
        """The historical default is unconditional and stays that way."""
        for state_only in (True, False):
            src = _ctx(state_only, pass_capacity=False)["extra_methods_c"]
            assert CLAMP in src, state_only


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()

# doppler's NCO, reduced: a fixed internal cap the kernel clamps to, and a
# kernel that is otherwise happy to write whatever capacity it is given.
CAP = 8
MAX_OUT_BODY = f"""size_t
nco_steps_u32_max_out(nco_state_t *state)
{{
    (void)state;
    return {CAP}u;
}}"""
KERNEL_BODY = """size_t
nco_steps_u32(nco_state_t *state, size_t n, uint32_t *out, size_t max_out)
{
    if (n > max_out)
        n = max_out;
    for (size_t i = 0; i < n; i++)
        out[i] = (uint32_t)(state->phase + i);
    return n;
}"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestRuntime:
    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        from just_makeit._apply import run as apply_run

        dest = tmp_path_factory.mktemp("gh920") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(
                dest,
                "nco",
                module=None,
                state_vars=[("phase", "uint32_t", "0")],
            )
            method_run(
                dest,
                "nco",
                "steps_u32",
                None,
                "void",
                "uint32_t",
                True,
                [],
                pass_capacity=True,
            )
            # The author's own prototype: the pre-gh-607 state-only form, on
            # the sacred header, which is what puts the project in the seam.
            header = dest / "native/inc/nco/nco_core.h"
            text = header.read_text("utf-8")
            text = re.sub(
                r"size_t nco_steps_u32_max_out\s*\([^)]*\)",
                "size_t nco_steps_u32_max_out(nco_state_t *state)",
                text,
            )
            header.write_text(text, encoding="utf-8")

            core = dest / "native/src/nco/nco_core.c"
            text = core.read_text("utf-8")
            for pattern, body in (
                (r"size_t\nnco_steps_u32_max_out\(.*?\n\}", MAX_OUT_BODY),
                (r"size_t\nnco_steps_u32\(nco_state_t.*?\n\}", KERNEL_BODY),
            ):
                text, n_sub = re.subn(pattern, body, text, count=1, flags=re.S)
                assert n_sub == 1, "stub shape changed; update this test"
            core.write_text(text, encoding="utf-8")
            apply_run(dest)

        build = dest / "build"
        for cmd in (
            [
                "cmake",
                "-S",
                str(dest),
                "-B",
                str(build),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            ["cmake", "--build", str(build)],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            assert r.returncode == 0, f"{cmd[0]}:\n{r.stdout}\n{r.stderr}"
        return dest

    def _run(self, dest, body):
        return subprocess.run(
            [sys.executable, "-c", body],
            cwd=dest,
            env={**os.environ, "PYTHONPATH": str(dest / "src")},
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_a_request_above_the_cap_is_not_truncated(self, built):
        """The headline defect, in the one form that shows it."""
        want = CAP * 4
        r = self._run(
            built,
            f"from p.nco import Nco\nprint(len(Nco().steps_u32({want})))\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert int(r.stdout.strip()) == want, (
            f"asked for {want} samples and got {r.stdout.strip()}; the "
            f"binding sized the buffer from a max_out() that cannot see n"
        )

    def test_the_samples_are_the_ones_asked_for(self, built):
        """Length alone would pass on a buffer the kernel never filled."""
        want = CAP * 4
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.nco import Nco\n"
            f"a = Nco().steps_u32({want})\n"
            f"print(np.array_equal(a, np.arange({want}, dtype=np.uint32)))\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "True"

    def test_a_request_below_the_cap_is_exact(self, built):
        """The clamp only grows; it must not round a small call up to the cap.

        Returning `CAP` here would be the same defect wearing the fix.
        """
        r = self._run(
            built,
            "from p.nco import Nco\nprint(len(Nco().steps_u32(3)))\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert int(r.stdout.strip()) == 3
