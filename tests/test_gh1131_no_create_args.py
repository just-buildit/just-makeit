"""gh-1131: a handle whose constructor takes no arguments must compile.

`kwlist` and the `PyArg_ParseTupleAndKeywords` argument list were both built
by joining `create_args` into a fixed template, so an EMPTY list produced

    static char *kwlist[] = {, NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "", kwlist,
            )) {

which is not valid C. `jm apply` accepted the declaration, wrote the file,
and the project failed at `cmake --build` pointing at generated code the
author is told not to edit.

A no-argument constructor is an ordinary shape — a clock, a default device, a
singleton session — not a corner. Nothing caught it because every handle
fixture in the suite declared at least one `create_arg`, and `jm status` does
not track `_ext.c`, so the compiler was the first observer. It surfaced only
when gh-1113's docstring gate became the first test to BUILD a handle module
with no constructor arguments.

That is why the central test here compiles. A text assertion on `{NULL}` would
have passed against half a fix.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_LINK = (
    ["-bundle", "-undefined", "dynamic_lookup"]
    if sys.platform == "darwin"
    else ["-shared"]
)

_BASE = {
    "kind": "handle",
    "backing": "b",
    "header": "b/b.h",
    "type_name": "Clock",
    "close_fn": "b_close",
}


def _cfg(create_args=None, **over):
    m = {**_BASE, "create_fn": "b_open", "methods": [], **over}
    if create_args is not None:
        m["create_args"] = create_args
    return {"project": {"name": "p"}, "module": {"clk": m}}


class TestTheEmittedC:
    def test_no_create_args_emits_a_valid_kwlist(self):
        ext = _handle.render_ext(_cfg(), "clk")
        assert "static char *kwlist[] = {NULL};" in ext
        assert "{, NULL}" not in ext

    def test_no_create_args_emits_no_dangling_argument(self):
        """The other half. Fixing only the kwlist leaves `kwlist,\\n  ))`,
        which is the same compile error one line down."""
        ext = _handle.render_ext(_cfg(), "clk")
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "", kwlist)) {' in ext
        assert "kwlist,\n            ))" not in ext

    def test_the_key_omitted_entirely_behaves_the_same(self):
        """Both spellings reached the same join."""
        omitted = _handle.render_ext(_cfg(), "clk")
        empty = _handle.render_ext(_cfg(create_args=[]), "clk")
        assert omitted == empty

    def test_an_init_fn_handle_too(self):
        """`init_fn` is the other constructor shape and shares the parse."""
        cfg = _cfg()
        del cfg["module"]["clk"]["create_fn"]
        cfg["module"]["clk"]["init_fn"] = "b_init"
        ext = _handle.render_ext(cfg, "clk")
        assert "{, NULL}" not in ext
        assert "static char *kwlist[] = {NULL};" in ext

    def test_a_constructor_with_args_is_unchanged(self):
        """The fix must be invisible to every existing project."""
        ext = _handle.render_ext(
            _cfg(create_args=[{"name": "path", "type": "path"}]), "clk"
        )
        assert 'static char *kwlist[] = {"path", NULL};' in ext
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "O&", kwlist,' in ext


class TestTheStub:
    def test_no_create_args_has_no_trailing_comma(self):
        pyi = _handle.render_pyi(_cfg(), "clk")
        assert "def __init__(self) -> None: ..." in pyi
        assert "def __init__(self, )" not in pyi

    def test_with_args_is_unchanged(self):
        pyi = _handle.render_pyi(
            _cfg(create_args=[{"name": "path", "type": "path"}]), "clk"
        )
        assert (
            "def __init__(self, path: str | os.PathLike) -> None: ..." in pyi
        )


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_a_no_arg_handle_compiles_and_constructs(tmp_path):
    """The test that would have caught this, and the reason it never ran.

    Every other handle fixture declares a `create_arg`, so nothing in the
    suite had ever built this shape. Asserting on the text alone would pass
    against a half-fix: the kwlist and the argument list are two separate
    errors one line apart.
    """
    import numpy as np

    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "b.h").write_text(
        "#ifndef B_H\n#define B_H\ntypedef struct b b_t;\n"
        "b_t *b_open(void);\nvoid b_close(b_t *);\n#endif\n"
    )
    (tmp_path / "b.c").write_text(
        '#include "b/b.h"\n#include <stdlib.h>\n'
        "struct b { int x; };\n"
        "b_t *b_open(void) { return calloc(1, sizeof(struct b)); }\n"
        "void b_close(b_t *p) { free(p); }\n"
    )
    (tmp_path / "clk.c").write_text(_handle.render_ext(_cfg(), "clk"))
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    subprocess.run(
        [
            _CC,
            *_LINK,
            "-fPIC",
            f"-I{tmp_path}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{np.get_include()}",
            str(tmp_path / "clk.c"),
            str(tmp_path / "b.c"),
            "-o",
            str(pkg / f"clk{sysconfig.get_config_var('EXT_SUFFIX')}"),
        ],
        check=True,
        capture_output=True,
    )
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.')\n"
            "from p.clk import Clock\n"
            "c = Clock()\n"
            "c.close()\n"
            "print('constructed')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "constructed" in r.stdout
