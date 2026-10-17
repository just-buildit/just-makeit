"""gh-2225: every generated C command line reads a numeric flag checked.

A composer's c-face CLI (`_composer.render_cli`) read each numeric flag as
``({ct})strtoull(argv[++i], NULL, 0)`` (``strtod`` for a real), and a
``jm app`` C executable as ``(int8_t)strtol(argv[++i], NULL, 10)``
(`_app._C_PARSE`). Neither looked at the end pointer, ``errno`` or the
declared type's range, so ``--count abc`` ran with 0, ``--count
99999999999999999999`` with ``ULLONG_MAX`` and ``--gain8 300`` on an
``int8_t`` with 44 -- every one with exit status 0 and no message.

Now one checked parser, `_app.cli_number_c`, is what every numeric flag
of both faces calls: the argument is read whole at its declared type, or
the tool prints ``<prog>: --<flag>: <reason>`` and exits 2. The integer
limits are the type rows' own ``limits`` (`_types._CTYPE_META`), the
``<stdint.h>`` / ``<limits.h>`` macros, so the compiler states the range.

Two halves:

* **the render**: no generated command line calls ``strto*`` except
  inside the checked helpers, and a helper is emitted exactly when a flag
  in that translation unit calls it (an uncalled ``static`` is
  ``-Wunused-function``, gh-1863).
* **the build**: a composer CLI with a segment field of every numeric
  ``_CTYPE_META`` type and a source field of every source scalar, and a
  ``jm app`` executable with a flag of every type the app parses, both
  built warning-clean under ``-Wall -Wextra -Werror``. For each type,
  junk, trailing junk, overflow, a value one past each end of the range
  and (unsigned) a negative each exit 2 naming the flag; the minimum and
  the maximum are accepted and arrive EXACTLY. The ranges come from
  numpy -- an oracle that is not the table under test. The gh-1709 and
  gh-1711 fixtures build a composer CLI too, but not under ``-Werror``;
  gh-1767 asks for a composer in the scaffold sweep, which this is not.

GATE: a generated C command line refuses a numeric flag that is junk,
overflows or is out of its type's range (exit 2, naming the flag), and
takes the type's minimum and maximum exactly.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _app
from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _types as T

sys.path.insert(0, str(Path(__file__).parent))
from test_composer_codegen import _cfg  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Every numeric row of the type table -- the types a flag may declare.
NUMERIC = sorted(
    t for t, m in T._CTYPE_META.items() if m["kind"] in ("int", "float")
)

#: A ``strto*`` call: the unchecked parse every face used to emit.
_STRTO = re.compile(r"\bstrto[a-z]*\s*\(")


def _np(ctype: str):
    """numpy's scalar type for *ctype*'s row."""
    return getattr(np, T._CTYPE_META[ctype]["py_type"].split(".", 1)[1])


def _range(ctype: str) -> "tuple[int | float, int | float]":
    """The type's (min, max), from numpy -- not from the table under test."""
    if ctype == "bool":
        return 0, 1
    if T._CTYPE_META[ctype]["kind"] == "float":
        fi = np.finfo(_np(ctype))
        return float(fi.min), float(fi.max)
    ii = np.iinfo(_np(ctype))
    return int(ii.min), int(ii.max)


def _slug(ctype: str) -> str:
    return ctype.replace(" ", "_")


def cases(ctype: str) -> "list[tuple[str, object]]":
    """``(argument, expected)`` for one flag of *ctype*.

    *expected* is the value the tool must receive, or a ``str``: the
    reason its refusal must give.
    """
    lo, hi = _range(ctype)
    if T._CTYPE_META[ctype]["kind"] == "float":
        out: "list[tuple[str, object]]" = [
            (repr(lo), lo),
            (repr(hi), hi),
            ("0.5", 0.5),
            ("abc", "is not a number"),
            ("1.5x", "is not a number"),
            ("", "is not a number"),
            ("1e400", f"is out of range for {ctype}"),
            (repr(-hi * 2) if ctype == "float" else "-1e400", "out of range"),
        ]
        if ctype == "float":
            # Read at its own precision: numpy's own spelling of the
            # maximum is past it as a double, and rounds to it as a float.
            out.append((str(np.finfo(np.float32).max), hi))
        return out
    out = [
        (str(lo), lo),
        (str(hi), hi),
        ("abc", "is not an integer"),
        ("12abc", "is not an integer"),
        ("1.5", "is not an integer"),
        ("", "is not an integer"),
        ("9" * 30, f"is out of range for {ctype}"),
        (str(hi + 1), f"is out of range for {ctype}"),
        (str(lo - 1), f"is out of range for {ctype}"),
    ]
    if lo == 0:
        out.append(("-1", f"is out of range for {ctype}"))
        out.append(("-" + "9" * 30, f"is out of range for {ctype}"))
    return out


