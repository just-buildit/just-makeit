"""``docs/types.md`` says which types each slot takes; the code decides.

gh-2041. The page said ``bool``, ``int`` and ``long double _Complex`` were
not legal elements of a step ``T[]``, while all three scaffolded, built and
passed ``make test``. The page had one "array element" column for three
slots that never shared a set: a step ``T[]`` takes every step type
(:data:`_types.STEP_TYPES`), an array PARAMETER only the types with a numpy
enum in jm's array table (:data:`_types.SUPPORTED_ARRAY_CTYPES`), and a state
``T[N]`` that set plus ``int`` and ``long double _Complex``
(:data:`_types.STATE_ARRAY_NPY`). A table restating a registry goes stale
the moment the registry moves, so every element table on the page is read
back here and compared with the predicate the command line asks:

- the quick reference's Supported types table, column by column -- S
  (``state_type_error`` of ``T``), N (``state_type_error`` of ``T[N]``),
  IO (``step_type_error`` of ``T`` and of ``T[]``, which must agree, or
  one column cannot state both) and A (``SUPPORTED_ARRAY_CTYPES``, which
  ``--param``, ``--out-param`` and ``--out-type`` check) -- plus its NumPy
  column against each type's ``py_type``;
- the per-slot State variable types tables, whose rows must be exactly the
  state types;
- the Array parameter element types table, whose rows must be exactly
  ``SUPPORTED_ARRAY_CTYPES``.

Not held: the I and P columns. Every registered type is legal in both and
no predicate distinguishes them, so a check would restate the registry
rather than ask the code. Both directions are checked everywhere else, so a
type registered later fails here until the page places it.

Pure parsing, no scaffold: well under a second.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from just_makeit import _types as T

DOC = Path(__file__).parent.parent / "docs" / "types.md"

#: Every registered type, the rows each table is held to.
REGISTERED = set(T._CTYPE_META)

#: Column -> the set the code accepts there, each asked the way the command
#: line asks it.
CODE = {
    "S": {t for t in REGISTERED if T.state_type_error("f", t) is None},
    "N": {t for t in REGISTERED if T.state_type_error("f", f"{t}[4]") is None},
    "IO": {
        t
        for t in REGISTERED
        if T.step_type_error("x", t) is None
        and T.step_type_error("x", f"{t}[]") is None
    },
    "A": {t for t in REGISTERED if t in T.SUPPORTED_ARRAY_CTYPES},
}

_HEADING = re.compile(r"^(#{1,6}) ")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_ONE_SPAN = re.compile(r"^`([^`]+)`$")


def _section(text: str, heading: str) -> "list[str]":
    """The lines under *heading*, up to the next heading of its level or
    higher. *heading* is the whole line, so ``### Supported types`` and
    ``## Supported types`` are different sections; fenced code is skipped,
    since a ``# comment`` in a shell block is not a heading."""
    lines = text.splitlines()
    assert lines.count(heading) == 1, f"{heading!r} is not one heading"
    level = len(_HEADING.match(heading).group(1))
    out, fenced = [], False
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            m = _HEADING.match(line)
            if m and len(m.group(1)) <= level:
                break
        out.append(line)
    return out


def _tables(lines: "list[str]") -> "list[list[dict[str, str]]]":
    """Each markdown table in *lines*, as rows keyed by header cell."""
    tables, block = [], []
    for line in lines + [""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            cells = [
                [c.strip() for c in _CELL_SPLIT.split(row.strip())[1:-1]]
                for row in block
            ]
            header, rows = cells[0], cells[2:]
            tables.append([dict(zip(header, row)) for row in rows])
            block = []
    return tables


def _by_type(rows: "list[dict[str, str]]", key: str) -> "dict[str, dict]":
    """Rows whose *key* cell is one code span naming a type, by that type.
    A row that spells a shape (``T[N]``) or a non-type (``void``) is not a
    registered type and is left to the prose around the table."""
    out: "dict[str, dict]" = {}
    for row in rows:
        m = _ONE_SPAN.match(row[key])
        if m and m.group(1) in REGISTERED:
            assert m.group(1) not in out, f"{m.group(1)} listed twice"
            out[m.group(1)] = row
    return out


def _quick_reference() -> "dict[str, dict]":
    text = DOC.read_text(encoding="utf-8")
    (table,) = _tables(_section(text, "### Supported types"))
    return _by_type(table, "C type")


def _diff(documented: set, code: set) -> str:
    return (
        f"page marks but the code refuses: {sorted(documented - code)}; "
        f"code accepts but the page does not mark: {sorted(code - documented)}"
    )


def test_quick_reference_lists_every_registered_type() -> None:
    assert set(_quick_reference()) == REGISTERED


@pytest.mark.parametrize("column", sorted(CODE))
def test_quick_reference_column_is_what_the_code_accepts(column: str) -> None:
    rows = _quick_reference()
    marked = {t for t, row in rows.items() if row[column] == "✓"}
    assert marked == CODE[column], f"column {column}: " + _diff(
        marked, CODE[column]
    )


def test_one_io_column_states_scalar_and_array() -> None:
    """The IO column says "as a scalar and as the element of a step T[]".
    That is one answer only while the code gives one; when it stops, the
    column must split rather than go on stating the wider of the two."""
    split = sorted(
        t
        for t in REGISTERED
        if (T.step_type_error("x", t) is None)
        != (T.step_type_error("x", f"{t}[]") is None)
    )
    assert not split, f"scalar and T[] step legality differ for {split}"


def test_quick_reference_dtypes_are_the_registered_ones() -> None:
    wrong = {
        t: row["NumPy dtype"]
        for t, row in _quick_reference().items()
        if row["NumPy dtype"] != f"`{T._CTYPE_META[t]['py_type']}`"
    }
    assert not wrong, wrong


def test_state_variable_tables_list_the_state_types() -> None:
    text = DOC.read_text(encoding="utf-8")
    rows: "dict[str, dict]" = {}
    for table in _tables(_section(text, "## Supported types")):
        rows.update(_by_type(table, "Type"))
    assert set(rows) == CODE["S"], _diff(set(rows), CODE["S"])
    wrong = {
        t: row["NumPy type"]
        for t, row in rows.items()
        if row["NumPy type"] != f"`{T._CTYPE_META[t]['py_type']}`"
    }
    assert not wrong, wrong


def test_array_parameter_table_is_the_array_registry() -> None:
    text = DOC.read_text(encoding="utf-8")
    heading = "## Array parameter element types { #array-element-types }"
    (table,) = _tables(_section(text, heading))
    rows = _by_type(table, "C element")
    code = set(T.SUPPORTED_ARRAY_CTYPES)
    assert set(rows) == code, _diff(set(rows), code)
    wrong = {
        t: (row["`T[]` form"], row["NumPy dtype"])
        for t, row in rows.items()
        if (row["`T[]` form"], row["NumPy dtype"])
        != (f"`{t}[]`", f"`{T._CTYPE_META[t]['py_type']}`")
    }
    assert not wrong, wrong
