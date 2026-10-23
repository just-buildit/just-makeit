"""gh-2347: an object property of JSON type.

A core that already holds a structured document -- doppler's SigMF reader
holds the sidecar's ``annotations`` array (doppler-dsp/doppler#2565) -- had no
way to hand Python the structure. A ``const char *`` property returns the text,
so every caller re-parses it; a container property (gh-543) builds the
container from count/key/value accessors, and a free-form JSON object needs a
hand-written ``PyObject *`` value_fn.

``type = "json"`` declares it. The C getter::

    char *<stem>_get_<name>(const <stem>_state_t *state);

returns malloc'd JSON text that the binding OWNS: it parses it with
``json.loads`` and frees it on every path. ``NULL`` raises ``RuntimeError``
naming the getter; text that does not parse raises ``ValueError`` naming the
property. ``py_type`` is the stub annotation, ``Any`` when absent.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from _jminc import INC_ROOT  # noqa: E402
import ast
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._cli import main as cli_main
from just_makeit._context import make_properties_ctx
from just_makeit._context._methods import validate_json_property
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import plain_accessor_decls
from just_makeit._property import run as property_run
from just_makeit._script import _property_flags
from _jm_stub import drop_jm_stubs  # noqa: E402


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_SKIP = _skip_reason()

ANN = {
    "name": "annotations",
    "type": "json",
    "py_type": "list[dict[str, Any]]",
    "doc": "The capture's annotations.",
}


def _ctx(props):
    return make_properties_ctx("rdr", "Rdr", props, csym="rdr")


class TestGetter:
    """The binding owns the text: parse, then free on every path."""

    def test_calls_the_getter_once(self):
        src = _ctx([ANN])["getset_def"]
        assert src.count("rdr_get_annotations(self->handle)") == 1

    def test_null_is_raised_not_parsed(self):
        src = _ctx([ANN])["getset_def"]
        guard = src.index("if (!_js)")
        assert guard < src.index('PyImport_ImportModule("json")')
        assert "annotations: rdr_get_annotations returned NULL" in src

    def test_text_is_freed_on_every_path(self):
        """Once if json cannot be imported, once after loads -- which is
        before the result is inspected, so the error path frees it too."""
        body = _ctx([ANN])["getset_def"].split("Rdr_getprop_annotations")[1]
        assert body.count("free(_js);") == 2
        assert body.index("free(_js);\n    if (!_v") > 0

    def test_malformed_text_names_the_property(self):
        src = _ctx([ANN])["getset_def"]
        assert (
            "annotations: rdr_get_annotations returned malformed JSON" in src
        )
        assert "PyExc_ValueError" in src

    def test_destroyed_handle_is_guarded(self):
        src = _ctx([ANN])["getset_def"]
        assert 'PyErr_SetString(PyExc_RuntimeError, "destroyed");' in src

    def test_no_setter(self):
        assert "Rdr_setprop_annotations" not in _ctx([ANN])["getset_def"]


class TestDeclaration:
    def test_header_declares_the_owned_char_pointer(self):
        decls = _ctx([ANN])["property_decls"]
        assert "char *rdr_get_annotations(const rdr_state_t *state);" in decls
        assert "must free()" in decls

    def test_cli_and_apply_share_the_prototype(self):
        assert plain_accessor_decls(
            "rdr", "annotations", "json", False, csym="rdr"
        ) == ["char *rdr_get_annotations(const rdr_state_t *state);"]


class TestStub:
    @pytest.mark.parametrize(
        "py_type, annotation",
        [(None, "Any"), ("list[dict[str, Any]]", "list[dict[str, Any]]")],
        ids=["default", "declared"],
    )
    def test_annotation(self, py_type, annotation):
        p = {"name": "annotations", "type": "json"}
        if py_type:
            p["py_type"] = py_type
        pyi = _ctx([p])["property_stubs_pyi"]
        assert f"def annotations(self) -> {annotation}:" in pyi

    def test_doc_reaches_both_faces(self):
        ctx = _ctx([ANN])
        assert "The capture's annotations." in ctx["property_stubs_pyi"]
        assert "The capture's annotations." in ctx["getset_def"]


class TestDiagnostics:
    def test_writable_is_refused(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_json_property("rdr", {**ANN, "writable": True})

    @pytest.mark.parametrize(
        "clash",
        [
            "field",
            "buf_field",
            "expr",
            "enum",
            "codec",
            "capsule",
            "value_type",
            "count_fn",
            "key_fn",
            "value_fn",
            "entry_fn",
        ],
    )
    def test_conflicting_backing(self, clash):
        with pytest.raises(ValueError, match=f"`{clash}` cannot be combined"):
            validate_json_property("rdr", {**ANN, clash: "x"})

    def test_error_is_refused(self):
        with pytest.raises(ValueError, match="no setter"):
            validate_json_property("rdr", {**ANN, "error": "ValueError"})

    def test_py_type_on_another_kind_is_refused(self):
        with pytest.raises(ValueError, match="json property only"):
            validate_json_property(
                "rdr", {"name": "n", "type": "double", "py_type": "int"}
            )

    @pytest.mark.parametrize(
        "bad", ["Sequence[int]", "list[", "os.system('x')", "list[int"]
    )
    def test_py_type_must_be_a_stub_safe_expression(self, bad):
        with pytest.raises(ValueError, match="py_type"):
            validate_json_property("rdr", {**ANN, "py_type": bad})

    def test_render_raises_rather_than_emitting_bad_c(self):
        with pytest.raises(ValueError, match="read-only"):
            _ctx([{**ANN, "writable": True}])


class TestScriptRoundTrip:
    def test_py_type_is_emitted(self):
        flags = "".join(_property_flags(ANN, None))
        assert "--type json" in flags
        assert '--py-type "list[dict[str, Any]]"' in flags


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["rdr"], [("cap", "size_t", "16")], c_prefix=None)
    property_run(
        dest,
        "rdr",
        "annotations",
        None,
        "json",
        False,
        py_type="list[dict[str, Any]]",
        doc="The capture's annotations.",
    )
    return dest


class TestProject:
    def test_manifest_records_the_declaration(self, project):
        props = {p["name"]: p for p in C.properties(C.load(project), "rdr")}
        assert props["annotations"]["type"] == "json"
        assert props["annotations"]["py_type"] == "list[dict[str, Any]]"

    def test_core_header_declares_the_getter(self, project):
        h = (project / INC_ROOT / "rdr" / "rdr_core.h").read_text()
        assert "char *rdr_get_annotations(const rdr_state_t *state);" in h

    def test_core_gains_a_stub_body(self, project):
        c = (project / "native" / "src" / "rdr" / "rdr_core.c").read_text()
        assert "<<IMPLEMENT: rdr_get_annotations>>" in c

    def test_apply_is_idempotent(self, project):
        apply_run(project)
        ext = project / "native" / "src" / "rdr" / "rdr_ext.c"
        before = ext.read_text()
        apply_run(project)
        assert ext.read_text() == before

    def test_stub_parses_and_imports_any(self, project):
        # Through apply's replay too, which must carry py_type (gh-2347).
        apply_run(project)
        text = (project / "src" / "dsp" / "rdr.pyi").read_text()
        ast.parse(text)
        assert "def annotations(self) -> list[dict[str, Any]]:" in text
        assert any(
            ln.startswith("from typing import") and "Any" in ln
            for ln in text.splitlines()
        )


class TestManifestPath:
    """A json property authored in the manifest, reaching the header by apply."""

    def test_apply_declares_the_getter(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["rdr"], [("cap", "size_t", "16")], c_prefix=None)
        cfg = C.load(dest)
        C.add_property(cfg, "rdr", dict(ANN))
        C.save(dest, cfg)
        apply_run(dest)
        h = (dest / INC_ROOT / "rdr" / "rdr_core.h").read_text()
        assert "char *rdr_get_annotations(const rdr_state_t *state);" in h

    def test_apply_declares_the_getter_on_a_module_object(self, tmp_path):
        """doppler's layout: a module object's header is reconciled by
        apply's additive pass (gh-627), which declares only what it knows
        to be a plain accessor -- json among them."""
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [], c_prefix=None)
        module_run(dest, "wfm", ["reader"])
        object_run(
            dest, "reader", module="wfm", state_vars=[("cap", "size_t", "16")]
        )
        cfg = C.load(dest)
        C.add_property(cfg, "reader", dict(ANN))
        C.save(dest, cfg)
        apply_run(dest)
        h = (dest / INC_ROOT / "reader" / "reader_core.h").read_text()
        assert (
            "char *reader_get_annotations(const reader_state_t *state);" in h
        )
        stub = (dest / "src" / "dsp" / "wfm" / "wfm.pyi").read_text()
        ast.parse(stub)
        assert "def annotations(self) -> list[dict[str, Any]]:" in stub


class TestCli:
    """The flags as a user reaches them, through the argv parser."""

    @staticmethod
    def _cli(monkeypatch, cwd, *args):
        """Run ``jm <args>`` in *cwd*; returns the SystemExit code (0 if none)."""
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", ["just-makeit", *args])
        try:
            cli_main()
        except SystemExit as exc:
            return exc.code or 0
        return 0

    @pytest.fixture()
    def fresh(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["rdr"], [("cap", "size_t", "16")], c_prefix=None)
        return dest

    def test_flags_reach_the_manifest(self, fresh, monkeypatch):
        rc = self._cli(
            monkeypatch,
            fresh,
            "property",
            "rdr",
            "annotations",
            "--type",
            "json",
            "--py-type",
            "list[dict[str, Any]]",
        )
        assert rc == 0
        props = {p["name"]: p for p in C.properties(C.load(fresh), "rdr")}
        assert props["annotations"]["py_type"] == "list[dict[str, Any]]"

    def test_bad_declaration_exits_before_the_manifest(
        self, fresh, monkeypatch, capsys
    ):
        before = (fresh / C.FILENAME).read_text()
        rc = self._cli(
            monkeypatch,
            fresh,
            "property",
            "rdr",
            "annotations",
            "--type",
            "json",
            "--writable",
        )
        assert rc != 0
        assert "read-only" in capsys.readouterr().err
        assert (fresh / C.FILENAME).read_text() == before


class TestBuildAndRun:
    """The ownership rule is only observable in a built extension."""

    CORE_IMPL = textwrap.dedent(
        """

        /* gh-2347 json getter: the text is malloc'd; the binding frees it. */
        #include <stdlib.h>
        #include <string.h>

        static char *
        _dup (const char *s)
        {
          size_t n = strlen (s) + 1;
          char  *d = malloc (n);
          if (d)
            memcpy (d, s, n);
          return d;
        }

        char *
        rdr_get_annotations (const rdr_state_t *state)
        {
          switch (state->mode)
            {
            case 0:
              return _dup ("[{\\"core:sample_start\\": 0,"
                           " \\"core:label\\": \\"tone\\"}]");
            case 1:
              return _dup ("[]");
            case 2:
              return NULL;
            default:
              return _dup ("{\\"core:sample_start\\": ");
            }
        }
        """
    )

    @pytest.fixture(scope="class")
    @classmethod
    def built(cls, tmp_path_factory):
        if _SKIP:
            pytest.skip(_SKIP)
        root = tmp_path_factory.mktemp("jp") / "proj"
        new_run(
            "proj", root, ["rdr"], [("cap", "size_t", "16")], c_prefix=None
        )
        property_run(root, "rdr", "mode", None, "int", True, field=True)
        property_run(
            root,
            "rdr",
            "annotations",
            None,
            "json",
            False,
            py_type="list[dict[str, Any]]",
        )
        core = root / "native" / "src" / "rdr" / "rdr_core.c"
        core.write_text(
            drop_jm_stubs(
                core.read_text(encoding="utf-8"), "rdr_get_annotations"
            )
            + cls.CORE_IMPL,
            encoding="utf-8",
        )
        apply_run(root)
        build = root / "build"
        for cmd in (
            ["cmake", "-S", str(root), "-B", str(build)],
            ["cmake", "--build", str(build)],
        ):
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
        return root

    def _run(self, root, body):
        script = (
            "import sys, gc, resource\n"
            f"sys.path.insert(0, {str(root / 'src')!r})\n"
            "import proj.rdr as m\n" + textwrap.dedent(body)
        )
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
        return res.stdout.strip()

    def test_round_trip(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            print(r.annotations)
            """,
        )
        assert out == "[{'core:sample_start': 0, 'core:label': 'tone'}]"

    def test_empty(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            r.mode = 1
            print(r.annotations)
            """,
        )
        assert out == "[]"

    def test_null_raises_runtime_error(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            r.mode = 2
            try:
                r.annotations
                print("NO ERROR")
            except RuntimeError as e:
                print(e)
            """,
        )
        assert out == "annotations: rdr_get_annotations returned NULL"

    def test_malformed_raises_value_error(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            r.mode = 3
            try:
                r.annotations
                print("NO ERROR")
            except ValueError as e:
                print(type(e).__name__, e)
            """,
        )
        assert out.startswith(
            "ValueError annotations: rdr_get_annotations returned malformed"
            " JSON: "
        )

    def test_every_read_is_independent(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            a = r.annotations
            a.append({})
            print(len(r.annotations))
            """,
        )
        assert out == "1"

    # A binding that forgets free() leaks the ~50-byte text on every read:
    # 500k reads is ~25 MB on top of allocator noise of ~1 MB.
    LEAK_BAR_BYTES = 12 * 1024 * 1024

    def test_repeated_reads_do_not_leak(self, built):
        out = self._run(
            built,
            """
            unit = 1 if sys.platform == "darwin" else 1024
            rss = lambda: (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
            )
            r = m.Rdr(16)
            for _ in range(50000):
                r.annotations
            gc.collect(); base = rss()
            for _ in range(500000):
                r.annotations
            r.mode = 3
            for _ in range(200000):
                try:
                    r.annotations
                except ValueError:
                    pass
            gc.collect()
            print(rss() - base)
            """,
        )
        grew = int(out)
        assert grew < self.LEAK_BAR_BYTES, f"RSS grew {grew / 1e6:.1f} MB"
