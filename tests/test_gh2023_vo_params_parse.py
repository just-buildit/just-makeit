"""gh-2023: a variable_output method's params parse as every method's do.

A ``variable_output`` method with params and no input parsed them with its
own loop, a second copy of ``_context/_parse._build_params_parse`` that
knew scalars and plain arrays only. It looked a param's format char up in
the type table and fell back to ``d`` for anything else: a ``path`` param
became ``path p = 0;`` and did not compile, and an ``enum`` param was
parsed as its ``int`` while both stubs said ``str``, so the choice string
the stub advertised was refused. gh-2220 then found the same loop parsing a
``size_t`` with the masking ``K`` after the one tuple-parse slot had
dropped it. The params now go through ``_build_params_parse``, with
``out=`` as its trailing argument (gh-2028), as the shape with an input
has since gh-1960.

GATE: the shape's binding parses an ``enum`` param as its choice string, a
``path`` through ``PyUnicode_FSConverter``, a ``bytes`` as ``y#`` and a
``size_t`` as an object; built ``-Werror``, each is called as its stub says.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: One method per param kind the copy did not know, on the shape it owned:
#: ``variable_output``, params, no input.
FRAGMENT = """
[[enum]]
name = "mode"
values = ["a", "b"]

[e]
arg_type = "void"
return_type = "void"
no_step = "true"

[[e.methods]]
name = "pick"
arg_type = "void"
return_type = "float"
variable_output = true
max_out = 4
params = [{name = "m", type = "int", enum = "mode"}]

[[e.methods]]
name = "rd"
arg_type = "void"
return_type = "float"
variable_output = true
max_out = 4
params = [{name = "p", type = "path"}]

[[e.methods]]
name = "by"
arg_type = "void"
return_type = "float"
variable_output = true
max_out = 4
params = [
  {name = "b", type = "bytes"},
  {name = "n", type = "size_t", default = "2"},
]
"""

#: ``method: the PyArg format its binding must parse with``. ``out=`` is
#: the trailing optional ``O`` on each.
FORMATS = {"pick": "s|O", "rd": "O&|O", "by": "y#|OO"}


def _project(root: Path) -> Path:
    assert run_cli("new", "t", cwd=root).returncode == 0
    proj = root / "t"
    frag = root / "e.toml"
    frag.write_text(FRAGMENT, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _wrapper(ext: str, method: str) -> str:
    """The body of the generated ``E_<method>`` wrapper."""
    m = re.search(rf"^E_{method}\(.*?^\}}", ext, re.M | re.S)
    assert m, f"no E_{method} wrapper"
    return m.group(0)


def test_each_param_kind_parses_as_its_stub_says(tmp_path):
    ext = (_project(tmp_path) / "native" / "src" / "e" / "e_ext.c").read_text(
        "utf-8"
    )
    for method, fmt in FORMATS.items():
        body = _wrapper(ext, method)
        got = re.search(
            r'PyArg_ParseTupleAndKeywords\(args, kwds, "([^"]*)"', body
        )
        assert got and got.group(1) == fmt, (method, got and got.group(1))
    # The choice string is validated to its `[[enum]]` int, and the path
    # converted -- not an `int` or a `double` parsed in their place.
    assert "_enum_index_E" in _wrapper(ext, "pick")
    assert "PyUnicode_FSConverter" in _wrapper(ext, "rd")


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_shape_builds_and_takes_what_its_stub_says(tmp_path):
    proj = _project(tmp_path)
    for cmd in (
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        ["cmake", "--build", "build", "--parallel", "4"],
    ):
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])
    driver = proj / "drive_gh2023.py"
    driver.write_text(
        "import sys\n"
        "import numpy as np\n"
        "sys.path.insert(0, 'src')\n"
        "import t\n"
        "e = t.E()\n"
        "out = []\n"
        "for call in ('e.pick(\"a\")', 'e.rd(\".\")', 'e.by(b\"ab\")',\n"
        "             'e.by(b\"ab\", n=np.uint64(3))', 'e.pick(\"zz\")',\n"
        "             'e.by(b\"ab\", -1)'):\n"
        "    try:\n"
        "        out.append(type(eval(call)).__name__)\n"
        "    except Exception as x:\n"
        "        out.append(type(x).__name__)\n"
        "print(' '.join(out))\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.split() == [
        "ndarray",
        "ndarray",
        "ndarray",
        "ndarray",
        "ValueError",
        "OverflowError",
    ], r.stdout
