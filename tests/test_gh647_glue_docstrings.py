"""gh-647: jm's own glue methods carry real docstrings, on both faces.

``state_bytes`` / ``get_state`` / ``set_state``, ``destroy``/``close`` and the
context-manager pair are 100% jm-generated. A downstream project cannot document
them by writing C Doxygen -- there is no hand-written declaration to attach a
comment to -- so the text has to come from jm. Before this they were bare
one-liners, and ``__enter__``/``__exit__`` had no docstring at all (``NULL`` in
the method table).

The tests below pin the three properties that make it a fix rather than a
rewording:

1. **Both faces carry it**, from one definition. The old literals had already
   drifted -- ``destroy`` said "Release C resources immediately." in the stub
   and "Release resources." at runtime.
2. **The prose names the right object.** ``get_state`` used to say "the
   engine's mutable state" on every component, naming a long-gone example.
3. **The generated C stays ASCII.** These strings become C string literals, not
   comments; the rest of jm's generated C uses non-ASCII only inside comments.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from conftest import flatten_signatures  # noqa: E402

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._gluedoc import glue_methods  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_SERIAL = ("state_bytes", "get_state", "set_state")
_LIFECYCLE = ("destroy", "__enter__", "__exit__")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
        serializable=True,
    )
    apply_run(root)
    return root


def _pyi(root):
    return (root / "src" / "demo" / "widget.pyi").read_text(encoding="utf-8")


def _ext(root):
    return (root / "native" / "src" / "widget" / "widget_ext.c").read_text(
        encoding="utf-8"
    )


class TestOneDefinitionTwoFaces:
    def test_every_glue_method_is_documented_in_the_stub(self, project):
        pyi = _pyi(project)
        for name in _SERIAL + _LIFECYCLE:
            gm = glue_methods("Widget")[name]
            assert gm.block.brief in pyi, f"{name} undocumented in .pyi"

    def test_every_glue_method_is_documented_at_runtime(self, project):
        ext = _ext(project)
        for name in _SERIAL + _LIFECYCLE:
            gm = glue_methods("Widget")[name]
            assert gm.block.brief in ext, f"{name} undocumented at runtime"

    def test_context_manager_pair_is_no_longer_null(self, project):
        # This was the one part of the generated surface with no docstring at
        # all on either face, so help() showed nothing for it.
        ext = _ext(project)
        for entry in ('{"__enter__"', '{"__exit__"'):
            tail = ext.split(entry)[1].split("},")[0]
            assert "NULL" not in tail, f"{entry} doc is still NULL"

    def test_the_two_faces_agree(self, project):
        # destroy used to say "Release C resources immediately." in the stub
        # and "Release resources." at runtime.
        brief = glue_methods("Widget")["destroy"].block.brief
        assert brief in _pyi(project)
        assert brief in _ext(project)


class TestProseIsAboutThisObject:
    def test_get_state_does_not_name_a_foreign_component(self, project):
        pyi = _pyi(project)
        assert "engine" not in pyi.lower(), (
            "the serialization prose names a component from an old example "
            "rather than the object it documents"
        )

    def test_prose_is_parametrised_by_class_name(self):
        assert "Fir" in glue_methods("Fir")["__enter__"].block.body[0]
        assert "Biquad" in glue_methods("Biquad")["__enter__"].block.body[0]

    def test_exit_names_the_objects_own_teardown(self):
        # A reader-shaped object spells it `close`, not `destroy`.
        gm = glue_methods("Reader", close_name="close")["__exit__"]
        assert "`close()`" in gm.block.body[0]
        assert "`destroy()`" not in gm.block.body[0]


class TestGeneratedCStaysAscii:
    def test_no_non_ascii_in_generated_c(self, project):
        # These become C string literals, not comments. jm's generated C uses
        # non-ASCII only inside comments today.
        for i, line in enumerate(_ext(project).splitlines(), 1):
            stripped = line.lstrip()
            if not stripped.startswith('"'):
                continue
            assert all(ord(c) < 128 for c in line), (
                f"widget_ext.c:{i} puts non-ASCII in a C string literal: "
                f"{line.strip()!r}"
            )

    def test_glue_prose_is_ascii(self):
        for name, gm in glue_methods("Widget").items():
            text = " ".join(
                [gm.block.brief, gm.block.returns]
                + gm.block.body
                + [d for _n, d in gm.block.params]
            )
            assert all(ord(c) < 128 for c in text), (
                f"{name} prose is non-ASCII"
            )


class TestRenderedShape:
    def test_c_doc_lines_are_wrapped(self):
        # Each line becomes a C string literal a human reads; clang-format
        # will not reflow them.
        for name, gm in glue_methods("Widget").items():
            for line in gm.c_doc_lines():
                assert len(line) <= 79, f"{name}: {line!r}"

    def test_stub_renders_numpy_sections(self):
        doc = "\n".join(glue_methods("Widget")["set_state"].pyi_doc())
        assert "Parameters" in doc and "blob : bytes" in doc
        doc = "\n".join(glue_methods("Widget")["state_bytes"].pyi_doc())
        assert "Returns" in doc and "int" in doc

    def test_no_examples_section(self):
        # The generated .pyi docstrings are harvested and executed by the
        # doctest gate; an example here would have to construct a real object
        # of an arbitrary component.
        for name, gm in glue_methods("Widget").items():
            doc = "\n".join(gm.pyi_doc())
            assert ">>>" not in doc, f"{name} would feed the doctest gate"

    def test_documented_params_all_appear_in_the_signature(self):
        # griffe reports "documented parameter not in the signature" for this
        # mismatch, and __exit__ had it: a `*args: object` signature over
        # three documented names. Both now come from py_params.
        for name, gm in glue_methods("Widget").items():
            sig = gm.pyi_params(defaults=True)
            for pname, _desc in gm.block.params:
                assert f"{pname}: " in sig, (
                    f"{name} documents {pname!r}, absent from `{sig}`"
                )

    def test_exit_signature_is_named_not_varargs(self, project):
        pyi = flatten_signatures(_pyi(project))
        assert "def __exit__(self, *args: object)" not in pyi
        assert "def __exit__(self, exc_type: object | None = ..." in pyi

    def test_enter_return_type_is_not_quoted_in_the_doc(self):
        # The forward-reference quoting belongs to the emitted signature, not
        # to the numpy Returns type column.
        doc = "\n".join(glue_methods("Widget")["__enter__"].pyi_doc())
        assert '"Widget"' not in doc
        assert "Widget" in doc
