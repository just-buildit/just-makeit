"""`depends_on` auto-includes the dependency's header (gh-170).

When a component declares ``depends_on = ["lfsr"]`` and uses the dependency's
types (e.g. an opaque ``lfsr_state_t *`` field), jm links `lfsr_core` AND now
injects ``#include "lfsr/lfsr_core.h"`` into the dependent's ``_core.h`` so it
compiles without a manual edit. Also covers the `mutable` synonym for `out` on
a module-function array param (the related const-vs-writable observation).
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._init import _inject_includes_into_core_h  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold_dep_pair(dest: Path):
    _silent(new_run, "tp", dest)
    _silent(
        object_run,
        dest,
        "lfsr",
        None,
        state_vars=[("seed", "uint32_t", "1")],
        arg_type="uint8_t",
        return_type="uint8_t",
    )
    _silent(
        object_run,
        dest,
        "wfm",
        None,
        no_state=True,
        no_step=True,
        init_params=[("n", "uint32_t", "8")],
    )
    cfg = C.load(dest)
    cfg["wfm"]["depends_on"] = ["lfsr"]
    C.save(dest, cfg)


# ── (a) apply injects the dependency include, idempotently ───────────────────
def test_apply_injects_depends_on_include(tmp_path):
    _scaffold_dep_pair(tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")
    hdr = tmp_path / "tp/native/inc/wfm/wfm_core.h"
    assert '#include "lfsr/lfsr_core.h"' in hdr.read_text(encoding="utf-8")
    _silent(apply_run, tmp_path / "tp")  # idempotent
    assert (
        hdr.read_text(encoding="utf-8").count('#include "lfsr/lfsr_core.h"')
        == 1
    )


# ── (b) the include sits among the other #includes, before the struct ────────
def test_include_placement(tmp_path):
    _scaffold_dep_pair(tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")
    text = (tmp_path / "tp/native/inc/wfm/wfm_core.h").read_text(
        encoding="utf-8"
    )
    assert text.index("clib_common.h") < text.index("lfsr/lfsr_core.h")
    # before the struct typedef (the doc comment mentions wfm_state_t earlier)
    assert text.index("lfsr/lfsr_core.h") < text.index("} wfm_state_t;")


# ── (c) mutable is a synonym for out on a module-function array param ─────────
def test_function_mutable_param_is_non_const(tmp_path):
    dest = tmp_path / "tp"
    _silent(new_run, "tp", dest, modules=["dsp"])
    cfg = C.load(dest)
    cfg["module"]["dsp"]["functions"] = [
        {
            "name": "process",
            "params": [
                {"name": "input", "type": "float[]"},
                {"name": "output", "type": "float[]", "mutable": True},
            ],
        }
    ]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    decl = next(
        line
        for line in (dest / "native/inc/dsp/dsp_core.h")
        .read_text(encoding="utf-8")
        .splitlines()
        if "process(" in line
    )
    assert "float *output" in decl  # writable
    assert "const float *output" not in decl
    assert "const float *input" in decl  # non-mutable stays const
    # `mutable` canonicalises to `out` on a re-dump (round-trip)
    assert "out = true" in C._dump(C.load(dest))


# ── unit: the injector is idempotent and a no-op without deps ────────────────
def test_inject_includes_idempotent(tmp_path):
    hdr = tmp_path / "x_core.h"
    hdr.write_text(
        "#ifndef X_CORE_H\n#define X_CORE_H\n"
        '#include "clib_common.h"\n\n'
        "typedef struct { int a; } x_state_t;\n"
        "#endif /* X_CORE_H */\n",
        encoding="utf-8",
    )
    assert _inject_includes_into_core_h(hdr, "x", ["dep"]) is True
    assert '#include "dep/dep_core.h"' in hdr.read_text(encoding="utf-8")
    assert _inject_includes_into_core_h(hdr, "x", ["dep"]) is False  # no-op
    assert _inject_includes_into_core_h(hdr, "x", []) is False  # no deps
