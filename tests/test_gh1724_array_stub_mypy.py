"""gh-1724: mypy reads each array parameter as its declared type.

Split from ``test_gh1724_array_init_stub.py`` because it needs mypy, a dev
group tool, so it runs on the PROJECT_ENV_TESTS path (Makefile, gh-1442),
where the dev group is visible. Behind a skipif in the isolated env it
skipped in CI and ran only on laptops, which the suite's skip gate refuses.
mypy is imported unconditionally: absent, this fails loudly rather than
skipping.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from mypy import api as mypy_api  # noqa: F401  (the dependency, stated)

sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _stubs  # noqa: E402
from just_makeit._context import _build_no_state_init_ctx  # noqa: E402
import pytest  # noqa: E402
from test_gh1724_array_init_stub import (  # noqa: E402
    _SHAPES,
    _cfg,
    build_project,
)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """The every-face project the sibling module builds, built here once."""
    return build_project(tmp_path_factory.mktemp("gh1724mypy"))


# -- the type checker, on the generated stubs --------------------------------


def _mypy(project: Path, use: str, tmp_path: Path) -> list[int]:
    """Line numbers mypy reports an error on, for *use* against *project*."""
    from mypy import api

    script = tmp_path / "use.py"
    script.write_text(use, encoding="utf-8")
    out, err, _ = api.run(
        [
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(script),
        ]
    )
    errs = [ln for ln in out.splitlines() if ": error:" in ln]
    # Armed: mypy ran and type-checked the file; a usage error or a crash
    # reports no `error:` line, which would read as every call passing.
    assert "Success" in out or "Found" in out, out + err
    # A stub mypy cannot read fails as an error in the STUB, not in use.py,
    # and would read as the call being refused.
    assert all(e.split(":")[0].endswith("use.py") for e in errs), out + err
    return sorted({int(e.split(":")[1]) for e in errs})


# (call, mypy must refuse it)
_CALLS = [
    # The declared ndarray is accepted on every face.
    ("Fld(f32, f32, f64x2, bits=u8)", False),
    ("Fld(f32, f32, f64x2).peek(f32)", False),
    ("Fld(f32, f32, f64x2).peek8(u8)", False),
    ("Fld(f32, f32, f64x2).fill(u8, u8)", False),
    ("Fld(f32, f32, f64x2).drain(f32, out=f32)", False),
    ("Opt(bank=f32x2)", False),
    ("Opt()", False),
    ("Acc().steps(f32, out=f32)", False),
    ("Acc().set_h(f32)", False),
    ("acc.buf = f32", False),
    ("Cblk().steps(f32, out=f32, gain=1.0)", False),
    ("Cacc().steps(f32, out=f32, gain=1.0)", False),
    ("Acc8().steps(u8, out=u8)", False),
    ("Blk().steps(f32, out=f32)", False),
    ("Arr().step(u8)", False),
    ("Mfld(f32, f32, f64x2, bits=u8).peek(f32)", False),
    ("Macc8().steps(u8)", False),
    ("peekf(f32)", False),
    ("tobin(u8, u8)", False),
    # A byte INPUT also takes the byte buffers jm reads (gh-1700) ...
    ('Fld(f32, f32, f64x2, bits=b"\\x01")', False),
    ('Fld(f32, f32, f64x2).peek8(bytearray(b"\\x01"))', False),
    ('Acc8().steps(b"\\x01")', False),
    ('Macc8().steps(b"\\x01")', False),
    ('Arr().step(memoryview(b"\\x01"))', False),
    # ... and a writable byte array does not (gh-1733).
    ('Fld(f32, f32, f64x2).fill(u8, b"\\x00")', True),
    ('tobin(u8, b"\\x00")', True),
    ('Acc8().steps(u8, out=bytearray(b"\\x00"))', True),
    # A list is refused on an input, on every face: the stub states the
    # declared ndarray, deliberately narrower than the runtime.
    ("Fld([1.0], f32, f64x2)", True),
    ("Fld(f32, [1.0], f64x2)", True),
    ("Fld(f32, f32, [[1.0]])", True),
    ("Fld(f32, f32, f64x2, bits=[1])", True),
    ("Fld(f32, f32, f64x2).peek([1.0])", True),
    ("Fld(f32, f32, f64x2).drain([1.0])", True),
    ("Opt(bank=[[1.0]])", True),
    ("Acc().steps([1.0])", True),
    ("Acc().set_h([1.0])", True),
    ("acc.buf = [1.0]", True),
    ("Cblk().steps([1.0])", True),
    ("Cacc().steps([1.0])", True),
    ("Blk().steps([1.0])", True),
    ("Arr().step([1])", True),
    ("Mfld([1.0], f32, f64x2)", True),
    ("peekf([1.0])", True),
    ("tobin([1], u8)", True),
]

_PRELUDE = """\
import numpy as np
import numpy.typing as npt
from jmp import Acc, Acc8, Arr, Blk, Cacc, Cblk, Fld, Opt
from jmp.m import Macc8, Mfld, peekf, tobin

