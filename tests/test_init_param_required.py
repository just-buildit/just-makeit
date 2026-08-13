"""gh-266: `required` init-param flag.

A scalar init-param declared ``required = true`` (CLI:
``--init-param name:type:required``) parses as a positional *before* the PyArg
``|`` — so omitting it raises ``TypeError`` at construction instead of passing
the type's zero through to a constructor that returns NULL and surfaces a late,
opaque ``MemoryError`` (doppler's block-I/O friction, CLAUDE.md:124).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._cli_parse import parse_init_param_flag
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


# (name, type, default, default_raw, real_type, real_create_fn, optional,
#  create_fn, required)
def _req(name, ctype):
    return (name, ctype, "", "", "", "", False, "", True)


def _opt(name, ctype, default):
    return (name, ctype, default, "", "", "", False, "", False)


def _build(tmp_path, init_params, *, name="writer"):
    dest = tmp_path / "p"
    if not (dest / C.FILENAME).exists():
        new_run("p", dest)
    object_run(
        dest,
        name,
        None,
        no_state=True,
        arg_type="float _Complex",
        return_type="float _Complex",
        init_params=init_params,
    )
    return dest


def _ext(dest, name="writer"):
    return (dest / f"native/src/{name}/{name}_ext.c").read_text("utf-8")


def _pyi(dest, name="writer"):
    return (dest / f"src/p/{name}.pyi").read_text("utf-8")


class TestParseFlag:
    def test_required_scalar(self):
        (tup, i) = parse_init_param_flag(
            ["x", "block_size:size_t:required"], 0
        )
        assert tup == (
            "block_size",
            "size_t",
            "",
            "",
            "",
            "",
            False,
            "",
            True,
        )
        assert i == 2

    def test_required_case_insensitive(self):
        tup, _ = parse_init_param_flag(["x", "n:int32_t:REQUIRED"], 0)
        assert tup[8] is True

    def test_optional_unaffected(self):
        tup, _ = parse_init_param_flag(["x", "gain:double:1.5"], 0)
        assert tup[8] is False
        assert tup[2] == "1.5"

    def test_required_rejects_array(self, capsys):
        with pytest.raises(SystemExit):
            parse_init_param_flag(["x", "coeffs:float[]:required"], 0)
        assert "only valid for scalar" in capsys.readouterr().err


class TestCodegen:
    def test_required_before_pipe(self, tmp_path):
        dest = _build(
            tmp_path,
            [_req("block_size", "size_t"), _opt("gain", "double", "1.5")],
        )
        ext = _ext(dest)
        assert '"K|d"' in ext
        assert '{"block_size", "gain", NULL}' in ext
        assert "writer_create(block_size, gain)" in ext

    def test_all_required_has_no_pipe(self, tmp_path):
        dest = _build(
            tmp_path, [_req("rows", "size_t"), _req("cols", "size_t")]
        )
        ext = _ext(dest)
        # Two required size_t -> "KK", no optional separator at all.
        assert '"KK"' in ext
        assert '"KK|"' not in ext

    def test_required_seeds_zero_local(self, tmp_path):
        # The declaration must be valid C even though the value is overwritten.
        dest = _build(tmp_path, [_req("block_size", "size_t")])
        assert "unsigned long long block_size_raw = 0ULL;" in _ext(dest)


class TestStub:
    def test_required_first_no_default(self, tmp_path):
        dest = _build(
            tmp_path,
            [_opt("gain", "double", "1.5"), _req("block_size", "size_t")],
        )
        init = next(
            ln for ln in _pyi(dest).splitlines() if "def __init__" in ln
        )
        # Required hoisted ahead of the defaulted param, and carries no default.
        assert init.index("block_size") < init.index("gain")
        assert "block_size: int," in init
        assert "block_size: int =" not in init
        assert "gain: float = 1.5" in init


class TestRoundTrip:
    def test_required_persists_and_applies(self, tmp_path):
        dest = _build(tmp_path, [_req("block_size", "size_t")])
        cfg = C.load(dest)
        params = C.init_params(cfg, "writer")
        assert params[0][8] is True
        # Drop the generated binding and re-materialize from the manifest.
        (dest / "native/src/writer/writer_ext.c").unlink()
        apply_run(dest)
        assert '"K"' in _ext(dest)


@pytest.mark.skipif(
    not (
        shutil.which("cmake")
        and any(shutil.which(c) for c in ("cc", "gcc", "clang"))
    ),
    reason="needs cmake + C compiler",
)
def test_required_raises_typeerror_at_runtime(tmp_path):
    dest = _build(
        tmp_path, [_req("block_size", "size_t"), _opt("gain", "double", "1.5")]
    )
    build = dest / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(dest), "-B", str(build)],
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

    sos = list(build.rglob("writer*.so")) + list(
        (dest / "src/p").glob("writer*.so")
    )
    assert sos, "no writer extension built"
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util as u;"
            f"s=u.spec_from_file_location('writer', r'{sos[0]}');"
            "m=u.module_from_spec(s); s.loader.exec_module(m);"
            "W=m.Writer;"
            "err=0\n"
            "try:\n"
            "    W()\n"
            "except TypeError:\n"
            "    err=1\n"
            "assert err==1, 'no TypeError without block_size';"
            "assert W(8) is not None;"
            "print('ok')",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert check.returncode == 0, f"{check.stdout}\n{check.stderr}"
    assert "ok" in check.stdout


# ── gh-273: a required param with no default has no valid smoke/doctest seed ──


def _csmoke(dest, name="writer"):
    return (dest / f"native/tests/test_{name}_core.c").read_text("utf-8")


def _pytest_py(dest, name="writer"):
    return (dest / f"src/p/tests/test_{name}.py").read_text("utf-8")


class TestSeedDeferral:
    def test_required_no_default_c_smoke_is_tolerant(self, tmp_path):
        dest = _build(tmp_path, [_req("fs", "double")])
        c = _csmoke(dest)
        # No hard assertion that a zero-seeded construction succeeds.
        assert "CHECK(obj != NULL)" not in c
        assert "if (!obj) return 1;" not in c
        # Instead: a tolerant skip that still builds and passes.
        assert "SKIPPED" in c
        assert "return 0;" in c

    def test_required_no_default_pytest_skips(self, tmp_path):
        dest = _build(tmp_path, [_req("fs", "double")])
        py = _pytest_py(dest)
        assert "def setUp(self):" in py
        assert "self.skipTest(" in py

    def test_required_no_default_pure_pytest_skips(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        cfg = C.load(dest)
        cfg["project"]["pytest"] = "true"
        C.save(dest, cfg)
        object_run(
            dest,
            "writer",
            None,
            no_state=True,
            arg_type="float _Complex",
            return_type="float _Complex",
            init_params=[_req("fs", "double")],
        )
        py = _pytest_py(dest)
        assert "pytestmark = pytest.mark.skip(" in py

    def test_required_no_default_float_pyi_has_no_doctest(self, tmp_path):
        # double's zero literal is `.0` (non-empty), so the suppression must
        # key off `required`, not an empty rendered literal.
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "bar",
            None,
            state_vars=[("gain", "double", "1.0")],
            arg_type="float",
            return_type="float",
            init_params=[_req("fs", "double"), _opt("gain", "double", "1.0")],
        )
        assert ">>> obj = Bar(" not in _pyi(dest, "bar")

    def test_required_with_default_keeps_assertions(self, tmp_path):
        # The documented escape hatch: `required` enforces the positional, the
        # `default` seeds a valid smoke/doctest value — so tests stay strict.
        dest = _build(
            tmp_path,
            [("fs", "double", "2.048e6", "", "", "", False, "", True)],
        )
        c = _csmoke(dest)
        # gh-934: REQUIRE, not CHECK — the strict path still asserts
        # non-null, it just reports before returning now.
        assert "REQUIRE(obj != NULL)" in c
        assert "SKIPPED" not in c
        assert "def setUp(self):" not in _pytest_py(dest)

    def test_all_optional_unaffected(self, tmp_path):
        dest = _build(tmp_path, [_opt("gain", "double", "1.5")])
        c = _csmoke(dest)
        # gh-934: REQUIRE, not CHECK — the strict path still asserts
        # non-null, it just reports before returning now.
        assert "REQUIRE(obj != NULL)" in c
        assert "SKIPPED" not in c
        assert "def setUp(self):" not in _pytest_py(dest)
