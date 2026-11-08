"""gh-2571: every scaffolded Python file has its imports in isort order.

doppler selects ruff's isort rule (``I``), and a fresh ``test_<obj>.py``
failed it on ``I001``: ``import unittest``, ``import numpy as np`` and
``from <pkg> import X`` in one block. The class was wider -- the benchmark,
both ``.pyi`` stubs and both re-export ``__init__.py`` files too -- and one
post-pass, :func:`_pyfmt.sort_imports`, now lays out every one of them.

The ruff verdict itself is ``tests/test_gh1478_ruff_clean_scaffold.py``,
which selects ``I``; it needs the project environment (``make
test-examples``). This file is the half ``make test`` runs: the import heads
ruff's own ``--fix`` produced, asserted literally, and every generated file a
fixed point of the pass, so an emitter that stops calling it goes red here.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sysconfig
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _pyfmt

TEMPLATES = Path(_pyfmt.__file__).parent / "templates" / "py"


def _is_stdlib(name: str) -> bool:
    """Whether top-level module *name* ships with the interpreter.

    Asked of the import system rather than ``sys.stdlib_module_names``,
    which 3.9 -- a version jm supports -- does not have.
    """
    spec = importlib.util.find_spec(name)
    if spec is None:
        return False
    origin = spec.origin or ""
    if origin in ("built-in", "frozen"):
        return True
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    return stdlib in Path(origin).resolve().parents


def test_the_stdlib_set_is_the_stdlib() -> None:
    """A name in :data:`_pyfmt._STDLIB` that is not stdlib would put a
    third-party import in the wrong section."""
    wrong = sorted(n for n in _pyfmt._STDLIB if not _is_stdlib(n))
    assert not wrong, wrong


def test_every_stdlib_import_a_template_writes_is_listed() -> None:
    """Registration-free over the templates: a template that starts
    importing another stdlib module needs it in the set, or the pass files
    it as third-party."""
    used: "dict[str, str]" = {}
    pat = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", re.MULTILINE)
    for tmpl in sorted(TEMPLATES.glob("*.py")):
        for root in pat.findall(tmpl.read_text(encoding="utf-8")):
            if root != "__future__" and _is_stdlib(root):
                used.setdefault(root, tmpl.name)
    assert used, "the scan found no stdlib import: it is reading nothing"
    missing = {r: f for r, f in used.items() if r not in _pyfmt._STDLIB}
    assert not missing, missing


#: doppler's shape (a module object with a view and init params), a
#: standalone object in both test faces, and a second standalone added
#: after the first, whose re-export sorts ABOVE it.
CALLS = [
    "new p --pytest --pytest-benchmark",
    "object g --state on:bool:true --arg-type float --return-type float",
    "object b --arg-type float[] --return-type float[]",
    "module m",
    "object fir --module m --arg-type float --return-type float"
    " --init-param n:size_t:16 --init-param gain:double:1.0",
    "object bq --module m --no-step --state k:int:1",
    "function clamp --module m",
    "view fir FirView --module m --create-fn fir_create_view"
    " --init-param scale:double:2.0",
]


@pytest.fixture(scope="module")
def proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2571")
    first, *rest = (shlex.split(c) for c in CALLS)
    r = run_cli(*first, cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp / "p"
    for argv in rest:
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, f"jm {' '.join(argv)}\n{r.stdout}{r.stderr}"
    return root


#: The import head of each file as ``ruff check --select I --fix`` leaves
#: it, measured with jm's pinned ruff -- not with the pass under test.
HEADS = {
    "src/p/tests/test_g.py": (
        "import numpy as np\nimport pytest\n\nfrom p import G\n"
    ),
    "src/p/benchmarks/bench_g.py": (
        "import numpy as np\nimport pytest\n\nfrom p import G\n"
    ),
    "src/p/m/tests/test_fir.py": (
        "import numpy as np\nimport pytest\n\nfrom p.m import Fir\n"
    ),
    "src/p/g.pyi": (
        "from typing import final\n\nimport numpy as np\n"
        "import numpy.typing as npt\nfrom numpy.typing import NDArray\n"
    ),
    "src/p/m/m.pyi": (
        "from typing import final\n\nimport numpy as np\n"
        "import numpy.typing as npt\nfrom numpy.typing import NDArray\n"
    ),
    "src/p/__init__.py": (
        "from .b import B  # noqa: E402\nfrom .g import G  # noqa: E402\n"
    ),
    "src/p/m/__init__.py": (
        "from .m import Bq, Fir, FirView, clamp  # noqa: E402\n"
    ),
}


@pytest.mark.parametrize("rel", sorted(HEADS))
def test_the_import_head_is_isorts(proj: Path, rel: str) -> None:
    text = (proj / rel).read_text(encoding="utf-8")
    assert HEADS[rel] in text, f"{rel}:\n{text[:600]}"


def test_every_generated_file_is_a_fixed_point(proj: Path) -> None:
    """The pass changes nothing jm wrote: every emitter already called it.

    The ``__init__.py`` files are merge targets, so jm sorts only their
    relative re-exports (``relative_only``); every other file is jm's
    whole.
    """
    files = sorted(proj.rglob("*.py")) + sorted(proj.rglob("*.pyi"))
    assert len(files) > 10, files
    stale = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        init = f.name == "__init__.py"
        if _pyfmt.sort_imports(text, "p", relative_only=init) != text:
            stale.append(f.relative_to(proj).as_posix())
    assert not stale, stale
