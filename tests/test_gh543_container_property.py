"""gh-543: container-valued object properties (dict / list / tuple).

A property could be a scalar, an enum, a computed value, an inline expression
or an ndarray view -- never a mapping or a sequence. Anything dict-shaped had
to be hand-written into a sacred ext fragment, which cost ~150 lines in
doppler's ``Reader.keywords`` and ~25 in ``RateConverter.stages``. Worse, a
fragment-added property never reaches the generated ``.pyi``, so it was
invisible to a type checker even though the runtime was correct.

The kind is declared as ``type = "dict" | "list" | "tuple"`` plus an iteration
protocol the core implements::

    count_fn   size_t       (const state *)
    key_fn     const char * (const state *, size_t)   -- dict only
    value_fn   <value_type> (const state *, size_t)

``value_type`` is either a scalar jm type -- jm emits the conversion, and the
accessor stays in the pure-C core -- or ``object``, meaning ``value_fn``
returns a ``PyObject *`` itself. The escape hatch exists because a value's
Python type can be data-dependent: each BLUE keyword's type comes from a code
stored in the file, so no static annotation can describe it.

Two guards here are load-bearing rather than defensive, both of the gh-521
class (an unchecked pointer that segfaults rather than raising):

  * ``key_fn`` returning NULL -- dereferenced by ``PyDict_SetItemString``.
  * a pointer-typed ``value_fn`` returning NULL -- for the only such type,
    ``const char *``, ``PyUnicode_FromString(NULL)`` reaches ``strlen(NULL)``.
    Verified: deleting the guard from the generated binding turns this file's
    scenario into exit 139.
"""

import ast
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._context import make_properties_ctx
from just_makeit._context._methods import (
    container_fn_names,
    validate_container_property,
)
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._script import _property_flags


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()

DICT_OBJ = {
    "name": "keywords",
    "type": "dict",
    "value_type": "object",
    "count_fn": "rdr_num_keywords",
    "key_fn": "rdr_keyword_tag",
    "value_fn": "rdr_keyword_value",
}
LIST_STR = {
    "name": "stages",
    "type": "list",
    "value_type": "const char *",
}
TUPLE_F64 = {"name": "gains", "type": "tuple", "value_type": "double"}


def _getset(props):
    return make_properties_ctx("rdr", "Rdr", props)["getset_def"]


class TestContainerConstruction:
    """The right CPython container, built and returned."""

    @pytest.mark.parametrize(
        "prop, new_fn, set_fn",
        [
            (DICT_OBJ, "PyDict_New()", "PyDict_SetItemString"),
            (LIST_STR, "PyList_New((Py_ssize_t)_n)", "PyList_SET_ITEM"),
            (TUPLE_F64, "PyTuple_New((Py_ssize_t)_n)", "PyTuple_SET_ITEM"),
        ],
        ids=["dict", "list", "tuple"],
    )
    def test_builds_the_declared_container(self, prop, new_fn, set_fn):
        src = _getset([prop])
        assert new_fn in src
        assert set_fn in src

    def test_count_is_read_before_the_container_is_sized(self):
        """PyList_New needs the length, so the count call must precede it."""
        src = _getset([LIST_STR])
        assert src.index("rdr_num_stages(self->handle)") < src.index(
            "PyList_New"
        )

    def test_sequence_set_item_steals_so_no_decref_on_success(self):
        """SET_ITEM steals the reference; decrefing it too would be a
        use-after-free the moment the caller touched the element."""
        body = _getset([LIST_STR])
        after_set = body.split("PyList_SET_ITEM")[1].split("return _c")[0]
        assert "Py_DECREF(_v)" not in after_set

    def test_dict_set_item_borrows_so_the_value_is_released(self):
        """PyDict_SetItemString does NOT steal -- the value must be released
        on both the success and the failure path, or every read leaks."""
        body = _getset([DICT_OBJ]).split("PyDict_SetItemString")[1]
        assert body.count("Py_DECREF(_v)") == 2


