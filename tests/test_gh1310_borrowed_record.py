"""A BORROWED view whose element is a record — gh-1310's decision B.

numpy has no complex-integer dtype, so integer IQ comes back as a structured
array ``[('i','<i2'),('q','<i2')]``. gh-1310 measured all four zero-copy
spellings of the same bytes and chose this one: 1-D, one element per sample,
byte order stated, and arithmetic refused LOUDLY rather than the packed
``int32`` form's silent `d + 1 -> [1,1,3,3,5,5,7,7]`.

The ring that needs it borrows (#1312), and `record_dtype` could not be
declared with `borrow` -- it required `variable_output`, which `borrow`
refuses by name. That was one flag standing in for a second question:
`record_dtype` names the ELEMENT TYPE, and who owns the buffer is a separate
question. The two owners stay mutually exclusive; the element type is shared.

The gate that matters is **itemsize and offsets measured from compiled C**.
Comparing the descr against jm's own construction of it is the emitter's own
traversal agreeing with itself; only the compiler knows what it laid out.
"""

# gh-1591: this file's hand-written C and expectations spell jm's bare
# derived names, so its projects opt out of the prefix `jm new` now
# defaults to; the default is gated by tests/test_gh1591_*.py.

from __future__ import annotations
from _jminc import INC_DIR, INC_ROOT  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


#: Deliberately PADDED. `int16, int32, int16` is 12 bytes as the compiler lays
#: it out and 8 as numpy packs a bare format list -- so a dtype built from the
#: field list rather than from `offsetof`/`sizeof` reads every row after the
#: first from the wrong bytes. The unpadded `i`/`q` pair agrees either way and
#: cannot tell the two apart, which is why this shape is the one under test.
PADDED_FIELDS = [
    {"name": "i", "type": "int16_t"},
    {"name": "big", "type": "int32_t"},
    {"name": "q", "type": "int16_t"},
]

IQ_FIELDS = [
    {"name": "i", "type": "int16_t"},
    {"name": "q", "type": "int16_t"},
]


def _declare(root: Path, fields=None, **kw):
    """A ring whose `wait(n)` borrows a view of records."""
    fields = IQ_FIELDS if fields is None else fields
    _silent(new_run, "p", root, c_prefix=None)
    _silent(
        object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
    )
    _silent(
        method_run,
        root,
        "ring",
        "wait",
        None,
        "void",
        "float _Complex",
        False,
        [],
        params=[("n", "size_t")],
        result_fields=fields,
        record_dtype="iq_pair_t",
        borrow=True,
        **kw,
    )
    return root


class TestItCanBeDeclaredAtAll:
    """The coupling that made B unreachable."""

    def test_borrow_with_record_dtype_is_accepted(self, tmp_path):
        _declare(tmp_path / "p")

    def test_record_dtype_still_needs_an_array_result(self, tmp_path):
        """Decoupled from `variable_output`, NOT unconditional -- a record
        element type with neither owner has no array to be the element of."""
        root = tmp_path / "p"
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        with pytest.raises(SystemExit):
            _silent(
                method_run,
                root,
                "ring",
                "wait",
                None,
                "void",
                "double",
                False,
                [],
                result_fields=IQ_FIELDS,
                record_dtype="iq_pair_t",
            )

    def test_the_two_owners_are_still_exclusive(self, tmp_path):
        """`borrow` and `variable_output` remain different answers to who
        owns the result; gh-1310 shares the ELEMENT TYPE, not the owner."""
        root = tmp_path / "p"
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        with pytest.raises(SystemExit):
            _silent(
                method_run,
                root,
                "ring",
                "wait",
                None,
                "void",
                "double",
                True,
                [],
                params=[("n", "size_t")],
                result_fields=IQ_FIELDS,
                record_dtype="iq_pair_t",
                borrow=True,
            )


