"""gh-598: an unmapped result-field type was built under "i" with no cast.

Both list-of-records builders did::

    _fmt_c, _cast = _PYBUILD_FMT.get(_rft, ("i", ""))

An unmapped type got format ``"i"`` **and an empty cast**, so the field was
handed to ``Py_BuildValue``'s varargs as-is under an ``int`` format — an ABI
mismatch, not a conversion.

It did not take a typo to hit. ``_PYBUILD_FMT`` was a *second, smaller* table
than ``_CTYPE_META``: ten fully registered types were missing from it, so a
plain ``ptrdiff_t`` field silently truncated (5000000000 read back as
705032704) while compiling without a warning.

Five of those ten -- ``bool``, ``int8_t``, ``int16_t``, ``uint8_t``,
``uint16_t`` -- were *accidentally* correct, because default argument
promotion widens them to ``int``. That is what let the gap survive: a test
sweeping small integer types comes back green and reads as proof the table is
fine. They are listed in ``ACCIDENTALLY_OK`` and asserted alongside the genuine
failures, because they are the ones a naive regression test would have
"covered" while proving nothing.

The fix points both builders at ``_CTYPE_META[type]["to_py"]`` -- the
primitive the ``single = true`` record path, scalar returns and property
getters already used -- via the shared ``_types.record_tuple_build``, and
deletes ``_PYBUILD_FMT``. That *closes* the class rather than rejecting it:
``ptrdiff_t``, ``const char *`` and the complex types now work. Apply-time
validation is the backstop for genuine typos.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._types import _CTYPE_META, record_tuple_build

# Registered in _CTYPE_META but absent from the old _PYBUILD_FMT.
ACCIDENTALLY_OK = ["bool", "int8_t", "int16_t", "uint8_t", "uint16_t"]
WERE_BROKEN = [
    "ptrdiff_t",
    "const char *",
    "float _Complex",
    "double _Complex",
    "long double _Complex",
]


class TestBuilderUsesTheSSOT:
    def test_every_registered_type_converts(self):
        """No registered type may fall back -- that was the whole bug."""
        for ctype in _CTYPE_META:
            out = record_tuple_build([{"name": "f", "type": ctype}], "r[i]")
            assert out.startswith('"(N)"')
            # The conversion must be the one _CTYPE_META defines, verbatim.
            assert _CTYPE_META[ctype]["to_py"]("r[i].f") in out

    @pytest.mark.parametrize("ctype", WERE_BROKEN + ACCIDENTALLY_OK)
    def test_previously_unmapped_types_no_longer_use_i(self, ctype):
        out = record_tuple_build([{"name": "f", "type": ctype}], "r[i]")
        assert '"(i)"' not in out

    def test_steals_references_so_conversions_cannot_leak(self):
        # "N" (not "O") is the documented format for an object constructed in
        # the argument list; "O" would incref and leak every field.
        out = record_tuple_build(
            [{"name": "a", "type": "int"}, {"name": "b", "type": "double"}],
            "r[i]",
        )
        assert out.startswith('"(NN)"')

    def test_unregistered_type_fails_loudly(self):
        # No silent fallback of any kind remains.
        with pytest.raises(KeyError):
            record_tuple_build([{"name": "f", "type": "wat_t"}], "r[i]")

    def test_old_table_is_gone(self):
        import just_makeit._types as T

        assert not hasattr(T, "_PYBUILD_FMT")


class TestManifestValidation:
    """The backstop: a typo becomes a manifest error, not a traceback."""

    def test_unknown_result_field_type_is_reported(self):
        cfg = {
            "det": {
                "methods": [
                    {
                        "name": "scan",
                        "return_type": "hit_t",
                        "result_fields": [{"name": "idx", "type": "wat_t"}],
                    }
                ]
            }
        }
        errors = C.manifest_type_errors(cfg)
        assert len(errors) == 1
        assert "'det' method 'scan'" in errors[0]
        assert "result field 'idx' has unknown type 'wat_t'" in errors[0]

    def test_help_omits_void_for_a_field(self):
        # `void` is a legal return type but never a legal field type.
        cfg = {
            "det": {
                "methods": [
                    {
                        "name": "scan",
                        "return_type": "hit_t",
                        "result_fields": [{"name": "i", "type": "nope"}],
                    }
                ]
            }
        }
        assert "Supported: bool," in C.manifest_type_errors(cfg)[0]

    def test_hint_still_offered_for_a_field(self):
        cfg = {
            "det": {
                "methods": [
                    {
                        "name": "scan",
                        "return_type": "hit_t",
                        "result_fields": [{"name": "i", "type": "long"}],
                    }
                ]
            }
        }
        assert "int64_t" in C.manifest_type_errors(cfg)[0]

    @pytest.mark.parametrize("ctype", WERE_BROKEN + ACCIDENTALLY_OK)
    def test_registered_field_types_are_accepted(self, ctype):
        cfg = {
            "det": {
                "methods": [
                    {
                        "name": "scan",
                        "return_type": "hit_t",
                        "result_fields": [{"name": "f", "type": ctype}],
                    }
                ]
            }
        }
        assert C.manifest_type_errors(cfg) == []

    def test_module_function_result_fields_are_walked_too(self):
        cfg = {
            "module": {
                "m": {
                    "functions": [
                        {
                            "name": "f",
                            "return_type": "hit_t",
                            "result_fields": [{"name": "x", "type": "bogus"}],
                        }
                    ]
                }
            }
        }
        errors = C.manifest_type_errors(cfg)
        assert len(errors) == 1
        assert "module 'm' function 'f'" in errors[0]


class TestGeneratedCode:
    """Both peer sites must emit the corrected conversion."""

    @pytest.fixture
    def method_ext(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(dest, "det", module=None, arg_type="void", no_step=True)
        method_run(
            dest,
            "det",
            "scan",
            None,
            "void",
            "hit_t",
            False,
            [],
            result_fields=[
                {"name": "idx", "type": "ptrdiff_t"},
                {"name": "mag", "type": "double"},
            ],
            max_results=4,
        )
        return (dest / "native/src/det/det_ext.c").read_text("utf-8")

    def test_method_site_uses_to_py(self, method_ext):
        assert (
            'Py_BuildValue("(NN)", '
            "PyLong_FromLongLong((long long)results[i].idx), "
            "PyFloat_FromDouble(results[i].mag))" in method_ext
        )

    def test_method_site_has_no_bare_i_format(self, method_ext):
        assert 'Py_BuildValue("(id)"' not in method_ext

    def test_function_site_uses_to_py(self):
        from just_makeit._render import _py_wrapper_for_function

        src = _py_wrapper_for_function(
            "find",
            [],
            "hit_t",
            result_fields=[
                {"name": "idx", "type": "ptrdiff_t"},
                {"name": "tag", "type": "const char *"},
            ],
        )
        assert (
            'Py_BuildValue("(NN)", '
            "PyLong_FromLongLong((long long)_results[_i].idx), "
            "PyUnicode_FromString(_results[_i].tag))" in src
        )


HIT_T = (
    "typedef struct { ptrdiff_t idx; bool ok; double mag; "
    "const char *tag; } hit_t;"
)
KERNEL = """    (void)max_results;
    result[0].idx = (ptrdiff_t)5000000000;
    result[0].ok = true;
    result[0].mag = 1.5;
    result[0].tag = "peak";
    return 1;"""


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestRuntime:
    """Compile and run it -- the truncation was only visible at runtime."""

    def test_wide_and_stringish_fields_round_trip(self, tmp_path):
        dest = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(dest, "det", module=None, arg_type="void", no_step=True)
            method_run(
                dest,
                "det",
                "scan",
                None,
                "void",
                "hit_t",
                False,
                [],
                result_fields=[
                    {"name": "idx", "type": "ptrdiff_t"},
                    {"name": "ok", "type": "bool"},
                    {"name": "mag", "type": "double"},
                    {"name": "tag", "type": "const char *"},
                ],
                max_results=4,
            )
        hdr = dest / "native/inc/det/det_core.h"
        hdr.write_text(
            hdr.read_text("utf-8").replace(
                '#include "clib_common.h"',
                f'#include "clib_common.h"\n\n{HIT_T}',
            ),
            encoding="utf-8",
        )
        core = dest / "native/src/det/det_core.c"
        patched = core.read_text("utf-8").replace(
            "    (void)result; (void)max_results;\n"
            "    return 0; /* placeholder */",
            KERNEL,
        )
        assert KERNEL in patched, "stub body shape changed; update this test"
        core.write_text(patched, encoding="utf-8")

        build = dest / "build"
        for cmd in (
            [
                "cmake",
                "-S",
                str(dest),
                "-B",
                str(build),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            ["cmake", "--build", str(build)],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            assert r.returncode == 0, (
                f"{cmd[0]} failed:\n{r.stdout}\n{r.stderr}"
            )

        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from p.det import Det; print(repr(Det().scan()[0]))",
            ],
            cwd=dest,
            env={**os.environ, "PYTHONPATH": str(dest / "src")},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert out.returncode == 0, out.stderr
        # idx would be 705032704 under the old "i" fallback; ok would be 1.
        assert out.stdout.strip() == "(5000000000, True, 1.5, 'peak')"
