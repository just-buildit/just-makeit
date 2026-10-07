"""gh-2008: an ``[[<obj>.array_args]]`` row's unread key is reported.

The rows -- the ``--array-arg name:dtype`` constructor arrays -- were walked by
nothing in :func:`_keys.unknown_keys`, and :func:`_config.array_args` reads
only ``name`` and ``type`` / ``dtype``. So any other key on a row was accepted,
kept, and did nothing, in silence: gh-2004's ``rank = 1`` written there left
the array flattening through ``PyArray_SIZE``, and a misspelt ``dtype`` was
just as quiet.

The rows now have a vocabulary, ``_keys.ARRAY_ARG_KEYS``, and are walked like
every other table. It is closed on purpose (the maintainer's call on the
issue): an ``array_args`` row is the older spelling of a constructor array and
does not grow gh-2004's shape keys, so a ``rank`` / ``elements_per_sample``
there is reported with the table that honours it, ``[[<obj>.init_params]]``.

Every case here is DERIVED from ``_keys``: the keys reported are every key any
vocabulary knows, minus this row's, plus spellings no vocabulary has; and the
vocabulary is held equal to what the reader actually reads, by recording the
reader's lookups. Nothing is listed that could fall behind.

GATE: every key an ``array_args`` row does not read is reported, naming the
row.
"""

from __future__ import annotations

import pytest

from _jmrun import run_cli, warning_lines

from just_makeit import _config as C
from just_makeit import _keys
from just_makeit._keys import (
    ARRAY_ARG_KEYS,
    KIND_KEYS,
    SHAPE_KEYS,
    unknown_keys,
)

#: Spellings no vocabulary contains: the typo the issue names, and one more.
_NOBODYS = ("dtpye", "shape")

#: Every key some table reads and this row does not, plus `_NOBODYS`.
_NOT_READ_HERE = sorted(
    (set().union(*KIND_KEYS.values()) - ARRAY_ARG_KEYS) | set(_NOBODYS)
)


def _cfg(row: dict) -> dict:
    """A manifest whose object ``o`` declares the one array_args *row*."""
    return {
        "project": {"name": "p"},
        "o": {
            "arg_type": "float",
            "return_type": "float",
            "array_args": [row],
        },
    }


@pytest.mark.parametrize("key", _NOT_READ_HERE)
def test_a_key_the_row_does_not_read_is_reported_naming_the_row(key):
    (found,) = unknown_keys(_cfg({"name": "w", "type": "float32", key: 1}))
    assert (found.kind, found.where, found.key) == ("array_arg", "o.w", key)
    assert found.message().startswith(f"o.w: unknown array_arg key `{key}`")


@pytest.mark.parametrize("key", SHAPE_KEYS)
def test_a_shape_key_points_at_the_table_that_honours_it(key):
    """gh-2004 reads these on an init param; the report says so."""
    (found,) = unknown_keys(_cfg({"name": "w", "type": "float32", key: 1}))
    msg = found.message()
    assert "`[[<object>.init_params]]`" in msg, msg
    assert "gh-2004" in msg, msg


@pytest.mark.parametrize(
    "row",
    [
        {"name": "w", "type": "float32"},
        {"name": "w", "dtype": "float32"},
        {key: "float32" for key in ARRAY_ARG_KEYS},
    ],
    ids=["type", "dtype", "every vocabulary key"],
)
def test_a_key_the_row_reads_is_not_reported(row):
    assert unknown_keys(_cfg(row)) == []


class _Recording(dict):
    """A row that remembers every key looked up on it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read: set = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)


def test_the_vocabulary_is_exactly_what_the_reader_reads():
    """Both directions, from the reader itself rather than a copy of it.

    A key the reader takes that the vocabulary lacks would be reported as
    unread while it changed the build; a vocabulary key the reader never
    looks up would silence the report on a key that does nothing. A row with
    no type is what makes the reader try both spellings.
    """
    row = _Recording({"name": "w"})
    C.array_args({"o": {"array_args": [row]}}, "o")
    assert row.read == set(ARRAY_ARG_KEYS)


def test_apply_reports_the_issue_trigger_and_still_exits_0(tmp_path):
    """The issue's own fragment, through the command it was found with.

    Advisory, like every unknown key (`_keys` module docstring): reported
    once, and the apply goes ahead. On main it printed no warning at all.
    """
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    frag = tmp_path / "frag.toml"
    frag.write_text(
        '[o]\narg_type = "float"\nreturn_type = "float"\n'
        'no_state = "true"\nno_step = "true"\n\n'
        '[[o.array_args]]\nname = "w"\ntype = "float32"\nrank = 1\n',
        encoding="utf-8",
    )
    _keys._SEEN.clear()
    r = run_cli("apply", str(frag), cwd=tmp_path / "p")
    assert r.returncode == 0, r.stderr
    hits = [
        line
        for line in warning_lines(r.stdout + r.stderr)
        if "unknown array_arg key `rank`" in line
    ]
    assert len(hits) == 1, r.stdout + r.stderr
    assert "o.w:" in hits[0] and "init_params" in hits[0], hits[0]
