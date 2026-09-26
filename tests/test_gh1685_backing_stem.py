"""gh-1685: a capsule / composer ``backing`` that names a jm component spells
the backing API through that component's C stem.

`backing` spells the backing API from a stem: `<backing>_create`,
`_destroy`, `_reset`, `_state_t`, `_get_<prop>`, and a composer's
`_execute` / `_segments` / JSON functions. When `backing` names a jm
COMPONENT, those are the component's derived symbols. `c_prefix` renames
them, and `jm upgrade` respells the author's C to match, but the glue kept
the bare spelling. So after `c_prefix = "zz"` + `jm upgrade` + `jm apply`
(all exit 0), `lo_fn_ext.c` called `lo_create` while the header declared
`zz_lo_create`, and the build failed.

Decision (on the issue): a `backing` naming a component is a REFERENCE to
the component, like `depends_on` and `composes`, so its C symbols go
through `_csym.backing_stem` = `_csym.stem`. Anything else is a
hand-written core (doppler's `ddcr`) and is author-named: used exactly as
written, as gh-1671 treats every author-named key. Only the C symbols move.
The header path, the PyCapsule name (an ABI its consumers check) and the
Python function names stay on the FILE stem, which is the split a component
itself has. `jm status` names the reading each `backing` took.

GATE: a capsule module backed by component `lo`, built and called from
      Python before the prefix, still builds and round-trips from Python
      after `c_prefix` + `jm upgrade` + `jm apply`, with its Python names
      and capsule name unchanged; a composer's rendered glue spells every
      backing symbol through the stem when `backing` is a component and
      exactly as written when it is not; and `jm status` (text and
      `--json`) reports which rule each `backing` took.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

import _gh1653_fixture as FX
import test_composer_codegen as COMPOSER
from _jmrun import run_cli
from just_makeit import _composer
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._new import run as new_run

P = FX.PREFIX

CAPSULE_TOML = """
[module.lo_fn]
kind = "capsule"
backing = "lo"
header = "q/lo/lo_core.h"
depends_on = [{ name = "lo", link = true }]
init_params = [{ name = "gain", type = "double" }]
methods = [{ name = "reset" }]
"""

#: The Python face and the ABI string: both on the FILE stem, before and
#: after the prefix.
ROUND_TRIP = (
    "import q.lo_fn.lo_fn as m\n"
    "s = m.lo_create(2.0)\n"
    "m.lo_reset(s)\n"
    "m.lo_destroy(s)\n"
    "print(sorted(n for n in dir(m) if not n.startswith('_')))\n"
)


def _build(root, tag):
    """Configure + build with Python on, then call the capsule from Python.
    ``(step, rc, tail)`` per step; recorded, never asserted here (gh-1430)."""
    b = root / f"b-{tag}"
    steps = []
    for cmd in (
        [
            "cmake",
            "-S",
            ".",
            "-B",
            str(b),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(b)],
        [sys.executable, "-c", ROUND_TRIP],
    ):
        r = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
            timeout=600,
        )
        steps.append((cmd[:2], r.returncode, (r.stdout + r.stderr)[-3000:]))
        if r.returncode:
            break
    return steps


def _ok(steps):
    for what, rc, out in steps:
        assert rc == 0, (what, out)
    return steps[-1][2]


@pytest.fixture(scope="module")
def capsule(tmp_path_factory):
    root = tmp_path_factory.mktemp("g1685") / "q"
    new_run("q", root, c_prefix=None)
    obj = run_cli(
        "object",
        "lo",
        "--state",
        "gain:double:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    toml = root / "just-makeit.toml"
    _textio.write_text(toml, toml.read_text() + CAPSULE_TOML)
    first = run_cli("apply", cwd=root)
    before = _build(root, "before")
    FX.set_prefix(root)
    upgraded = run_cli("upgrade", cwd=root)
    applied = run_cli("apply", cwd=root)
    after = _build(root, "after") if applied.returncode == 0 else []
    status = run_cli("status", cwd=root)
    as_json = run_cli("status", "--json", cwd=root)
    ext = (root / "native" / "src" / "lo_fn" / "lo_fn_ext.c").read_text()
    return obj, first, before, upgraded, applied, after, status, as_json, ext


def test_the_capsule_builds_and_runs_before_the_prefix(capsule):
    obj, first, before, *_ = capsule
    assert obj.returncode == 0, obj.stdout + obj.stderr
    assert first.returncode == 0, first.stdout + first.stderr
    _ok(before)


def test_the_capsule_builds_and_runs_after_the_upgrade(capsule):
    _o, _f, before, upgraded, applied, after, *_ = capsule
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert applied.returncode == 0, applied.stdout + applied.stderr
    # The Python face is unchanged: same function names as before.
    assert _ok(after) == _ok(before)


def test_the_glue_calls_the_components_prefixed_api(capsule):
    ext = capsule[-1]
    for sym in ("create", "destroy", "reset", "state_t"):
        assert f"{P}_lo_{sym}" in ext, sym
    # A bare `lo_*` survives only as a Python name or in the wrapper's own
    # `_fn_lo_*` name -- never as a call into the core.
    calls = re.findall(r"(?<![\w\"])lo_(?:create|destroy|reset)\s*\(", ext)
    assert not calls, calls


def test_the_capsule_name_and_python_names_keep_the_file_stem(capsule):
    ext = capsule[-1]
    assert '_CAPS[] = "q.lo_fn.lo_state"' in ext
    assert '{"lo_create", _fn_lo_create,' in ext


def test_status_names_the_rule_a_backing_took(capsule):
    status, as_json = capsule[6], capsule[7]
    rule = f"component `lo` -> symbols via its stem `{P}_lo_*`"
    assert f"[module.lo_fn] backing = 'lo': {rule}" in status.stdout, (
        status.stdout
    )
    assert json.loads(as_json.stdout)["backings"] == [
        {"module": "lo_fn", "backing": "lo", "rule": rule}
    ]


# -- composer: the same stem, at the render ---------------------------------

_COMPOSER_SYMS = re.compile(
    r"(?<!\w)(?:\w+_)?wfm_compose_(?:create|destroy|execute|segments"
    r"|state_t|from_json|from_file|to_json)\b"
)


def _composer_symbols(cfg):
    text = "".join(
        f(cfg, "wfm_compose")
        for f in (
            _composer.render_ext,
            _composer.render_json_funcs,
            _composer.render_cli,
        )
    )
    return {s for s in _COMPOSER_SYMS.findall(text) if not s.startswith("_")}


def test_a_composer_backed_by_a_component_uses_its_stem():
    cfg = COMPOSER._cfg()
    cfg["project"]["c_prefix"] = P
    cfg["wfm_compose"] = {}  # `backing` now names a jm component
    syms = _composer_symbols(cfg)
    assert syms and all(s.startswith(f"{P}_wfm_compose_") for s in syms), syms


def test_an_author_named_backing_is_used_as_written():
    """doppler's shape: a hand-written core under a prefix is untouched."""
    cfg = COMPOSER._cfg()
    cfg["project"]["c_prefix"] = P
    syms = _composer_symbols(cfg)
    assert syms and all(s.startswith("wfm_compose_") for s in syms), syms
    assert _csym.backing_rule(cfg, "wfm_compose") == (
        "author-named -> symbols spelled `wfm_compose_*` as written"
    )
