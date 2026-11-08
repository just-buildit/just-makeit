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

import ast
import re
import shutil
import subprocess
import sys
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
    # gh-2552: its constructor also takes a trailing `const char **why`,
    # which every C call must still pass LAST, after the `default_raw` seeds.
    'create_error = "ValueError"\ncreate_error_message = "corr refused"\n'
    "create_why = true\n"
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


#: A module function with a required `x`, a defaulted `k` (gh-240) and a
#: `cap` defaulted to a C constant Python has no spelling of (gh-1488).
_FN = (
    "[module.m]\nobjects = []\n"
    '\n[[module.m.functions]]\nname = "scale"\nreturn_type = "double"\n'
    '\n[[module.m.functions.params]]\nname = "x"\ntype = "double"\n'
    '\n[[module.m.functions.params]]\nname = "k"\ntype = "double"\n'
    'default = "2.0"\n'
    '\n[[module.m.functions.params]]\nname = "cap"\ntype = "double"\n'
    'default = "INFINITY"\n'
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
    (root / "modules" / "m.toml").write_text(_FN, encoding="utf-8")
    out = run_cli("apply", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    # gh-2557: the app face, a C tool and a console script over `corr`.
    for target, name in (("c", "capc"), ("console", "capp")):
        out = run_cli(
            "app",
            "--target",
            target,
            "--object",
            "corr",
            "--name",
            name,
            cwd=root,
        )
        assert out.returncode == 0, out.stdout + out.stderr
    # gh-2560: the same pair over a module function.
    for target, name in (("c", "fnc"), ("console", "fnp")):
        out = run_cli(
            "app",
            "--target",
            target,
            "--module",
            "m",
            "--function",
            "scale",
            "--name",
            name,
            cwd=root,
        )
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


# -- gh-2557: the app face -------------------------------------------------
#
# `jm app` read a constructor flag's default from `default` alone, so a
# required or `default_raw` param rendered `size_t size = ;` in the C tool
# (no build) and `noise_hi=None` in the console script (a `TypeError` on
# any run that did not pass the flag).


def test_the_c_app_starts_each_flag_at_the_bindings_default(
    project: Path,
) -> None:
    text = (project / "native/src/app/capc.c").read_text(encoding="utf-8")
    assert "size_t noise_hi = (unsigned long long)-1ULL;" in text
    assert "double cn0_dbhz = NAN;" in text
    # The required one has no default: the run is refused without it.
    assert "if (!size_given) {" in text
    assert not re.search(r"= ;", text), text


def test_the_console_app_never_passes_an_unset_default(
    project: Path,
) -> None:
    text = (project / "src/rp/cli.py").read_text(encoding="utf-8")
    ast.parse(text)
    for name in _RAW:
        assert f"{name}=args.{name}" not in text, text
    assert re.search(r'"--size",\n\s+type=int,\n\s+required=True', text)


def _built(project: Path) -> Path:
    out = run_cli("test", cwd=project)
    assert out.returncode == 0, out.stdout + out.stderr
    exe = project / "build" / "capc"
    assert exe.is_file(), sorted(p.name for p in exe.parent.iterdir())
    return exe


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_c_app_requires_its_required_flag_and_runs(
    project: Path, tmp_path: Path
) -> None:
    exe = _built(project)
    sample = tmp_path / "x.f32"
    sample.write_bytes(bytes(4))
    bare = subprocess.run(
        [str(exe), "-i", str(sample)], capture_output=True, text=True
    )
    assert bare.returncode == 2, bare.stderr
    assert "--size is required" in bare.stderr
    run = subprocess.run(
        [str(exe), "--size", "4", "-i", str(sample)], capture_output=True
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == bytes(4)


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_console_app_runs_without_the_defaulted_flags(
    project: Path, tmp_path: Path
) -> None:
    _built(project)
    sample = tmp_path / "x.f32"
    sample.write_bytes(bytes(4))
    run = subprocess.run(
        [sys.executable, "-m", "rp.cli", "--size", "4", "-i", str(sample)],
        capture_output=True,
        cwd=project / "src",
    )
    assert run.returncode == 0, run.stderr.decode()
    assert run.stdout == bytes(4)


# -- gh-2560: a function app -----------------------------------------------
#
# Its flags came from a builder of their own: Python required every
# parameter, while C started each at zero, enforced none and ignored a
# declared default. Both now go through the one flag model.


def test_the_function_c_app_agrees_with_its_python_face(
    project: Path,
) -> None:
    c = (project / "native/src/app/fnc.c").read_text(encoding="utf-8")
    assert "double k = 2.0;" in c
    assert "if (!x_given) {" in c
    assert "k_given" not in c
    assert "double cap = INFINITY;" in c
    assert "#include <math.h>" in c
    py = (project / "src/rp/m/cli.py").read_text(encoding="utf-8")
    ast.parse(py)
    assert re.search(r'"--x",\n\s+type=float,\n\s+required=True', py)
    assert re.search(r'"--k",\n\s+type=float,\n\s+default=2.0', py)
    # `cap` is passed only when given, so the binding's INFINITY applies.
    assert "args.cap" not in py
    assert '"cap",' in py


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_function_apps_require_x_and_default_k(project: Path) -> None:
    _built(project)
    exe = project / "build" / "fnc"
    bare = subprocess.run([str(exe)], capture_output=True, text=True)
    assert bare.returncode == 2, bare.stdout + bare.stderr
    assert "--x is required" in bare.stderr
    ok = subprocess.run([str(exe), "--x", "3"], capture_output=True)
    assert ok.returncode == 0, ok.stderr
    py = [sys.executable, "-m", "rp.m.cli"]
    bare = subprocess.run(py, capture_output=True, cwd=project / "src")
    assert bare.returncode == 2, bare.stderr
    ok = subprocess.run(
        [*py, "--x", "3"], capture_output=True, cwd=project / "src"
    )
    assert ok.returncode == 0, ok.stderr


# -- with gh-2552's create_why ---------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["native/tests/test_corr_core.c", "native/benchmarks/bench_corr_core.c"],
)
def test_the_c_seed_passes_the_raw_defaults_then_why(
    project: Path, path: str
) -> None:
    """The `default_raw` seeds and `create_why`'s NULL, in that order."""
    text = (project / path).read_text(encoding="utf-8")
    assert "_create(4, 3, (unsigned long long)-1ULL, NAN, NULL);" in text, text


def test_the_c_app_passes_its_flags_then_why(project: Path) -> None:
    text = (project / "native/src/app/capc.c").read_text(encoding="utf-8")
    assert "double cn0_dbhz = NAN;" in text
    assert re.search(
        r"_create\(size, taps, noise_hi, cn0_dbhz, &\w+\);", text
    ), text
