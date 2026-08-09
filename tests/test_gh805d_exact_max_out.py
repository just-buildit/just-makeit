"""`exact_max_out` buys exact allocation without changing the kernel (gh-805 §D).

A variable-output binding sizes its output at ``max(max_out(...), n)``. The
clamp is there because ``max_out`` has only ever been a *hint*: it may return
``0`` (the scaffolded placeholder), and for a generator it may return a fixed
internal cap while ``steps(count)` writes exactly ``count``.

That default costs the case #805 §D reports. A decimator producing 256 from
1024 allocates 1024, and — worse, because there is no workaround — an ``out=``
buffer correctly sized to 256 is **rejected**:

    ValueError: out has 512 elements, need >= 65536

`pass_capacity` already escapes the clamp, but only by changing the kernel's
signature to take its capacity. `exact_max_out` is the same trust for a kernel
that does not want that: the author asserts ``max_out`` bounds any call, and
the binding allocates exactly it.

**Why an assertion and not a derivation.** The obvious rule — trust it when
the prototype takes ``n_in``, since it was handed the input size — is wrong,
and this repo's own fixtures prove it. ``Nco().steps_ovf(n)`` declares the
length-bearing form, returns a fixed 65536, and writes exactly the ``n`` asked
for; dropping the clamp there reintroduces gh-600's heap corruption. Arity
says what the C function was *told*, never what it does with it.

So the flag is opt-in, and the zero-guard survives it: `_method` scaffolds
``return 0; /* placeholder */``, and a project that has not implemented the
function must not allocate nothing and hand the kernel a buffer to overrun.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._context._methods import _capacity_exprs
from just_makeit._keys import METHOD_KEYS
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._script import run as script_run

_SKIP = (
    ""
    if shutil.which("cmake") and (shutil.which("cc") or shutil.which("gcc"))
    else "cmake/cc not available"
)


def _project(tmp_path, *, exact: bool):
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(
            root,
            "cic",
            None,
            arg_type="float _Complex",
            return_type="float _Complex",
        )
    cfg = C.load(root)
    entry = {
        "name": "decimate",
        "arg_type": "float _Complex",
        "return_type": "float _Complex",
        "variable_output": True,
    }
    if exact:
        entry["exact_max_out"] = True
    cfg["cic"]["methods"] = [entry]
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    return root, (root / "native/src/cic/cic_ext.c").read_text()


def test_the_clamp_is_dropped_on_both_paths(tmp_path):
    """Allocation and `out=` validation must move together.

    They are one question asked twice. When they disagree, `out=` validates
    against a different bound than the binding would itself have allocated.
    """
    _, ext = _project(tmp_path, exact=True)
    assert "if (!_cap) _cap = _need;" in ext, ext[:200]
    assert "_min_cap = _omax ? _omax : ((size_t)n);" in ext, ext[:200]
    assert "_cap < _need" not in ext, "the allocation clamp survived"
    assert "_omax > (size_t)n" not in ext, "the out= clamp survived"


def test_the_zero_guard_survives(tmp_path):
    """`return 0; /* placeholder */` must not allocate nothing.

    jm scaffolds that body. Trusting it literally hands the kernel a
    zero-length buffer to write into, which is the one way this flag could
    turn an over-allocation into memory corruption.
    """
    _, ext = _project(tmp_path, exact=True)
    assert "if (!_cap)" in ext, (
        "the zero-guard is gone, so an unimplemented max_out allocates 0"
    )


def test_without_the_flag_nothing_changes(tmp_path):
    """Opt-in means every existing project renders exactly as before."""
    _, ext = _project(tmp_path, exact=False)
    assert "if (!_cap || _cap < _need) _cap = _need;" in ext
    assert "_min_cap = _omax > (size_t)n ? _omax : ((size_t)n);" in ext


def test_pass_capacity_still_wins(tmp_path):
    """It already implies the assertion and is stronger — the kernel enforces
    the bound itself, so there is no zero-guard either."""
    alloc, cap = _capacity_exprs(True, True, "_need")
    assert alloc == "    (void)_need;\n"
    assert cap == "        size_t _min_cap = _omax;\n"


def test_the_flag_round_trips_and_replays(tmp_path):
    """Manifest, `jm apply` replay and `jm script` all carry it.

    A method flag that reaches the emitter but not the serializer works until
    the next apply and then silently stops — the shape CLAUDE.md warns about,
    where `_apply` and `_script` enumerate method keys one by one.
    """
    root, _ = _project(tmp_path, exact=True)
    assert C.load(root)["cic"]["methods"][0].get("exact_max_out") is True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        script_run(root)
    assert "--exact-max-out" in buf.getvalue(), (
        f"`jm script` drops the flag, so replaying the history loses it:\n"
        f"{buf.getvalue()}"
    )


def test_it_is_a_recognised_method_key():
    """Otherwise `jm status` warns about jm's own flag (gh-805 §G)."""
    assert "exact_max_out" in METHOD_KEYS


