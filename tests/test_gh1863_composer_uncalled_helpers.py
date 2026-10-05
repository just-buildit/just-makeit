"""gh-1863: a composer emits a static helper only when something calls it.

A composer module's ``_ext.c`` carried ``_enum_index`` and ``_attach_bytes``
whatever its shape. With no enum looked up and no bytes field, neither had
a caller, and an uncalled ``static`` function is ``-Wunused-function``: two
warnings from generated glue under ``-Wall -Wextra``, which a downstream
building with ``-Werror`` cannot fix in a file ``jm apply`` regenerates.
gh-1745 had already done this for the module and handle faces; the
composer was the peer it missed.

The helpers stay exactly when the rest of their translation unit calls
them (``_composer._drop_uncalled``). These tests render both shapes, with and
without callers, and check the extension and the generated CLI. The
end-to-end gate is the ``composer_seams`` example, which builds the glue with
``-Wall -Wextra -Werror``.

GATE: a composer emits `_enum_index` / `_attach_bytes` only with a caller.
"""

from __future__ import annotations

import copy
import re

from test_composer_codegen import _cfg

from just_makeit import _composer

MODULE = "wfm_compose"


def _defines(text: str, fn: str) -> bool:
    """*fn* is defined in *text* (a ``static int`` at column 0)."""
    return re.search(rf"^static int\n{fn}\(", text, re.M) is not None


def _calls(text: str, fn: str) -> int:
    """Calls of *fn* in *text*, its definition excluded."""
    return len(re.findall(rf"(?<!\n){fn}\(", text))


def _no_callers():
    """The codegen fixture without an enum field or a bytes field."""
    cfg = copy.deepcopy(_cfg())
    src = cfg["module"][MODULE]["source"]
    src["fields"] = [
        f for f in src["fields"] if not f.get("enum") and not f.get("bytes")
    ]
    for f in cfg["module"][MODULE].get("segment", {}).get("fields", []):
        f.pop("enum", None)
    return cfg


def test_helpers_kept_when_called():
    text = _composer.render_ext(_cfg(), MODULE)
    for fn in ("_enum_index", "_attach_bytes"):
        assert _calls(text, fn), f"fixture no longer calls {fn}"
        assert _defines(text, fn), f"{fn} is called and not defined"


def test_helpers_dropped_when_uncalled():
    text = _composer.render_ext(_no_callers(), MODULE)
    for fn in ("_enum_index", "_attach_bytes"):
        assert not _calls(text, fn), f"fixture still calls {fn}"
        assert not _defines(text, fn), (
            f"{fn} is defined with no caller: -Wunused-function"
        )


def test_cli_lookup_only_with_a_caller():
    for cfg in (_cfg(), _no_callers()):
        text = _composer.render_cli(cfg, MODULE)
        assert _defines(text, "_enum_index") == bool(
            _calls(text, "_enum_index")
        )
