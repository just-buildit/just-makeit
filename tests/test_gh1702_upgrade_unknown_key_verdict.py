"""`jm upgrade` says what `jm apply` will do with an unknown key (gh-1702).

gh-887 made `upgrade` report a key this jm does not read, rather than calling
the manifest "already up to date". It also printed::

    Not up to date: `just-makeit apply` will refuse until these are resolved.

and that half was never true. `apply` treats an unknown key as advisory --
`_keys.warn_unknown_keys` reports with `gates=False`, and the `_keys` module
docstring says the walk deliberately does not raise -- so it warns and exits
0. Measured on doppler's manifest at 0.92.2: eight unknown composer keys,
`apply` exit 0, `upgrade` "Not up to date". A permanently false "not up to
date" trains the reader to skip the line the day it is true.

gh-887's own refusal was never the key. It was the declaration the key left
incoherent -- `error` with `check_return` instead of `status_return` -- and
that is refused inside `_config.load`, which both commands pass through. So
the one predicate that decides a refusal is the same code in both, and these
tests hold the two verdicts equal by running the REAL commands over each
shape, rather than by asserting a sentence.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402

from just_makeit import _config as C  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: Each shape: the keys written onto one method row, and whether `apply`
#: refuses the result. The verdict column is what `apply` is MEASURED to do
#: below, never trusted from this table alone.
SHAPES = {
    # A key jm reads nowhere: doppler's case.
    "unread": ({"zzprobe": "1"}, False),
    # A key valid on another table, with a named replacement: still only a
    # warning. The replacement is the author's call (gh-887).
    "retired": ({"check_return": "true"}, False),
    # The same retired key beside `error`, which then has nothing to raise
    # on: refused -- by `load`, for the declaration, in both commands.
    "retired_incoherent": (
        {"check_return": "true", "error": "ValueError"},
        True,
    ),
}


def _project(root: Path, extra: dict) -> Path:
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
        method_run(root, "w", "close", None, "void", "int", False, [])
    cfg = C.load(root)
    for meth in cfg["w"]["methods"]:
        if meth["name"] == "close":
            meth.update(extra)
    C.save(root, cfg)
    return root


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_upgrade_and_apply_agree(tmp_path, shape):
    """`upgrade` claims a refusal exactly when `apply` exits non-zero."""
    extra, refuses = SHAPES[shape]
    root = _project(tmp_path / "p", extra)
    up = run_cli("upgrade", cwd=root)
    ap = run_cli("apply", cwd=root)

    assert (ap.returncode != 0) is refuses, (
        f"{shape}: apply exit {ap.returncode}, expected refusal={refuses}; "
        f"the table above is wrong, not the product\n{ap.stderr}"
    )
    upgrade_refuses = up.returncode != 0 or "will refuse" in up.stdout
    assert upgrade_refuses is refuses, (
        f"{shape}: `upgrade` and `apply` disagree -- apply exit "
        f"{ap.returncode}, upgrade exit {up.returncode}:\n{up.stdout}"
        f"{up.stderr}"
    )


@pytest.mark.parametrize("shape", ["unread", "retired"])
def test_an_advisory_key_is_not_called_not_up_to_date(tmp_path, shape):
    """The key is reported and called advisory -- and nothing claims more."""
    extra, _ = SHAPES[shape]
    root = _project(tmp_path / "p", extra)
    out = run_cli("upgrade", cwd=root).stdout
    (key,) = [k for k in extra]
    assert key in out, f"the unread key is not named:\n{out}"
    assert "Not up to date" not in out, out
    assert "Advisory" in out and "exits 0" in out, (
        f"upgrade does not say what apply will do with the key:\n{out}"
    )


def test_an_incoherent_declaration_refuses_both_with_one_message(tmp_path):
    """The refusal is the same code in both, so the same words.

    If this ever diverges, `upgrade` grew its own verdict for a manifest
    and the two can disagree again.
    """
    extra, _ = SHAPES["retired_incoherent"]
    root = _project(tmp_path / "p", extra)
    up = run_cli("upgrade", cwd=root)
    ap = run_cli("apply", cwd=root)

    def _error(stderr: str) -> str:
        return stderr[stderr.index("error:") :].strip()

    assert "error:" in up.stderr and "error:" in ap.stderr, (up, ap)
    assert _error(up.stderr) == _error(ap.stderr)
