"""gh-529: `out_type` on a method reported a scalar `.pyi` return.

A `[[<obj>.methods]]` entry with `out_type = "float _Complex"` generates a C
wrapper that allocates a fresh output array per call and returns it as an
ndarray -- exactly what `out_type` does on a `jm function` -- and its
PyMethodDef docstring already reads `... -> ndarray`. Only the `.pyi` return
annotation lagged: it was computed from `return_type` (default
`float _Complex` -> `complex`, or `void` -> `None`), so the stub told a type
checker the method returned a scalar while the runtime returned an array.

The issue also reported "no C declaration injected into the core header"; that
half is stale -- the header decl is present on the current tree (this file
checks it stays that way). The remaining defect is purely the stub, so the fix
is to make the annotation agree, not to reject `out_type` on methods (the
runtime already behaves like a function, and rejecting would break the gh-65
scalar-length path and any manifest using it).

The bug lived in BOTH `.pyi` generators -- `make_methods_ctx` (standalone) and
`_stubs._obj_stub` (module-aggregated). ``TestBothGeneratorsAgree`` pins them
together so a future edit cannot fix one and leave the other, which is the
single most repeated failure mode in this codebase.
"""

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run


def _add_read_method(root, obj, module, out_type="float _Complex"):
    method_run(
        root,
        obj,
        "read",
        module,
        "void",  # arg_type
        "void",  # return_type
        False,  # variable_output
        [],  # multi_output
        params=[("n", "size_t")],
        out_type=out_type,
    )


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


class TestStandaloneStub:
    """`make_methods_ctx` -> the standalone `<obj>.pyi`."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["rdr"], [("cap", "size_t", "16")])
        _add_read_method(dest, "rdr", None)
        return dest

    def test_return_is_ndarray_not_scalar(self, project):
        pyi = (project / "src" / "dsp" / "rdr.pyi").read_text()
        assert "def read(self, n: int) -> NDArray[np.complex64]:" in pyi
        assert "-> complex:" not in pyi.split("def read")[1][:60]

    def test_stub_parses(self, project):
        ast.parse((project / "src" / "dsp" / "rdr.pyi").read_text())

    def test_header_decl_is_present(self, project):
        """The issue's second claim ('no C declaration injected') is stale --
        assert it stays injected, with the `*out` output param."""
        h = (project / "native" / "inc" / "rdr" / "rdr_core.h").read_text()
        assert (
            "rdr_read(rdr_state_t *state, size_t n, float complex *out)" in h
        )


class TestModuleStub:
    """`_stubs._obj_stub` -> the module-aggregated `<module>.pyi` (the peer)."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [])
        module_run(dest, "io", ["rdr"])
        object_run(
            dest, "rdr", module="io", state_vars=[("cap", "size_t", "16")]
        )
        _add_read_method(dest, "rdr", "io")
        return dest

    def test_return_is_ndarray_not_scalar(self, project):
        pyi = (project / "src" / "dsp" / "io" / "io.pyi").read_text()
        assert "def read(self, n: int) -> NDArray[np.complex64]:" in pyi

    def test_stub_parses(self, project):
        ast.parse((project / "src" / "dsp" / "io" / "io.pyi").read_text())


class TestBothGeneratorsAgree:
    """The two `.pyi` generators must spell the same annotation.

    This is the guard the codebase keeps needing: a fix applied to one stub
    writer and not the other has shipped a divergence more than once.
    """

    @pytest.mark.parametrize(
        "out_type, expected",
        [
            ("float _Complex", "NDArray[np.complex64]"),
            ("double _Complex", "NDArray[np.complex128]"),
            ("float", "NDArray[np.float32]"),
            ("double", "NDArray[np.float64]"),
            ("int32_t", "NDArray[np.int32]"),
        ],
    )
    def test_same_annotation(self, tmp_path, out_type, expected):
        # standalone
        s = tmp_path / "s"
        new_run("s", s, ["rdr"], [("cap", "size_t", "16")])
        _add_read_method(s, "rdr", None, out_type=out_type)
        s_pyi = (s / "src" / "s" / "rdr.pyi").read_text()

        # module
        m = tmp_path / "m"
        new_run("m", m, [], [])
        module_run(m, "io", ["rdr"])
        object_run(m, "rdr", module="io", state_vars=[("cap", "size_t", "16")])
        _add_read_method(m, "rdr", "io", out_type=out_type)
        m_pyi = (m / "src" / "m" / "io" / "io.pyi").read_text()

        want = f"def read(self, n: int) -> {expected}:"
        assert want in s_pyi, f"standalone: {expected}"
        assert want in m_pyi, f"module: {expected}"


class TestBuildAndRun:
    """The stub now matches what the C already did -- prove it by running."""

    @pytest.fixture()
    def built(self, tmp_path):
        if _SKIP:
            pytest.skip(_SKIP)
        root = tmp_path / "proj"
        new_run("proj", root, ["rdr"], [("cap", "size_t", "16")])
        _add_read_method(root, "rdr", None)
        core = root / "native" / "src" / "rdr" / "rdr_core.c"
        body = core.read_text().replace(
            "    (void)state; (void)n; (void)out;\n",
            "    (void)state;\n"
            "    for (size_t i = 0; i < n; i++) out[i] = (float)i;\n",
        )
        core.write_text(body)
        build = root / "build"
        cfg = subprocess.run(
            # Pin the extension to the interpreter this test runs, so the
            # built .so imports under sys.executable rather than whatever
            # higher Python cmake's FindPython3 would otherwise pick.
            [
                "cmake",
                "-S",
                str(root),
                "-B",
                str(build),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
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

    def test_read_returns_a_complex64_ndarray(self, built):
        script = (
            f"import sys; sys.path.insert(0, {str(built / 'src')!r})\n"
            "import proj.rdr as m, numpy as np\n"
            "y = m.Rdr(16).read(4)\n"
            "assert isinstance(y, np.ndarray), type(y)\n"
            "assert y.dtype == np.complex64, y.dtype\n"
            "assert y.shape == (4,), y.shape\n"
            "print('ok')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
        assert res.stdout.strip() == "ok"
