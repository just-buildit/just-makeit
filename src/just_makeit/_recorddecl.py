"""_recorddecl.py -- `just-makeit record`: name a C struct and its columns.

gh-1405. A record is a struct the AUTHOR writes in the sacred header; this
declares its name and field list so jm can describe the same bytes to numpy
on both sides of the boundary::

    [[ring.records]]
    name = "iq16_t"
    fields = [
        { name = "i", type = "int16_t" },
        { name = "q", type = "int16_t" },
    ]

Declared ONCE and referenced twice — ``arg_type = "iq16_t[]"`` on the method
that writes rows, ``record_dtype = "iq16_t"`` on the one that reads them. The
whole reason the table exists is that a restatement drifts: doppler's ring
buffer has three hand-written faces that disagree today about what
``I16Buffer.wait`` returns.

A CLI command rather than TOML alone, because a declaration only reachable by
hand-editing the manifest is the foot-gun this project refuses: every shape
jm supports has a command that produces it.

The struct's *layout* is never taken from this list — the generated dtype
reads ``offsetof``/``sizeof`` from the compiler (`_record.dtype_c`), so a
padded struct cannot be described wrongly here. What this list decides is
which fields are exposed, and under what names.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _types as T


def parse_field(spec: str) -> dict:
    """``"i:int16_t"`` -> ``{"name": "i", "type": "int16_t"}``.

    Raises
    ------
    ValueError
        When the spelling is not ``name:type``, or the type is not one jm
        can map to a numpy format.

    Examples
    --------
    >>> parse_field("i:int16_t")
    {'name': 'i', 'type': 'int16_t'}
    >>> parse_field("bad")
    Traceback (most recent call last):
    ValueError: --field wants name:type, got 'bad'
    """
    if spec.count(":") != 1:
        raise ValueError(f"--field wants name:type, got {spec!r}")
    name, ctype = (part.strip() for part in spec.split(":"))
    if not name or not ctype:
        raise ValueError(f"--field wants name:type, got {spec!r}")
    if ctype not in T._CTYPE_META:
        raise ValueError(
            f"field '{name}': type '{ctype}' is not a registered scalar.\n"
            f"A record column must be one of: {', '.join(sorted(T._CTYPE_META))}"
        )
    return {"name": name, "type": ctype}


def run(
    root: Path,
    object_name: str,
    record_name: str,
    fields: list[dict],
    *,
    doc: str = "",
) -> None:
    """Declare record *record_name* on *object_name*.

    Parameters
    ----------
    root : Path
        Project root (the directory holding ``just-makeit.toml``).
    object_name : str
        The component that speaks this record.
    record_name : str
        The C struct's name, as the sacred header declares it.
    fields : list of dict
        ``{"name", "type"}`` rows, in the order they should be exposed.
    doc : str, optional
        One line describing what a row is.

    Notes
    -----
    Re-declaring an existing record REPLACES its field list, so a column
    added to the struct reaches the manifest by running the command again
    rather than by hand-editing. Nothing else about the component changes.
    """
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = C.load(root)
    if object_name not in C.components(cfg):
        print(
            f"error: unknown object '{object_name}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not fields:
        print(
            "error: a record needs at least one --field name:type.\n"
            "An empty record describes no bytes, so nothing could be "
            "generated from it.",
            file=sys.stderr,
        )
        sys.exit(1)

    seen: set[str] = set()
    for f in fields:
        if f["name"] in seen:
            print(
                f"error: field '{f['name']}' is declared twice.\n"
                "numpy takes the field names as a set; a repeat would "
                "silently drop one column.",
                file=sys.stderr,
            )
            sys.exit(1)
        seen.add(f["name"])

    entry: dict = {"name": record_name, "fields": list(fields)}
    if doc:
        entry["doc"] = doc

    section = cfg.setdefault(object_name, {})
    rows = list(section.get("records", []))
    for i, existing in enumerate(rows):
        if str(existing.get("name") or "") == record_name:
            rows[i] = entry
            break
    else:
        rows.append(entry)
    section["records"] = rows
    C.save(root, cfg)

    cols = ", ".join(f"{f['name']}:{f['type']}" for f in fields)
    print(f"just-makeit: record '{record_name}' on '{object_name}'")
    print(f"  fields  {cols}")
    print()
    print(
        f"Done!  Declare `{record_name}` in the sacred header, then reference "
        f"it with\n"
        f"       --arg-type '{record_name}[]' (rows in) or "
        f"--record-dtype {record_name} (rows out)."
    )
