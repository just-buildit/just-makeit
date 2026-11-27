"""gh-2805: a name twice in a record declaration's fields is refused.

gh-2112's ``_config.duplicate_name_errors`` walked a method's inline
``result_fields`` but not the fields of a gh-1405 record declaration
(``[[<obj>.records]]``). Those columns become the numpy dtype the binding
builds at runtime from ``offsetof``, so a repeat compiled cleanly and every
member speaking the record raised ``ValueError: name already used as a name
or title`` on its first call. ``jm record`` refused a repeat with a loop of
its own; a hand-written manifest went straight through ``apply``.

The first test derives its cases from what the manifest declares: every
record :func:`_config.records` returns gets a repeat in its own fields, and
the check must name that record. ``jm record`` now asks the same helper, so
its refusal is the same sentence.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from _jmrun import run_cli

from just_makeit import _config as C


def _snapshot(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha1(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """Two records on one object, the second sharing a column name with
    the first (legal: each record is its own dtype), and a method reading
    one of them."""
    r = run_cli(
        "new", "p", "--object", "ring", "--arg-type", "float",
        "--return-type", "float", "--state", "i:int:0", cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    for args in (
        ("record", "ring", "iq16_t", "--field", "i:int16_t",
         "--field", "q:int16_t"),
        ("record", "ring", "tick_t", "--field", "n:uint64_t",
         "--field", "i:int16_t", "--field", "t:double"),
        ("method", "ring", "wait", "--arg-type", "void", "--return-type",
         "int16_t", "--variable-output", "--record-dtype", "iq16_t"),
    ):  # fmt: skip
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    return root


def test_every_declared_records_fields_are_checked(proj: Path) -> None:
    cfg = C.load(proj)
    assert C.duplicate_name_errors(cfg) == [], (
        "the fixture repeats names only across records and the state,"
        " which is legal"
    )
    cases = [
        (comp, i)
        for comp in C.components(cfg)
        for i, rec in enumerate(C.records(cfg, comp))
        if rec.get("fields")
    ]
    assert len(cases) == 2, cases
    unchecked = []
    for comp, i in cases:
        bad = copy.deepcopy(cfg)
        rec = bad[comp]["records"][i]
        rec["fields"].append(dict(rec["fields"][-1]))
        want = (
            f"'{comp}' record '{rec['name']}' fields:"
            f" '{rec['fields'][-1]['name']}' is declared twice; "
            + C.RECORD_TWICE_WHY
        )
        if C.duplicate_name_errors(bad) != [want]:
            unchecked.append((comp, rec["name"], C.duplicate_name_errors(bad)))
    assert not unchecked, (
        "a record's fields are one numpy dtype, but a name in them twice is"
        " not refused (gh-2805); see _config.duplicate_name_errors:\n  "
        + "\n  ".join(map(str, unchecked))
    )


def test_apply_and_status_refuse_a_hand_written_repeat(proj: Path) -> None:
    """The issue: a repeat written by hand applied cleanly before."""
    frag = proj / "objects" / "ring.toml"
    text = frag.read_text(encoding="utf-8")
    old = '[[ring.records.fields]]\nname = "q"\n'
    assert text.count(old) == 1, text
    frag.write_text(
        text.replace(old, '[[ring.records.fields]]\nname = "i"\n'),
        encoding="utf-8",
    )
    before = _snapshot(proj)
    for verb in ("apply", "status"):
        r = run_cli(verb, cwd=proj)
        assert r.returncode == 1, (verb, r.stdout + r.stderr)
        assert "Traceback" not in r.stderr, r.stderr
        assert (
            "'ring' record 'iq16_t' fields: 'i' is declared twice" in r.stderr
        ), r.stderr
    assert _snapshot(proj) == before, "a refused command wrote to the tree"


def test_jm_record_refuses_with_the_shared_sentence(proj: Path) -> None:
    """``jm record`` asks the one check, before it writes anything."""
    before = _snapshot(proj)
    r = run_cli(
        "record", "ring", "pair_t", "--field", "a:int32_t",
        "--field", "a:int32_t", cwd=proj,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert (
        "'ring' record 'pair_t' fields: 'a' is declared twice; "
        + C.RECORD_TWICE_WHY
    ) in r.stderr, r.stderr
    assert _snapshot(proj) == before, "a refused command wrote to the tree"