def test_the_cases_are_not_vacuous():
    """Every numeric row is a case, and both kinds and signs are present."""
    assert {"int8_t", "uint64_t", "size_t", "bool", "float", "double"} <= set(
        NUMERIC
    )
    assert all(cases(t) for t in NUMERIC)
    assert all(t in NUMERIC for t in _composer._SOURCE_SCALARS)
    assert _app._C_PARSE <= set(NUMERIC)


def test_every_integer_row_states_its_limits():
    """The parser reads a row's ``limits``; an integer row without one
    would reach the CLI as a KeyError at render time."""
    missing = [
        t
        for t in NUMERIC
        if T._CTYPE_META[t]["kind"] == "int"
        and "limits" not in T._CTYPE_META[t]
    ]
    assert not missing


# ── the render ───────────────────────────────────────────────────────────────


def _every_type_cfg() -> dict:
    """The codegen fixture with a CLI, and a segment field of every
    numeric type beside its own."""
    cfg = copy.deepcopy(_cfg())
    mod = cfg["module"]["wfm_compose"]
    mod["cli"] = {"enabled": True}
    mod["segment"]["fields"] += [
        {"name": f"g_{_slug(t)}", "type": t} for t in NUMERIC
    ]
    return cfg


def _outside_helpers(text: str) -> str:
    """*text* without the checked helpers' own definitions."""
    for src in _app._CLI_NUMBER_FNS.values():
        text = text.replace(src, "")
    return text


def test_composer_cli_parses_no_number_unchecked():
    text = _composer.render_cli(_every_type_cfg(), "wfm_compose")
    assert not _STRTO.search(_outside_helpers(text)), (
        "a composer CLI flag is parsed by a bare strto* call"
    )
    for t in NUMERIC:
        flag = f"g_{_slug(t)}"
        assert _app.cli_number_c(t, flag, "argv[++i]", base=0) in text, t


def _defined(text: str, fn: str) -> bool:
    return re.search(rf"^{fn}\(", text, re.M) is not None


def _called(text: str, fn: str) -> bool:
    return re.search(rf"(?<!\n){fn}\(", text) is not None


def test_composer_cli_helper_only_with_a_caller():
    """Each helper is defined exactly when a flag calls it (gh-1863)."""
    no_numbers = copy.deepcopy(_cfg())
    mod = no_numbers["module"]["wfm_compose"]
    mod["cli"] = {"enabled": True}
    mod["source"]["fields"] = [
        f for f in mod["source"]["fields"] if f.get("enum") or f.get("bytes")
    ]
    mod["segment"]["fields"] = []
    seen = set()
    for cfg in (_every_type_cfg(), no_numbers):
        text = _composer.render_cli(cfg, "wfm_compose")
        for fn in _app._CLI_NUMBER_FNS:
            assert _defined(text, fn) == _called(text, fn), fn
            seen.add((fn, _called(text, fn)))
    # Both outcomes were exercised for every helper.
    assert seen == {(fn, b) for fn in _app._CLI_NUMBER_FNS for b in (0, 1)}