class TestEveryFaceNamesTheRecord:
    """Four faces described the method; three of them ignored `record_dtype`
    and kept describing `float _Complex`. Each is pinned separately because
    each was wrong for its own reason."""

    def test_the_sacred_header_returns_a_record_pointer(self, tmp_path):
        root = _declare(tmp_path / "p")
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert "iq_pair_t *ring_wait(ring_state_t *state, size_t n);" in h, h

    def test_the_stub_defines_one(self, tmp_path):
        root = _declare(tmp_path / "p")
        c = (root / "native/src/ring/ring_core.c").read_text()
        assert "iq_pair_t *\nring_wait(" in c, c

    def test_the_binding_builds_a_descr_not_an_enum(self, tmp_path):
        root = _declare(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        assert "Ring_wait_get_dtype" in ext, ext
        assert "PyArray_NewFromDescr" in ext, ext
        assert "offsetof(iq_pair_t, i)" in ext, ext
        # the builtin-enum constructor must NOT appear for this method
        assert "PyArray_SimpleNewFromData" not in ext, ext

    def test_the_stub_annotates_a_structured_array(self, tmp_path):
        root = _declare(tmp_path / "p")
        pyi = (root / "src/p/ring.pyi").read_text()
        assert "def wait(self, n: int) -> NDArray[Any]:" in pyi, pyi
        assert "np.complex64" not in pyi.split("def wait")[1][:200]

    def test_the_bench_sink_is_a_pointer(self, tmp_path):
        """gh-1312 never taught the bench face about `borrow`, so it declared
        `volatile <T> sink` for a pointer-returning kernel -- a constraint
        violation in the one generated file no test compiles."""
        root = _declare(tmp_path / "p")
        b = (root / "native/benchmarks/bench_ring_core.c").read_text()
        assert "iq_pair_t *volatile wait_sink;" in b, b
        # and NOT the list-of-records bench, whose kernel does not exist
        assert "wait_results[" not in b, b


class TestTheFallbackFailsLoudly:
    """`_borrow_np_enum` fell back to NPY_CFLOAT on a lookup miss. gh-1310
    adds the first caller that can reach the resolver with a type deliberately
    absent from `_CTYPE_META`, and a complex64 view over int16 data is 4x the
    itemsize with no error -- the `_PYBUILD_FMT` shape that cost a silent
    truncation."""

    def test_an_unregistered_element_type_raises(self):
        from just_makeit._context._parse import borrow_view_c

        with pytest.raises(ValueError):
            borrow_view_c("p", "n", writeable=False)  # neither
        with pytest.raises(ValueError):
            borrow_view_c(
                "p", "n", "NPY_INT16", descr_fn="f", writeable=False
            )  # both


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheLayoutMatchesTheCompiler:
    """The independent oracle. Every assertion below compares the dtype the
    generated binding hands back against numbers a SEPARATE C program printed
    -- never against jm's own construction of the descr, which would agree
    with itself whatever it did."""

    STRUCTS = {
        "iq": "typedef struct {\n    int16_t i;\n    int16_t q;\n} iq_pair_t;",
        "padded": (
            "typedef struct {\n    int16_t i;\n    int32_t big;\n"
            "    int16_t q;\n} iq_pair_t;"
        ),
    }

    @classmethod
    def _build(cls, root: Path, shape: str, fields):
        _declare(root, fields=fields)
        h = root / INC_ROOT / "ring/ring_core.h"
        common = f'#include "{INC.include("clib_common.h", root)}"'
        h.write_text(
            h.read_text().replace(
                common,
                common + "\n#include <stdint.h>\n\n" + cls.STRUCTS[shape],
                1,
            )
        )
        c = root / "native/src/ring/ring_core.c"
        text = c.read_text()
        i = text.index("ring_wait(ring_state_t *state, size_t n)")
        body_start = text.index("{", i) + 1
        j = text.index("\n}\n", i)
        kernel = (
            "\n    static iq_pair_t buf[8];\n"
            "    if (n > 8) return NULL;\n"
            "    for (size_t k = 0; k < n; k++) {\n"
            "        buf[k].i = (int16_t)(k + 1);\n"
            "        buf[k].q = (int16_t)(100 + k);\n"
            "    }\n"
            "    return buf;"
        )
        c.write_text(text[:body_start] + kernel + text[j:])
        for args in (
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            ["cmake", "--build", str(root / "build"), "--target", "ring"],
        ):
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=900
            )
            assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        return root

    @staticmethod
    def _c_layout(root: Path, tmp: Path) -> dict:
        """sizeof/offsetof straight from the COMPILER, as its own program."""
        src = tmp / "layout.c"
        src.write_text(
            "#include <stdio.h>\n#include <stddef.h>\n"
            f'#include "{INC.core_include("ring", root)}"\n'
            "int main(void) {\n"
            '    printf("%zu %zu %zu\\n", sizeof(iq_pair_t),\n'
            "        offsetof(iq_pair_t, i), offsetof(iq_pair_t, q));\n"
            "    return 0;\n}\n"
        )
        exe = tmp / "layout"
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        r = subprocess.run(
            [
                cc,
                str(src),
                "-I",
                str(root / INC_DIR),
                "-I",
                str(root / INC_ROOT / "ring"),
                "-o",
                str(exe),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0, r.stderr
        out = subprocess.run(
            [str(exe)], capture_output=True, text=True, timeout=60
        ).stdout.split()
        return {
            "sizeof": int(out[0]),
            "i": int(out[1]),
            "q": int(out[2]),
        }

    @staticmethod
    def _view(root: Path, n: int = 4):

        code = (
            "import sys, glob, json\n"
            f"sys.path[:0] = glob.glob({str(root)!r} + '/build*/**/',"
            " recursive=True) + [" + repr(str(root / "src")) + "]\n"
            "from p.ring import Ring\n"
            "import numpy as np\n"
            "a = Ring(cap=8).wait(%d)\n"
            % n
            + "print(json.dumps({'itemsize': a.itemsize,"
            " 'names': list(a.dtype.names),"
            " 'offsets': [a.dtype.fields[x][1] for x in a.dtype.names],"
            " 'ndim': a.ndim, 'len': len(a),"
            " 'writeable': bool(a.flags.writeable),"
            " 'rows': [list(map(int, r)) for r in a]}))\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        import json

        return json.loads(r.stdout.strip().splitlines()[-1])

    @pytest.mark.parametrize(
        "shape,fields",
        [("iq", IQ_FIELDS), ("padded", PADDED_FIELDS)],
        ids=["iq", "padded"],
    )
    def test_itemsize_and_offsets_are_the_compilers(
        self, tmp_path, shape, fields
    ):
        root = self._build(tmp_path / "p", shape, fields)
        c = self._c_layout(root, tmp_path)
        v = self._view(root)
        assert v["itemsize"] == c["sizeof"], (v, c)
        assert v["offsets"][0] == c["i"], (v, c)
        assert v["offsets"][-1] == c["q"], (v, c)

    def test_it_is_one_element_per_sample_and_read_only(self, tmp_path):
        """The property that rejected 1-D interleaved int16 (length 2n)."""
        root = self._build(tmp_path / "p", "iq", IQ_FIELDS)
        v = self._view(root, 4)
        assert v["ndim"] == 1
        assert v["len"] == 4, v
        assert v["names"] == ["i", "q"]
        assert v["rows"] == [[1, 100], [2, 101], [3, 102], [4, 103]], v
        assert v["writeable"] is False

    def test_arithmetic_is_refused_loudly(self, tmp_path):
        """The measurement that rejected packed int32, where `d + 1`
        increments I only and says nothing."""
        root = self._build(tmp_path / "p", "iq", IQ_FIELDS)
        code = (
            "import sys, glob\n"
            f"sys.path[:0] = glob.glob({str(root)!r} + '/build*/**/',"
            " recursive=True) + [" + repr(str(root / "src")) + "]\n"
            "from p.ring import Ring\n"
            "a = Ring(cap=8).wait(4)\n"
            "try:\n"
            "    a + 1\n"
            "    print('SILENT')\n"
            "except TypeError:\n"
            "    print('RAISED')\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.stdout.strip().splitlines()[-1] == "RAISED", (
            r.stdout + r.stderr
        )
