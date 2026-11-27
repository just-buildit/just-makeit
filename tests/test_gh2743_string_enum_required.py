"""gh-2743 / gh-2565: a string-enum init param's default, on every face.

gh-2743: a ``string_enum:`` init param with no ``default`` was neither
required nor defaulted. The binding parsed it after the PyArg ``|`` with the
local seeded ``""``, then validated ``""`` and refused it, so every call that
omitted the argument raised ``ValueError`` -- including the scaffold's own
pytest, which constructs with no arguments. Both ``.pyi`` writers said
``= ...``, an omittable argument. The maintainer decision on the issue: no
default means REQUIRED. The binding parses it before the ``|``, both stubs
show it with no default, and the generated tests pass a choice.

gh-2565: the C smoke test and bench seeded every string-enum param with
index 0, so a param declared ``default = "slow"`` was built as ``"fast"`` in
C while the binding's default object is ``"slow"``. The C seed is now the
choice the Python tests construct with: the declared default (its bound C
constant when the spec binds one), else -- for a required param -- the
first choice, which is as valid as any and is the C enum's zero.

The fixture is built by running jm, standalone and module, plus both app
targets, and then the generated suite is BUILT and RUN against a ``_core.c``
whose ``create()`` refuses every value but the default: index 0 there fails
the C smoke test, and a refused ``So()`` fails the generated pytest.
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

#: The required enum (no default) and the defaulted one, per face.
_REQUIRED = {"standalone": "so", "module": "sm"}
_DEFAULTED = {"standalone": "sd", "module": "sn"}

#: Where each object's stub lives.
_PYI = {
    "so": "src/ep/so.pyi",
    "sd": "src/ep/sd.pyi",
    "sm": "src/ep/grp/grp.pyi",
    "sn": "src/ep/grp/grp.pyi",
}

#: The only value the hand-written `create()` of a defaulted object accepts:
#: the index of `slow`, its declared default.
_REFUSE_ALL_BUT_DEFAULT = (
    "if (mode != 1) {\n        free(obj);\n        return NULL;\n    }"
)


def _jm(root: Path, *args: str) -> None:
    out = run_cli(*args, cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2743")
    _jm(tmp, "new", "ep", "--pytest-benchmark")
    root = tmp / "ep"
    _jm(root, "module", "grp")
    for obj, module in (("so", None), ("sm", "grp")):
        # A defaulted scalar declared FIRST, so the required enum is
        # hoisted ahead of it on every face, as the kwlist hoists it.
        _jm(
            root,
            "object",
            obj,
            *(("--module", module) if module else ()),
            "--init-param",
            "gain2:double:1.0",
            "--init-param",
            "kind:string_enum:lo,hi",
        )
    for obj, module in (("sd", None), ("sn", "grp")):
        _jm(
            root,
            "object",
            obj,
            *(("--module", module) if module else ()),
            "--init-param",
            "mode:string_enum:fast,slow:slow",
        )
        core = root / "native" / "src" / obj / f"{obj}_core.c"
        text = core.read_text(encoding="utf-8")
        assert text.count("(void)mode;") == 1, text
        core.write_text(
            text.replace("(void)mode;", _REFUSE_ALL_BUT_DEFAULT),
            encoding="utf-8",
        )
    for target, name in (("c", "soc"), ("console", "sop")):
        _jm(
            root,
            "app",
            "--target",
            target,
            "--object",
            "so",
            "--name",
            name,
        )
    return root


def _init_sig(stub: str, cls: str) -> str:
    """The parameter list of *cls*'s ``__init__`` in *stub*, one line."""
    body = re.split(rf"^class {cls}\b", stub, maxsplit=1, flags=re.M)[1]
    m = re.search(r"def __init__\((.*?)\) -> None", body, re.S)
    assert m, body
    return " ".join(m.group(1).split()).rstrip(",")


@pytest.mark.parametrize("face", sorted(_REQUIRED))
def test_both_stubs_show_the_required_enum_without_a_default(
    project: Path, face: str
) -> None:
    obj = _REQUIRED[face]
    stub = (project / _PYI[obj]).read_text(encoding="utf-8")
    sig = _init_sig(stub, obj.capitalize())
    # Required, so first -- ahead of the defaulted scalar -- and no `=`.
    assert sig == 'self, kind: Literal["lo", "hi"], gain2: float = 1.0', sig


@pytest.mark.parametrize("face", sorted(_DEFAULTED))
def test_both_stubs_keep_a_declared_default(project: Path, face: str) -> None:
    obj = _DEFAULTED[face]
    stub = (project / _PYI[obj]).read_text(encoding="utf-8")
    sig = _init_sig(stub, obj.capitalize())
    assert sig == 'self, mode: Literal["fast", "slow"] = "slow"', sig


