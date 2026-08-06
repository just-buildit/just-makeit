"""gh-779 — a spliced member must bring its file-scope declarations with it.

The incremental path that adds a member to an existing sacred
``_ext_<obj>.c`` splices two things: *functions*, located by name, and
*PyMethodDef/PyGetSetDef rows*. A file-scope ``static`` is neither, so a
wrapper that references one gained a body naming a symbol nothing declared —
and the module did not compile. A full render prepends the declaration to the
function, so the two only come apart on the incremental path.

gh-729 hit this with a record's ``<fn>_type``/``<fn>_desc`` and fixed it with
a finder that knows *that* shape by name. gh-779 is an ``enum =`` property's
``_enum_<Class>_<prop>[]`` table arriving by the identical route, and the
reporter's read is the one this file pins: **the per-referent-type split is
the bug**, not either instance of it. A third kind would fail the same way,
and nobody would find out until it failed to compile.

So the carry asks what the body references and whether the reference render
declares it, rather than asking whether it is a shape jm was told about.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _docsync as D  # noqa: E402

REFERENCE = """\
#include "reader/reader_core.h"

static const char *const _enum_Reader_fs_source[] = {
    "UNKNOWN",
    "HEADER",
    "USER",
    NULL,
};

static PyObject *
Reader_getprop_fs_source(ReaderObject *self, void *closure)
{
    int _v = reader_get_fs_source(self->handle);
    return PyUnicode_FromString(_enum_Reader_fs_source[_v]);
}

static PyGetSetDef Reader_getset[] = {
  {"fs_source", (getter)Reader_getprop_fs_source, NULL, "fs_source", NULL},
  {NULL}
};
"""

EXISTING = """\
#include "reader/reader_core.h"

