"""gh-685: a view method derives from the block its parent derives from.

A view shares its parent's `_core.c` and calls the same C functions, so
`ddc_execute_ctrl`'s Doxygen documents `MatchedDDC.execute_ctrl` exactly as it
documents `DDC.execute_ctrl`. Only the parent derived from it:

    class DDC:
        def execute_ctrl(self, x) -> float:
            \"\"\"VIEWMARK controlled execute.        # derived
            Parameters ...

    class MatchedDDC:
        def execute_ctrl(self, x) -> float:
            \"\"\"Execute ctrl.\"\"\"                    # name-based stub

The cause is not derivation: the block parses and reaches the parent. The stub
builder keys its lookups on the component it is rendering, and for a view that
is a *synthetic* id (`ddc__view_matchedddc`), so every `<component>_<member>`
lookup missed.

**Narrower than filed.** The issue assumed both faces; the runtime face was
already correct -- `_make_view_ctx` passes the parent's blocks -- so this is a
stub-face-only fix. The runtime assertions below exist to keep it that way.

Aliasing by member name is right for a view's *own* methods too: per
`_config.view_methods`, an added method "scaffolds a shared C stub", so every
method a view exposes lives in the parent's C namespace either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

_AUTHORED = """ * @brief VIEWMARK controlled execute.
 *
 * @param x  Input block.
 * @return Peak magnitude."""


def _project(tmp_path: Path, *, author: bool = True) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "dsp")
    object_run(
        root,
        "ddc",
        "dsp",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    method_run(
        root, "ddc", "execute_ctrl", "dsp", "float[]", "float", False, []
    )
    view_run(root, "ddc", "MatchedDDC", "dsp", create_fn="ddc_create_matched")
    if author:
        h = root / "native" / "inc" / "ddc" / "ddc_core.h"
        t = h.read_text(encoding="utf-8")
        assert " * @brief execute_ctrl." in t
        h.write_text(
            t.replace(" * @brief execute_ctrl.", _AUTHORED), encoding="utf-8"
        )
        apply_run(root)
    return root


def _pyi(root: Path) -> str:
    return next(root.rglob("*.pyi")).read_text(encoding="utf-8")


def _class_body(pyi: str, name: str) -> str:
    """The body of `class <name>`, matched exactly.

    A substring split would be wrong here: "class Ddc" is not a prefix of
    "class MatchedDDC", but the reverse trap is easy to hit with view names,
    so anchor on the line.
    """
    marker = f"\nclass {name}:"
    assert marker in pyi, f"no `class {name}:` in the stub"
    return pyi.split(marker)[1].split("\nclass ")[0]


def _view_c(root: Path) -> str:
    return (
        root / "native" / "src" / "dsp" / "dsp_ext_matchedddc.c"
    ).read_text(encoding="utf-8")


class TestViewInheritsTheParentsBlock:
    def test_view_method_derives(self, tmp_path):
        body = _class_body(_pyi(_project(tmp_path)), "MatchedDDC")
        assert "VIEWMARK controlled execute." in body

    def test_view_gets_the_full_block_not_just_the_brief(self, tmp_path):
        body = _class_body(_pyi(_project(tmp_path)), "MatchedDDC")
        assert "Input block." in body
        assert "Peak magnitude." in body

    def test_parent_still_derives(self, tmp_path):
        body = _class_body(_pyi(_project(tmp_path)), "Ddc")
        assert "VIEWMARK controlled execute." in body

    def test_name_based_stub_is_gone(self, tmp_path):
        body = _class_body(_pyi(_project(tmp_path)), "MatchedDDC")
        assert '"""Execute ctrl."""' not in body


class TestRuntimeFaceWasAlreadyCorrect:
    """Narrower than the issue assumed -- keep it that way."""

    def test_runtime_derives(self, tmp_path):
        assert "VIEWMARK controlled execute." in _view_c(_project(tmp_path))

    def test_both_faces_now_agree(self, tmp_path):
        root = _project(tmp_path)
        body = _class_body(_pyi(root), "MatchedDDC")
        assert ("VIEWMARK controlled execute." in body) and (
            "VIEWMARK controlled execute." in _view_c(root)
        )


class TestUnauthoredIsUnchanged:
    def test_scaffold_falls_back_to_the_name_stub(self, tmp_path):
        body = _class_body(
            _pyi(_project(tmp_path, author=False)), "MatchedDDC"
        )
        assert "Execute ctrl." in body

    def test_fresh_scaffold_is_not_stale(self, tmp_path):
        assert status_run(_project(tmp_path, author=False)) == 0

    def test_authored_project_is_idempotent(self, tmp_path):
        root = _project(tmp_path)
        assert status_run(root) == 0
        apply_run(root)
        assert status_run(root) == 0