def _app_project(root: Path) -> Path:
    """A project with an object app taking a flag of every type an app
    parses, a function app and a subcommand app."""
    assert run_cli("new", "cl", cwd=root).returncode == 0
    proj = root / "cl"
    r = run_cli(
        "object",
        "gen",
        "--preset",
        "generator",
        "--return-type",
        "float _Complex",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    flags = []
    for t in sorted(_app._C_PARSE):
        flags += ["--flag", f"x_{_slug(t)}:{t}:0"]
    r = run_cli(
        "app",
        "--target",
        "c",
        "--object",
        "gen",
        "--name",
        "tool",
        *flags,
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("module", "m", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "function",
        "pick",
        "--module",
        "m",
        "--param",
        "a:int8_t",
        "--param",
        "b:double",
        "--return-type",
        "int",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "app",
        "--target",
        "c",
        "--function",
        "pick",
        "--module",
        "m",
        "--name",
        "picker",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = C.load(proj)
    cfg.setdefault("app", []).append(
        {
            "target": "c",
            "name": "multi",
            "commands": [
                {"name": "go", "flags": [{"name": "n", "type": "uint16_t"}]}
            ],
        }
    )
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


def test_app_faces_parse_no_number_unchecked(tmp_path):
    """The object, function and subcommand apps -- each C face of `jm
    app` -- call the checked parser, defined before its first call."""
    proj = _app_project(tmp_path)
    app = proj / "native" / "src" / "app"
    for name in ("tool", "picker", "multi"):
        text = (app / f"{name}.c").read_text(encoding="utf-8")
        assert not _STRTO.search(_outside_helpers(text)), name
        called = [fn for fn in _app._CLI_NUMBER_FNS if _called(text, fn)]
        assert called, f"{name}: no numeric flag reached the parser"
        for fn in _app._CLI_NUMBER_FNS:
            assert _defined(text, fn) == (fn in called), (name, fn)
            if fn in called:
                first_call = re.search(rf"(?<!\n){fn}\(", text)
                assert re.search(rf"^{fn}\(", text, re.M).start() < (
                    first_call.start()
                ), (name, fn)


# ── the build ────────────────────────────────────────────────────────────────


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def _build(proj: Path, *targets: str) -> Path:
    """Configure C-only and build *targets*, warning-clean. The build dir."""
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            "-DBUILD_PYTHON=OFF",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    for t in targets:
        _run(["cmake", "--build", "build", "--target", t], proj)
    return proj / "build"


def _exe(build: Path, name: str) -> Path:
    found = [p for p in build.rglob(name) if p.is_file()]
    assert found, f"{name} was not built"
    return found[0]


def _refusal(r, flag: str, arg: str, reason: str) -> "str | None":
    """Why *r* is not a refusal of ``--<flag> <arg>`` giving *reason*."""
    if r.returncode != 2:
        return f"exit {r.returncode}, not 2"
    if f": --{flag}: '{arg}' " not in r.stderr or reason not in r.stderr:
        return f"refused as {r.stderr.strip()!r}"
    return None


SOURCE = list(_composer._SOURCE_SCALARS)


def _signed(ctype: str) -> bool:
    return _range(ctype)[0] < 0


def _print_c(member: str, ctype: str) -> str:
    """A C statement printing *member* exactly, as ``<member>=<value>``."""
    name = member.rsplit(".", 1)[-1].rsplit(">", 1)[-1]
    if T._CTYPE_META[ctype]["kind"] == "float":
        return f'fprintf(stderr, "{name}=%a\\n", (double){member});'
    if _signed(ctype):
        return f'fprintf(stderr, "{name}=%lld\\n", (long long){member});'
    return f'fprintf(stderr, "{name}=%llu\\n", (unsigned long long){member});'


def _header() -> str:
    src = "".join(f"  {t} f_{_slug(t)};\n" for t in SOURCE)
    seg = "".join(f"  {t} g_{_slug(t)};\n" for t in NUMERIC)
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stdbool.h>\n#include <stddef.h>\n"
        "#include <stdint.h>\n"
        f"typedef struct {{\n{src}}} src_t;\n"
        f"typedef struct {{\n  src_t *sources;\n  size_t n_sources;\n{seg}"
        "} seg_t;\n"
        "typedef struct mix_state mix_state_t;\n"
        "mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);\n"
        "mix_state_t *mix_from_file(const char *path);\n"
        "size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);\n"
        "void mix_destroy(mix_state_t *st);\n"
        "#endif\n"
    )


def _source(pkg: str) -> str:
    """The backing: create() prints every field it was handed."""
    prints = "".join(
        f"  {_print_c(f's->sources[0].f_{_slug(t)}', t)}\n" for t in SOURCE
    ) + "".join(f"  {_print_c(f's->g_{_slug(t)}', t)}\n" for t in NUMERIC)
    return f"""#include "{pkg}/mix/mix_core.h"
#include <stdio.h>
#include <stdlib.h>
struct mix_state {{
  int unused;
}};
mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c)
{{
  (void)n; (void)r; (void)c;
{prints}  return calloc(1, sizeof(mix_state_t));
}}
mix_state_t *mix_from_file(const char *path) {{ (void)path; return NULL; }}
size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m)
{{ (void)st; (void)o; (void)m; return 0; }}
void mix_destroy(mix_state_t *st) {{ free(st); }}
"""


BACKING_CMAKE = """\
add_library(backing_core OBJECT mix_core.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
"""


def _composer_project(root: Path) -> Path:
    pkg = "cc"
    assert run_cli("new", pkg, cwd=root).returncode == 0
    proj = root / pkg
    inc = proj / "native" / "inc" / pkg / "mix"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mix_core.h").write_text(_header(), encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "mix_core.c").write_text(_source(pkg), encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {})["mix"] = {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [{"name": f"f_{_slug(t)}", "type": t} for t in SOURCE],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [{"name": f"g_{_slug(t)}", "type": t} for t in NUMERIC],
        },
        "oo": {"composer_type_name": "Mix"},
        "cli": {"enabled": True, "name": "mixcli"},
        "extra_link_libs": ["backing_core"],
    }
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _received(stderr: str) -> dict:
    """``{field: value}`` from the backing's prints."""
    out = {}
    for line in stderr.splitlines():
        name, eq, value = line.partition("=")
        if eq:
            out[name] = (
                float.fromhex(value)
                if "0x" in value or "inf" in value
                else int(value)
            )
    return out