def test_the_cli_accepts_the_flag(tmp_path):
    """`--exact-max-out`, the peer of `--pass-capacity`."""
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(
            root, "cic", None, arg_type="void", return_type="float _Complex"
        )
        method_run(
            root,
            "cic",
            "gen",
            None,
            "void",
            "float _Complex",
            True,
            [],
            exact_max_out=True,
        )
    assert C.load(root)["cic"]["methods"][0].get("exact_max_out") is True


_MAX_OUT_STUB = (
    "size_t\ncic_decimate_max_out(cic_state_t *state, size_t n_in)\n{\n"
    "    (void)state; (void)n_in;\n"
    "    return 0; /* placeholder */\n}"
)
_MAX_OUT_IMPL = (
    "size_t\ncic_decimate_max_out(cic_state_t *state, size_t n_in)\n{\n"
    "    (void)state;\n    return n_in / 4;\n}"
)
_DECIMATE_STUB = (
    "size_t\ncic_decimate(cic_state_t *state, const float complex *in,"
    " size_t n_in, float complex *out)\n{\n"
    "    (void)state;\n    (void)in; (void)n_in;\n"
    "    (void)out;\n    return 0; /* placeholder */\n}"
)
_DECIMATE_IMPL = (
    "size_t\ncic_decimate(cic_state_t *state, const float complex *in,"
    " size_t n_in, float complex *out)\n{\n"
    "    (void)state;\n"
    "    size_t n_out = n_in / 4;\n"
    "    for (size_t i = 0; i < n_out; i++) out[i] = in[i * 4];\n"
    "    return n_out;\n}"
)


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestExactMaxOutRuntime:
    """Built and run, because the assertions above are about text.

    §D's reported `ValueError` is something a caller actually sees, so it is
    provable here in a way grepping the generated C is not.

    The *allocation* size deliberately is not tested here: the internal path
    calls `PyArray_Resize`, which reallocs down to `n_out`, so the transient
    over-allocation leaves no trace any Python assertion can reach. A test
    claiming to measure it passed with the flag entirely disabled — it is held
    by `test_the_clamp_is_dropped_on_both_paths` against the emitted C
    instead, which is the only surface where it is visible.
    """

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh805d") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(
                dest,
                "cic",
                module=None,
                state_vars=[("gain", "double", "1.0")],
                no_step=True,
            )
            method_run(
                dest,
                "cic",
                "decimate",
                None,
                "float _Complex",
                "float _Complex",
                True,
                [],
                exact_max_out=True,
            )
        core = dest / "native/src/cic/cic_core.c"
        text = core.read_text(encoding="utf-8")
        assert _MAX_OUT_STUB in text, "stub shape changed; update this test"
        assert _DECIMATE_STUB in text, "stub shape changed; update this test"
        core.write_text(
            text.replace(_MAX_OUT_STUB, _MAX_OUT_IMPL).replace(
                _DECIMATE_STUB, _DECIMATE_IMPL
            ),
            encoding="utf-8",
        )
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

    def _run(self, dest: Path, body: str):
        return subprocess.run(
            [sys.executable, "-c", body],
            cwd=dest,
            env={**os.environ, "PYTHONPATH": str(dest / "src")},
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_an_out_buffer_sized_to_the_true_bound_is_accepted(self, built):
        """§D's reported failure, run for real.

        Pre-flag this raised `ValueError: out has 256 elements, need >= 1024`
        — the check was correct given the contract, and the contract was the
        thing that was wrong.
        """
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.cic import Cic\n"
            "x = np.ones(1024, np.complex64)\n"
            "out = np.empty(256, np.complex64)\n"
            "y = Cic().decimate(x, out=out)\n"
            "assert len(y) == 256, len(y)\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, (
            f"out= sized to the true bound was rejected\n"
            f"{r.stdout}\n{r.stderr}"
        )
        assert r.stdout.strip() == "ok"

    def test_an_undersized_out_buffer_is_still_rejected(self, built):
        """The check is narrowed, not removed."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.cic import Cic\n"
            "try:\n"
            "    Cic().decimate(np.ones(1024, np.complex64),"
            " out=np.empty(8, np.complex64))\n"
            "except ValueError:\n"
            "    print('ok')\n"
            "else:\n"
            "    print('ACCEPTED an 8-element buffer for 256 outputs')\n",
        )
        assert r.stdout.strip() == "ok", f"{r.stdout}\n{r.stderr}"
