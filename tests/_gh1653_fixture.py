"""A bare project whose author C lives where gh-1653 found `jm upgrade` blind.

- `mixer` depends on a sibling `lo` (`depends_on`, linked) and holds it as
  opaque state, `type = "lo_state_t *"`, created and destroyed in manifest
  `create_impl` / `destroy_impl` bodies that call `lo`'s derived API. jm
  copies both into the C verbatim.
- `lo`'s `_core.c` defines its `steps()` with jm's own `JM_DEFINE_STEPS
  (lo, ...)` -- the stem as a macro ARGUMENT -- over an author
  `lo_step_batch`, which no render declares but the macro pastes.
- An author macro and a comment that quote `lo_state_t` / `lo_create`, which
  must not move.
- A local named after the component, `lo`: the bare stem, in code, outside
  the macro -- which must not move either.

Built by running jm (`_new.run`, the CLI, `apply`), never hand-written; the
author edits are the ones a person makes by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit import _textio

PREFIX = "zz"

MIXER_TOML = '''[mixer]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "true"
depends_on = [{ name = "lo", link = true }]

create_impl = """
lo_state_t *lo = lo_create(2.0f); /* lo_create: quoted in a comment */
if (!lo) { free(obj); return NULL; }
obj->osc = lo;
"""
destroy_impl = """
lo_destroy(state->osc);
"""

[[mixer.state]]
name = "osc"
type = "lo_state_t *"
opaque = true
'''

#: The mixer's sacred files: every line of C in them came from MIXER_TOML.
MIXER_SACRED = (
    "native/inc/q/mixer/mixer_core.h",
    "native/src/mixer/mixer_core.c",
)

AUTHOR_MACRO = "#define LO_STATE_MAGIC 0x10 /* an author macro: lo_state_t */"

BATCH_H = f"""
{AUTHOR_MACRO}
#define LO_BATCH (JM_SIMD_WIDTH_F32 / 2)
#if JM_SIMD_WIDTH_F32 > 1
static inline void
lo_step_batch(lo_state_t *state, const float _Complex *in,
              float _Complex *out)
{{
    for (int k = 0; k < LO_BATCH; k++)
        out[k] = in[k] * state->gain;
}}
#endif
"""

KERNEL = (
    "#define LO_CHUNK 64\n"
    "JM_DEFINE_STEPS (lo, lo_state_t, float _Complex, 0, LO_BATCH, LO_CHUNK)"
)


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def build(where: Path) -> Path:
    """The bare project, built and applied; returns its root."""
    root = where / "q"
    new_run("q", root, perf=True, c_prefix=None, fragments=True)
    _ok(
        "object",
        "lo",
        "--state",
        "gain:float:2.0",
        "--state",
        "delay:float _Complex[4]:0",
        "--mutable",
        cwd=root,
    )
    _textio.write_text(root / "objects" / "mixer.toml", MIXER_TOML)
    _ok("apply", cwd=root)
    h = next((root / "native" / "inc").rglob("lo_core.h"))
    text = h.read_text()
    at = text.rindex("#ifdef __cplusplus\n}")
    _textio.write_text(h, text[:at] + BATCH_H + text[at:])
    c = root / "native" / "src" / "lo" / "lo_core.c"
    text = c.read_text()
    new = re.sub(
        r"void\s+lo_steps\([^)]*\)\s*\{.*?\n\}", KERNEL, text, flags=re.S
    )
    assert new != text, "lo_steps() not found to replace"
    _textio.write_text(c, new)
    return root


def set_prefix(root: Path) -> None:
    toml = root / C.FILENAME
    text = toml.read_text()
    _textio.write_text(
        toml,
        text.replace("[project]\n", f'[project]\nc_prefix = "{PREFIX}"\n', 1),
    )
