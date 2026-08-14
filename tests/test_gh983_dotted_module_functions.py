"""gh-983: a dotted module id is a manifest key, never a path.

`C.module_paths(id)` splits a module's name into three roles — `cname`
(`dsp_filters`: the native directory, the CMake target, every C identifier),
`leaf` (`filters`: the `.so` basename and `PyInit_`), and `pypath`
(`dsp/filters`: the Python package directory). The dotted id itself names
nothing on disk.

Four call sites used it as though it did, and the family is worth reading as a
whole because only one of them was loud:

===========================================  =========================
site                                         how it failed
===========================================  =========================
`_function.run`                              **traceback** — opened a
                                             header no `jm module` ever
                                             wrote
`_remove` (function)                         silent — `unlink` of a path
                                             that never existed is
                                             `exists() == False`
`_regenerate._stale_ext_modules`             silent — found no stale
                                             `.so`, so the relink
                                             guarantee did not hold
`_object._load_module_doc_blocks`            silent — documented `{}`
                                             fallback fired, and every
                                             function got a stub
                                             docstring instead of its
                                             authored Doxygen
===========================================  =========================

Only the first raised, and it raised on the *creating* side — which is the
only reason the other three were never reached in a real project. A dotted
module simply could not have a module-level function, so the three silent ones
had no way to be observed. Fixing the loud one is what puts them in reach,
which is why they are all here.

The gate is the invariant, not the four sites, and it is stated in two halves
because the obvious single form is wrong. **No path under a project may
contain a dot-bearing module id** — that one holds. "No generated C may
mention it" does not: a Python type's `.tp_name` is genuinely
`pkg.dsp.filters.Fir`, and a module's header comment names the id its author
typed. Both were measured as false positives before this settled.

What is never correct is a reference that resolves to nothing, so the second
half asserts exactly that: **every `#include` in generated C names a file that
exists, and every `add_subdirectory` names a directory that exists.** Both are
derived from the tree, so a fifth site is covered without editing this file —
and the second half also catches a wrong path spelled for a reason nobody has
thought of yet.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402

DOTTED = "dsp.filters"
CNAME = "dsp_filters"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        # A REPLACED environment drops COVERAGE_PROCESS_START and
        # COVERAGE_FILE, so everything these tests drive through the CLI is
        # instrumented and then discarded — gh-978's defect, reintroduced one
        # layer out by a test helper rather than by the Makefile. It shows up
        # as `codecov/patch` failing on code the suite plainly exercises, which
        # is the signal that found it. Merge, do not replace; `NO_COLOR` and
        # `PYTHONPATH` still win.
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    assert _cli("module", DOTTED, cwd=root).returncode == 0
    return root


def _add_function(root: Path, name: str = "taps"):
    return _cli(
        "function",
        name,
        "--module",
        DOTTED,
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=root,
    )


# ── The defect ───────────────────────────────────────────────────────────────


def test_function_on_a_dotted_module_succeeds(project: Path):
    """It did not merely misplace a file — it raised."""
    r = _add_function(project)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr


def test_the_c_lands_under_the_cname(project: Path):
    assert _add_function(project).returncode == 0
    assert (project / "native" / "src" / CNAME / "taps.c").is_file()
    assert not (project / "native" / "src" / DOTTED).exists()


def test_the_declaration_reaches_the_header_the_module_wrote(project: Path):
    """The header `jm module` writes and the one `jm function` injects into
    must be the same file. They were not, which is the whole bug."""
    assert _add_function(project).returncode == 0
    header = project / "native" / "inc" / CNAME / f"{CNAME}_core.h"
    assert "taps" in header.read_text(encoding="utf-8")


def test_the_stub_includes_a_header_that_exists(project: Path):
    """A `#include "dsp.filters/dsp.filters_core.h"` compiles on no machine.
    Asserting the path resolves is what makes this a check and not a spelling
    preference."""
    assert _add_function(project).returncode == 0
    body = (project / "native" / "src" / CNAME / "taps.c").read_text(
        encoding="utf-8"
    )
    m = re.search(r'#include "([^"]+_core\.h)"', body)
    assert m, body
    assert (project / "native" / "inc" / m.group(1)).is_file()


def test_removing_it_deletes_the_c_and_strips_the_declaration(project: Path):
    """The silent half. `unlink` on a path that was never written raises
    nothing and reports nothing, so this side would have gone on "working"."""
    assert _add_function(project).returncode == 0
    r = _cli(
        "remove",
        "function",
        "taps",
        "--module",
        DOTTED,
        "--force",
        cwd=project,
    )
    assert r.returncode == 0, r.stderr
    assert not (project / "native" / "src" / CNAME / "taps.c").exists()
    header = project / "native" / "inc" / CNAME / f"{CNAME}_core.h"
    assert "taps" not in header.read_text(encoding="utf-8")


def test_authored_doxygen_reaches_the_generated_docstring(project: Path):
    """The quietest one: the doc reader opened a path that does not exist,
    took its documented `{}` fallback, and every function in a dotted module
    got the name-based stub instead of what its author wrote."""
    assert _add_function(project).returncode == 0
    header = project / "native" / "inc" / CNAME / f"{CNAME}_core.h"
    body = header.read_text(encoding="utf-8")
    assert "double taps(double x);" in body, body
    header.write_text(
        body.replace(
            "double taps(double x);",
            "/**\n"
            " * @brief Filter taps for a cutoff.\n"
            " * @param x cutoff, normalised.\n"
            " * @return the tap weight.\n"
            " */\n"
            "double taps(double x);",
        ),
        encoding="utf-8",
    )
    assert _cli("apply", cwd=project).returncode == 0
    pyi = (
        project / "src" / "p" / "dsp" / "filters" / "filters.pyi"
    ).read_text(encoding="utf-8")
    assert "Filter taps for a cutoff." in pyi, pyi


def test_regenerate_finds_the_built_extension(project: Path):
    """`_stale_ext_modules` exists to guarantee cmake relinks. It built a path
    no project has, found nothing, and the guarantee silently did not hold —
    so assert against the path CMake's OUTPUT_NAME actually produces."""
    from just_makeit._regenerate import _stale_ext_modules
    import sysconfig

    assert (
        _cli("object", "fir", "--module", DOTTED, cwd=project).returncode == 0
    )
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    built = project / "src" / "p" / "dsp" / "filters" / f"filters{suffix}"
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(b"")

    cfg = C.load(project)
    assert _stale_ext_modules(project, cfg, "p", "fir", DOTTED) == [built]


