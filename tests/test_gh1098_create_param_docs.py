"""gh-1098: the `create()` Doxygen kept describing the OLD signature.

jm injects the `create()` declaration into the sacred `_core.h` and refreshes
it whenever the manifest changes. It never touched the comment above it, so
adding an `init_param` to an existing object left the header documenting
`@param gain` — a parameter no longer taken — and documenting none of the ones
now taken.

That is not cosmetic. jm already renders the right text: `create_param_docs` is
built beside `create_params` from the same chain and carries the per-kind
contract prose — an array's length parameter, a nullable capsule's `May be
NULL (Python: None)` (gh-805 §H put it there so the author reads it where they
write the body that has to honour it), a required scalar's `(required)`. It
reached the header at scaffold time only.

It is also the one part of the declaration `jm status --check` cannot see:
gh-1076's CTOR check compares the parameter *list* and passes while the block
above it is wrong.

**Prose is never rewritten**, and that is the design rather than a limitation.
`_init._inject_decls_into_core_h` states the rule this had to respect — "a
refreshed signature replaces the prototype line alone, leaving whatever
documentation is already above it untouched, so re-running a command never
re-stamps a skeleton over authored prose" — and the header's `@brief` is read
back as the Python class docstring (`_docstring.authored_class_brief`). So
only the `@param` SET is reconciled: names that survive keep their
descriptions byte-for-byte, new names arrive **bare** for the author to fill
(`scaffold_doc_block`'s rule, for its reasons), and names that are gone go.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import reconcile_param_docs  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _apply(root: Path):
    return __import__("just_makeit._apply", fromlist=["run"]).run(root)


FRAG = (
    '\n[[obj.init_params]]\nname = "ref"\ntype = "float _Complex[][]"\n'
    '\n[[obj.init_params]]\nname = "dwell"\ntype = "size_t"\n'
    'default = "16"\n'
)


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "obj",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("gain", "float", "0.0f")],
    )
    man = root / "objects" / "obj.toml"
    if not man.exists():
        man = root / "just-makeit.toml"
    man.write_text(man.read_text(encoding="utf-8") + FRAG, encoding="utf-8")
    _quiet(_apply, root)
    return root


def _header(root: Path) -> str:
    return (root / "native" / "inc" / "obj" / "obj_core.h").read_text(
        encoding="utf-8"
    )


def _create_block(text: str) -> str:
    """The Doxygen block above `obj_create`."""
    at = text.index("obj_state_t *obj_create(")
    start = text.rindex("/**", 0, at)
    return text[start : text.index("*/", start) + 2]


class TestTheParamSetMatchesTheSignature:
    def test_a_parameter_that_is_gone_is_dropped(self, tmp_path):
        """`gain` is the scaffold's state field. Once `init_params` drive the
        constructor it is not a parameter at all, and the header said it was.
        """
        assert "@param gain" not in _create_block(
            _header(_project(tmp_path, "a"))
        )

    def test_every_real_parameter_is_documented(self, tmp_path):
        """Including the two jm synthesises for a 2-D array's extents — a
        reader of the header has no other way to learn they exist."""
        block = _create_block(_header(_project(tmp_path, "b")))
        for name in ("ref", "ref_dim0", "ref_dim1", "dwell"):
            assert f"@param {name}" in block

    def test_they_are_in_signature_order(self, tmp_path):
        block = _create_block(_header(_project(tmp_path, "c")))
        got = [
            ln.split("@param ")[1].split()[0]
            for ln in block.splitlines()
            if "@param " in ln
        ]
        assert got == ["ref", "ref_dim0", "ref_dim1", "dwell"]


class TestProseIsNeverRewritten:
    def test_the_brief_and_note_survive(self, tmp_path):
        """`@brief` is read back as the Python class docstring, so losing it
        would silently change the generated API's documentation."""
        block = _create_block(_header(_project(tmp_path, "d")))
        assert "@brief Create a obj instance." in block
        assert "@note Caller must call obj_destroy() when done." in block
        assert "@return Heap-allocated state" in block

    def test_an_authored_description_is_kept_byte_for_byte(self, tmp_path):
        """The manifest gains a parameter as well, so the declaration really
        is refreshed and the reconciler really does run.

        Authoring prose and re-applying an UNCHANGED manifest proves nothing:
        the declaration matches, so it is never replaced and the block is
        never reached. Measured — that version stayed green with the
        reconciler rewriting every description.
        """
        root = _project(tmp_path, "e")
        h = root / "native" / "inc" / "obj" / "obj_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                " * @param dwell",
                " * @param dwell  Dwell time in samples. AUTHORED.",
            ),
            encoding="utf-8",
        )
        man = root / "objects" / "obj.toml"
        if not man.exists():
            man = root / "just-makeit.toml"
        man.write_text(
            man.read_text(encoding="utf-8")
            + '\n[[obj.init_params]]\nname = "extra"\ntype = "int"\n'
            'default = "1"\n',
            encoding="utf-8",
        )
        _quiet(_apply, root)
        text = _header(root)
        # the refresh happened...
        assert "@param extra" in text
        # ...and it did not cost the authored line
        assert " * @param dwell  Dwell time in samples. AUTHORED." in text

    def test_a_second_apply_changes_nothing(self, tmp_path):
        """Idempotence is what keeps `jm status --check` quiet. A reconciler
        that reordered or re-stamped on every run would make a project report
        drift against itself forever."""
        root = _project(tmp_path, "f")
        before = _header(root)
        _quiet(_apply, root)
        assert _header(root) == before


class TestTheReconcilerItself:
    """Unit-level, because the interesting cases are hard to scaffold."""

    BLOCK = (
        "/**\n"
        " * @brief Create.\n"
        " *\n"
        " * @param gain  Initial gain (default: 0.0f).\n"
        " * @param dwell  AUTHORED.\n"
        " * @return state.\n"
        " */"
    )

    def test_it_adds_drops_and_keeps_in_one_pass(self):
        out = reconcile_param_docs(
            self.BLOCK, "s_t *s_create(const float *ref, size_t dwell);"
        )
        assert "@param gain" not in out
        assert " * @param ref\n" in out
        assert " * @param dwell  AUTHORED." in out

    def test_a_block_with_no_params_gains_them_before_return(self):
        out = reconcile_param_docs(
            "/**\n * @brief M.\n * @return s.\n */", "s_t *s_create(int n);"
        )
        assert out.index("@param n") < out.index("@return")

    def test_an_unreadable_decl_leaves_the_block_alone(self):
        """Best-effort in the same direction as every other header reader: a
        shape jm cannot parse is not a licence to rewrite the author's file.
        """
        assert reconcile_param_docs(self.BLOCK, "not a declaration") == (
            self.BLOCK
        )

    def test_it_is_idempotent(self):
        decl = "s_t *s_create(const float *ref, size_t dwell);"
        once = reconcile_param_docs(self.BLOCK, decl)
        assert reconcile_param_docs(once, decl) == once
