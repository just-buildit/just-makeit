"""gh-1651: an object declared ``--class-name X`` is importable as ``X`` after
`jm apply`, standalone and in a module alike.

`jm apply` registered a standalone ``--class-name Renamed`` object as
``Named`` -- its ``tp_name``, its ``PyModule_AddObject`` and its ``.pyi``
class -- while ``__init__.py`` still imported ``Renamed``, so the package did
not import. `status --check` called the tree clean, because it compares the
tree with the same replay that produced it. A test through `status` cannot
see this class of bug by construction.

So this is the independent oracle: a real CMake build, ctest, and the
generated pytest, which imports the declared class name -- after `jm apply`,
which is the step that dropped it. Nothing here goes through the replay's own
comparison.

GATE: after `jm apply`, a `--class-name` object builds, and the package
      imports it under its declared name, standalone and in a module.
"""

from __future__ import annotations

import shutil

import pytest

from _jminc import INC_ROOT
from _jmrun import run_cli

pytestmark = pytest.mark.skipif(
    shutil.which("cmake") is None, reason="needs cmake and a C compiler"
)


@pytest.mark.parametrize("module", [None, "grp"], ids=["standalone", "module"])
def test_a_class_name_object_imports_under_its_name_after_apply(
    tmp_path, module
):
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    if module:
        assert run_cli("module", module, cwd=root).returncode == 0
    args = ["object", "named", "--class-name", "Renamed"]
    if module:
        args += ["--module", module]
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr

    # Document create(), as any author does. An authored brief is what makes
    # `apply` re-render a standalone object's binding from the manifest
    # (`_sync_aggregates`) -- the path that registered it under the default
    # name. Without it that path never runs, and this test proves nothing.
    header = root / INC_ROOT / "named" / "named_core.h"
    text = header.read_text(encoding="utf-8")
    old = "@brief Create a named instance."
    assert text.count(old) == 1, text[:1500]
    header.write_text(
        text.replace(old, "@brief Build a widget with a declared name."),
        encoding="utf-8",
        newline="\n",
    )

    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr

    # Not vacuous: the generated pytest really imports the declared name, so
    # a passing run below is that import succeeding.
    tests = "".join(
        p.read_text(encoding="utf-8")
        for p in (root / "src" / "p").rglob("test_*.py")
    )
    assert "import Renamed" in tests, tests[:2000]

    # The build, ctest and the generated pytest, all through `jm test`.
    r = run_cli("test", cwd=root)
    assert r.returncode == 0, (
        "the project does not build or import after `jm apply`:\n"
        + (r.stdout + r.stderr)[-3000:]
    )
