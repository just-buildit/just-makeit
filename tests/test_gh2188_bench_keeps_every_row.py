"""Every row a benchmark records reaches its JSON (gh-2188).

`jm_bench.h` held its rows in a fixed array of 32, and `jm_bench_add`
returned without a word once it was full. A benchmark recording more ran
every row, printed every row (doppler's wrapper prints before it adds),
exited 0 and wrote a well-formed JSON missing everything past the 32nd.
`jm bench`, `bench-compare` and a published snapshot then saw fewer rows than
the binary measured. Found in doppler, where a 39-row spectrogram benchmark
recorded 32, caught only by a per-name check against the expected list.

The rows are now a heap array the header grows, so there is no cap to
overflow. These tests compile a driver against the header jm vendors and
read back the JSON it writes: the claim is about what reaches the file, so
the file is what is checked. Each row carries times of its own, so a row
that arrived with another row's data -- a grow that copied the wrong thing
-- fails as surely as a row that did not arrive.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc

HEADER_DIR = Path(__file__).parent.parent / "src/just_makeit/templates/c/inc"

_CC = default_cc()
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

#: Records argv[1] rows, row k timed at (k + 1) ms for each of 3 rounds, the
#: way a generated benchmark does: `= {0}`, `jm_bench_add`, then
#: `jm_bench_write_json`.
DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include "jm_bench.h"

int
main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 0;
    jm_bench_t _bench = {0};
    for (int k = 0; k < n; k++) {
        char name[32];
        double t[3];
        snprintf(name, sizeof(name), "row_%04d", k);
        for (int r = 0; r < 3; r++)
            t[r] = (double)(k + 1) * 1e-3;
        jm_bench_add(&_bench, name, t, 3, 10);
    }
    jm_bench_write_json(&_bench, "many");
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """The driver, built warning-clean as a `-Werror` project builds it."""
    d = tmp_path_factory.mktemp("gh2188")
    src = d / "driver.c"
    src.write_text(DRIVER, encoding="utf-8")
    exe = d / "driver"
    r = subprocess.run(
        [
            _CC,
            "-std=gnu99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(HEADER_DIR),
            "-o",
            str(exe),
            str(src),
            "-lm",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return exe


@_needs_cc
@pytest.mark.parametrize(
    "rows",
    [
        1,
        32,  # the old cap, which still fit
        33,  # the first row the old cap dropped
        1000,  # several doublings past the first allocation
    ],
)
def test_every_recorded_row_reaches_the_json(driver, tmp_path, rows):
    done = subprocess.run(
        [str(driver), str(rows)], cwd=tmp_path, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr

    benchmarks = json.loads(
        (tmp_path / "bench_many_core.json").read_text(encoding="utf-8")
    )["benchmarks"]
    names = [b["name"] for b in benchmarks]
    assert names == [f"row_{k:04d}" for k in range(rows)], (
        f"recorded {rows} rows, the JSON holds {len(names)}"
    )
    for k, b in enumerate(benchmarks):
        assert b["stats"]["mean"] == pytest.approx((k + 1) * 1e-3), (
            f"{b['name']} carries another row's times: {b['stats']}"
        )
        assert b["stats"]["rounds"] == 3
        assert b["stats"]["iterations"] == 10
