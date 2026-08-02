"""gh-684: generated declarations that live in the header derive from it.

`*_max_out` and the state accessors are generated, but jm declares them in the
sacred `<obj>_core.h` -- so an author can document them there like any other
function, and nothing read it. Both printed a canned string the header could
not override, and on both surfaces **the two faces did not even agree**:

    .pyi     "Return current gain."     |  "Max output length execute() ..."
    runtime  "Get gain."                |  "Max output length execute() ...
                                            Use to size the ``out=`` buffer."

which is the same drift gh-647 removed from the glue triplet.

`max_out` is the interesting half. Unlike gh-647's glue -- where jm owns the
semantics outright and `state_bytes()` means the identical thing everywhere --
`max_out` is uniform in *shape* and object-specific in *value*, and unless the
manifest declared the constant its C body is an `IMPLEMENT` stub **the author
writes**. `n` for a FIR, `ceil(n/R)` for a decimator. So a fixed generic string
would be actively wrong for the majority case: the header must win, and jm's
prose is only what fills the gap. When the constant *is* declared, jm knows the
answer and says it.

The accessor half is a direct cousin of gh-676, and a gap in it: that fix made
create/reset/step/steps derive across sixteen cells but did not cover
`get_`/`set_`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._gluedoc import max_out_method  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402


def _acc_project(tmp_path: Path, *, author: bool) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "acc",
        None,
        state_vars=[("acc", "float", "0.0f")],
        arg_type="float",
        return_type="float",
    )
    if author:
        h = root / "native" / "inc" / "acc" / "acc_core.h"
        t = h.read_text(encoding="utf-8")
        assert " * @brief Get current acc." in t
        t = t.replace(
            " * @brief Get current acc.",
            " * @brief ACCMARK running accumulator total.",
        ).replace(
            " * @brief Set acc.", " * @brief SETMARK overwrite the total."
        )
        h.write_text(t, encoding="utf-8")
        apply_run(root)
    return root


def _mo_project(tmp_path: Path, *, max_out: int = 0) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "fir",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    method_run(
        root,
        "fir",
        "execute",
        None,
        "float[]",
        "float",
        True,
        [],
        max_out=max_out,
    )
    return root


def _faces(root: Path, comp: str) -> tuple[str, str]:
    pyi = (root / "src" / "demo" / f"{comp}.pyi").read_text(encoding="utf-8")
    c = (root / "native" / "src" / comp / f"{comp}_ext.c").read_text(
        encoding="utf-8"
    )
    return pyi, c


class TestAccessorsDerive:
    @pytest.mark.parametrize("mark", ["ACCMARK", "SETMARK"])
    def test_authored_brief_reaches_both_faces(self, tmp_path, mark):
        pyi, c = _faces(_acc_project(tmp_path, author=True), "acc")
        assert mark in pyi
        assert mark in c

    def test_unauthored_keeps_the_canned_text(self, tmp_path):
        pyi, _c = _faces(_acc_project(tmp_path, author=False), "acc")
        assert '"""Return current acc."""' in pyi

    def test_fresh_scaffold_is_not_stale(self, tmp_path):
        # jm's own accessor templates must still read as scaffold, or a new
        # project derives its own boilerplate and reports drift against itself.
        assert status_run(_acc_project(tmp_path, author=False)) == 0

    def test_authored_project_is_idempotent(self, tmp_path):
        root = _acc_project(tmp_path, author=True)
        assert status_run(root) == 0


class TestMaxOutFallback:
    def test_fallback_has_real_numpy_sections(self, tmp_path):
        pyi, _c = _faces(_mo_project(tmp_path), "fir")
        block = pyi.split("def execute_max_out")[1]
        assert "Parameters" in block and "Returns" in block

    def test_both_faces_carry_the_same_brief(self, tmp_path):
        # They disagreed before: only the runtime carried the "out=" sentence.
        pyi, c = _faces(_mo_project(tmp_path), "fir")
        brief = max_out_method("execute", "n_in").block.brief
        assert brief in pyi
        assert brief.split(" can return")[0] in c

    def test_declared_constant_is_named(self, tmp_path):
        # With max_out declared, jm writes the body and knows the answer.
        pyi, _c = _faces(_mo_project(tmp_path, max_out=4), "fir")
        assert "Always 4" in pyi

    def test_undeclared_says_it_is_an_upper_bound(self):
        gm = max_out_method("execute", "n_in")
        assert "Upper bound" in gm.block.returns

    def test_no_realloc_substring(self):
        # "preallocate" contains "realloc", which a generated-C assertion
        # elsewhere greps for. Cheap to avoid, expensive to debug.
        doc = "\n".join(max_out_method("execute", "n_in").pyi_doc())
        assert "realloc" not in doc


class TestMaxOutHeaderWins:
    def test_authored_block_replaces_the_fallback(self, tmp_path):
        root = _mo_project(tmp_path)
        h = root / "native" / "inc" / "fir" / "fir_core.h"
        t = h.read_text(encoding="utf-8")
        assert "size_t fir_execute_max_out" in t
        h.write_text(
            t.replace(
                "size_t fir_execute_max_out",
                "/**\n"
                " * @brief MOMARK returns exactly n_in - num_taps + 1.\n"
                " * @param n_in  Input block length.\n"
                " * @return Output length; zero when n_in < num_taps.\n"
                " */\n"
                "size_t fir_execute_max_out",
                1,
            ),
            encoding="utf-8",
        )
        apply_run(root)
        pyi, c = _faces(root, "fir")
        assert "MOMARK" in pyi
        assert "MOMARK" in c
        # The fallback must be gone, not merely preceded.
        assert (
            "Largest number of samples"
            not in pyi.split("def execute_max_out")[1].split('"""')[1]
        )