static PyGetSetDef Reader_getset[] = {
  {NULL}
};
"""


class TestTheDeclarationTravels:
    def test_the_table_is_carried_with_the_getter(self):
        out = D.transplant_missing_bindings(EXISTING, REFERENCE)
        assert "Reader_getprop_fs_source" in out
        assert "static const char *const _enum_Reader_fs_source[] = {" in out

    def test_the_values_come_too_not_just_the_header(self):
        """A declaration truncated at the `{` compiles to an empty table and
        indexes out of bounds — worse than not carrying it."""
        out = D.transplant_missing_bindings(EXISTING, REFERENCE)
        decl = out[out.index("_enum_Reader_fs_source[]") :]
        assert '"HEADER"' in decl.split(";")[0]

    def test_it_is_not_carried_twice(self):
        """Applying to a fragment that already has the declaration must not
        redeclare it — a C redefinition error."""
        once = D.transplant_missing_bindings(EXISTING, REFERENCE)
        twice = D.transplant_missing_bindings(once, REFERENCE)
        assert twice.count("_enum_Reader_fs_source[] = {") == 1

    def test_an_unreferenced_declaration_is_left_behind(self):
        """Scoped to what the spliced body actually names — carrying every
        file-scope static in the reference would drag in unrelated ones."""
        ref = REFERENCE.replace(
            "static PyObject *\nReader_getprop_fs_source",
            'static const char *const _enum_Reader_unused[] = {"X",};\n\n'
            "static PyObject *\nReader_getprop_fs_source",
        )
        out = D.transplant_missing_bindings(EXISTING, ref)
        assert "_enum_Reader_fs_source" in out
        assert "_enum_Reader_unused" not in out


class TestTheFinder:
    def test_it_captures_the_whole_initialiser(self):
        decls = D._file_scope_decls(REFERENCE)
        assert "_enum_Reader_fs_source" in decls
        assert decls["_enum_Reader_fs_source"].endswith("};")

    def test_a_static_local_is_not_claimed(self):
        """Generated C indents a function body, and the pattern is anchored at
        column 0 — a `static char *kwlist[]` inside a wrapper is not a
        file-scope declaration to carry."""
        src = (
            "static int\nFoo_init(Obj *self)\n{\n"
            '    static char *kwlist[] = {"a", NULL};\n    return 0;\n}\n'
        )
        assert "kwlist" not in D._file_scope_decls(src)

    def test_a_static_inside_a_string_literal_is_not_claimed(self):
        src = 'static const char *d = "static int x = 1;";\n'
        assert list(D._file_scope_decls(src)) == ["d"]

    def test_only_referenced_names_come_back(self):
        body = "int _v = 0;\nreturn _enum_Reader_fs_source[_v];\n"
        got = D._referenced_file_scope_decls(REFERENCE, body)
        assert len(got) == 1 and "_enum_Reader_fs_source" in got[0]
        assert D._referenced_file_scope_decls(REFERENCE, "return NULL;") == []


@pytest.mark.skipif(
    not __import__("shutil").which("cmake"), reason="needs a C toolchain"
)
class TestItCompiles:
    """The claim is that the module builds — the symbol being textually
    present is only evidence for it."""

    def test_an_incrementally_added_enum_property_builds(self, tmp_path):
        import subprocess

        from just_makeit._apply import run as apply_run
        from just_makeit._module import run as module_run
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        root = tmp_path / "proj"
        new_run("proj", root, fragments=True)
        module_run(root, "wfm")
        object_run(root, "reader", "wfm", state_vars=[("g", "double", "1.0")])
        (root / "just-makeit.toml").write_text(
            (root / "just-makeit.toml").read_text()
            + '\n[[enum]]\nname = "fs_source"\n'
            'values = ["UNKNOWN", "HEADER", "USER"]\n'
        )
        apply_run(root)

        # The incremental path, and reaching it is the whole fixture. Calling
        # `jm property` does NOT reach it: that regenerates the module's
        # fragments wholesale, so the declaration is present either way and
        # this test passed with the fix reverted. doppler hit the bug by
        # editing the manifest fragment and running `apply`, which splices the
        # new member into the existing file — so that is what this does.
        obj_toml = root / "objects" / "reader.toml"
        obj_toml.write_text(
            obj_toml.read_text()
            + '\n[[reader.properties]]\nname = "fs_source"\n'
            'type = "int"\nenum = "fs_source"\n'
        )
        apply_run(root)

        frag = root / "native" / "src" / "wfm" / "wfm_ext_reader.c"
        text = frag.read_text()
        assert "_enum_Reader_fs_source[]" in text, "declaration missing"

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;from pathlib import Path;"
                "from just_makeit import _build;"
                "r=Path('.').resolve();_build._ensure_built(r, r / 'build')",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=900,
            env={
                **__import__("os").environ,
                "PYTHONPATH": str(
                    Path(__file__).resolve().parent.parent / "src"
                ),
            },
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


def _gnu(text: str) -> str:
    """*text* as the project's formatter would leave it — GNU 2-space bodies
    and a space before the paren. Applied textually so the test does not need
    clang-format installed and cannot drift when a new release reflows
    something unrelated."""
    import re as _re

    out = _re.sub(r"^(\w+) ?\(", r"\1 (", text, flags=_re.M)
    return _re.sub(r"^    ", "  ", out, flags=_re.M)


class TestTheDeclarationIsNotDuplicated:
    """Raised on review of #780, and it is gh-770 in a new costume.

    The dedupe guard was `if decl_text not in existing`. These fragments are
    reformatted after every apply, so the copy already in the file is GNU
    2-space while the one the reference renders is K&R 4-space: the same
    declaration, never equal as substrings. The guard failed **open**, the
    carry emitted a second copy, and the file did not compile —

        error: redefinition of '_enum_Reader_fs_source'

    Newly reachable *because* of gh-777: before it, an absent member was
    tolerated and nobody ran `apply` to fix it. After it the gate says fix it,
    and the fix was what broke the build.

    Every other test in this file generates both sides with jm, so both are
    K&R and the substring check succeeds by accident. That is exactly why this
    one formats the existing side first — the guard has to be tested against
    input it can actually fail on.
    """

    def _existing_with_the_table(self) -> str:
        """A fragment that already declares the table, in the project's style
        rather than jm's, and is missing only the member."""
        decl = REFERENCE[
            REFERENCE.index("static const char *const") : REFERENCE.index(
                "static PyObject *"
            )
        ]
        return _gnu(
            EXISTING.replace("static PyGetSetDef", decl + "static PyGetSetDef")
        )

    def test_a_differently_formatted_copy_is_recognised(self):
        existing = self._existing_with_the_table()
        assert "_enum_Reader_fs_source" in existing
        # The guard that makes this test mean something: the copy already in
        # the file must not be findable as a substring of the reference, or
        # the old text-based dedupe would have worked and nothing is proven.
        # A first draft asserted on the declaration's *header* line, which
        # re-indenting does not touch — so the guard itself was vacuous.
        ref_decl = D._file_scope_decls(REFERENCE)["_enum_Reader_fs_source"]
        assert ref_decl not in existing, (
            "fixture must differ in formatting from the reference render"
        )

        out = D.transplant_missing_bindings(existing, REFERENCE)
        assert out.count("_enum_Reader_fs_source[] = {") == 1, (
            "the declaration already present must not be emitted again"
        )

    def test_the_member_still_arrives(self):
        """Guard: deduping harder must not stop the member being spliced."""
        out = D.transplant_missing_bindings(
            self._existing_with_the_table(), REFERENCE
        )
        assert "Reader_getprop_fs_source" in out
        assert '"fs_source"' in out

    def test_the_same_holds_for_the_first_array_path(self):
        """`_splice_first_array` carried the identical text guard.

        That function bails unless the fragment has a `PyTypeObject` to wire
        the new array into — so a fixture without one exercises nothing, which
        is how the first version of this test passed with the guard reverted.
        """
        existing = self._existing_with_the_table().replace(
            "static PyGetSetDef Reader_getset[] = {\n  {NULL}\n};\n",
            "static PyTypeObject ReaderType = {\n"
            '  .tp_name = "Reader",\n'
            "  .tp_methods = Reader_methods,\n"
            "};\n",
        )
        assert "PyGetSetDef" not in existing
        assert "PyTypeObject" in existing, (
            "the splice bails without a type object — the fixture must have "
            "one or this test proves nothing"
        )
        out = D.transplant_missing_bindings(existing, REFERENCE)
        assert "Reader_getprop_fs_source" in out, "the member must arrive"
        assert out.count("_enum_Reader_fs_source[] = {") == 1