@pytest.mark.parametrize("obj", sorted([*_REQUIRED.values()]))
def test_the_binding_parses_it_before_the_bar(project: Path, obj: str) -> None:
    rel = "so/so_ext.c" if obj == "so" else f"grp/grp_ext_{obj}.c"
    ext = (project / "native" / "src" / rel).read_text(encoding="utf-8")
    assert '"s|d", kwlist' in ext, ext
    assert "const char *kind_str = NULL;" in ext, ext


@pytest.mark.parametrize("obj", sorted([*_DEFAULTED.values()]))
@pytest.mark.parametrize("kind", ["tests", "benchmarks"])
def test_the_c_seed_is_the_declared_default(
    project: Path, obj: str, kind: str
) -> None:
    stem = "test" if kind == "tests" else "bench"
    text = (project / "native" / kind / f"{stem}_{obj}_core.c").read_text(
        encoding="utf-8"
    )
    assert f"ep_{obj}_create(1);" in text, text


@pytest.mark.parametrize("obj", sorted([*_REQUIRED.values()]))
def test_the_python_tests_pass_the_seed_the_c_test_does(
    project: Path, obj: str
) -> None:
    rel = (
        f"src/ep/tests/test_{obj}.py"
        if obj == "so"
        else f"src/ep/grp/tests/test_{obj}.py"
    )
    text = (project / rel).read_text(encoding="utf-8")
    calls = set(re.findall(rf"\b{obj.capitalize()}\(([^()]*)\)", text))
    assert calls == {'kind="lo"'}, calls
    c = (project / "native" / "tests" / f"test_{obj}_core.c").read_text(
        encoding="utf-8"
    )
    assert f"ep_{obj}_create(1.0, 0);" in c, c


def test_a_default_that_is_no_choice_is_refused(tmp_path: Path) -> None:
    _jm(tmp_path, "new", "bp")
    out = run_cli(
        "object",
        "bo",
        "--init-param",
        "m:string_enum:a,b:zz",
        cwd=tmp_path / "bp",
    )
    assert out.returncode != 0, out.stdout
    assert 'default `zz` is not one of the choices "a", "b"' in out.stderr


def test_a_bound_constant_is_the_c_seed(tmp_path: Path) -> None:
    """gh-1450's constants: the seed is the default's constant, not 1."""
    _jm(tmp_path, "new", "cp")
    _jm(
        tmp_path / "cp",
        "object",
        "co",
        "--init-param",
        "m:string_enum:fast=CP_FAST,slow=CP_SLOW:slow",
    )
    c = (tmp_path / "cp" / "native/tests/test_co_core.c").read_text(
        encoding="utf-8"
    )
    assert "cp_co_create(CP_SLOW);" in c, c


def test_the_apps_require_the_flag(project: Path) -> None:
    c = (project / "native/src/app/soc.c").read_text(encoding="utf-8")
    assert "if (!kind_given) {" in c, c
    py = (project / "src/ep/cli.py").read_text(encoding="utf-8")
    assert re.search(
        r'"--kind",\n\s+choices=\["lo", "hi"\],\n\s+required=True', py
    ), py


@pytest.fixture(scope="module")
def built(project: Path) -> "tuple[Path, str, int]":
    """`jm test` once: builds everything, runs CTest, then the pytest.

    Returns its outcome rather than asserting on it, so a red build fails
    the tests below by name instead of erroring in setup.
    """
    if _NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    out = run_cli("test", cwd=project)
    return project, out.stdout + out.stderr, out.returncode


def test_the_generated_suite_builds_and_passes(
    built: "tuple[Path, str, int]",
) -> None:
    """Built and run: the C smoke tests against a `create()` refusing all
    but the default, and the generated pytest constructing every object."""
    project, log, code = built
    assert code == 0, log
    assert "100% tests passed" in log, log
    assert (project / "build" / "soc").is_file()


def test_omitting_the_required_enum_raises_typeerror(
    built: "tuple[Path, str, int]",
) -> None:
    project, log, code = built
    assert code == 0, log
    probe = (
        "from ep import So, Sd\n"
        "from ep.grp import Sm\n"
        "for cls in (So, Sm):\n"
        "    try:\n"
        "        cls()\n"
        "    except TypeError as e:\n"
        "        assert 'kind' in str(e), e\n"
        "    else:\n"
        "        raise SystemExit(cls.__name__ + '() constructed')\n"
        "So(kind='hi')\n"
        "Sd()\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=project / "src",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    exe = project / "build" / "soc"
    bare = subprocess.run(
        [str(exe)], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert bare.returncode == 2, bare.stdout + bare.stderr
    assert "--kind is required" in bare.stderr
    ok = subprocess.run(
        [str(exe), "--kind", "hi"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert ok.returncode == 0, ok.stderr
