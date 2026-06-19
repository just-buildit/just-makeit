"""End-to-end test: opaque_counter scaffold → apply → build → ctest.

The dead-simplest demonstration of opaque state fields.  A counter lives
on the heap, separately from the state struct, behind a `uint64_t *`
opaque field.  Every step() increments it by `step_size`.

The point isn't a useful DSP component — it's the absolute minimum
syntactic pattern for declaring a pointer struct field whose lifetime
the user manages via create_impl / destroy_impl.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/opaque_counter/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path


def _cmd(args, cwd):
    r = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


# Everything the component needs in one TOML fragment.
#
# • `count` is opaque — a `uint64_t *` allocated by create_impl, freed by
#   destroy_impl.  Python sees nothing of it.
# • `step_size` is a regular scalar field — Python gets a constructor
#   kwarg, a get_step_size(), a set_step_size(), and a reset() default.
# • `mutable = true` because step() writes to *state->count.
_OPAQUE_COUNTER_FRAGMENT = '''\
# Heap-allocated counter — the minimum opaque-field demo.
[opaque_counter]
arg_type     = "void"
return_type  = "uint64_t"
mutable      = "true"
create_impl  = """
obj->count = calloc(1, sizeof(*obj->count));
if (!obj->count) { free(obj); return NULL; }
obj->step_size = step_size;
"""
destroy_impl = """
free(state->count);
"""

[[opaque_counter.state]]
name    = "step_size"
type    = "uint64_t"
default = "1"

[[opaque_counter.state]]
name   = "count"
type   = "uint64_t *"
opaque = true
'''


# Patch step() to read+write through the opaque pointer.  Idempotent.
_STEP_PATCH_OLD = (
    "    (void)state; /* TODO: implement */\n    return (uint64_t)0;"
)
_STEP_PATCH_NEW = (
    "    *state->count += state->step_size;\n    return *state->count;"
)


def _patch_step(core_h: Path) -> None:
    text = core_h.read_text(encoding="utf-8")
    if _STEP_PATCH_NEW.split("\n")[0] in text:
        return  # already patched (idempotent)
    if _STEP_PATCH_OLD not in text:
        raise AssertionError(
            f"step() stub not found in {core_h} — template changed?"
        )
    core_h.write_text(
        text.replace(_STEP_PATCH_OLD, _STEP_PATCH_NEW), encoding="utf-8"
    )


def run(root: Path) -> None:
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    # 1. Empty project — manifest only.
    proj = root / "opaque_counter_demo"
    jm_new("opaque_counter_demo", proj)

    # 2. Author the whole component in one fragment.
    fragment = root / "opaque_counter.toml"
    fragment.write_text(_OPAQUE_COUNTER_FRAGMENT, encoding="utf-8")

    # 3. Materialize: jm apply writes core.h/core.c/ext.c/test.c/pyi/...
    jm_apply(proj, fragment=fragment)

    # 4. Sanity-check the generated artefacts before building.
    core_h = (
        proj / "native" / "inc" / "opaque_counter" / "opaque_counter_core.h"
    )
    core_c = (
        proj / "native" / "src" / "opaque_counter" / "opaque_counter_core.c"
    )
    h = core_h.read_text(encoding="utf-8")
    c = core_c.read_text(encoding="utf-8")

    # Opaque field appears in the struct verbatim.
    assert "uint64_t * count;" in h, h

    # No auto-getter/setter for the opaque field — Python sees nothing of it.
    assert "opaque_counter_get_count" not in h
    assert "opaque_counter_set_count" not in h

    # The scalar `step_size` DOES get auto-getters/setters.
    assert "opaque_counter_get_step_size" in h
    assert "opaque_counter_set_step_size" in h

    # create_impl ran — count is calloc'd.
    assert "obj->count = calloc" in c

    # destroy_impl ran — count is freed before the trailing free(state).
    body_pos = c.index("free(state->count)")
    free_pos = c.index("free(state);")
    assert body_pos < free_pos, (
        "destroy_impl body must execute before free(state)"
    )

    # 5. Patch the step() body to actually use the opaque pointer.
    _patch_step(core_h)

    # 6. cmake configure + build + ctest.
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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("opaque_counter: PASSED")