class TestValueTyping:
    """Typed values are converted by jm; `object` values are not."""

    def test_typed_value_is_converted_by_jm(self):
        assert "PyFloat_FromDouble(rdr_gains_value(" in _getset([TUPLE_F64])

    def test_object_value_is_passed_through_untouched(self):
        src = _getset([DICT_OBJ])
        assert "PyObject *_v = rdr_keyword_value(self->handle, _i);" in src

    def test_object_value_fn_is_forward_declared(self):
        """The hand-written *_extra.c is #included AFTER this fragment in the
        same translation unit, so without a forward declaration the call does
        not compile."""
        src = _getset([DICT_OBJ])
        decl = (
            "PyObject *rdr_keyword_value(const rdr_state_t *state, size_t i);"
        )
        assert decl in src
        assert src.index(decl) < src.index("Rdr_getprop_keywords")


class TestNullGuards:
    """Both guards are load-bearing: without them these crash, not raise."""

    def test_null_key_is_reported_not_dereferenced(self):
        src = _getset([DICT_OBJ])
        assert "if (!_k) {" in src
        assert "rdr_keyword_tag returned NULL at index %zu" in src

    def test_pointer_value_is_checked_before_conversion(self):
        """PyUnicode_FromString(NULL) reaches strlen(NULL). The raw call is
        bound to a local and checked before it is converted."""
        src = _getset([LIST_STR])
        assert "const char *_r = rdr_stages_value(self->handle, _i);" in src
        assert src.index("if (!_r) {") < src.index("PyUnicode_FromString(_r)")

    def test_non_pointer_value_needs_no_guard(self):
        """A double cannot be NULL; guarding it would be noise."""
        assert "if (!_r)" not in _getset([TUPLE_F64])

    def test_partially_built_container_is_released_on_error(self):
        """PyList_New/PyDict_New zero their slots, so decrefing a half-filled
        container is safe -- and mandatory, or the error path leaks it."""
        for prop in (DICT_OBJ, LIST_STR, TUPLE_F64):
            src = _getset([prop])
            assert "Py_DECREF(_c);\n            return NULL;" in src

    def test_destroyed_handle_is_guarded_like_every_other_property(self):
        assert '"destroyed"' in _getset([DICT_OBJ])


class TestAccessorDefaults:
    """Unspecified accessors derive from the component and property name."""

    def test_defaults(self):
        assert container_fn_names("rdr", "keywords", {}) == {
            "count_fn": "rdr_num_keywords",
            "key_fn": "rdr_keywords_key",
            "value_fn": "rdr_keywords_value",
        }

    def test_explicit_names_win(self):
        got = container_fn_names("rdr", "keywords", DICT_OBJ)
        assert got["key_fn"] == "rdr_keyword_tag"


class TestCoreHeaderDecls:
    """Only the pure-C accessors belong in the sacred header."""

    def test_plain_c_accessors_are_declared(self):
        decls = make_properties_ctx("rdr", "Rdr", [DICT_OBJ])["property_decls"]
        assert "size_t rdr_num_keywords(const rdr_state_t *state);" in decls
        assert (
            "const char *rdr_keyword_tag(const rdr_state_t *state, size_t i);"
            in decls
        )

    def test_pyobject_value_fn_is_not_declared_in_the_header(self):
        """It needs Python.h. Putting it in _core.h would force the pure-C
        library to depend on CPython -- the thing the escape hatch avoids."""
        decls = make_properties_ctx("rdr", "Rdr", [DICT_OBJ])["property_decls"]
        assert "rdr_keyword_value" not in decls

    def test_typed_value_fn_is_declared_in_the_header(self):
        decls = make_properties_ctx("rdr", "Rdr", [LIST_STR])["property_decls"]
        assert (
            "const char *rdr_stages_value(const rdr_state_t *state, size_t i);"
            in decls
        )

    def test_list_and_tuple_declare_no_key_accessor(self):
        decls = make_properties_ctx("rdr", "Rdr", [LIST_STR])["property_decls"]
        assert "_key" not in decls


