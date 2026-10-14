"""Every benchmark row states what it counts (gh-2282).

`jm_bench.h` wrote a row's `iters` and its per-ROUND times and nothing else:
its header comment said `ops` was samples per second while `iters` was
documented as calls per round, `extra_info` was a literal `{}`, and a reader
taking `mean` for a time per call was off by a factor of `iters`. doppler
published exactly that, and a private `MSa_s` line it had added to its copy
was lost to a re-vendor for 25 releases. `jm bench` labelled every row's
throughput `Sa/s` whatever it counted.

Now a row names its unit as a word, `jm_bench_add_unit(..., "bin")`: the
header defines the words jm's generated benchmarks use (`JM_BENCH_SAMPLE`,
...), and a project passes its own without editing the vendored header --
which is what doppler#2314 was. One function, `jm_bench_rate_label`, names
the rate; the JSON states both and `jm bench` prints what it reads. These
tests read the header's unit constants, build a driver recording one row of
EVERY one of them, rows of units the header has never heard of, and one
through the old five-argument `jm_bench_add`, and check the JSON it writes
against the timings the driver fed in -- so a unit added to the header is
checked without touching this file, and the claim is checked where it
lands: in the file.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc
from _jm_bench import materialize_bench
from _jmrun import run_cli

from just_makeit import _bench as B

HEADER_DIR = Path(__file__).parent.parent / "src/just_makeit/templates/c/inc"
HEADER = HEADER_DIR / "jm_bench.h"

_CC = default_cc()
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

#: Every row's per-round times, in milliseconds, scaled by its index: an
#: asymmetric set, so min, median and mean are three different numbers and
#: a per-unit time computed from the wrong one is caught.
_ROUND_MS = (1.0, 2.0, 6.0)
_ITERS = 1000


#: Units a project counts that jm's header does not define, passed from
#: the driver's own source as a downstream's benchmark passes them: doppler
#: counts FFT bins and filter taps (gh-2282 review).
_PROJECT_UNITS = ("bin", "tap")

#: The rate label each unit must carry, spelled here rather than derived, so
#: a wrong rule in the header is caught; a header constant added later
#: falls back to the documented "<unit>s/s".
_RATE = {
    "sample": "Sa/s",
    "call": "calls/s",
    "frame": "frames/s",
    "bin": "bins/s",
    "tap": "taps/s",
}


def _rate(unit: str) -> str:
    return _RATE.get(unit, f"{unit}s/s")


def _header_units() -> list[tuple[str, str]]:
    """``(constant, word)`` for each unit constant the header defines."""
    text = HEADER.read_text(encoding="utf-8")
    block = text[
        text.index("/* gh-2282: what one of a row's") : text.index(
            "#define JM_BENCH_UNIT_LEN"
        )
    ]
    found = re.findall(r'#define (JM_BENCH_\w+)\s+"([a-z0-9_]+)"', block)
    assert len(found) >= 3, f"unit constants not where expected:\n{block}"
    return found


def _units() -> list[tuple[str, str]]:
    """``(C expression, word)`` for every row the driver records with a
    unit: the header's constants, then the project's literals."""
    return _header_units() + [(f'"{u}"', u) for u in _PROJECT_UNITS]


def _driver(units: list[tuple[str, str]]) -> str:
    """A benchmark recording row k of unit k, and one legacy row last."""
    rows = []
    for k, (expr, name) in enumerate(units + [("", "legacy")]):
        times = ", ".join(f"{(k + 1) * ms * 1e-3!r}" for ms in _ROUND_MS)
        add = (
            f'jm_bench_add_unit(&b, "{name}", t{k}, 3, {_ITERS}, {expr});'
            if expr
            else f'jm_bench_add(&b, "{name}", t{k}, 3, {_ITERS});'
        )
        rows.append(f"    double t{k}[3] = {{{times}}};\n    {add}")
    body = "\n".join(rows)
    return (
        '#include "jm_bench.h"\n\n'
        "int\nmain(void)\n{\n"
        "    jm_bench_t b = {0};\n"
        f"{body}\n"
        '    jm_bench_write_json(&b, "units");\n'
        "    return 0;\n}\n"
    )


def _build(d: Path, source: str) -> Path:
    """*source* compiled against the vendored header, as a `-Werror`
    project builds its benchmarks."""
    src = d / "driver.c"
    src.write_text(source, encoding="utf-8")
    exe = d / "driver"
    built = subprocess.run(
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
    assert built.returncode == 0, built.stdout + built.stderr
    return exe


@pytest.fixture(scope="module")
def written(tmp_path_factory) -> list[dict]:
    """The rows the driver's JSON holds."""
    d = tmp_path_factory.mktemp("gh2282")
    exe = _build(d, _driver(_units()))
    ran = subprocess.run([str(exe)], cwd=d, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    data = json.loads((d / "bench_units_core.json").read_text("utf-8"))
    return data["benchmarks"]


def _expected(k: int) -> dict[str, float]:
    """Row k's per-round statistics, from the times the driver fed it."""
    ms = sorted((k + 1) * m * 1e-3 for m in _ROUND_MS)
    return {"min": ms[0], "median": ms[1], "mean": sum(ms) / len(ms)}


@_needs_cc
def test_every_row_names_its_unit(written):
    units = _units()
    assert [b["name"] for b in written] == [n for _, n in units] + ["legacy"]
    for b, (_, name) in zip(written, units):
        assert b["extra_info"]["unit"] == name, b["extra_info"]
        assert b["extra_info"]["rate_unit"] == _rate(name), b["extra_info"]


@_needs_cc
def test_a_project_unit_needs_no_header_edit(tmp_path):
    """A unit the header never defines, passed from the benchmark's own
    source, is written like any other: named, labelled, timed per unit.

    Its own driver, of project units alone, so a header that refused one --
    a closed set again -- fails HERE, naming the run, rather than erroring
    every test that shares the module fixture."""
    units = [(f'"{u}"', u) for u in _PROJECT_UNITS]
    exe = _build(tmp_path, _driver(units))
    ran = subprocess.run(
        [str(exe)], cwd=tmp_path, capture_output=True, text=True
    )
    assert ran.returncode == 0, f"a project unit was refused:\n{ran.stderr}"
    rows = json.loads((tmp_path / "bench_units_core.json").read_text("utf-8"))[
        "benchmarks"
    ]
    assert [b["name"] for b in rows] == [*_PROJECT_UNITS, "legacy"]
    for k, name in enumerate(_PROJECT_UNITS):
        b = rows[k]
        assert b["extra_info"] == {"unit": name, "rate_unit": _rate(name)}
        for key, per_round in _expected(k).items():
            assert b["stats"][f"{key}_per_unit"] == pytest.approx(
                per_round / _ITERS
            ), (name, key)


@_needs_cc
def test_the_five_argument_call_records_calls(written):
    """The old `jm_bench_add` keeps compiling, and means what `iters` was
    always documented to count."""
    legacy = written[-1]
    assert legacy["extra_info"] == {"unit": "call", "rate_unit": "calls/s"}


#: Units the header must refuse, and why: a unit the JSON could not carry
#: as written, or one that truncating would relabel.
_BAD_UNITS = {
    '""': "unit must be 1 to 31 characters",
    '"' + "u" * 32 + '"': "unit must be 1 to 31 characters",
    "NULL": "unit must be 1 to 31 characters",
    '"Bins"': "unit must be [a-z0-9_]",
    '"bin\\"s"': "unit must be [a-z0-9_]",
}


@_needs_cc
@pytest.mark.parametrize("expr", sorted(_BAD_UNITS))
def test_a_unit_the_json_cannot_carry_is_refused(tmp_path, expr):
    exe = _build(tmp_path, _driver([(expr, "bad")]))
    ran = subprocess.run(
        [str(exe)], cwd=tmp_path, capture_output=True, text=True
    )
    assert ran.returncode != 0, ran.stdout
    assert f'jm_bench: row "bad": {_BAD_UNITS[expr]}' in ran.stderr
    assert not (tmp_path / "bench_units_core.json").exists()


@_needs_cc
def test_per_unit_times_and_rates_match_the_timings(written):
    for k, b in enumerate(written):
        s = b["stats"]
        want = _expected(k)
        for key, per_round in want.items():
            assert s[key] == pytest.approx(per_round), (b["name"], key)
            assert s[f"{key}_per_unit"] == pytest.approx(per_round / _ITERS), (
                b["name"],
                key,
            )
        assert s["ops"] == pytest.approx(_ITERS / want["mean"]), b["name"]
        if b["extra_info"]["unit"] == "sample":
            assert b["extra_info"]["MSa_s"] == pytest.approx(
                _ITERS / want["mean"] / 1e6
            ), b["extra_info"]
        else:
            assert "MSa_s" not in b["extra_info"], b["extra_info"]


#: The keys a row carried before gh-2282, which a downstream reads: the
#: new JSON may add beside them and never rename or drop one.
_ROW_KEYS = {
    "group",
    "name",
    "fullname",
    "params",
    "param",
    "extra_info",
    "options",
    "stats",
}
_STATS_KEYS = {
    "min",
    "max",
    "mean",
    "stddev",
    "rounds",
    "median",
    "iqr",
    "q1",
    "q3",
    "iqr_outliers",
    "stddev_outliers",
    "outliers",
    "ld15iqr",
    "hd15iqr",
    "ops",
    "total",
    "iterations",
}


@_needs_cc
def test_the_json_is_a_superset_of_what_it_was(written):
    for b in written:
        assert _ROW_KEYS <= set(b), sorted(_ROW_KEYS - set(b))
        assert _STATS_KEYS <= set(b["stats"]), sorted(
            _STATS_KEYS - set(b["stats"])
        )
        assert b["stats"]["iterations"] == _ITERS


@_needs_cc
def test_the_report_labels_each_row_by_its_unit(written, capsys):
    """`jm bench`'s table: the times are per the row's unit, the
    throughput carries the row's own rate label, and nothing is `Sa/s`
    that does not count samples."""
    B._display_table("C", {"benchmarks": written}, None, c_side=True)
    out = capsys.readouterr().out
    lines = {
        b["name"]: next(
            ln for ln in out.splitlines() if f" {b['name']} " in ln
        )
        for b in written
    }
    for b in written:
        line = lines[b["name"]]
        unit = b["extra_info"]["unit"]
        rate = b["extra_info"]["rate_unit"]
        assert f"  {unit}  " in line, line
        assert line.rstrip().endswith(rate), line
        if unit != "sample":
            assert "Sa/s" not in line, line
    header = next(ln for ln in out.splitlines() if "Throughput" in ln)
    assert "Per" in header, header


def test_a_row_naming_no_unit_says_what_its_time_covers(capsys):
    """An older header's C row times a round of a count it never named,
    and a pytest-benchmark row is per call: neither is labelled Sa/s."""
    stats = {"min": 1e-3, "max": 1e-3, "mean": 1e-3, "stddev": 0.0}
    stats |= {"median": 1e-3, "ops": 2e6}
    old = {"benchmarks": [{"name": "old", "extra_info": {}, "stats": stats}]}
    B._display_table("C", old, None, c_side=True)
    B._display_table("Python", old, None, c_side=False)
    c_out, py_out = capsys.readouterr().out.split("=== Python")
    c_row = next(ln for ln in c_out.splitlines() if " old " in ln)
    py_row = next(ln for ln in py_out.splitlines() if " old " in ln)
    assert "  round  " in c_row and c_row.rstrip().endswith(" Mops/s")
    assert "  call  " in py_row and py_row.rstrip().endswith(" Mcalls/s")


#: A method with no input returning records (gh-244): its benchmark makes
#: ONE call a round. Declared in the manifest, the only way to declare one.
_RECORDS_METHOD = """
[[gain.methods]]
name = "events"
arg_type = "void"
return_type = "gain_event_t"

[[gain.methods.result_fields]]
name = "n"
type = "int"
"""

#: The other shapes the method bench times over a BENCH_N-element block,
#: each its own branch of `_bench_method_block`: a batch method, an array
#: input, and records computed from an input -- all BENCH_N samples a round.
_BLOCK_METHODS = """
[[gain.methods]]
name = "block"
arg_type = "float"
return_type = "float"
batch = true

[[gain.methods]]
name = "energy"
arg_type = "float[]"
return_type = "float"

[[gain.methods]]
name = "peaks"
arg_type = "float"
return_type = "gain_peak_t"

[[gain.methods.result_fields]]
name = "at"
type = "int"
"""


def test_generated_benchmarks_name_the_unit_they_count(tmp_path):
    """What each generated row truly counts, traced from its timing loop:
    `step()` BENCH_N times, `steps()` over BENCH_N, and a method taking
    one sample a call are BENCH_N samples a round; a no-input method called
    BENCH_N times is BENCH_N calls; a no-input records method called once a
    round is ONE call -- it claimed BENCH_N, so its rate was BENCH_N times
    too high."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for args in (
        ("object", "gain", "--arg-type", "float"),
        ("method", "gain", "scaled", "--arg-type", "float"),
        ("method", "gain", "level", "--arg-type", "void"),
    ):
        r = run_cli(*args, "--return-type", "float", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    frag = root / "objects" / "gain.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _RECORDS_METHOD + _BLOCK_METHODS,
        encoding="utf-8",
    )
    src = materialize_bench(root, "gain")
    rows = {
        name: (iters, unit)
        for name, iters, unit in re.findall(
            r'jm_bench_add_unit\(&_bench, "(\w+)", _times_\w+, ITERATIONS,'
            r"\s*(\w+), (JM_BENCH_\w+)\);",
            src,
        )
    }
    assert rows == {
        "step": ("BENCH_N", "JM_BENCH_SAMPLE"),
        "steps": ("BENCH_N", "JM_BENCH_SAMPLE"),
        "scaled": ("BENCH_N", "JM_BENCH_SAMPLE"),
        "level": ("BENCH_N", "JM_BENCH_CALL"),
        "events": ("1", "JM_BENCH_CALL"),
        "block": ("BENCH_N", "JM_BENCH_SAMPLE"),
        "energy": ("BENCH_N", "JM_BENCH_SAMPLE"),
        "peaks": ("BENCH_N", "JM_BENCH_SAMPLE"),
    }, src
    assert "jm_bench_add(" not in src.replace("jm_bench_add_unit(", "")
