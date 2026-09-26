"""gh-1591 phase 2: every symbol a `c_prefix` library exports carries it.

The source-level oracles (tests/test_gh1591_c_prefix.py) read what jm
WROTE. This reads what the linker SEES: the broad fixture, built with
`--c-prefix zz` and compiled, and every DEFINED global symbol in
``lib<pkg>`` -- shared and static -- must start with ``zz_``. The only
exceptions come from the manifest, never a hand list: ``<pkg>_version`` (the
precedent: jm's one project-named export) and the symbols the author named
(``fn =``, ``*_fn``), which jm never prefixes.

A registration-free check: a symbol shape nobody thought to list is caught
by being exported, the way a real collision would be.

Every platform, Windows included (gh-1648): what a library exports is read
by ``tests/_exports.py``, which on a clang-cl build reads the DLL's export
table and the static ``.lib``'s defined externals. The file is in the
Windows CI job's ``PROJECT_ENV_TESTS``, so the Windows leg runs it.

GATE: every defined global symbol in a `c_prefix` project's shared and
      static lib<pkg> -- .so/.dylib/.dll export table, .a/.lib -- starts
      with the prefix or is named by its manifest, on every CI platform.
"""

from __future__ import annotations

import subprocess

import pytest

import _csym_fixtures as FX
import _exports as EX
from just_makeit import _config as C

#: Rows whose C library builds on its own. Not `kinds`: it names an author
#: header (`kd/b.h`) the fixture does not write. Not `shapes`: its record
#: methods use structs (`peak_t`, `best_t`) the author declares, and it fails
#: to compile the same way without a prefix.
ROWS = ("std", "perf", "mod")


def _run(cmd, cwd):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True
    )
    assert r.returncode == 0, (cmd, r.stdout[-2000:], r.stderr[-2000:])
    return r.stdout


def _upgraded(where):
    """The fixture built BARE, then moved onto ``c_prefix = "zz"`` by `jm
    upgrade` and `apply` -- gh-1591 phase 3's path."""
    from _jmrun import run_cli

    roots = FX.build(where)
    for row in ROWS:
        root = roots[row]
        toml = root / C.FILENAME
        text = toml.read_text(encoding="utf-8")
        toml.write_text(
            text.replace("[project]\n", '[project]\nc_prefix = "zz"\n', 1),
            encoding="utf-8",
        )
        for cmd in ("upgrade", "apply"):
            r = run_cli(cmd, cwd=root)
            assert r.returncode == 0, (row, cmd, r.stdout + r.stderr)
    return roots


@pytest.fixture(scope="module", params=["fresh", "upgraded"])
def libs(tmp_path_factory, request):
    where = tmp_path_factory.mktemp(f"nm-{request.param}")
    if request.param == "fresh":
        roots = FX.build(where, "--c-prefix", "zz")
    else:
        roots = _upgraded(where)
    out = {"_roots": roots}
    for row in ROWS:
        root = roots[row]
        b = root / "b"
        _run(["cmake", "-S", ".", "-B", b, "-DBUILD_PYTHON=OFF"], root)
        _run(["cmake", "--build", b], root)
        out[row] = (b, C.project_name(C.load(root)))
    return out


@pytest.mark.parametrize("row", ROWS)
def test_every_export_carries_the_prefix(libs, row):
    # Read here, not in the fixture: a reader that finds nothing must fail
    # a NAMED test (gh-1430), not surface as a setup error.
    b, pkg = libs[row]
    syms = EX.exports(b, pkg)
    allowed = {f"{pkg}_version"} | FX.author_names(C.load(libs["_roots"][row]))
    assert any(s.startswith("zz_") for s in syms), sorted(syms)
    bad = sorted(
        s for s in syms if not s.startswith("zz_") and s not in allowed
    )
    assert bad == [], (
        f"{row}: lib exports symbols without the c_prefix and not named by "
        f"the manifest: {bad}"
    )


@pytest.mark.parametrize("row", ROWS)
def test_it_builds_tests_and_imports(libs, row):
    """`jm test`: the CMake build with the Python extension, ctest, and the
    generated pytest suite, which imports every class -- the declarations
    the upgrade respelled and the definitions it respelled agree."""
    from _jmrun import run_cli

    r = run_cli("test", cwd=libs["_roots"][row])
    assert r.returncode == 0, (row, r.stdout[-3000:] + r.stderr[-3000:])


def test_a_default_jm_new_exports_only_its_package_prefix(tmp_path):
    """gh-1591 2b: `jm new` with NO prefix flag namespaces the project by its
    package name, so a project nobody configured passes the gate above."""
    from _jmrun import run_cli

    root = tmp_path / "dflt"
    for args, cwd in (
        (
            ("new", "dflt", "--object", "fir", "--state", "gain:double:1.0"),
            tmp_path,
        ),
        (("module", "m"), root),
        (("function", "mix", "--module", "m", "--param", "x:float"), root),
    ):
        r = run_cli(*args, cwd=cwd)
        assert r.returncode == 0, (args, r.stdout + r.stderr)
    assert C.c_prefix(C.load(root)) == "dflt"
    b = root / "b"
    _run(["cmake", "-S", ".", "-B", b, "-DBUILD_PYTHON=OFF"], root)
    _run(["cmake", "--build", b], root)
    syms = EX.exports(b, "dflt")
    assert {"dflt_fir_create", "dflt_mix"} <= syms, sorted(syms)
    bad = sorted(s for s in syms if not s.startswith("dflt_"))
    assert bad == [], bad
