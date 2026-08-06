"""gh-788 gap 1, part 2 — the dtype builder wired into the return path.

Part 1 (`tests/test_gh788_record_dtype.py`) pinned the emitter in isolation:
`_record.dtype_c` produces a cached `PyArray_Descr *` whose offsets come from
`offsetof` and whose itemsize comes from `sizeof`. Nothing called it. This is
the wiring — `record_dtype = "<struct>"` on a `variable_output` method, and
every face that has to agree about it.

**Why the wiring is more than one substitution.** `record_dtype` reuses
`result_fields` for the dtype's columns, and `result_fields` already means
something else: "return a LIST of records through a `results[]`/`max_results`
out-param pair". Three separate places draw that distinction, and they were
already inconsistent before this issue — the declaration chain in
`make_methods_ctx` preferred `result_fields`, while the wrapper chain
preferred `variable_output`. A record-dtype method that tripped only one of
them would get a header prototype the binding never calls. So each of the
three is pinned here by name.

The four faces that must agree, all asserted below:

1. the sacred `_core.h` prototype   -> `size_t f(state, size_t n, rec_t *out)`
2. the `_core.c` stub               -> the same, with `(void)out;`
3. the `_ext.c` wrapper             -> `PyArray_NewFromDescr`, not `SimpleNew`
4. the `.pyi` and runtime `__doc__` -> one structured ndarray, no `out=`
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _record as R  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

REC = "dp_tlm_rec_t"

# doppler's actual telemetry record. uint64/float/uint16/uint16 is 16 bytes
# under both C's alignment rules and numpy's packing, which is precisely why
# it is NOT sufficient on its own -- see TestPaddingIsWhyOffsetsExist.
FIELDS = [
    {"name": "n", "type": "uint64_t", "doc": "Sequence number."},
    {"name": "value", "type": "float"},
    {"name": "probe", "type": "uint16_t"},
    {"name": "flags", "type": "uint16_t"},
]


def _project(tmp_path: Path, fields=None, **kw) -> Path:
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        object_run(
            root, "telemetry", None, state_vars=[("cap", "size_t", "0")]
        )
        method_run(
            root,
            "telemetry",
            "read",
            None,
            "void",
            "size_t",
            True,
            [],
            result_fields=[dict(f) for f in (fields or FIELDS)],
            record_dtype=kw.pop("record_dtype", REC),
            **kw,
        )
    return root


def _ext(root: Path) -> str:
    return (
        root / "native" / "src" / "telemetry" / "telemetry_ext.c"
    ).read_text()


def _core_h(root: Path) -> str:
    return (
        root / "native" / "inc" / "telemetry" / "telemetry_core.h"
    ).read_text()


def _core_c(root: Path) -> str:
    return (
        root / "native" / "src" / "telemetry" / "telemetry_core.c"
    ).read_text()


def _pyi(root: Path) -> str:
    return (root / "src" / "proj" / "telemetry.pyi").read_text()


class TestTheCFaces:
    """The struct is the output ELEMENT, so it must reach the prototype, the
    stub and the data-pointer cast — all three derived from one manifest key
    rather than spelled three times."""

    def test_the_header_declares_a_record_out_param(self, tmp_path):
        h = _core_h(_project(tmp_path))
        assert (
            f"size_t telemetry_read(telemetry_state_t *state, size_t n,"
            f" {REC} *out);" in h
        )

    def test_the_header_is_not_the_list_of_records_shape(self, tmp_path):
        """The bug this guards. `result_fields` alone would have declared
        `(state, size_t *result, size_t max_results)` — a prototype for a
        kernel the generated binding never calls."""
        h = _core_h(_project(tmp_path))
        assert "max_results" not in h
        assert "*result," not in h

    def test_the_core_c_stub_matches_the_header(self, tmp_path):
        c = _core_c(_project(tmp_path))
        assert (
            f"telemetry_read(telemetry_state_t *state, size_t n, {REC} *out)"
            in c
        )
        # A scaffolded stub must not warn on an unused parameter — that is an
        # error under -Werror before the author writes a line.
        assert "(void)out;" in c

    def test_the_data_pointer_is_cast_to_the_record(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert f"{REC} *_d0 = ({REC} *)PyArray_DATA" in ext

    def test_max_out_is_still_declared(self, tmp_path):
        """A record result is still variable-output: the binding sizes the
        allocation from max_out() exactly as every other shape does."""
        assert "telemetry_read_max_out" in _core_h(_project(tmp_path))


class TestTheAllocation:
    def test_it_allocates_from_the_descr(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "PyArray_NewFromDescr(" in ext
        assert "Telemetry_read_get_dtype()" in ext

    def test_it_does_not_fall_back_to_a_scalar_enum(self, tmp_path):
        """The silent-failure this replaces. `_vo_out_np` resolves through
        `_CTYPE_META.get(...)`, which yields NPY_FLOAT for anything it does
        not know — so an unguarded record would have quietly allocated an
        array of floats and every field would read as garbage."""
        ext = _ext(_project(tmp_path))
        body = ext[ext.index("Telemetry_read(TelemetryObject") :]
        body = body[: body.index("\n}\n")]
        assert "PyArray_SimpleNew" not in body
        assert "NPY_FLOAT" not in body

    def test_the_descr_reference_is_not_double_released(self, tmp_path):
        """`_get_dtype()` returns a NEW reference and PyArray_NewFromDescr
        STEALS one. They balance, so a Py_DECREF of `_descr` anywhere would
        be one too many — a refcount bug that only surfaces under load."""
        ext = _ext(_project(tmp_path))
        assert "Py_DECREF(_descr)" not in ext
        assert "Py_XDECREF(_descr)" not in ext

    def test_the_statics_precede_the_wrapper(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert ext.index("Telemetry_read_dtype = NULL") < ext.index(
            "Telemetry_read(TelemetryObject"
        )


class TestThePythonFaces:
    def test_the_stub_returns_one_structured_ndarray(self, tmp_path):
        pyi = _pyi(_project(tmp_path))
        assert "def read(self, count: int = 1) -> NDArray[Any]:" in pyi

    def test_the_stub_does_not_offer_out(self, tmp_path):
        """gh-788 carve-out, asserted rather than assumed. The out= branch
        acquires the caller's buffer with a scalar NPY_ enum, which for a
        record would CAST rather than reject. A stub advertising `out=` would
        type-check a call the binding cannot honour."""
        pyi = _pyi(_project(tmp_path))
        read = pyi[pyi.index("def read(") :]
        assert "out:" not in read[: read.index("def ", 5)]

    def test_it_is_not_annotated_as_a_list_of_tuples(self, tmp_path):
        """`result_fields` alone annotates `list[tuple]`. The richer shape
        must not be shadowed by the older one that merely names the same
        manifest key."""
        assert "list[tuple" not in _pyi(_project(tmp_path))

    def test_the_runtime_doc_documents_the_columns(self, tmp_path):
        """The point of the migration. A hand-written module carries its
        field docs in a header comment, a PyMethodDef literal and a .pyi with
        nothing linking the three; here one source feeds every face."""
        ext = _ext(_project(tmp_path))
        assert "Fields" in ext
        assert "Sequence number." in ext

    def test_the_runtime_doc_shows_the_field_names(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "y.dtype.names" in ext
        assert "('n', 'value', 'probe', 'flags')" in ext

    def test_the_runtime_doc_does_not_promise_out(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "Pass out= to write into your own buffer" not in ext

    def test_a_single_field_record_keeps_its_trailing_comma(self, tmp_path):
        """`('n',)` — Python's own repr, so the doctest is copy-pasteable
        rather than nearly right."""
        root = _project(tmp_path, fields=[{"name": "n", "type": "uint64_t"}])
        assert "    ('n',)" in _ext(root)


class TestTheManifest:
    def test_it_round_trips(self, tmp_path):
        cfg = C.load(_project(tmp_path))
        m = next(m for m in C.methods(cfg, "telemetry") if m["name"] == "read")
        assert m["record_dtype"] == REC
        assert [f["name"] for f in m["result_fields"]] == [
            "n",
            "value",
            "probe",
            "flags",
        ]

    def test_apply_replays_it_identically(self, tmp_path):
        root = _project(tmp_path)
        before = _ext(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        assert before == _ext(root), "apply must be idempotent here"

    def test_apply_does_not_rewrite_the_header_prototype(self, tmp_path):
        """The bug this caught. `jm apply` REPLAYS each method through
        `_method.run`, and the replay call forwards keys one by one — a key
        it does not name is simply absent, so `result_fields` read as the
        list-of-records form and apply rewrote the sacred header to
        `(state, size_t *result, size_t max_results)` over a `_core.c`
        definition still using the record one. It is a shape disagreement,
        not a detail, and it only failed at the user's next compile."""
        root = _project(tmp_path)
        before = _core_h(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        after = _core_h(root)
        assert f"{REC} *out);" in after
        assert "max_results" not in after
        assert before == after

    def test_script_reconstructs_the_flag(self, tmp_path):
        """`jm script` rebuilds the full CLI history from the manifest. A
        flag missing there produces a script that silently generates a
        DIFFERENT project than the one it claims to reproduce."""
        import just_makeit._script as S

        root = _project(tmp_path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.run(root)
        out = buf.getvalue()
        assert f"--record-dtype {REC}" in out
        assert "--result-field" in out
        # Not behind the --single guard its siblings ride: this is the other
        # record shape, and the CLI rejects the two together.
        assert "--single" not in out

    def test_it_needs_variable_output(self, tmp_path, capsys):
        root = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", root)
            object_run(root, "thing", None, state_vars=[("g", "double", "1")])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            method_run(
                root,
                "thing",
                "read",
                None,
                "void",
                "size_t",
                False,
                [],
                result_fields=[{"name": "n", "type": "uint64_t"}],
                record_dtype=REC,
            )
        assert "--variable-output" in capsys.readouterr().err

    def test_it_needs_result_fields(self, tmp_path, capsys):
        root = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", root)
            object_run(root, "thing", None, state_vars=[("g", "double", "1")])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            method_run(
                root,
                "thing",
                "read",
                None,
                "void",
                "size_t",
                True,
                [],
                record_dtype=REC,
            )
        assert "--result-field" in capsys.readouterr().err

    def test_it_is_not_combined_with_single(self, tmp_path, capsys):
        """--single returns ONE record as a named tuple; --record-dtype
        returns an ARRAY of them as a structured ndarray. Silently picking
        one would be a footgun."""
        root = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", root)
            object_run(root, "thing", None, state_vars=[("g", "double", "1")])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            method_run(
                root,
                "thing",
                "read",
                None,
                "void",
                "size_t",
                True,
                [],
                result_fields=[{"name": "n", "type": "uint64_t"}],
                record_dtype=REC,
                single=True,
            )
        assert "--single" in capsys.readouterr().err


class TestTheIncrementalSplice:
    """gh-729/gh-779 one costume along.

    The sacred `<mod>_ext_<obj>.c` fragment is never re-rendered; `jm apply`
    splices in only the members the manifest gained. That splicer carries
    *functions* the new wrapper calls by name and *initialised file-scope
    declarations* it references — and the dtype block is neither in the shape
    it recognises. The wrapper calls `<sid>_get_dtype()`, a definition rather
    than an initialised declaration, and the cache it reads is referenced by
    the builder rather than by the wrapper. Measured before the fix: the
    wrapper spliced in and called a function nothing declared.
    """

    def _spliceable(self, tmp_path: Path) -> Path:
        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            module_run(root, "tlm")
            object_run(
                root, "telemetry", "tlm", state_vars=[("cap", "size_t", "0")]
            )
            # One method, so the fragment exists and is sacred...
            method_run(
                root, "telemetry", "first", "tlm", "void", "float", True, []
            )
            # ...then declare a second in the MANIFEST ONLY. This is the state
            # apply splices into; adding it via `jm method` would re-render
            # the fragment instead and never exercise the splicer.
            cfg = C.load(root)
            C.add_method(
                cfg,
                "telemetry",
                {
                    "name": "read",
                    "arg_type": "void",
                    "return_type": "size_t",
                    "variable_output": True,
                    "record_dtype": REC,
                    "result_fields": [dict(f) for f in FIELDS],
                },
            )
            C.save(root, cfg)
        return root

    def _frag(self, root: Path) -> str:
        return (
            root / "native" / "src" / "tlm" / "tlm_ext_telemetry.c"
        ).read_text()

    def test_the_wrapper_and_its_dtype_block_travel_together(self, tmp_path):
        root = self._spliceable(tmp_path)
        assert "Telemetry_read" not in self._frag(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        frag = self._frag(root)
        assert frag.count("Telemetry_read_get_dtype(void)") == 1, (
            "the builder is spliced exactly once -- zero does not compile, "
            "two is a redefinition"
        )
        assert frag.count("static PyArray_Descr *Telemetry_read_dtype") == 1
        assert frag.index("Telemetry_read_dtype = NULL") < frag.index(
            "Telemetry_read_get_dtype();"
        )

    def test_a_second_apply_adds_nothing(self, tmp_path):
        root = self._spliceable(tmp_path)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        once = self._frag(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        assert once == self._frag(root)


class TestTheFinder:
    """`_record.find_dtype` is what the splicer above asks. It is pinned
    directly too, because a finder that silently returns "" fails toward
    looking correct — the splice just quietly omits the block."""

    def test_it_finds_the_whole_block_and_nothing_else(self):
        flds = [R.RecordField("n", "uint64_t", "")]
        c = R.dtype_c("W_read", "rec_t", flds)
        # Sentinels that cannot occur in the emitted block's own prose --
        # `before`/`after` do ("...every row after the first..."), and a
        # substring check against them passes for the wrong reason.
        text = (
            "static int zzleading = 0;\n" + c + "\nstatic int zztrail = 0;\n"
        )
        found = R.find_dtype(text, "W_read")
        assert found.startswith("static PyArray_Descr *W_read_dtype = NULL;")
        assert found.rstrip().endswith("}")
        assert "zzleading" not in found and "zztrail" not in found

    def test_it_is_scoped_to_the_sid(self):
        c = R.dtype_c("A_x", "rec_t", [R.RecordField("n", "uint64_t", "")])
        assert R.find_dtype(c, "B_y") == ""

    def test_it_returns_empty_when_absent(self):
        assert R.find_dtype("static int x = 0;", "W_read") == ""


# ── the claims that only a compiler and a running numpy can settle ──────────

_PAD_REC = "padded_rec_t"


def _implement(root: Path, structs: str, body_subs: list[tuple[str, str]]):
    """Declare the user's POD struct(s) and fill in the kernel stubs.

    jm never generates the record struct — it is the user's, declared in the
    sacred header, exactly as `--single`'s `tone_metrics_t` is. That is the
    whole reason the dtype has to be derived by the compiler at build time
    rather than computed by jm at generate time.
    """
    h = root / "native" / "inc" / "telemetry" / "telemetry_core.h"
    t = h.read_text()
    anchor = "/**\n * @brief Telemetry state."
    assert anchor in t
    h.write_text(t.replace(anchor, structs + "\n" + anchor, 1))
    c = root / "native" / "src" / "telemetry" / "telemetry_core.c"
    s = c.read_text()
    for old, new in body_subs:
        assert old in s, f"stub shape changed; cannot patch:\n{old}"
        s = s.replace(old, new, 1)
    c.write_text(s)


_MAX_OUT_STUB = "    (void)state; (void)n;\n    return 0; /* placeholder */"
_MAX_OUT_IMPL = "    (void)state;\n    return (size_t)n;"


def _build(root: Path):
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from just_makeit import _build;"
            "r=Path('.').resolve(); _build._ensure_built(r, r / 'build')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=900,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("cmake"), reason="needs a C toolchain")
class TestItCompilesAndRoundTrips:
    def test_the_doppler_record(self, tmp_path):
        """The end-to-end claim: doppler's `Telemetry.read()` dtype, produced
        by generated code, from a struct jm never saw the definition of."""
        root = _project(tmp_path)
        _implement(
            root,
            "typedef struct {\n"
            "    uint64_t n;\n"
            "    float value;\n"
            "    uint16_t probe;\n"
            "    uint16_t flags;\n"
            f"}} {REC};\n",
            [
                (_MAX_OUT_STUB, _MAX_OUT_IMPL),
                (
                    "    (void)state;\n    (void)n;\n    (void)out;\n"
                    "    return 0; /* placeholder */",
                    "    (void)state;\n"
                    "    for (size_t i = 0; i < (size_t)n; ++i) {\n"
                    "        out[i].n = (uint64_t)i;\n"
                    "        out[i].value = (float)i * 1.5f;\n"
                    "        out[i].probe = (uint16_t)(i + 7);\n"
                    "        out[i].flags = (uint16_t)(i & 1);\n"
                    "    }\n"
                    "    return (size_t)n;",
                ),
            ],
        )
        _build(root)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import numpy as np\n"
                "from proj import Telemetry\n"
                "y = Telemetry(cap=0).read(5)\n"
                "print(repr(y.dtype))\n"
                "print(y.dtype.itemsize)\n"
                "print(y['n'].tolist())\n"
                "print(y['value'].tolist())\n"
                "print(y['probe'].tolist())\n",
            ],
            cwd=root / "src",
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        dt, itemsize, ns, values, probes = proc.stdout.strip().splitlines()
        # The exact dtype the issue asks for.
        assert np.dtype(eval(dt, {"dtype": np.dtype})) == np.dtype(
            [
                ("n", "<u8"),
                ("value", "<f4"),
                ("probe", "<u2"),
                ("flags", "<u2"),
            ]
        )
        assert itemsize == "16"
        assert eval(ns) == [0, 1, 2, 3, 4]
        assert eval(values) == [0.0, 1.5, 3.0, 4.5, 6.0]
        assert eval(probes) == [7, 8, 9, 10, 11]

    def test_a_padded_record_is_read_from_the_right_bytes(self, tmp_path):
        """The measurement that makes offsets-from-`offsetof` load-bearing
        rather than defensive.

        `{uint8_t; uint64_t}` is 16 bytes in C and 9 packed by numpy. Had the
        dtype been built from a bare `(name, format)` list — numpy's default,
        and the obvious implementation — every row after the first would be
        read 7 bytes off. Here the values come back exact.
        """
        root = _project(
            tmp_path,
            fields=[
                {"name": "flag", "type": "uint8_t"},
                {"name": "big", "type": "uint64_t"},
            ],
            record_dtype=_PAD_REC,
        )
        _implement(
            root,
            f"typedef struct {{ uint8_t flag; uint64_t big; }} {_PAD_REC};\n",
            [
                (_MAX_OUT_STUB, _MAX_OUT_IMPL),
                (
                    "    (void)state;\n    (void)n;\n    (void)out;\n"
                    "    return 0; /* placeholder */",
                    "    (void)state;\n"
                    "    for (size_t i = 0; i < (size_t)n; ++i) {\n"
                    "        out[i].flag = (uint8_t)i;\n"
                    "        out[i].big = (uint64_t)(i * 100);\n"
                    "    }\n"
                    "    return (size_t)n;",
                ),
            ],
        )
        _build(root)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from proj import Telemetry\n"
                "y = Telemetry(cap=0).read(4)\n"
                "print(y.dtype.itemsize)\n"
                "print([v[1] for v in y.dtype.fields.values()])\n"
                "print(y['big'].tolist())\n"
                "print(y['flag'].tolist())\n",
            ],
            cwd=root / "src",
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        itemsize, offsets, bigs, flags = proc.stdout.strip().splitlines()
        assert itemsize == "16", "C padded the struct; the dtype must agree"
        assert eval(offsets) == [0, 8]
        assert eval(bigs) == [0, 100, 200, 300]
        assert eval(flags) == [0, 1, 2, 3]

    def test_numpy_would_have_packed_it_to_nine(self):
        """The other half of the pair above, and the reason it is not
        redundant: if numpy ever starts padding by default this fails, and
        the rationale should be re-read rather than the test deleted."""
        packed = np.dtype([("flag", np.uint8), ("big", np.uint64)])
        assert packed.itemsize == 9
