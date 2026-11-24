"""gh-2755: mypy --strict reads a module function's record return.

The mypy face of ``test_gh2755_function_single_record.py``. Split out
because it needs mypy, a dev group tool, so it runs on the
PROJECT_ENV_TESTS path (Makefile), as ``test_gh2736_enum_stub_mypy.py``
does; mypy is imported unconditionally, so an absent one fails loudly.

A use of the function is type-checked against the generated stub: the
result is the declared record class, each field reads as ``float``,
unpacking gives four floats, and a field the record does not have is an
error -- so the annotation is the record, not ``Any``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from mypy import api as mypy_api

sys.path.insert(0, str(Path(__file__).parent))

from test_gh2755_function_single_record import scene_project  # noqa: E402

_ERROR = re.compile(r"^(.*?):(\d+): error:")

USE = """\
from p.ccsds import Ccsds415Scene, ccsds415_scene

r = ccsds415_scene(1.0, 2.0, start_hz=3.0)
s: Ccsds415Scene = r
x: float = r.chip_rate
a, b, c, d = r
y: float = a + b + c + d
z = r.no_such_field
"""

#: The one line mypy must refuse: a field the record does not declare.
REFUSED = {8}


def test_the_record_return_type_checks(tmp_path, monkeypatch):
    root, err = scene_project(tmp_path)
    assert not err, err
    use = tmp_path / "use.py"
    use.write_text(USE, encoding="utf-8")
    monkeypatch.setenv("MYPYPATH", str(root / "src"))
    out, err, _ = mypy_api.run(
        [
            "--strict",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(use),
        ]
    )
    # Armed: mypy read the files; a usage error prints neither line.
    assert "Success" in out or "Found" in out, out + err
    errs = [m.groups() for m in map(_ERROR.match, out.splitlines()) if m]
    # A stub mypy cannot read fails in the STUB, which would read as the
    # use being refused.
    assert all(path.endswith("use.py") for path, _ in errs), out
    assert {int(line) for _, line in errs} == REFUSED, out
    assert '"Ccsds415Scene" has no attribute "no_such_field"' in out, out
