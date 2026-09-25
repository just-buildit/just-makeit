"""gh-1405: rows of the author's struct cross IN as a structured array.

jm generated a structured ndarray *out* of a method (`record_dtype`) and had
no way to take one *in*, so doppler's `I16Buffer.write()` -- the symmetric
half of a ring buffer whose `wait()` already returns records -- could not be
declared at all. jm's own `ring_buffer` example carried the asymmetry:
`wait` is `record_dtype="iq16_t"`, its `write` `arg_type="int16_t[]"`.

A record is declared ONCE per component and referenced by both directions,
so the two faces cannot describe different bytes.

The tests below pin the three properties that make the input side correct,
each of which was measured on a built extension before it was written down:

1. the length reaching C is in **records**, not scalars -- the `2 *` factor
   is exactly what three hand-written faces got to disagree about;
2. the values arrive in their declared fields;
3. a dtype that is not the record's is **refused**, not reinterpreted.

(3) is the one that needed finding. `PyArray_FromAny` accepts a same-itemsize
structured dtype whose fields are declared in the other order and hands C the
bytes unchanged, so `[('q','<i2'),('i','<i2')]` arrived with i and q silently
swapped. The guard is gh-581's rule for an `out=` buffer, one direction over.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

RECORD_H = """typedef struct {
    int16_t i;
    int16_t q;
} iq16_t;

typedef struct"""

KERNEL = """    if (x_len > 0) {
        state->last_i = x[0].i;
        state->last_q = x[0].q;
    }
    return x_len;"""


def _declare(tmp_path: Path) -> Path:
    """A component with a declared record and a method taking rows of it."""
    root = tmp_path / "w"
    root.mkdir()
    r = run_cli(
        "new",
        "p",
        "--object",
        "ring",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--state",
        "last_i:int16_t:0",
        "--state",
        "last_q:int16_t:0",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    proj = root / "p"
    r = run_cli(
        "record",
        "ring",
        "iq16_t",
        "--field",
        "i:int16_t",
        "--field",
        "q:int16_t",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "method",
        "ring",
        "write",
        "--arg-type",
        "iq16_t[]",
        "--return-type",
        "size_t",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    return proj


class TestTheDeclaration:
    def test_an_undeclared_record_is_refused_and_names_the_command(
        self, tmp_path
    ):
        """The refusal has to be actionable: the struct is the author's."""
        root = tmp_path / "u"
        root.mkdir()
        assert (
            run_cli(
                "new",
                "p",
                "--object",
                "ring",
                "--arg-type",
                "float",
                "--return-type",
                "float",
                cwd=root,
            ).returncode
            == 0
        )
        r = run_cli(
            "method",
            "ring",
            "write",
            "--arg-type",
            "nope_t[]",
            "--return-type",
            "size_t",
            cwd=root / "p",
        )
        assert r.returncode == 1
        assert "just-makeit record ring nope_t" in r.stderr

    def test_a_duplicate_column_is_refused(self, tmp_path):
        """numpy takes field names as a set; a repeat would drop one."""
        root = tmp_path / "d"
        root.mkdir()
        assert (
            run_cli(
                "new",
                "p",
                "--object",
                "ring",
                "--arg-type",
                "float",
                "--return-type",
                "float",
                cwd=root,
            ).returncode
            == 0
        )
        r = run_cli(
            "record",
            "ring",
            "iq16_t",
            "--field",
            "i:int16_t",
            "--field",
            "i:int16_t",
            cwd=root / "p",
        )
        assert r.returncode == 1
        assert "declared twice" in r.stderr


