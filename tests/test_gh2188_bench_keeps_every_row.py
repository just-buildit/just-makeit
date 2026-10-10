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

Two more ways the header lost a row, or wrote one that was not a row, go
with it:

* a row of 0 rounds made `jm_bench_write_json` read its order statistics
  out of an empty array. `jm_bench_add` now refuses one, on stderr and with
  a non-zero exit, through the same `jm_bench_fail` an allocation failure
  takes;
* nothing freed the rows, so a benchmark built with ``-fsanitize=address``
  exited with every row leaked. `jm_bench_write_json` frees them once the
  file is written. The driver counts the header's own allocations by
  macro, so the check holds on every platform the suite runs on, not only
  where LeakSanitizer does.
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

#: Records argv[1] rows, row k timed at (k + 1) ms for each of argv[2]
#: rounds (default 3), the way a generated benchmark does: `= {0}`,
#: `jm_bench_add`, then `jm_bench_write_json`. Then it prints what the header
#: left allocated and what the bench holds.
#:
#: The allocation count is the header's own: `realloc` and `free` are macros
#: around the real ones for the header alone. Every header `jm_bench.h`
#: includes is included first, so no system prototype is rewritten.
DRIVER = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/utsname.h>

static long live = 0;

static void *
count_realloc(void *p, size_t n)
{
    void *q = realloc(p, n);
    if (q && !p)
        live++;
    return q;
}

static void
count_free(void *p)
{
    if (p)
        live--;
    free(p);
}

#define realloc(p, n) count_realloc((p), (n))
#define free(p) count_free(p)
#include "jm_bench.h"
#undef realloc
#undef free

int
main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 0;
    int rounds = argc > 2 ? atoi(argv[2]) : 3;
    jm_bench_t _bench = {0};
    for (int k = 0; k < n; k++) {
        char name[32];
        double t[3];
        snprintf(name, sizeof(name), "row_%04d", k);
        for (int r = 0; r < 3; r++)
            t[r] = (double)(k + 1) * 1e-3;
        jm_bench_add(&_bench, name, t, rounds, 10);
    }
    jm_bench_write_json(&_bench, "many");
    printf("after write: live=%ld count=%d cap=%d entries=%s\n", live,
           _bench.count, _bench.cap, _bench.entries ? "set" : "NULL");
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


@_needs_cc
@pytest.mark.parametrize("rounds", [0, -1])
def test_a_row_without_rounds_is_refused(driver, tmp_path, rounds):
    """Not written with a garbage minimum, and not dropped: the run stops,
    naming the row, before any JSON exists."""
    done = subprocess.run(
        [str(driver), "2", str(rounds)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0, (
        f"a row of {rounds} rounds was accepted:\n{done.stdout}"
    )
    assert 'jm_bench: row "row_0000": rounds must be at least 1' in (
        done.stderr
    ), done.stderr
    assert not (tmp_path / "bench_many_core.json").exists()


@_needs_cc
@pytest.mark.parametrize("rows", [0, 1, 33, 1000])
def test_writing_the_json_frees_every_row(driver, tmp_path, rows):
    """What a LeakSanitizer build would report at exit, counted directly:
    every allocation the header made is freed, and the bench is left as
    `= {0}` made it."""
    done = subprocess.run(
        [str(driver), str(rows)], cwd=tmp_path, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "after write: live=0 count=0 cap=0 entries=NULL" in done.stdout, (
        done.stdout
    )
