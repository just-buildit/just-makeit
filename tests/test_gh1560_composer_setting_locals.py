"""gh-1560: two composer settings never declare the same C local.

A composer's `tp_init` gives each gh-1126 setting ``n`` a value local and a
was-it-passed flag. The flag was ``_st_<n>_set`` -- the VALUE local of a
sibling setting named ``<n>_set`` -- so settings ``gain`` and ``gain_set``
declared ``int _st_gain_set`` twice and the project did not compile. The flag
is now ``_stset_<n>``, which no value local (all ``_st_...``) can spell.

The gate compiles the emitted pop and apply code for adversarial sibling
pairs, so a redeclaration is a compile error here, not a string match.

GATE: no two composer settings, whatever their names, declare the same C local
      in the generated ``tp_init``; the emitted code compiles for sibling pairs
      built to collide.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sysconfig

import pytest

from just_makeit import _composer

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

#: Sibling pairs chosen to collide under some plausible naming scheme.
PAIRS = [
    ("gain", "gain_set"),
    ("gain_set", "gain"),
    ("a", "stset_a"),
    ("set", "_set"),
]


def _cfg(names) -> dict:
    return {
        "project": {"name": "p"},
        "module": {
            "wc": {
                "kind": "composer",
                "settings": [
                    {
                        "name": n,
                        "setter_fn": f"b_set_{i}",
                        "getter_fn": f"b_get_{i}",
                        "type": "int",
                    }
                    for i, n in enumerate(names)
                ],
            }
        },
    }


@pytest.mark.parametrize("names", PAIRS, ids="+".join)
def test_each_local_is_declared_once(names):
    code = _composer._settings_pop_c(_cfg(names), "wc")
    declared = re.findall(r"^    \w+ (\w+) = 0;$", code, re.M)
    assert len(declared) == 2 * len(names), code
    assert len(set(declared)) == len(declared), declared


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
@pytest.mark.parametrize("names", PAIRS, ids="+".join)
def test_the_settings_code_compiles(tmp_path, names):
    cfg = _cfg(names)
    protos = "".join(
        f"void b_set_{i}(void *s, int v);\n" for i in range(len(names))
    )
    src = tmp_path / "probe.c"
    src.write_text(
        "#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n"
        f"{protos}"
        "typedef struct { void *state; } S;\n"
        "int probe(S *self, PyObject *kw)\n{\n"
        f"{_composer._settings_pop_c(cfg, 'wc')}"
        f"{_composer._settings_apply_c(cfg, 'wc')}"
        "    return 0;\n}\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            _CC,
            "-c",
            "-std=c11",
            "-Werror",
            f"-I{sysconfig.get_paths()['include']}",
            str(src),
            "-o",
            str(tmp_path / "probe.o"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