class TestStub:
    """The half of the value that a hand-written fragment could never give."""

    @pytest.mark.parametrize(
        "prop, annotation",
        [
            (DICT_OBJ, "dict[str, Any]"),
            (LIST_STR, "list[str]"),
            (TUPLE_F64, "tuple[float, ...]"),
        ],
        ids=["dict", "list", "tuple"],
    )
    def test_annotation(self, prop, annotation):
        pyi = make_properties_ctx("rdr", "Rdr", [prop])["property_stubs_pyi"]
        assert f"-> {annotation}:" in pyi

    def test_stub_parses(self):
        pyi = make_properties_ctx(
            "rdr", "Rdr", [DICT_OBJ, LIST_STR, TUPLE_F64]
        )["property_stubs_pyi"]
        ast.parse("class Rdr:\n" + pyi)

    def test_no_setter_is_advertised(self):
        pyi = make_properties_ctx("rdr", "Rdr", [DICT_OBJ])[
            "property_stubs_pyi"
        ]
        assert ".setter" not in pyi


class TestDiagnostics:
    """Every one of these would otherwise be a compiler error in generated
    code the user never wrote."""

    def test_unknown_value_type(self):
        with pytest.raises(ValueError, match="unsupported value_type"):
            validate_container_property(
                "rdr", {"name": "k", "type": "dict", "value_type": "float[]"}
            )

    def test_key_fn_on_a_sequence(self):
        with pytest.raises(ValueError, match="only for a dict"):
            validate_container_property(
                "rdr", {"name": "s", "type": "list", "key_fn": "f"}
            )

    def test_writable_container(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_container_property(
                "rdr", {"name": "k", "type": "dict", "writable": True}
            )

    @pytest.mark.parametrize("clash", ["field", "buf_field", "expr", "enum"])
    def test_conflicting_backing(self, clash):
        with pytest.raises(ValueError, match="cannot be combined"):
            validate_container_property(
                "rdr", {"name": "k", "type": "dict", clash: "x"}
            )

    def test_render_raises_rather_than_emitting_bad_c(self):
        with pytest.raises(ValueError):
            make_properties_ctx(
                "rdr", "Rdr", [{"name": "k", "type": "dict", "writable": True}]
            )


class TestScriptRoundTrip:
    """`jm script` must rebuild the same project, or it is worse than useless
    (gh-490)."""

    def test_every_recorded_key_is_emitted(self):
        flags = "".join(_property_flags(DICT_OBJ, None))
        for tok in (
            "--type dict",
            "--value-type object",
            "--count-fn rdr_num_keywords",
            "--key-fn rdr_keyword_tag",
            "--value-fn rdr_keyword_value",
        ):
            assert tok in flags

    def test_unspecified_accessors_are_not_invented(self):
        """They re-derive from the same defaults on replay, so emitting them
        would freeze today's naming into the reconstructed script."""
        flags = "".join(_property_flags(TUPLE_F64, None))
        assert "--count-fn" not in flags
        assert "--key-fn" not in flags


class TestStandaloneProject:
    """A standalone object -- including the `_extra.c` hook it never had."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["rdr"], [("cap", "size_t", "16")])
        property_run(
            dest,
            "rdr",
            "keywords",
            None,
            "dict",
            False,
            value_type="object",
            doc="Extended header.",
        )
        property_run(
            dest,
            "rdr",
            "stages",
            None,
            "list",
            False,
            value_type="const char *",
        )
        return dest

    def test_manifest_records_the_declaration(self, project):
        props = {p["name"]: p for p in C.properties(C.load(project), "rdr")}
        assert props["keywords"]["type"] == "dict"
        assert props["keywords"]["value_type"] == "object"
        assert props["stages"]["value_type"] == "const char *"

    def test_core_header_gains_only_the_pure_c_accessors(self, project):
        h = (project / "native" / "inc" / "rdr" / "rdr_core.h").read_text()
        assert "size_t rdr_num_keywords(const rdr_state_t *state);" in h
        assert "const char *rdr_stages_value(" in h
        assert "PyObject" not in h

    def test_standalone_extra_is_wired_when_present(self, project):
        """Module objects have had this hook since the aggregator existed; a
        standalone object had none, so the PyObject * escape hatch had nowhere
        to live."""
        ext_dir = project / "native" / "src" / "rdr"
        assert (
            '#include "rdr_ext_extra.c"'
            not in (ext_dir / "rdr_ext.c").read_text()
        )
        (ext_dir / "rdr_ext_extra.c").write_text("/* hand-written */\n")
        apply_run(project)
        assert (
            '#include "rdr_ext_extra.c"' in (ext_dir / "rdr_ext.c").read_text()
        )

    def test_apply_is_idempotent(self, project):
        apply_run(project)
        before = (project / "native" / "src" / "rdr" / "rdr_ext.c").read_text()
        apply_run(project)
        after = (project / "native" / "src" / "rdr" / "rdr_ext.c").read_text()
        assert before == after

    def test_stub_parses_and_imports_any(self, project):
        text = (project / "src" / "dsp" / "rdr.pyi").read_text()
        ast.parse(text)
        assert "dict[str, Any]" in text
        assert "list[str]" in text


class TestModuleProject:
    """doppler's actual layout: a module object, whose stub is written by the
    aggregated generator rather than the standalone one."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [])
        module_run(dest, "wfm", ["reader"])
        object_run(
            dest,
            "reader",
            module="wfm",
            state_vars=[("cap", "size_t", "16")],
        )
        property_run(
            dest,
            "reader",
            "keywords",
            "wfm",
            "dict",
            False,
            value_type="object",
        )
        property_run(
            dest,
            "reader",
            "stages",
            "wfm",
            "tuple",
            False,
            value_type="const char *",
        )
        return dest

    def test_module_stub_imports_any_and_parses(self, project):
        """`_uses_any` re-derives the typing imports independently of the
        property renderer; missing the container case emits a bare `Any`."""
        text = (project / "src" / "dsp" / "wfm" / "wfm.pyi").read_text()
        ast.parse(text)
        assert "from typing import Any" in text
        assert "dict[str, Any]" in text
        assert "tuple[str, ...]" in text

    def test_the_aggregated_translation_unit_compiles(self, project):
        """String assertions cannot catch a brace or refcount error. Compile
        the aggregator exactly as the build does."""
        import sysconfig

        try:
            import numpy
        except ImportError:  # pragma: no cover - deps fixture absent
            pytest.skip("numpy headers unavailable")
        src = project / "native" / "src" / "wfm"
        # The PyObject *-returning value_fn lives in a hand-written extra.
        (src / "wfm_ext_reader_extra.c").write_text(
            "PyObject *\n"
            "reader_keywords_value(const reader_state_t *state, size_t i)\n"
            "{\n"
            "    (void)state;\n"
            "    return PyLong_FromSize_t(i);\n"
            "}\n"
        )
        apply_run(project)
        cc = sysconfig.get_config_var("CC") or "cc"
        cmd = cc.split() + [
            "-c",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror=implicit-function-declaration",
            f"-I{project / 'native' / 'inc'}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{numpy.get_include()}",
            str(src / "wfm_ext.c"),
            "-o",
            str(project / "wfm_ext.o"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


class TestBuildAndRun:
    """The only level at which refcounting is actually observable.

    A compile proves the C is well-formed; it says nothing about whether the
    dict leaks its values or the list double-frees them. Both are silent until
    they are not, so this builds a real project and reads the properties half a
    million times.
    """

    CORE_IMPL = textwrap.dedent(
        """

        /* gh-543 container accessors. */
        static const char *const _tags[] = { "COMMENT", "F_C" };
        static const char *const _stages[] = { "decim/4", "resamp/3-2" };

        size_t
        rdr_num_keywords (const rdr_state_t *state)
        {
          (void)state;
          return 2;
        }

        const char *
        rdr_keywords_key (const rdr_state_t *state, size_t i)
        {
          (void)state;
          return i < 2 ? _tags[i] : NULL;
        }

        size_t
        rdr_num_stages (const rdr_state_t *state)
        {
          (void)state;
          return 2;
        }

        const char *
        rdr_stages_value (const rdr_state_t *state, size_t i)
        {
          (void)state;
          return i < 2 ? _stages[i] : NULL;
        }
        """
    )

    EXTRA_IMPL = textwrap.dedent(
        """
        /* Hand-written: returns a PyObject *, so it needs Python.h and
         * cannot live in the pure-C core. */
        PyObject *
        rdr_keywords_value (const rdr_state_t *state, size_t i)
        {
            (void)state;
            return i == 0 ? PyUnicode_FromString ("10 dB pad")
                          : PyFloat_FromDouble (1234500000.0);
        }
        """
    )

    @pytest.fixture()
    def built(self, tmp_path):
        if _SKIP:
            pytest.skip(_SKIP)
        root = tmp_path / "proj"
        new_run("proj", root, ["rdr"], [("cap", "size_t", "16")])
        property_run(
            root, "rdr", "keywords", None, "dict", False, value_type="object"
        )
        property_run(
            root,
            "rdr",
            "stages",
            None,
            "list",
            False,
            value_type="const char *",
        )
        src = root / "native" / "src" / "rdr"
        with (src / "rdr_core.c").open("a", encoding="utf-8") as fh:
            fh.write(self.CORE_IMPL)
        (src / "rdr_ext_extra.c").write_text(self.EXTRA_IMPL, encoding="utf-8")
        apply_run(root)

        build = root / "build"
        cfg = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(build)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert cfg.returncode == 0, cfg.stderr
        bld = subprocess.run(
            ["cmake", "--build", str(build)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert bld.returncode == 0, f"{bld.stdout}\n{bld.stderr}"
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

    def test_values_round_trip_to_python(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            print(r.keywords)
            print(r.stages)
            """,
        )
        assert out.splitlines()[0] == (
            "{'COMMENT': '10 dB pad', 'F_C': 1234500000.0}"
        )
        assert out.splitlines()[1] == "['decim/4', 'resamp/3-2']"

    def test_every_read_returns_an_independent_container(self, built):
        """Callers mutate what they get back, so one caller's edit must not be
        visible to another.

        Asserted by mutating rather than by comparing ``id()``: a container
        that is built and immediately dropped is freed at once, and CPython
        hands the next allocation the same address, so distinct reads
        legitimately share an id. Holding the references is what makes the
        identity meaningful -- and mutation isolation is the property that
        actually matters.
        """
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            held = [r.keywords for _ in range(5)]
            print(len({id(d) for d in held}) == 5)
            first = r.keywords
            first["INJECTED"] = 1
            print("INJECTED" not in r.keywords)
            seq = r.stages
            seq.append("extra")
            print(len(r.stages) == 2)
            """,
        )
        assert out.splitlines() == ["True", "True", "True"]

    # Measured, not estimated. Deleting the single trailing Py_DECREF(_v) from
    # a generated dict getter and rerunning the loop below grows RSS by 48 MB;
    # the correct binding grows it by 0 on Linux, and macOS CI showed ~1.2 MB
    # of steady-state allocator drift. The bar sits an order of magnitude above
    # the noise and a factor of three below the smallest real leak, so it
    # cannot be satisfied by a binding that forgets a reference.
    LEAK_BAR_BYTES = 16 * 1024 * 1024

    def test_repeated_reads_do_not_leak(self, built):
        """A missing Py_DECREF on the dict's borrowed value leaks one object
        per entry per read -- invisible in a unit test, fatal in a loop.

        ``ru_maxrss`` is bytes on macOS and kilobytes on Linux, so it is
        normalised before it is compared; the first version of this test
        asserted an exact zero against the raw number and failed on macOS
        against ~1.2 MB of allocator noise.
        """
        out = self._run(
            built,
            """
            unit = 1 if sys.platform == "darwin" else 1024
            rss = lambda: (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
            )
            r = m.Rdr(16)
            for _ in range(50000):
                r.keywords; r.stages
            gc.collect(); base = rss()
            for _ in range(500000):
                r.keywords; r.stages
            gc.collect()
            print(rss() - base)
            """,
        )
        grew = int(out)
        assert grew < self.LEAK_BAR_BYTES, (
            f"RSS grew by {grew / 1e6:.1f} MB over 500k reads; a real leak "
            f"would be >100 MB and steady-state noise ~1 MB"
        )

    def test_destroyed_object_raises(self, built):
        out = self._run(
            built,
            """
            r = m.Rdr(16)
            r.destroy()
            try:
                r.keywords
                print("NO ERROR")
            except RuntimeError as e:
                print(e)
            """,
        )
        assert out == "destroyed"
