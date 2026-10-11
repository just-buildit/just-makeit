"""gh-1889 / gh-1890: `jm add` asks before it writes, and only appends.

gh-1889. `jm add` saved the manifest and only then reached the prompt, which
was `jm regenerate`'s own. Answering N printed ``aborted.`` and exited 0 with
the new ``[[g.state]]`` row already on disk and nothing rebuilt behind it:
`jm status` reported STALE, and the `jm apply` it advises rewrote
``create()`` against a ``_core.c`` that was never regenerated, so the project
stopped compiling. A non-interactive run (stdin at EOF) declined the same way,
which is how two example tests went on passing through it.

gh-1890. `jm add` rebuilt ``cfg[obj]["state"]`` from `C.state_vars`, whose
``(name, type, default)`` triples skip an opaque row by design. So one add
deleted every opaque field and every ``doc``, ``no_ctor``, ``controllable``
and ``str_hint`` on the rows it kept -- the "rebuild a row from a tuple"
class of gh-838 and gh-1760.

GATE: a declined add (``n``, and EOF) exits non-zero and leaves every file in
      the project byte-identical, with `jm status` at exit 0; and an add over
      state rows that between them carry every key in `_keys.STATE_KEYS`
      (checked against `_keys`, so a new key without a row here fails) keeps
      those rows' text byte for byte, appends only the new one, and leaves
      `status --check` at exit 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _keys


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, by relative path, as bytes."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("answer", ["n\n", ""], ids=["n", "eof"])
def test_a_declined_add_writes_nothing(tmp_path, answer):
    """gh-1889: the prompt comes before the save, and declining fails."""
    assert run_cli("new", "p", "--object", "g", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    before = _snapshot(proj)

    r = run_cli("add", "--state", "k:int:3", cwd=proj, stdin=answer)

    assert r.returncode != 0, (
        "a declined `jm add` exited 0, so a script cannot tell that nothing "
        f"was added\n{r.stdout}\n{r.stderr}"
    )
    after = _snapshot(proj)
    changed = sorted(
        k
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    )
    assert not changed, (
        f"a declined `jm add` wrote {changed}: the manifest was saved before "
        "the prompt, so the project is left STALE with nothing rebuilt"
    )
    s = run_cli("status", cwd=proj)
    assert s.returncode == 0, s.stdout + s.stderr


#: One value per `_keys.STATE_KEYS` member, spread over the rows that can
#: legally carry it: a `str_hint` needs an array field and is refused on an
#: opaque one, and `opaque` takes no default. The union is checked against
#: `_keys` below, so a new state key fails here until a row carries it.
_ROWS = """
[[o.state]]
name = "drive"
type = "double"
default = "1.5"
doc = "Drive, applied per sample."
controllable = true

[[o.state]]
name = "taps"
type = "float[4]"
doc = "Filter taps."
no_ctor = true
str_hint = "pass a float32 array, e.g. np.zeros(4, np.float32)"

[[o.state]]
name = "scratch"
type = "float *"
doc = "Work buffer the author allocates in create_impl."
opaque = true
"""


def test_the_rows_carry_every_state_key():
    """The fixture is complete by construction, not by memory."""
    rows = C.tomllib.loads(_ROWS)["o"]["state"]
    carried = set().union(*(set(row) for row in rows))
    assert carried == _keys.STATE_KEYS, (
        "`_keys.STATE_KEYS` and the rows `jm add` is proven to keep differ: "
        f"missing {sorted(_keys.STATE_KEYS - carried)}, unknown "
        f"{sorted(carried - _keys.STATE_KEYS)}. Put the new key on a row in "
        "_ROWS."
    )


@pytest.fixture()
def rows_project(tmp_path):
    """A project whose object `o` carries `_ROWS`, applied."""
    assert run_cli("new", "p", "--object", "o", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    frag = proj / "objects" / "o.toml"
    frag.write_bytes(frag.read_bytes() + _ROWS.encode("utf-8"))
    applied = run_cli("apply", cwd=proj)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert _ROWS in frag.read_text(encoding="utf-8"), (
        "the fixture rows did not survive `jm apply`"
    )
    return proj


def test_add_keeps_every_existing_row(rows_project):
    """gh-1890: the existing rows are appended to, never rebuilt."""
    frag = rows_project / "objects" / "o.toml"
    before = frag.read_text(encoding="utf-8")

    r = run_cli("add", "--state", "extra:int:7", "--force", cwd=rows_project)
    assert r.returncode == 0, r.stdout + r.stderr

    after = frag.read_text(encoding="utf-8")
    assert after.startswith(before), (
        "`jm add` rewrote state rows it was only meant to append to:\n"
        f"--- before\n{before}\n--- after\n{after}"
    )
    old = C.tomllib.loads(before)["o"]["state"]
    new = C.tomllib.loads(after)["o"]["state"]
    assert new == old + [{"name": "extra", "type": "int", "default": "7"}]
    s = run_cli("status", "--check", cwd=rows_project)
    assert s.returncode == 0, s.stdout + s.stderr


@pytest.mark.parametrize(
    "flags",
    [
        # An opaque row's name: `C.state_vars` skips the row, so a check
        # that read it let a second `scratch` field through.
        ("--state", "scratch:int:0"),
        # The same name twice in one command.
        ("--state", "fresh:int:0", "--state", "fresh:int:1"),
    ],
    ids=["opaque-row", "repeated"],
)
def test_a_taken_name_is_refused_before_anything_is_written(
    rows_project, flags
):
    """Every row's name is taken, the opaque ones and the new ones too."""
    before = _snapshot(rows_project)
    r = run_cli("add", *flags, "--force", cwd=rows_project)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "already exists" in r.stderr, r.stderr
    assert _snapshot(rows_project) == before
