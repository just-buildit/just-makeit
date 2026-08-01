"""gh-645: a module can be documented, on both faces.

A module is the one public surface with no header to derive from. Its
extension `m_doc` and its re-export `__init__.py` are both wholly
jm-generated, so unlike an object -- whose documentation comes from the sacred
`<obj>_core.h` -- there was nowhere for an author to say what a module is for.
`m_doc` was a fixed `"<Module> module."` literal and the shim carried a
`#` comment, which is not a docstring and which griffe does not read.

`[module.X] doc` now feeds both from one string:

* the extension's `m_doc` -- what `help(pkg.mod)` prints;
* a real module docstring on the shim -- what griffe/mkdocstrings renders as
  the module's page.

Two classes of test here beyond "it appears":

**Escaping.** The prose comes from TOML, so it can contain anything. A triple
quote or a trailing quote makes the generated `__init__.py` unparseable; a
quote or a backslash makes the generated C not compile. Both are escaped
rather than rejected -- refusing an author's punctuation would be worse than
quoting it. (Writing this very docstring hit the first case.)

**Threading through `jm apply`.** `doc` is read at module-creation time, so
the replay has to forward it exactly as it forwards `package`. This is the
gh-663 shape: a new manifest key that works through the CLI and is silently
dropped by the flow that regenerates from the manifest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._context._modpath import make_module_ctx  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402

_DOC = "Filter bank: FIR, IIR and polyphase resamplers."


def _project(tmp_path: Path, doc: str = _DOC) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "filt", doc=doc)
    object_run(
        root,
        "widget",
        "filt",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return root


def _ext_c(root: Path) -> str:
    return (root / "native" / "src" / "filt" / "filt_ext.c").read_text(
        encoding="utf-8"
    )


def _init_py(root: Path) -> str:
    return (root / "src" / "demo" / "filt" / "__init__.py").read_text(
        encoding="utf-8"
    )


class TestBothFaces:
    def test_runtime_m_doc(self, tmp_path):
        assert _DOC in _ext_c(_project(tmp_path))

    def test_module_docstring_on_the_shim(self, tmp_path):
        text = _init_py(_project(tmp_path))
        assert text.startswith(f'"""{_DOC}"""'), (
            "the docstring must be the first statement, or it is not a module "
            "docstring at all -- just a string expression griffe ignores"
        )

    def test_persisted_to_the_manifest(self, tmp_path):
        root = _project(tmp_path)
        assert C.module_doc(C.load(root), "filt") == _DOC

    def test_no_slot_leaks_into_generated_code(self, tmp_path):
        root = _project(tmp_path)
        assert "<<" not in _ext_c(root)
        assert "<<" not in _init_py(root)


class TestUndeclaredIsUnchanged:
    """The default has to render exactly as it did before this key existed."""

    def test_m_doc_keeps_the_generated_default(self, tmp_path):
        root = _project(tmp_path, doc="")
        assert '.m_doc     = "Filt module."' in _ext_c(root)

    def test_shim_has_no_docstring(self, tmp_path):
        assert not _init_py(_project(tmp_path, doc="")).startswith('"""')

    def test_fresh_scaffold_is_clean(self, tmp_path):
        assert status_run(_project(tmp_path, doc="")) == 0


class TestSurvivesApply:
    """gh-663's shape: a key the CLI honours and the replay drops."""

    def test_apply_is_idempotent(self, tmp_path):
        root = _project(tmp_path)
        assert status_run(root) == 0
        apply_run(root)
        assert status_run(root) == 0

    def test_doc_survives_a_regeneration(self, tmp_path):
        root = _project(tmp_path)
        apply_run(root)
        assert _DOC in _ext_c(root)
        assert _DOC in _init_py(root)


class TestEscaping:
    """Prose from TOML must not be able to emit code that will not build."""

    @pytest.mark.parametrize(
        "doc",
        [
            'Has "quotes" inside.',
            'Ends with a quote"',
            "Back\\slash",
            'Contains """ a triple quote.',
            "Two\nlines.",
        ],
    )
    def test_generated_python_parses(self, doc):
        src = make_module_ctx("filt", "demo", doc=doc)["module_docstring_py"]
        compile(src + "x = 1\n", "<generated>", "exec")

    @pytest.mark.parametrize(
        "doc", ['Has "quotes".', "Back\\slash", "Two\nlines."]
    )
    def test_generated_c_literal_is_well_formed(self, doc):
        lit = make_module_ctx("filt", "demo", doc=doc)["module_doc_c"]
        # Every piece is a complete "..." literal; adjacent ones concatenate.
        for piece in lit.split("\n"):
            piece = piece.strip()
            assert piece.startswith('"') and piece.endswith('"'), piece
            # An unescaped interior quote would terminate the literal early.
            body = piece[1:-1]
            i = 0
            while i < len(body):
                if body[i] == "\\":
                    i += 2
                    continue
                assert body[i] != '"', f"unescaped quote in {piece!r}"
                i += 1

    def test_multiline_doc_reaches_both_faces(self, tmp_path):
        root = _project(tmp_path, doc="First line.\nSecond line.")
        assert "Second line." in _ext_c(root)
        assert "Second line." in _init_py(root)
