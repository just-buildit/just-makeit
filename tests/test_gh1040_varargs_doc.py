"""gh-1040: a `--varargs` method's `doc` must reach both faces.

`_context/_methods` resolves the summary — manifest `doc`, else the header
`@brief` — two lines above the `varargs` branch, and that branch then threw it
away. Both faces canned their text from the method's **name**:

    {"configure", …, "configure(*args, **kwargs)."},
    def configure(self, *args, **kwargs) -> Any:
        \"\"\"Configure.\"\"\"

so `--doc "Reconfigure the amplifier from a mapping."` was computed and
dropped. The two faces agreed with each other and with neither source, which
is why it read as working.

The shape is worth naming: a `--varargs` method is the one whose binding jm
does **not** write. Its purpose is therefore the one jm can least infer from
its name, and it was the only shape given no way to say so.

Both faces now route through the pair that share a section builder —
`render_runtime_doc` and `render_numpy_doc` — rather than spelling anything
locally. That is the gh-642/gh-651 invariant: the runtime block IS the stub
block, minus indentation and delimiters. A local literal in one of them is
exactly what gh-1039's canned-site detector exists to find, and this was the
last site it had left.

`Parameters` stays empty on purpose. A varargs method's arguments are unknown
to jm by definition — that is what the flag means — so there is nothing to
document, and entries for `*args` / `**kwargs` would describe the mechanism
rather than the method. Everything the header does say still comes through.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

DOC = "Reconfigure the amplifier from a mapping."


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "d40"
    _quiet(new_run, "d40", root)
    _quiet(
        object_run,
        root,
        "amp",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("gain", "float", "1.0f")],
    )
    return root


def _varargs(root: Path, name: str = "configure", **kw):
    _quiet(
        method_run,
        root,
        "amp",
        name,
        None,
        arg_type="void",
        return_type="void",
        variable_output=False,
        multi_output=[],
        varargs=True,
        **kw,
    )


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "amp" / "amp_ext.c").read_text(
        encoding="utf-8"
    )


def _pyi(root: Path) -> str:
    return (root / "src" / "d40" / "amp.pyi").read_text(encoding="utf-8")


def _pmd_doc(ext: str, name: str) -> str:
    """The C string literal following this method's PyMethodDef row.

    Read off the emitted C rather than matched against a template — a literal
    is what let both faces agree on the wrong text.
    """
    i = ext.index(f'{{"{name}",')
    row = ext[i : ext.index("},", i)]
    return "".join(
        line.strip().strip('"')
        for line in row.splitlines()
        if line.strip().startswith('"')
    )


def _pyi_method(pyi: str, name: str) -> str:
    i = pyi.index(f"    def {name}(")
    rest = pyi[i:]
    end = rest.find('"""', rest.index('"""') + 3)
    return rest[: end + 3]


class TestTheManifestDocReachesBothFaces:
    def test_the_runtime_binding_carries_it(self, project):
        _varargs(project, doc=DOC)
        assert DOC in _pmd_doc(_ext(project), "configure")

    def test_the_stub_carries_it(self, project):
        _varargs(project, doc=DOC)
        assert DOC in _pyi_method(_pyi(project), "configure")

    def test_neither_face_cans_the_name(self, project):
        """The old text, specifically.

        Both faces produced a sentence built from the method name. Asserting
        its absence is what distinguishes "the doc arrived" from "the doc
        arrived and the canned line is still above it".
        """
        _varargs(project, doc=DOC)
        assert "Configure." not in _pyi_method(_pyi(project), "configure")

    def test_the_signature_line_survives(self, project):
        """The runtime face's first line is the call signature.

        It is the one part of the old literal worth keeping — `help()` opens
        with it — so the fix must not have thrown it out with the canned
        summary.
        """
        _varargs(project, doc=DOC)
        assert "configure(*args, **kwargs)" in _pmd_doc(
            _ext(project), "configure"
        )


class TestTheFallbackStillWorks:
    """A method that declares nothing must still read as something.

    The precedence is manifest `doc` > header `@brief` > the name-derived
    sentence. Only the first two were being dropped; removing the third would
    trade one defect for an empty docstring.
    """

    def test_no_doc_falls_back_to_the_name(self, project):
        _varargs(project)
        assert "Configure." in _pyi_method(_pyi(project), "configure")
        assert "Configure." in _pmd_doc(_ext(project), "configure")


class TestTheHeaderBriefIsHonoured:
    """`@brief` on the bound symbol, not just the manifest `doc`.

    Precedence is manifest `doc` > header `@brief` > the name-derived
    sentence, and the varargs branch dropped BOTH of the first two with one
    line. Exercised at the context level, because that is where a
    `DoxyBlock` can be supplied for the symbol the binding declares — a
    varargs method's body lives in its own `<fn>_core.c`, so there is no
    `_core.h` prototype for a header block to hang off in a scaffolded tree.
    """

    @staticmethod
    def _ctx(doc="", brief=""):
        from just_makeit._context._methods import make_methods_ctx
        from just_makeit._docstring import DoxyBlock

        blocks = {"amp_configure": DoxyBlock(brief=brief)} if brief else {}
        method = {"name": "configure", "varargs": True}
        if doc:
            method["doc"] = doc
        return make_methods_ctx(
            "amp",
            "Amp",
            [method],
            pkg="d40",
            doc_blocks=blocks,
        )

    def test_a_header_brief_reaches_both_faces(self):
        ctx = self._ctx(brief="Drain the reconfiguration queue.")
        blob = ctx["extra_methods_pymethoddef"] + ctx["pyi_extra_methods"]
        assert "Drain the reconfiguration queue." in blob, blob
        assert "Configure." not in blob, blob

    def test_the_manifest_doc_outranks_the_header(self):
        """The precedence, asserted rather than assumed.

        Both come through the same `_brief` expression, so a fix that read
        only one of them would pass every other test in this file.
        """
        ctx = self._ctx(doc=DOC, brief="Drain the reconfiguration queue.")
        blob = ctx["extra_methods_pymethoddef"] + ctx["pyi_extra_methods"]
        assert DOC in blob
        assert "Drain the reconfiguration queue." not in blob


class TestBothFacesAgree:
    """gh-642's invariant: the runtime block IS the stub block.

    Asserted here because this shape reached it last — while both faces were
    canned they agreed trivially, which is the version of agreement that
    hides a defect rather than preventing one.
    """

    def test_the_summary_is_the_same_sentence(self, project):
        _varargs(project, doc=DOC)
        runtime = _pmd_doc(_ext(project), "configure")
        stub = _pyi_method(_pyi(project), "configure")
        assert DOC in runtime and DOC in stub

    def test_the_stub_has_no_leftover_canned_line(self, project):
        _varargs(project, doc=DOC)
        stub = _pyi_method(_pyi(project), "configure")
        assert stub.count(DOC) == 1, stub
