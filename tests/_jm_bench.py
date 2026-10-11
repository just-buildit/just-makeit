"""Test helper: the C benchmark jm renders for a component, as it stands.

gh-1987: ``native/benchmarks/bench_<comp>_core.c`` is the author's, so no
member verb rewrites it -- `jm method` used to, and that is how a test read
"the bench for an object with this method". The one render left that sees
every declared member is the bench `apply` materialises when the file is
missing, so that is what a test asking about jm's bench render reads.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path


def materialize_bench(root: Path, comp: str) -> str:
    """Delete *comp*'s C benchmark, `apply`, and return the one it wrote.

    The project at *root* is left with the freshly materialised file, so a
    fixture may go on to compile or scan it.
    """
    from just_makeit._apply import run as apply_run

    bench = root / "native" / "benchmarks" / f"bench_{comp}_core.c"
    assert bench.is_file(), f"no benchmark to re-materialise: {bench}"
    bench.unlink()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        apply_run(root)
    assert bench.is_file(), (
        f"apply did not materialise {bench}:\n{buf.getvalue()}"
    )
    return bench.read_text(encoding="utf-8")
