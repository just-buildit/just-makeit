"""Every function the binding calls must resolve at LINK time (gh-1361).

A shared object links with undefined symbols, so a function the generated
binding calls and nothing defines built cleanly and failed only at import --
which is how gh-1303 hid. jm now generates ``native/tests/test_<comp>_symbols.c``,
a table of the address of every such function, linked into the component's C
test executable: a missing definition fails ``make test`` at link time, naming
the symbol, whatever was supposed to define it.

The compiled class is the one that settles it, and it runs the sabotage at
three build configurations, because the reviewer's point on gh-1361 was that
the gate is only as strong as the optimiser allows: measured, ``used`` alone
survived Debug and Release and was discarded by ``-Wl,--gc-sections``, which
is why the table also carries ``retain``.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _linkcheck  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._remove import run as remove_run  # noqa: E402

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path) -> Path:
    """A standalone `o` (with a computed property) and a module object `w`
    (with a method), each added by the verb an author would use."""
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(object_run, root, "o", None, state_vars=[("n", "int", "0")])
    _quiet(property_run, root, "o", "level", None, "double", False)
    _quiet(module_run, root, "m")
    _quiet(object_run, root, "w", "m", state_vars=[("n", "int", "0")])
    _quiet(
        method_run,
        root,
        "w",
        "tune",
        "m",
        "void",
        "void",
        False,
        [],
        params=[("gain", "double", "")],
    )
    return root


def _table(root: Path, comp: str) -> "list[str]":
    text = _linkcheck.symbols_file(root, comp).read_text(encoding="utf-8")
    return re.findall(r"\(jm_any_fn\)(\w+),", text)


class TestTheTableIsDerived:
    def test_every_member_the_binding_calls(self, tmp_path):
        root = _project(tmp_path)
        o, w = _table(root, "o"), _table(root, "w")
        # a computed accessor, and steps() -- whose prototype wraps lines
        assert {"o_create", "o_get_level", "o_steps"} <= set(o), o
        # a method added to a MODULE object after it was created
        assert {"w_create", "w_tune"} <= set(w), w

    def test_nothing_the_c_test_could_not_link(self, tmp_path):
        """Python-API calls never reach a C executable's table."""
        root = _project(tmp_path)
        for comp in ("o", "w"):
            assert not [n for n in _table(root, comp) if n.startswith("Py")]

    def test_the_verbs_leave_it_current(self, tmp_path):
        """Measured: before the `after` hook, `jm method` left the table one
        member behind and `status --check` failed on a project it had just
        written."""
        root = _project(tmp_path)
        assert _quiet(_status.run, root, check=True) == 0

    def test_apply_is_a_no_op_on_a_fresh_project(self, tmp_path):
        root = _project(tmp_path)
        before = {
            c: _linkcheck.symbols_file(root, c).read_text() for c in ("o", "w")
        }
        _quiet(apply_run, root)
        for c, text in before.items():
            assert _linkcheck.symbols_file(root, c).read_text() == text

    def test_remove_takes_it_with_the_component(self, tmp_path):
        root = _project(tmp_path)
        _quiet(remove_run, root, "object", "o", force=True)
        assert not _linkcheck.symbols_file(root, "o").exists()
        assert "o" not in C.components(C.load(root))


def _strip_getter(root: Path) -> None:
    core = root / "native" / "src" / "o" / "o_core.c"
    text = core.read_text(encoding="utf-8")
    stripped = re.sub(
        r"/\* <<IMPLEMENT: o_get_level>> \*/\ndouble\no_get_level\(.*?\n\}\n",
        "",
        text,
        flags=re.S,
    )
    assert "o_get_level" not in stripped
    core.write_text(stripped, encoding="utf-8")


def _build_test(root: Path, build: str, cflags: str, ldflags: str):
    bdir = root / f"build-{build}-{bool(ldflags)}"
    cfg = subprocess.run(
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(bdir),
            f"-DCMAKE_BUILD_TYPE={build}",
            f"-DCMAKE_C_FLAGS={cflags}",
            f"-DCMAKE_EXE_LINKER_FLAGS={ldflags}",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert cfg.returncode == 0, cfg.stdout[-2000:] + cfg.stderr[-2000:]
    return subprocess.run(
        ["cmake", "--build", str(bdir), "--target", "test_o_core"],
        capture_output=True,
        text=True,
        timeout=900,
    )


_CONFIGS = [
    pytest.param("Debug", "", "", id="debug"),
    pytest.param("Release", "", "", id="release"),
    pytest.param(
        "Release",
        "-ffunction-sections -fdata-sections",
        "-Wl,--gc-sections",
        id="release-gc-sections",
        marks=pytest.mark.skipif(
            sys.platform == "darwin", reason="GNU ld/lld flag spelling"
        ),
    ),
]


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestAMissingDefinitionFailsAtLink:
    @pytest.mark.parametrize("build, cflags, ldflags", _CONFIGS)
    def test_it_names_the_symbol(self, tmp_path, build, cflags, ldflags):
        root = _project(tmp_path)
        _strip_getter(root)
        r = _build_test(root, build, cflags, ldflags)
        assert r.returncode != 0, "a missing definition linked"
        assert "o_get_level" in r.stdout + r.stderr

    @pytest.mark.parametrize("build, cflags, ldflags", _CONFIGS)
    def test_and_links_when_it_is_there(
        self, tmp_path, build, cflags, ldflags
    ):
        """The control: the table itself compiles and links cleanly."""
        r = _build_test(_project(tmp_path), build, cflags, ldflags)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


class TestOnlyThingsWithAnAddress:
    """Both measured on doppler, where a table did not compile."""

    def test_a_keyword_is_not_a_function(self):
        header = (
            '_Static_assert(sizeof(int) == 4, "int");\n'
            "void o_tick(o_state_t *state);\n"
        )
        binding = "o_tick(s); n = sizeof(x);"
        assert _linkcheck.bound_symbols(binding, header) == ["o_tick"]

    def test_a_function_like_macro_is_not_a_function(self):
        header = (
            "#define O_EMIT(s, v) o_emit_impl((s), (v))\n"
            "void o_emit_impl(o_state_t *s, int v);\n"
            "O_EMIT(unused, 0);\n"
        )
        binding = "O_EMIT(s, 1); o_emit_impl(s, 2);"
        assert _linkcheck.bound_symbols(binding, header) == ["o_emit_impl"]

    def test_a_c99_inline_definition_is_not_taken(self):
        """No external symbol exists to take the address of (perf `step`)."""
        header = (
            "JM_FORCEINLINE float o_step(o_state_t *s, float x)\n"
            "{\n    return x;\n}\n"
            "void o_steps(o_state_t *s,\n"
            "             const float *in, float *out, size_t n);\n"
        )
        binding = "o_step(s, x); o_steps(s, a, b, n);"
        assert _linkcheck.bound_symbols(binding, header) == ["o_steps"]