def _want(ctype: str, value):
    """*value* as the C type holds it (a float rounds to binary32)."""
    if ctype == "float":
        return float(np.float32(value))
    return value


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_composer_cli_refuses_a_bad_number(tmp_path):
    """Every numeric source and segment flag of a built composer CLI."""
    proj = _composer_project(tmp_path)
    exe = _exe(_build(proj, "mixcli"), "mixcli")
    out = tmp_path / "samples.bin"
    flags = [(f"f_{_slug(t)}", t) for t in SOURCE] + [
        (f"g_{_slug(t)}", t) for t in NUMERIC
    ]
    problems = []
    for flag, ctype in flags:
        for arg, want in cases(ctype):
            r = subprocess.run(
                [str(exe), f"--{flag}", arg, "--out", str(out)],
                capture_output=True,
                text=True,
            )
            what = f"--{flag} {arg!r} ({ctype})"
            if isinstance(want, str):
                why = _refusal(r, flag, arg, want)
                if why:
                    problems.append(f"{what}: {why}")
            elif r.returncode != 0:
                problems.append(f"{what}: exit {r.returncode}: {r.stderr}")
            elif _received(r.stderr).get(flag) != _want(ctype, want):
                got = _received(r.stderr).get(flag)
                problems.append(f"{what}: arrived as {got!r}")
    assert not problems, "\n".join(problems)


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_app_refuses_a_bad_number(tmp_path):
    """Every type a built `jm app` executable parses, and its other faces.

    The object app's ``--record`` writes what each flag resolved to, every
    type exactly: a real with the digits that read back to its bits
    (gh-2254), so a real's extremes are held exactly here too."""
    proj = _app_project(tmp_path)
    build = _build(proj, "tool", "picker", "multi")
    tool = _exe(build, "tool")
    rec = tmp_path / "record.json"
    problems = []
    for ctype in sorted(_app._C_PARSE):
        flag = f"x_{_slug(ctype)}"
        for arg, want in cases(ctype):
            r = subprocess.run(
                [
                    str(tool),
                    f"--{flag}",
                    arg,
                    "--count",
                    "0",
                    "--record",
                    str(rec),
                    "--output",
                    str(tmp_path / "out.bin"),
                ],
                capture_output=True,
                text=True,
            )
            what = f"--{flag} {arg!r} ({ctype})"
            if isinstance(want, str):
                why = _refusal(r, flag, arg, want)
                if why:
                    problems.append(f"{what}: {why}")
                continue
            if r.returncode != 0:
                problems.append(f"{what}: exit {r.returncode}: {r.stderr}")
                continue
            got = json.loads(rec.read_text(encoding="utf-8"))[flag]
            if _want(ctype, got) != _want(ctype, want):
                problems.append(f"{what}: recorded as {got!r}")

    # The function app (its template gained the helper slot) and a
    # subcommand's flags: the same parser, behind a different main().
    picker, multi = _exe(build, "picker"), _exe(build, "multi")
    for exe, argv, flag, arg, reason in (
        (picker, ["--a", "300", "--b", "0"], "a", "300", "out of range"),
        (picker, ["--a", "1", "--b", "x"], "b", "x", "is not a number"),
        (multi, ["go", "--n", "-1"], "n", "-1", "out of range"),
        (multi, ["go", "--n", "65536"], "n", "65536", "out of range"),
    ):
        r = subprocess.run([str(exe), *argv], capture_output=True, text=True)
        why = _refusal(r, flag, arg, reason)
        if why:
            problems.append(f"{exe.name} {argv}: {why}")
    for exe, argv in (
        (picker, ["--a", "-128", "--b", "1e308"]),
        (multi, ["go", "--n", "65535"]),
    ):
        r = subprocess.run([str(exe), *argv], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"{exe.name} {argv}: exit {r.returncode}")
    assert not problems, "\n".join(problems)
