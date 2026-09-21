"""gh-1432: the generated invariants test could not run.

`test_<obj>_invariants.py` is jm-owned ("DO NOT EDIT"), so a downstream
cannot fix its copy -- and doppler's `make test-python` collects it, so the
suite was red on import. Four defects, all of them the text-versus-execution
gap: the file reads fine and cannot run.

1. **Import path.** An object in a module is `pkg.module.Cls`, and the file
   said `from pkg import Cls`. The user-owned sibling `test_<obj>.py` gets
   this right, so the spelling was known to jm -- this writer just did not
   use it. Now through `_docstring.class_import_line`, the one emitter the
   `.pyi` and the runtime docstrings already share.
2. **Location.** Written to `src/<pkg>/tests/` rather than beside that
   sibling in the module's own tests directory. The same mistake as (1):
   an object in a module lives in the module, on both faces.
3. **`_ELEM_DTYPE` used and never assigned.** A `NameError` on the first
   line that runs, and `F821` under ruff -- doppler could not even commit
   the file. `_dtype_expr`'s docstring says a struct element's dtype is
   read off the BINDING rather than restated, and the line that reads it
   was never written.
4. **A zero-seeded required constructor.** The user-owned scaffold skips
   itself when jm cannot seed a valid call; this file asserted anyway.

Two more found while fixing them, both silent:

- a **header-only** component keeps its kernels in the `.h`, and the stub
  check read only `_core.c` -- so every kernel read as a stub, which is
  exactly the shape doppler ships;
- with every pair skipped the file was still written, header and no tests:
  the hollow shape `_hollow.py` exists to catch.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

from _jmrun import run_cli


def _undefined_names(src: str) -> set:
    """Module-level F821, in process: names loaded but never bound.

    Not a full scope analysis -- a mini version of the one check that
    matters here, because `_ELEM_DTYPE` was a name used on two lines and
    assigned on none, and every assertion jm had about this file was
    satisfied by text that could not execute.
    """
    tree = ast.parse(src)
    bound: set = {"__name__", "__file__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            for a in node.args.args:
                bound.add(a.arg)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
    used = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    return {n for n in used - bound if not hasattr(builtins, n)}


def _project(tmp_path: Path, *, real_kernels: bool) -> Path:
    """A module object with a record written AND read -- the pair shape."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    assert (
        run_cli(
            "object",
            "r",
            "--module",
            "m",
            "--no-state",
            "--no-step",
            "--header-only",
            "--init-param",
            "capacity:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "record",
            "r",
            "iq_t",
            "--field",
            "i:int16_t",
            "--field",
            "q:int16_t",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "method",
            "r",
            "write",
            "--module",
            "m",
            "--arg-type",
            "iq_t[]",
            "--return-type",
            "bool",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "method",
            "r",
            "read",
            "--module",
            "m",
            "--borrow",
            "--param",
            "n:size_t",
            "--return-type",
            "float _Complex",
            "--record-dtype",
            "iq_t",
            cwd=proj,
        ).returncode
        == 0
    )
    if real_kernels:
        # A header-only component's kernels live in the HEADER, which is
        # the half the stub check was reading from the wrong file.
        hdr = proj / "native" / "inc" / "r" / "r_core.h"
        s = hdr.read_text()
        i = s.index("r_read(r_state_t *state")
        o = s.index("{", i)
        c = s.index("\n}", o)
        s = (
            s[:o] + "{\n    static iq_t buf[64];\n"
            "    if (n > 64) return NULL;\n"
            "    return buf;" + s[c:]
        )
        hdr.write_text(s)
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _invariants(proj: Path) -> Path:
    return proj / "src" / "q" / "m" / "tests" / "test_r_invariants.py"


class TestTheFileCanActuallyRun:
    def test_it_sits_beside_the_test_it_names(self, tmp_path):
        """Its own docstring points at `test_r.py` "beside it"."""
        proj = _project(tmp_path, real_kernels=True)
        assert _invariants(proj).exists()
        assert (_invariants(proj).parent / "test_r.py").exists()

    def test_the_import_is_the_one_the_sibling_uses(self, tmp_path):
        proj = _project(tmp_path, real_kernels=True)
        src = _invariants(proj).read_text()
        assert "from q.m import R" in src
        # The spelling that raised ImportError in doppler.
        assert "from q import R" not in src

    def test_no_name_is_used_without_being_bound(self, tmp_path):
        """`_ELEM_DTYPE` was used twice and assigned never (F821)."""
        proj = _project(tmp_path, real_kernels=True)
        src = _invariants(proj).read_text()
        assert _undefined_names(src) == set(), _undefined_names(src)
        assert "_ELEM_DTYPE" not in src

    def test_the_struct_dtype_comes_from_the_binding(self, tmp_path):
        """Read off the reader, never restated -- the declared element is
        declared once, and the C builds the layout from offsetof."""
        proj = _project(tmp_path, real_kernels=True)
        src = _invariants(proj).read_text()
        assert ".dtype" in src
        assert "np.dtype([" not in src

    def test_it_parses_and_compiles(self, tmp_path):
        proj = _project(tmp_path, real_kernels=True)
        compile(_invariants(proj).read_text(), "test_r_invariants.py", "exec")


class TestItIsNotWrittenWhenItWouldSayNothing:
    def test_a_stub_kernel_writes_no_file(self, tmp_path):
        """Header and no tests is the hollow shape, and worse than none.

        The struct face reads its dtype off the reader, so it needs a real
        one -- the same condition the round trip already stated.
        """
        proj = _project(tmp_path, real_kernels=False)
        assert not _invariants(proj).exists()
