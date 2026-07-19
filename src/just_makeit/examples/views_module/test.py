"""End-to-end test for the views_module example (gh-504).

Called by tests/test_examples.py as: run(root: Path) -> None

Exercises `just-makeit view` — two Python classes over ONE generated C core:

  - `just-makeit object acc` scaffolds an accumulator (state `sum`, step adds).
  - `just-makeit view SeededAcc --create-fn acc_create_seeded
    --exclude-method total` adds a SECOND class over the same `acc_state_t`,
    differing in its constructor and trimming a method (`total()`).
  - Both classes compile into one `.so`, import from one subpackage, share the
    same step behaviour, and construct differently (`Acc(sum=0.0)` starts empty;
    `SeededAcc(seed=10.0)` starts pre-loaded); `Acc` exposes `total()`,
    `SeededAcc` does not.

The point: there is exactly one `acc_core.c` (one struct, one step()), and the
view is pure generated glue over it plus a hand-written alternate constructor.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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


def _patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"anchor not found in {path.name}:\n{old}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def run(root: Path) -> None:
    from just_makeit._module import run as module_run
    from just_makeit._new import run as new_run
    from just_makeit._method import run as method_run
    from just_makeit._object import run as object_run
    from just_makeit._view import run as view_run

    dest = root / "acc_bank"

    # ── 1. Scaffold project + module + accumulator object ────────────────────
    new_run("acc_bank", dest)
    module_run(dest, "bank")
    object_run(
        dest,
        "acc",
        module="bank",
        state_vars=[("sum", "double", "0.0")],
        arg_type="double",
        return_type="double",
        mutable=True,
    )
    # A read-only "total" method on the accumulator (returns the running sum).
    method_run(dest, "acc", "total", "bank", "void", "double", False, [])

    # ── 2. Add a SECOND class over the same core: a pre-seeded accumulator ────
    # It shares acc_state_t and step(), builds from a different constructor, and
    # trims the surface — SeededAcc deliberately omits total().
    view_run(
        dest,
        "acc",
        "SeededAcc",
        "bank",
        "acc_create_seeded",
        init_params=[("seed", "double", "0.0")],
        exclude_methods=["total"],
    )

    # Both classes present, one core, distinct constructors.
    frag_acc = (
        dest / "native" / "src" / "bank" / "bank_ext_acc.c"
    ).read_text()
    frag_view = (
        dest / "native" / "src" / "bank" / "bank_ext_seededacc.c"
    ).read_text()
    agg = (dest / "native" / "src" / "bank" / "bank_ext.c").read_text()
    assert "AccObject" in frag_acc
    assert "SeededAccObject" in frag_view
    assert "self->handle = acc_create_seeded(seed);" in frag_view
    assert 'PyModule_AddObject(m, "Acc"' in agg
    assert 'PyModule_AddObject(m, "SeededAcc"' in agg
    # total() is on the parent but excluded from the view (its wrapper is not
    # emitted); the shared C function still exists in the core.
    assert "total" in frag_acc
    assert "total" not in frag_view
    # Exactly one core lib — the view adds no new object library.
    cmake = (dest / "native" / "src" / "bank" / "CMakeLists.txt").read_text()
    assert "acc_core" in cmake
    assert "seededacc_core" not in cmake

    # ── 3. Implement step() and the view's alternate constructor ─────────────
    core_h = dest / "native" / "inc" / "acc" / "acc_core.h"
    _patch(
        core_h,
        "    (void)state; /* TODO: implement using state variables */\n"
        "    return (double)x;",
        "    state->sum += x;\n    return state->sum;",
    )
    core_c = dest / "native" / "src" / "acc" / "acc_core.c"
    # acc_create_seeded starts the accumulator pre-loaded, reusing acc_create.
    _patch(
        core_c,
        "    /* <<IMPLEMENT>>: build the state for the SeededAcc view. */\n"
        "    return NULL;",
        "    acc_state_t *s = acc_create(seed);\n    return s;",
    )
    # total() returns the running sum without mutating.
    _patch(
        core_c,
        "    (void)state;\n    return (double)0.0;",
        "    return state->sum;",
    )

    # ── 4. Build + C tests ───────────────────────────────────────────────────
    _cmd(["make"], cwd=dest, env=_make_env())
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=dest)

    # ── 5. Python: both classes from one .so, one shared step, two ctors ─────
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys; sys.path.insert(0, 'src')
from acc_bank.bank import Acc, SeededAcc

# Same step behaviour (sum += x), different starting point.
a = Acc(sum=0.0)
assert a.step(1.0) == 1.0
assert a.step(2.5) == 3.5

s = SeededAcc(seed=10.0)
assert s.step(1.0) == 11.0
assert s.step(2.5) == 13.5

# They are distinct classes backed by the same C core.
assert Acc is not SeededAcc
assert type(a).__name__ == "Acc"
assert type(s).__name__ == "SeededAcc"

# total() is on the parent; the view trims it (exclude_methods).
assert a.total() == 3.5
assert hasattr(Acc, "total")
assert not hasattr(SeededAcc, "total"), "view should not expose total()"
print("views_module: all checks passed")
""",
        ],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Python smoke test failed:\n{result.stdout}\n{result.stderr}"
        )
    print(result.stdout.strip())

    # ── 6. One .pyi, two class blocks ────────────────────────────────────────
    pyi = (dest / "src" / "acc_bank" / "bank" / "bank.pyi").read_text()
    assert "class Acc:" in pyi
    assert "class SeededAcc:" in pyi
    # The view's __init__ takes its own constructor param.
    view_block = pyi[pyi.index("class SeededAcc") :]
    assert "seed: float" in next(
        ln for ln in view_block.splitlines() if "__init__" in ln
    )


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("views_module: PASSED")
