"""The text and `--json` faces of `jm status` name the same UNRECONCILED
fragments per bucket (gh-1566).

Both faces read one classifier (`_status._unreconciled_buckets`); this reads
what each actually PRINTS, so a face that stops rendering a bucket -- the
state `--json` was in for every bucket until gh-1566 -- fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _jmrun import run_cli

#: The text header of each bucket, keyed by its `--json` name.
BUCKETS = {
    "actionable": "ACTIONABLE",
    "binding_ahead": "BINDING AHEAD",
    "apply_fixes": "APPLY FIXES THESE",
    "unexplained": "UNEXPLAINED",
}
_HEADER = re.compile(
    r"^  (%s) \(\d+\)" % "|".join(map(re.escape, BUCKETS.values()))
)
_PATH = re.compile(r"^    [!~] (\S+)$")


def _text_buckets(text: str) -> "dict[str, list[str]]":
    by_label = {v: k for k, v in BUCKETS.items()}
    out: "dict[str, list[str]]" = {k: [] for k in BUCKETS}
    current = None
    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            current = by_label[m.group(1)]
            continue
        # A bucket's description continues on two-space lines; only a
        # top-level line (a new section) ends it.
        if current and line and not line.startswith("  "):
            current = None
        p = _PATH.match(line) if current else None
        if p:
            out[current].append(p.group(1))
    return out


def assert_status_faces_agree(root: Path, nonempty: str) -> None:
    """Both faces list the same fragments per bucket; *nonempty* is a bucket
    the caller's fixture is known to fill, so agreement is not vacuous."""
    text = run_cli("status", cwd=root).stdout
    data = json.loads(run_cli("status", "--json", cwd=root).stdout)
    from_json = {
        k: [e["path"] for e in data["unreconciled"][k]] for k in BUCKETS
    }
    assert from_json[nonempty], (nonempty, from_json, text)
    assert _text_buckets(text) == from_json, (text, from_json)
