"""gh-2571: every scaffolded Python file has its imports in isort order.

doppler selects ruff's isort rule (``I``), and a fresh ``test_<obj>.py``
failed it on ``I001``: ``import unittest``, ``import numpy as np`` and
``from <pkg> import X`` in one block. The class was wider -- the benchmark,
both ``.pyi`` stubs and both re-export ``__init__.py`` files. The fix is at
each emitter: the templates write their heads in order, every stub writer
lays its imports out through ``_stubs.import_head``, and jm's re-export
lines go through ``_object.sort_reexports``.

The ruff verdict is ``tests/test_gh1478_ruff_clean_scaffold.py``, which
selects ``I`` -- the independent oracle; it needs the project environment
(``make test-examples``). This file is the half ``make test`` runs: the
import heads ruff's own ``--fix`` produced, asserted literally, and the
promise that an author's lines in a package ``__init__.py`` are left
exactly as written.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from _jmrun import run_cli


def _scaffold(tmp: Path, calls: "list[str]") -> Path:
    first, *rest = (shlex.split(c) for c in calls)
    r = run_cli(*first, cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp / "p"
    for argv in rest:
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, f"jm {' '.join(argv)}\n{r.stdout}{r.stderr}"
    return root


#: doppler's shape (a module object with a view and init params), a module
#: of seven objects whose names carry digits and a view, and two
#: standalone objects
#: added in the order that sorts them wrong: `w10` before `w2`.
OBJ = "--arg-type float --return-type float"
CALLS = [
    "module st",
    *(f"object {o} --module st {OBJ}" for o in ("s10", "s2", "s1", "s11")),
    *(f"object {o} --module st {OBJ}" for o in ("s3", "s4", "s5")),
    "view s2 SV --module st --create-fn s2_create_view",
    "module m",
    "object fir --module m --init-param n:size_t:16 --init-param g:double:1"
    f" {OBJ}",
    "view fir FirView --module m --create-fn fir_create_view"
    " --init-param scale:double:2.0",
    "function clamp --module m",
    f"object w10 {OBJ}",
    f"object w2 {OBJ}",
    "object b --arg-type float[] --return-type float[]",
    # A file that drops numpy: its section's blank line goes with it.
    "object n --no-step --state k:int:1",
    # A stub whose typing line names several: emitted `final,
    # AsyncIterator, Callable, Iterator`, sorted by the head builder.
    "object gen --arg-type void --return-type float --async-stream",
]


@pytest.fixture(scope="module")
def proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _scaffold(
        tmp_path_factory.mktemp("gh2571"),
        ["new p --pytest --pytest-benchmark", *CALLS],
    )


@pytest.fixture(scope="module")
def unittest_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("gh2571u"), ["new p", *CALLS])


#: The import head of each file as ``ruff check --select I --fix`` leaves
#: it, measured with jm's pinned ruff -- not with jm's own code.
HEADS = {
    "src/p/tests/test_w2.py": (
        "import numpy as np\nimport pytest\n\nfrom p import W2\n"
    ),
    "src/p/benchmarks/bench_w2.py": (
        "import numpy as np\nimport pytest\n\nfrom p import W2\n"
    ),
    "src/p/m/tests/test_fir.py": (
        "import numpy as np\nimport pytest\n\nfrom p.m import Fir\n"
    ),
    "src/p/gen.pyi": (
        "from typing import AsyncIterator, Callable, Iterator, final\n\n"
        "import numpy as np\n"
    ),
    "src/p/w2.pyi": (
        "from typing import final\n\nimport numpy as np\n"
        "import numpy.typing as npt\nfrom numpy.typing import NDArray\n"
    ),
    "src/p/m/m.pyi": (
        "from typing import final\n\nimport numpy as np\n"
        "import numpy.typing as npt\nfrom numpy.typing import NDArray\n"
    ),
    "src/p/__init__.py": (
        "from .b import B  # noqa: E402\n"
        "from .gen import Gen  # noqa: E402\n"
        "from .n import N  # noqa: E402\n"
        "from .w2 import W2  # noqa: E402\n"
        "from .w10 import W10  # noqa: E402\n"
    ),
    # Two-character capitals are CONSTANTS to isort, ahead of the classes.
    "src/p/st/__init__.py": (
        "from .st import S1, S2, S3, S4, S5, S10, S11, SV  # noqa: E402\n"
    ),
    "src/p/m/__init__.py": (
        "from .m import Fir, FirView, clamp  # noqa: E402\n"
    ),
}

UNITTEST_HEADS = {
    "src/p/tests/test_w2.py": (
        "import unittest\n\nimport numpy as np\n\nfrom p import W2\n"
    ),
    "src/p/benchmarks/bench_w2.py": (
        "import time\n\nimport numpy as np\n\nfrom p import W2\n"
    ),
    "src/p/tests/test_n.py": ("import unittest\n\nfrom p import N\n"),
    "src/p/st/tests/test_s2.py": (
        "import unittest\n\nimport numpy as np\n\nfrom p.st import S2\n"
    ),
}


@pytest.mark.parametrize("rel", sorted(HEADS))
def test_the_import_head_is_isorts(proj: Path, rel: str) -> None:
    text = (proj / rel).read_text(encoding="utf-8")
    assert HEADS[rel] in text, f"{rel}:\n{text[:700]}"


@pytest.mark.parametrize("rel", sorted(UNITTEST_HEADS))
def test_the_unittest_face_head_is_isorts(
    unittest_proj: Path, rel: str
) -> None:
    text = (unittest_proj / rel).read_text(encoding="utf-8")
    assert UNITTEST_HEADS[rel] in text, f"{rel}:\n{text[:700]}"


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.relative_to(root).parts
    }


def test_apply_puts_an_older_projects_re_exports_in_order(
    tmp_path: Path,
) -> None:
    """A project an older jm scaffolded -- its re-exports in the order its
    objects were added -- loses the finding on its next apply, and a
    second apply then changes nothing."""
    root = _scaffold(
        tmp_path, ["new p", f"object w2 {OBJ}", f"object b {OBJ}"]
    )
    init = root / "src" / "p" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    ordered = (
        "from .b import B  # noqa: E402\nfrom .w2 import W2  # noqa: E402\n"
    )
    assert ordered in text, text
    older = (
        "from .w2 import W2  # noqa: E402\nfrom .b import B  # noqa: E402\n"
    )
    init.write_text(text.replace(ordered, older), encoding="utf-8")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert init.read_text(encoding="utf-8") == text
    before = _tree(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    after = _tree(root)
    assert after == before, sorted(
        k
        for k in after.keys() | before.keys()
        if after.get(k) != before.get(k)
    )
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


#: An author's ``__init__.py``: their own imports -- relative, absolute, two
#: on one line in the "wrong" order, and one in exactly the shape jm writes
#: -- around which jm adds its re-exports.
AUTHOR = [
    '"""p: the author\'s package."""',
    "",
    "import json",
    "from .b import B; from .a import A",
    "from ._version import __version__  # noqa: E402",
    # jm's exact shape, and a name that sorts after jm's: still the
    # author's, so it stays where it is.
    "from .zz import Z  # noqa: E402",
    "",
    "DEFAULTS = json.loads('{}')",
    "",
]


def test_an_authors_lines_are_never_touched(tmp_path: Path) -> None:
    root = _scaffold(tmp_path, ["new p"])
    init = root / "src" / "p" / "__init__.py"
    init.write_text("\n".join(AUTHOR), encoding="utf-8")
    for argv in (
        shlex.split(f"object w10 {OBJ}"),
        shlex.split(f"object w2 {OBJ}"),
        ["apply"],
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    lines = init.read_text(encoding="utf-8").split("\n")
    authored = [ln for ln in AUTHOR if ln]
    # In the file, unchanged, and in the order the author wrote them.
    at = [lines.index(ln) for ln in authored]
    assert at == sorted(at), "\n".join(lines)
    # jm's re-exports go after the author's last import and are sorted
    # among themselves only: the author's `zz`, which sorts after them, is
    # not pulled into their block.
    assert (
        "from .zz import Z  # noqa: E402\n"
        "from .w2 import W2  # noqa: E402\n"
        "from .w10 import W10  # noqa: E402\n"
    ) in "\n".join(lines), "\n".join(lines)