class TestTheGeneratedC:
    def test_the_prototype_takes_the_struct(self, tmp_path):
        proj = _declare(tmp_path)
        header = (proj / INC_ROOT / "ring" / "ring_core.h").read_text()
        assert (
            "size_t ring_write(ring_state_t *state, const iq16_t *x,"
            " size_t x_len);" in header
        )

    def test_the_binding_builds_and_uses_the_records_descr(self, tmp_path):
        proj = _declare(tmp_path)
        ext = (proj / "native" / "src" / "ring" / "ring_ext.c").read_text()
        # Called AND defined -- a call with no definition does not compile,
        # which is how the first cut of this shipped.
        assert ext.count("Ring_write_x_get_dtype") == 2
        assert "offsetof(iq16_t, i)" in ext
        assert "PyArray_FromAny" in ext
        # ...and refuses rather than converting.
        assert "PyArray_EquivTypes" in ext

    def test_the_stub_annotates_it_like_the_reading_side(self, tmp_path):
        proj = _declare(tmp_path)
        pyi = (proj / "src" / "p" / "ring.pyi").read_text()
        assert "def write(self, x: NDArray[Any]) -> int:" in pyi


@pytest.mark.slow
class TestAgainstARealExtension:
    """Compile it and push arrays through, which is the only real proof."""

    def _built(self, tmp_path: Path) -> Path:
        proj = _declare(tmp_path)
        header = proj / INC_ROOT / "ring" / "ring_core.h"
        header.write_text(
            header.read_text().replace("typedef struct", RECORD_H, 1),
            encoding="utf-8",
        )
        core = proj / "native" / "src" / "ring" / "ring_core.c"
        text = core.read_text()
        stub = "    (void)state; (void)x; (void)x_len;\n    return (size_t)0;"
        assert stub in text, "the scaffolded body moved"
        core.write_text(text.replace(stub, KERNEL, 1), encoding="utf-8")
        build = subprocess.run(
            ["make"], cwd=proj, capture_output=True, text=True, timeout=900
        )
        assert build.returncode == 0, build.stderr[-3000:]
        return proj

    def _probe(self, proj: Path, body: str) -> str:
        script = proj / "probe.py"
        script.write_text(
            "import sys\nsys.path.insert(0, 'src')\n"
            "import numpy as np\nfrom p import Ring\n"
            "dt = np.dtype([('i', '<i2'), ('q', '<i2')])\n"
            "rev = np.dtype([('q', '<i2'), ('i', '<i2')])\n"
            "r = Ring(last_i=0, last_q=0)\n" + body,
            encoding="utf-8",
        )
        out = subprocess.run(
            [sys.executable, str(script)],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        return out.stdout.strip()

    def test_the_length_is_in_records_not_scalars(self, tmp_path):
        proj = self._built(tmp_path)
        got = self._probe(
            proj, "print(r.write(np.array([(3, -4), (5, 6)], dtype=dt)))"
        )
        assert got == "2", f"{got} would be scalars, not records"

    def test_the_values_arrive_in_their_declared_fields(self, tmp_path):
        proj = self._built(tmp_path)
        got = self._probe(
            proj,
            "r.write(np.array([(3, -4)], dtype=dt))\n"
            "print(r.get_last_i(), r.get_last_q())",
        )
        assert got == "3 -4"

    def test_a_dtype_declared_in_the_other_order_is_refused(self, tmp_path):
        """The one that needed finding.

        `PyArray_FromAny` accepts it and hands C the bytes unchanged, so i
        and q arrived swapped: `get_last_i()` returned q's value. Refusing
        is the only safe answer -- there is no cast to make, and numpy's own
        equality says these dtypes differ.
        """
        proj = self._built(tmp_path)
        got = self._probe(
            proj,
            "try:\n"
            "    r.write(np.array([(9, 7)], dtype=rev))\n"
            "    print('ACCEPTED', r.get_last_i(), r.get_last_q())\n"
            "except TypeError as exc:\n"
            "    print('refused')\n",
        )
        assert got == "refused", f"silent swap: {got}"

    @pytest.mark.parametrize(
        "expr", ["np.arange(4, dtype=np.int16)", "[(1, 2)]", "None"]
    )
    def test_a_non_record_input_is_refused(self, tmp_path, expr):
        proj = self._built(tmp_path)
        got = self._probe(
            proj,
            f"try:\n    r.write({expr})\n    print('ACCEPTED')\n"
            "except TypeError:\n    print('refused')\n",
        )
        assert got == "refused"
