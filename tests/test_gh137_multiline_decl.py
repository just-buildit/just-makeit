"""gh-137: `_inject_decls_into_core_h` must recognise a multi-line prototype.

The single-line replace pattern missed a declaration wrapped across lines (e.g.
a 5-arg variable_output `*_execute(..., out, max_out)`), so the generated decl
was appended as a second, conflicting/duplicate declaration — `conflicting
types` at compile time and perpetual `jm status` drift. A multi-line fallback
now replaces it in place. ``skip_names`` still preserves a user's existing decl
(interactive safety net) regardless of formatting.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._init import _inject_decls_into_core_h

_HEADER = """\
#ifndef X_CORE_H
#define X_CORE_H
#ifdef __cplusplus
extern "C" {
#endif

  /* Process a block. */
  size_t x_execute (x_state_t *s, const float _Complex *in, size_t n_in,
                    float _Complex *out, size_t max_out);

#ifdef __cplusplus
}
#endif
#endif /* X_CORE_H */
"""

# jm's generated form: single line, canonical param names, same 5-arg arity.
_GEN = (
    "size_t x_execute(x_state_t *state, const float complex *in, "
    "size_t n_in, float complex *out, size_t max_out);"
)


def _ndecls(text: str) -> int:
    return len(re.findall(r"\bx_execute\s*\(", text))


def test_multiline_decl_replaced_in_place(tmp_path):
    h = tmp_path / "x_core.h"
    h.write_text(_HEADER, encoding="utf-8")

    assert _inject_decls_into_core_h(h, "x", [_GEN]) is True
    text = h.read_text(encoding="utf-8")
    # Replaced, not duplicated: exactly one declaration of x_execute.
    assert _ndecls(text) == 1
    assert _GEN in text
    # The old multi-line form is gone.
    assert "const float _Complex *in, size_t n_in," not in text


def test_idempotent_second_inject(tmp_path):
    h = tmp_path / "x_core.h"
    h.write_text(_HEADER, encoding="utf-8")
    _inject_decls_into_core_h(h, "x", [_GEN])
    # Now the generated decl is present verbatim → no further change.
    assert _inject_decls_into_core_h(h, "x", [_GEN]) is False


def test_skip_names_preserves_multiline_decl(tmp_path):
    """A name in skip_names is preserved verbatim even though it is multi-line
    — the generated decl is neither replaced nor appended (no duplicate)."""
    h = tmp_path / "x_core.h"
    h.write_text(_HEADER, encoding="utf-8")
    four_arg = (
        "size_t x_execute(x_state_t *state, const float complex *in, "
        "size_t n_in, float complex *out);"
    )
    changed = _inject_decls_into_core_h(
        h, "x", [four_arg], skip_names=frozenset({"x_execute"})
    )
    text = h.read_text(encoding="utf-8")
    assert changed is False
    assert _ndecls(text) == 1
    assert four_arg not in text  # not appended
