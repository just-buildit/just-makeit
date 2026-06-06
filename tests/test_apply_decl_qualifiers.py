"""`jm apply` is idempotent against decorative decl qualifiers (gh-169).

A user may hand-tune a module-function prototype in `<mod>_core.h` with
`JM_RESTRICT` (perf) or drop a `const` on a mutable buffer param. `apply` must
recognise such a decl as the same one it would generate and leave it alone,
instead of replacing it (clobbering the qualifiers) or appending a second,
conflicting declaration that fails to compile.
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._init import (  # noqa: E402
    _normalize_decl,
    _inject_decls_into_core_h,
)


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _project_with_fn(dest: Path):
    _silent(new_run, "tp", dest, modules=["dsp"], perf=True)
    cfg = C.load(dest)
    cfg["module"]["dsp"]["functions"] = [
        {
            "name": "process",
            "params": [
                {"name": "input", "type": "float[]"},
                {"name": "output", "type": "float[]", "mutable": True},
                {"name": "n", "type": "size_t"},
            ],
        }
    ]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    return dest / "native/inc/dsp/dsp_core.h"


_RESTRICT_SINGLE = (
    "void process(const float *JM_RESTRICT input, size_t input_len, "
    "float *JM_RESTRICT output, size_t output_len, size_t n);"
)
_RESTRICT_MULTI = (
    "void process(const float *JM_RESTRICT input, size_t input_len,\n"
    "             float *JM_RESTRICT output, size_t output_len, size_t n);"
)


def _decorate(hdr: Path, decorated: str):
    text = hdr.read_text(encoding="utf-8")
    # output is a mutable param, so it is generated non-const (gh-170).
    generated = (
        "void process(const float *input, size_t input_len, "
        "float *output, size_t output_len, size_t n);"
    )
    assert generated in text, "generated decl not found to decorate"
    hdr.write_text(text.replace(generated, decorated), encoding="utf-8")


def test_apply_preserves_jm_restrict_single_line(tmp_path):
    hdr = _project_with_fn(tmp_path / "tp")
    _decorate(hdr, _RESTRICT_SINGLE)
    _silent(apply_run, tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")  # twice — must be idempotent
    out = hdr.read_text(encoding="utf-8")
    assert out.count("process(") == 1  # no duplicate
    assert "JM_RESTRICT" in out  # user's qualifiers preserved


def test_apply_preserves_jm_restrict_multi_line(tmp_path):
    hdr = _project_with_fn(tmp_path / "tp")
    _decorate(hdr, _RESTRICT_MULTI)
    _silent(apply_run, tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")
    out = hdr.read_text(encoding="utf-8")
    assert out.count("process(") == 1
    assert "JM_RESTRICT" in out


# ── the legitimate replace path (builtin override) still works ───────────────
def test_inject_still_replaces_genuine_signature_change(tmp_path):
    hdr = tmp_path / "x_core.h"
    hdr.write_text(
        "#ifndef X_CORE_H\n#define X_CORE_H\n"
        "void x_reset(x_state_t *state);\n"
        "#endif /* X_CORE_H */\n",
        encoding="utf-8",
    )
    # a genuinely different signature (extra param) must replace, not skip
    changed = _inject_decls_into_core_h(
        hdr, "x", ["void x_reset(x_state_t *state, int mode);"]
    )
    out = hdr.read_text(encoding="utf-8")
    assert changed
    assert out.count("x_reset(") == 1
    assert "int mode" in out


def test_normalize_decl_strips_qualifiers():
    a = "void f(const float *JM_RESTRICT x, size_t n);"
    b = "void f(float *x, size_t n);"
    assert _normalize_decl(a) == _normalize_decl(b)
    # a real signature difference stays distinct
    c = "void f(float *x, size_t n, int k);"
    assert _normalize_decl(a) != _normalize_decl(c)