# ── The invariant ────────────────────────────────────────────────────────────


def test_no_generated_path_contains_a_dotted_module_id(project: Path):
    """The gate, derived rather than enumerated.

    Every site above is one instance of a single rule: a dot-bearing module id
    is a manifest key and names nothing on disk. Asserting the rule over the
    whole tree covers a fifth site with no edit here — which the four-site
    table in this file's docstring cannot do, and is exactly why the table is
    documentation and this is the test.

    `just-makeit.toml` is excluded: the dotted id is its `[module."dsp.filters"]`
    key, and that is the one place it belongs.
    """
    assert _add_function(project).returncode == 0
    assert (
        _cli("object", "fir", "--module", DOTTED, cwd=project).returncode == 0
    )
    assert _cli("apply", cwd=project).returncode == 0

    offenders = [
        p.relative_to(project).as_posix()
        for p in project.rglob("*")
        if DOTTED in p.relative_to(project).as_posix()
    ]
    assert offenders == [], offenders


def test_every_generated_include_resolves(project: Path):
    """The same rule stated where it bites hardest, and stated as the property
    rather than as "no dots".

    A grep for the dotted id in generated C is the wrong check: two correct
    uses exist — the `.tp_name` of a Python type is genuinely
    `pkg.dsp.filters.Fir`, and a module's header comment names the id a reader
    typed. What is never correct is an `#include` that resolves to nothing,
    which is what `#include "dsp.filters/dsp.filters_core.h"` was.

    So: every quoted include in every generated `.c`/`.h` must name a file
    that exists, resolved the way the compiler does — beside the source, or
    under `native/inc`. That covers this bug, and any future one that spells a
    path wrong for a reason nobody has thought of yet.
    """
    assert _add_function(project).returncode == 0
    assert (
        _cli("object", "fir", "--module", DOTTED, cwd=project).returncode == 0
    )
    assert _cli("apply", cwd=project).returncode == 0

    inc = project / "native" / "inc"
    unresolved = []
    for src in sorted(project.rglob("*")):
        if src.suffix not in (".c", ".h"):
            continue
        for target in re.findall(
            r'^\s*#\s*include\s+"([^"]+)"',
            src.read_text(encoding="utf-8", errors="replace"),
            re.M,
        ):
            if (src.parent / target).is_file() or (inc / target).is_file():
                continue
            unresolved.append(
                f"{src.relative_to(project).as_posix()} -> {target}"
            )
    assert unresolved == [], unresolved


def test_add_subdirectory_names_a_directory_that_exists(project: Path):
    """The CMake half of the same property. A dotted `add_subdirectory` fails
    at configure time, which is louder than a bad include but no more
    correct — and it is the other place the id could reach a path."""
    assert (
        _cli("object", "fir", "--module", DOTTED, cwd=project).returncode == 0
    )
    assert _cli("apply", cwd=project).returncode == 0
    body = (project / "CMakeLists.txt").read_text(encoding="utf-8")
    subdirs = re.findall(r"^add_subdirectory\(([^)]+)\)", body, re.M)
    assert subdirs, body
    missing = [d for d in subdirs if not (project / d).is_dir()]
    assert missing == [], missing


def test_the_flat_case_is_untouched(project: Path, tmp_path: Path):
    """cname == leaf == pypath == id for a dotless module, so every path this
    fix rewrote must land where it always did. A rename that broke the common
    case while fixing the rare one would pass every assertion above."""
    assert _cli("new", "q", cwd=tmp_path).returncode == 0
    flat = tmp_path / "q"
    assert _cli("module", "filters", cwd=flat).returncode == 0
    assert (
        _cli(
            "function",
            "taps",
            "--module",
            "filters",
            "--param",
            "x:double",
            "--return-type",
            "double",
            cwd=flat,
        ).returncode
        == 0
    )
    assert (flat / "native" / "src" / "filters" / "taps.c").is_file()
    assert (flat / "native" / "inc" / "filters" / "filters_core.h").is_file()
    assert _cli("status", "--check", cwd=flat).returncode == 0
