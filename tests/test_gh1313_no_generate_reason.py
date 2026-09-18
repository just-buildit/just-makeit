"""A `no_generate` opt-out says why, or `jm status --check` fails (gh-1313).

`no_generate` records THAT a module is hand-written and nothing about why.
"jm cannot express this shape" and "nobody migrated it yet" want opposite
treatment, and without a reason they read the same -- doppler's ring buffer had
to be re-derived from its macro to write gh-1299 up.

jm cannot tell a new opt-out from an old one (the key is set by hand, never
through a command), so there is no grandfather list: the finding gates from
the release that ships it, and is cleared by one line of TOML. A reason left on
a module that generates again is stale and gates too.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def _project(tmp_path: Path, table: str) -> Path:
    """One object, plus a hand-written module `hw` declared by *table*."""
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(object_run, root, "o", None, state_vars=[("n", "int", "0")])
    toml = root / "just-makeit.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + "\n[module.hw]\n" + table,
        encoding="utf-8",
    )
    (root / "native" / "src" / "hw").mkdir(parents=True)
    (root / "native" / "src" / "hw" / "CMakeLists.txt").write_text(
        "# hand-written\n", encoding="utf-8"
    )
    _quiet(apply_run, root)
    return root


def _check(root: Path) -> tuple[int, str]:
    return _quiet(_status.run, root, check=True)


def test_an_explained_opt_out_is_clean(tmp_path):
    """The control: everything else in the fixture is in sync."""
    rc, out = _check(
        _project(
            tmp_path,
            'no_generate = "true"\n'
            'no_generate_reason = "vendored pocketfft; gh-275"\n',
        )
    )
    assert rc == 0, out
    assert "UNEXPLAINED" not in out


@pytest.mark.parametrize(
    "table",
    [
        'no_generate = "true"\n',
        'no_generate = "true"\nno_generate_reason = "   "\n',
    ],
    ids=["absent", "blank"],
)
def test_an_unexplained_opt_out_fails_the_check(tmp_path, table):
    rc, out = _check(_project(tmp_path, table))
    assert rc == 1, out
    assert "UNEXPLAINED OPT-OUT (1)" in out
    assert "[module.hw] no_generate, and no reason given" in out
    # The fix is in the message: the key to write.
    assert "no_generate_reason = " in out


def test_a_reason_that_outlived_its_opt_out_fails_the_check(tmp_path):
    rc, out = _check(
        _project(tmp_path, 'no_generate_reason = "left behind"\n')
    )
    assert rc >= 1, out
    assert "[module.hw] no_generate_reason, but the module is not" in out


def test_status_allow_does_not_waive_it(tmp_path):
    """One line of TOML clears it; a waiver would only hide it."""
    root = _project(tmp_path, 'no_generate = "true"\n')
    toml = root / "just-makeit.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            "[project]\n", '[project]\nstatus_allow = ["**"]\n', 1
        ),
        encoding="utf-8",
    )
    rc, out = _check(root)
    assert rc >= 1, out


def test_the_json_report_names_it(tmp_path):
    root = _project(tmp_path, 'no_generate = "true"\n')
    rc, out = _quiet(_status.run, root, as_json=True)
    assert json.loads(out)["no_generate_reason"] == [
        {"module": "hw", "problem": "unexplained"}
    ]
    assert rc == 1
