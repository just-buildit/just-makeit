"""gh-2140: a composer CLI reads an enum segment field as a choice.

The generated composer CLI (`_composer.render_cli`) read every segment
field as a number. An ``enum`` source field was a choice string, looked up
in its ``[[enum]]`` table; the segment-field loop had no such branch, so
doppler's ``gap_noise`` shape --

    [[module.X.segment.fields]]
    name = "gap_noise"
    type = "int"
    enum = "gap_noise"
    default = "auto"

-- rendered ``int gap_noise = (int)auto;``, which does not compile (``auto``
is a C keyword), and ``--gap_noise off`` read ``strtoull("off")``: 0,
whatever choice was named.

Now a source and a segment enum field get ONE treatment,
`_composer._cli_enum_flag`: the flag's local is the field's C value,
initialised to the manifest default through `_enumc.default_c` (as every
other face spells it), and the flag is looked up through
`_enumc.validate_c` -- the one choice-string emitter (gh-1026) -- whose
refusal on a C command line is ``<prog>: --<flag>: invalid choice '<s>'
(choices: ...)`` and exit 2. A constant-bound enum (gh-1450) reaches C as
its constant, on the source field as well: that branch had stored the
choice's index.

GATE: a built composer CLI with an enum source field and enum segment
fields (one constant-bound) starts at each field's default, sets the
field's C value for every choice, and refuses an unknown choice with exit
2 naming the flag.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C

sys.path.insert(0, str(Path(__file__).parent))
from test_composer_codegen import _cfg  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The enums the built CLI declares: name -> (choices, the C value of each).
#: ``gap_noise`` is doppler's, position is the C int; ``lvl`` binds
#: constants the backing header defines (gh-1450), so position is NOT the
#: value -- the test states the values, not the code under test.
ENUMS = {
    "gap_noise": (["auto", "off"], [0, 1]),
    "lvl": (["lo", "hi"], [3, 7]),
    "wave": (["tone", "noise", "pn"], [0, 1, 2]),
}

#: (table, field, enum, default): every enum flag the CLI carries.
FIELDS = [
    ("segment", "gap_noise", "gap_noise", "auto"),
    ("segment", "level", "lvl", "hi"),
    ("source", "kind", "wave", "noise"),
    ("source", "tier", "lvl", "lo"),
]


def _manifest_enums() -> list:
    out = []
    for name, (values, _vals) in ENUMS.items():
        row = {"name": name, "values": values}
        if name == "lvl":
            row["enumerators"] = ["LVL_LO", "LVL_HI"]
        out.append(row)
    return out


# ── the render ───────────────────────────────────────────────────────────────


def _gap_noise_cfg() -> dict:
    """The codegen fixture with a CLI and doppler's gap_noise field."""
    cfg = copy.deepcopy(_cfg())
    cfg["enum"].append({"name": "gap_noise", "values": ["auto", "off"]})
    mod = cfg["module"]["wfm_compose"]
    mod["cli"] = {"enabled": True}
    mod["segment"]["fields"].append(
        {
            "name": "gap_noise",
            "type": "int",
            "enum": "gap_noise",
            "default": "auto",
        }
    )
    return cfg


def _case(text: str, flag: str) -> str:
    """The argv case for ``--<flag>`` in a rendered CLI."""
    m = re.search(
        rf'else if \(!strcmp\(a, "--{flag}"\)[^\n]*(?:\n(?!        else ).*)*',
        text,
    )
    assert m, f"no --{flag} case"
    return m.group(0)


def test_enum_segment_field_renders_as_a_choice():
    text = _composer.render_cli(_gap_noise_cfg(), "wfm_compose")
    assert "(int)auto" not in text
    assert re.search(r"^    int gap_noise = 0;$", text, re.M), (
        "the default is not the choice's C value"
    )
    case = _case(text, "gap_noise")
    assert "strto" not in case, "the choice is read as a number"
    assert "_enum_index(_enum_gap_noise, " in case
    assert ": --gap_noise: invalid choice" in case


