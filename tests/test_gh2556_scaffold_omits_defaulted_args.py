"""gh-2556: a scaffolded construction omits every argument with a default.

The generated pytest and benchmark constructed an object with every init
param spelled out, each default restated in Python. A ``default_raw`` -- a C
expression such as ``(unsigned long long)-1ULL`` or ``NAN`` -- has no Python
spelling, so the restatement came out as ``noise_hi=...`` (Ellipsis, a
``TypeError`` before any test ran) or ``cn0_dbhz=.0`` (0 where the binding's
default is NaN: a different object from the default one). On an object with
a state struct a third builder of the same call restated them as the type's
zero instead, which built and passed while testing the wrong object.

The rule now, from one builder (``_build_no_state_init_ctx``): only a
REQUIRED parameter is spelled -- its ``example_value`` where given (gh-1105),
else its seed -- and a defaulted one is left out, so the binding applies its
own default. A defaulted parameter with an ``example_value`` is still passed:
that is the author asking for the value, not a restatement of it. The C smoke
and bench, which cannot omit an argument, pass the ``default_raw`` itself.

The fixture is built by running jm, standalone and module, with and without a
state struct, and the generated suite is then built and run.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli


#: An SGR escape; stripped so the summary anchor holds under FORCE_COLOR
#: (gh-1456).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The two parameters whose only default is `default_raw`.
_RAW = {
    "noise_hi": ("size_t", "(unsigned long long)-1ULL"),
    "cn0_dbhz": ("double", "NAN"),
}


def _raw_params(obj: str) -> str:
    return "".join(
        f'\n[[{obj}.init_params]]\nname = "{n}"\ntype = "{t}"\n'
        f'default_raw = "{raw}"\n'
        for n, (t, raw) in _RAW.items()
    )


#: A standalone object with no state struct, a required param carrying an
#: `example_value`, and a defaulted one with an `example_value` too.
_CORR = (
    '[corr]\narg_type = "float"\nreturn_type = "float"\nno_state = "true"\n'
    '\n[[corr.init_params]]\nname = "size"\ntype = "size_t"\n'
    'required = true\nexample_value = "4"\n'
    '\n[[corr.init_params]]\nname = "taps"\ntype = "int"\n'
    'default = "8"\nexample_value = "3"\n' + _raw_params("corr")
)

#: A module object WITH a state struct, whose required `gain` is seeded
#: from the state var it shares a name with (gh-122).
_FIR = (
    '[fir]\narg_type = "float"\nreturn_type = "float"\n'
    '\n[[fir.state]]\nname = "gain"\ntype = "float"\ndefault = "0.5f"\n'
    '\n[[fir.init_params]]\nname = "gain"\ntype = "double"\n'
    "required = true\n" + _raw_params("fir")
)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2556")
    out = run_cli("new", "rp", "--pytest-benchmark", cwd=tmp)
    assert out.returncode == 0, out.stdout + out.stderr
    root = tmp / "rp"
    (root / "objects").mkdir(exist_ok=True)
    (root / "modules").mkdir(exist_ok=True)
    (root / "objects" / "corr.toml").write_text(_CORR, encoding="utf-8")
    (root / "objects" / "fir.toml").write_text(_FIR, encoding="utf-8")
    (root / "modules" / "filt.toml").write_text(
        '[module.filt]\nobjects = ["fir"]\n', encoding="utf-8"
    )
    out = run_cli("apply", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    return root


#: (class, its generated pytest, its generated benchmark) per face.
_FACES = {
    "standalone": (
        "Corr",
        "src/rp/tests/test_corr.py",
        "src/rp/benchmarks/bench_corr.py",
    ),
    "module": (
        "Fir",
        "src/rp/filt/tests/test_fir.py",
        "src/rp/filt/benchmarks/bench_fir.py",
    ),
}


def _calls(text: str, cls: str) -> list[str]:
    """Every argument list `cls(...)` is called with in *text*."""
    return re.findall(rf"\b{cls}\(([^()]*)\)", text)


@pytest.mark.parametrize("face", sorted(_FACES))
@pytest.mark.parametrize("kind", ["test", "bench"])
def test_defaulted_args_are_omitted(
    project: Path, face: str, kind: str
) -> None:
    cls, test, bench = _FACES[face]
    text = (project / (test if kind == "test" else bench)).read_text(
        encoding="utf-8"
    )
    calls = _calls(text, cls)
    assert calls, f"no {cls}(...) construction in {kind}"
    for args in calls:
        for name in _RAW:
            assert f"{name}=" not in args, args
        assert "..." not in args, args


def test_required_and_example_values_are_still_spelled(
    project: Path,
) -> None:
    """Only a required param, or one with an `example_value`, is passed."""
    corr = (project / _FACES["standalone"][1]).read_text(encoding="utf-8")
    assert set(_calls(corr, "Corr")) == {"size=4, taps=3"}
    fir = (project / _FACES["module"][1]).read_text(encoding="utf-8")
    assert set(_calls(fir, "Fir")) == {"gain=0.5"}


@pytest.mark.parametrize(
    "path",
    [
        "native/benchmarks/bench_corr_core.c",
        "native/benchmarks/bench_fir_core.c",
    ],
)
def test_the_c_bench_passes_the_default_raw(project: Path, path: str) -> None:
    """C cannot omit an argument; it passes the binding's own default."""
    text = (project / path).read_text(encoding="utf-8")
    call = re.search(r"_create\(([^;]*)\);", text)
    assert call, text
    for _, raw in _RAW.values():
        assert raw in call.group(1), call.group(1)


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_generated_suite_passes(project: Path) -> None:
    out = run_cli("test", cwd=project)
    assert out.returncode == 0, out.stdout + out.stderr
    # pytest's own summary line, not the whole output: CTest prints
    # "0 tests failed out of 2" above it.
    summary = re.findall(r"^=+ (.*?) =+$", _ANSI.sub("", out.stdout), re.M)
    assert summary and "passed" in summary[-1], out.stdout
    assert "failed" not in summary[-1], out.stdout