f32: npt.NDArray[np.float32] = np.zeros(2, np.float32)
f32x2: npt.NDArray[np.float32] = np.zeros((2, 2), np.float32)
f64x2: npt.NDArray[np.float64] = np.zeros((2, 2), np.float64)
u8: npt.NDArray[np.uint8] = np.zeros(2, np.uint8)
acc = Acc()
"""


def test_mypy_reads_the_declaration_on_every_face(
    project, tmp_path, monkeypatch
):
    monkeypatch.setenv("MYPYPATH", str(project / "src"))
    first = _PRELUDE.count("\n") + 1
    use = _PRELUDE + "".join(f"{call}\n" for call, _ in _CALLS)
    refused = {first + i for i, (_, bad) in enumerate(_CALLS) if bad}
    got = _mypy(project, use, tmp_path)
    lines = use.splitlines()
    assert set(got) == refused, {
        "refused but should pass": [lines[n - 1] for n in set(got) - refused],
        "passed but should be refused": [
            lines[n - 1] for n in refused - set(got)
        ],
    }


def test_mypy_accepts_either_dispatch_dtype(tmp_path, monkeypatch):
    """A dtype-dispatch array names both declared dtypes, each exactly."""
    ips, aa, _, _ = _SHAPES["dtype dispatch"]
    cfg = _cfg(ips, aa)
    pkg = tmp_path / "stubpkg"
    pkg.mkdir()
    for gen, stub in (
        ("module", _stubs._obj_stub(cfg, "obj", pkg="p", module="m")),
        (
            "standalone",
            "class Obj:\n    def __init__(self, "
            + _build_no_state_init_ctx(
                "obj", "Obj", C.init_params(cfg, "obj"), csym="obj"
            )["init_params_pyi"]
            + ") -> None: ...\n",
        ),
    ):
        stub = "from typing import Any, final\n" + textwrap.dedent(stub)
        stub = "\n".join(_stubs.numpy_imports(stub)) + "\n" + stub
        (pkg / f"{gen}.pyi").write_text(stub, encoding="utf-8")
    use = (
        "import numpy as np\n"
        "import numpy.typing as npt\n"
        "from stubpkg.module import Obj as M\n"
        "from stubpkg.standalone import Obj as S\n"
        "c: npt.NDArray[np.complex64] = np.zeros(2, np.complex64)\n"
        "r: npt.NDArray[np.float32] = np.zeros(2, np.float32)\n"
        "M(c); M(r); S(c); S(r)\n"
        "M([1.0])\n"
        "S([1.0])\n"
    )
    monkeypatch.setenv("MYPYPATH", str(tmp_path))
    assert _mypy(tmp_path, use, tmp_path) == [8, 9]