def test_every_enum_field_of_either_table_is_a_choice():
    """Registration-free: every enum field the manifest declares, source
    or segment, is looked up in its table and refused naming its flag."""
    cfg = _gap_noise_cfg()
    text = _composer.render_cli(cfg, "wfm_compose")
    mod = cfg["module"]["wfm_compose"]
    seen = set()
    for table in ("source", "segment"):
        for f in mod[table]["fields"]:
            if not f.get("enum"):
                continue
            n, e = f["name"], f["enum"]
            case = _case(text, n)
            assert "strto" not in case, n
            assert f"_enum_index(_enum_{e}, " in case, n
            assert f": --{n}: invalid choice" in case, n
            assert re.search(rf"^            {n} = _v;$", case, re.M), n
            seen.add(table)
    assert seen == {"source", "segment"}


# ── the build ────────────────────────────────────────────────────────────────


def _header() -> str:
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stddef.h>\n"
        "enum { LVL_LO = 3, LVL_HI = 7 };\n"
        "typedef struct {\n  int kind;\n  int tier;\n"
        "  double freq;\n} src_t;\n"
        "typedef struct {\n  src_t *sources;\n  size_t n_sources;\n"
        "  int gap_noise;\n  int level;\n  size_t count;\n} seg_t;\n"
        "typedef struct mix_state mix_state_t;\n"
        "mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);\n"
        "mix_state_t *mix_from_file(const char *path);\n"
        "size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);\n"
        "void mix_destroy(mix_state_t *st);\n"
        "#endif\n"
    )


def _source(pkg: str) -> str:
    """The backing: create() prints the enum fields it was handed."""
    return f"""#include "{pkg}/mix/mix_core.h"
#include <stdio.h>
#include <stdlib.h>
struct mix_state {{
  int unused;
}};
mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c)
{{
  (void)n; (void)r; (void)c;
  fprintf(stderr, "gap_noise=%d\\n", s->gap_noise);
  fprintf(stderr, "level=%d\\n", s->level);
  fprintf(stderr, "kind=%d\\n", s->sources[0].kind);
  fprintf(stderr, "tier=%d\\n", s->sources[0].tier);
  return calloc(1, sizeof(mix_state_t));
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


def _field(name: str, enum: str, default: str) -> dict:
    return {"name": name, "type": "int", "enum": enum, "default": default}


def _project(root: Path) -> Path:
    pkg = "ce"
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
    cfg["enum"] = _manifest_enums()
    src = [_field(n, e, d) for t, n, e, d in FIELDS if t == "source"]
    seg = [_field(n, e, d) for t, n, e, d in FIELDS if t == "segment"]
    cfg.setdefault("module", {})["mix"] = {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": src + [{"name": "freq", "type": "double"}],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": seg + [{"name": "count", "type": "size_t"}],
        },
        "oo": {"composer_type_name": "Mix"},
        "cli": {"enabled": True, "name": "mixcli"},
        "extra_link_libs": ["backing_core"],
    }
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def _received(stderr: str) -> dict:
    out = {}
    for line in stderr.splitlines():
        name, eq, value = line.partition("=")
        if eq:
            out[name] = int(value)
    return out


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_built_cli_sets_and_refuses_every_choice(tmp_path):
    proj = _project(tmp_path)
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
    _run(["cmake", "--build", "build", "--target", "mixcli"], proj)
    exe = [p for p in (proj / "build").rglob("mixcli") if p.is_file()][0]
    out = str(tmp_path / "samples.bin")

    def cli(*argv: str):
        return subprocess.run(
            [str(exe), *argv, "--out", out], capture_output=True, text=True
        )

    problems = []
    r = cli()
    defaults = {n: ENUMS[e][1][ENUMS[e][0].index(d)] for _t, n, e, d in FIELDS}
    if r.returncode != 0 or _received(r.stderr) != defaults:
        problems.append(f"no flags: {r.returncode} {r.stderr!r}")
    for _table, name, enum, _default in FIELDS:
        choices, values = ENUMS[enum]
        for choice, value in zip(choices, values):
            r = cli(f"--{name}", choice)
            got = _received(r.stderr).get(name)
            if r.returncode != 0 or got != value:
                problems.append(
                    f"--{name} {choice}: exit {r.returncode}, got {got!r},"
                    f" want {value}"
                )
        for bad in ("bogus", str(values[-1]), ""):
            r = cli(f"--{name}", bad)
            said = f": --{name}: invalid choice '{bad}' (choices: "
            if r.returncode != 2 or said not in r.stderr:
                problems.append(
                    f"--{name} {bad!r}: exit {r.returncode}, {r.stderr!r}"
                )
            elif ", ".join(choices) not in r.stderr:
                problems.append(f"--{name} {bad!r}: names no choices")
    assert not problems, "\n".join(problems)
