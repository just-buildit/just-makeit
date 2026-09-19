"""gh-1290: a record that gains a `result_field` must not fail silently.

A ``single`` method returns a named ``PyStructSequence``. Adding a field to
its ``result_fields`` moves the `.pyi` and leaves the sacred fragment behind,
half-updated:

* ``PyStructSequence_Field Dev_read_fields[]`` keeps the fields it was
  generated with;
* ``Dev_read_desc``'s ``n_in_sequence`` keeps the old count — **while its
  doc field says ``"DevRec(a, b)"``**, because gh-1267's transplant refreshes
  that one. The descriptor contradicts itself;
* the wrapper still fills the slots it filled before.

So `help()` announces a field that `.b` raises ``AttributeError`` for, against
a stub a type checker blesses. `jm apply` printed nothing and
`jm status --check` returned **0**.

**Reported, never repaired, and the asymmetry is the point.** gh-1273's enum
table is rewritten in place because jm owns every byte of it and nothing else
moves with it. A record's third moving part is in the **wrapper body**, which
is the author's under gh-767 — and growing the table without the body is
strictly worse than leaving both, since the sequence gains a slot nothing
ever sets and an unset ``PyStructSequence`` slot is a NULL the caller reads
back as a tuple item. Today's failure is an ``AttributeError`` on a field the
stub promises: wrong, but honest. Same rule `warn_init_kwargs_drift` follows,
reaching the same answer for the same reason.

**The gate is real, not a mark.** `_report.warn(gates=True)` only prints a
``!``; nothing about it makes `status --check` fail, and `_status` does not
read that counter. Its own docstring says so: *"a warning marked as gating
that does not fail the gate teaches the reader to ignore the mark."* So
`status` asks the same question itself, through the same `record_drift`, on
the `before`/`after` it already holds — the route gh-612 opened for the
kwlist. `TestTheGateIsReal` is what stops the mark drifting away from the
exit code.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._docsync import record_drift  # noqa: E402

from _jmrun import JmRun, run_cli

FRAG = Path("native/src/m/m_ext_dev.c")

_ONE_FIELD = """
[[dev.methods]]
name = "read"
arg_type = "void"
return_type = "dev_rec_t"
single = true

[[dev.methods.result_fields]]
name = "a"
type = "uint64_t"
"""

_SECOND_FIELD = """
[[dev.methods.result_fields]]
name = "b"
type = "uint64_t"
"""


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


def _scaffold(tmp: Path, extra: str = "") -> Path:
    assert _cli("new", "rp", cwd=tmp).returncode == 0
    root = tmp / "rp"
    assert _cli("module", "m", cwd=root).returncode == 0
    assert (
        _cli(
            "object", "dev", "--module", "m", "--state", "n:int:0", cwd=root
        ).returncode
        == 0
    )
    frag = root / "objects" / "dev.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _ONE_FIELD + extra,
        encoding="utf-8",
    )
    r = _cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _add_second_field(root: Path) -> None:
    frag = root / "objects" / "dev.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _SECOND_FIELD, encoding="utf-8"
    )


@pytest.fixture
def drifted(tmp_path) -> Path:
    """One field generated, a second declared afterwards."""
    root = _scaffold(tmp_path)
    _add_second_field(root)
    assert _cli("apply", cwd=root).returncode == 0
    return root


class TestTheFixtureReallyDrifts:
    """A gate measured against a tree that does not drift proves nothing."""

    def test_the_fragment_kept_one_field(self, drifted: Path):
        body = (drifted / FRAG).read_text(encoding="utf-8")
        block = body[body.index("Dev_read_fields[]") :]
        block = block[: block.index("};")]
        assert '"a"' in block and '"b"' not in block, block

    def test_the_descriptor_contradicts_itself(self, drifted: Path):
        """gh-1267 refreshes the doc and nothing refreshes the arity.

        This is why the detector reads BOTH halves: a check on the doc alone
        would have called this record current.
        """
        body = (drifted / FRAG).read_text(encoding="utf-8")
        desc = body[body.index("Dev_read_desc") :]
        desc = desc[: desc.index("};")]
        assert "DevRec(a, b)" in desc, desc
        assert re.search(r",\s*1\s*$", desc.strip()), desc

    def test_the_stub_promises_the_field(self, drifted: Path):
        pyi = (drifted / "src" / "rp" / "m" / "m.pyi").read_text(
            encoding="utf-8"
        )
        assert "tuple[int, int]" in pyi, pyi


class TestApplyReportsIt:
    def test_apply_warns_naming_both_counts(self, drifted: Path):
        _add_second_field  # noqa: B018 - readability anchor
        r = _cli("apply", cwd=drifted)
        blob = r.stdout + r.stderr
        assert "Dev_read builds 1 field(s)" in blob, blob
        assert "manifest declares 2" in blob, blob
        assert "n_in_sequence 1 should be 2" in blob, blob

    def test_the_warning_is_marked_gating(self, drifted: Path):
        r = _cli("apply", cwd=drifted)
        blob = r.stdout + r.stderr
        assert "warning !:" in blob, blob

    def test_it_names_a_remedy_rather_than_the_problem(self, drifted: Path):
        """A gate whose finding cannot be acted on is a complaint.

        `TestTheRemedyWorks` proves the named command actually clears it.
        """
        blob = _cli("apply", cwd=drifted).stderr
        assert "regenerate that member alone" in blob, blob


class TestTheGateIsReal:
    """The mark is a claim about `status --check`. Test the claim."""

    def test_status_check_fails(self, drifted: Path):
        assert _cli("status", "--check", cwd=drifted).returncode == 1

    def test_status_lists_it_readably(self, drifted: Path):
        out = _cli("status", cwd=drifted).stdout
        assert "RECORDS (1)" in out, out
        assert "Dev_read builds 1 field(s)" in out, out

    def test_the_json_face_carries_it(self, drifted: Path):
        """`status --json` is what a downstream CI reads."""
        out = _cli("status", "--json", cwd=drifted).stdout
        data = json.loads(out[out.index("{") :])
        assert data["record_drift"], data
        assert data["record_drift"][0]["allowed"] is False


class TestNoFalsePositive:
    """The other half of a gate: it must be quiet when nothing is wrong."""

    def test_a_correct_record_is_silent(self, tmp_path):
        root = _scaffold(tmp_path, extra=_SECOND_FIELD)
        r = _cli("apply", cwd=root)
        assert "builds" not in (r.stdout + r.stderr), r.stdout + r.stderr
        assert _cli("status", "--check", cwd=root).returncode == 0

    def test_adding_a_new_record_method_is_silent(self, tmp_path):
        """Declaring a record on an EXISTING fragment is an addition.

        `transplant_missing_bindings` splices the tables in, so there is
        nothing stale -- a detector that read "in the manifest, not in the
        fragment" as drift would fire on the ordinary way records arrive.
        """
        assert _cli("new", "rp", cwd=tmp_path).returncode == 0
        root = tmp_path / "rp"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "dev",
                "--module",
                "m",
                "--state",
                "n:int:0",
                cwd=root,
            ).returncode
            == 0
        )
        assert _cli("apply", cwd=root).returncode == 0
        frag = root / "objects" / "dev.toml"
        frag.write_text(
            frag.read_text(encoding="utf-8") + _ONE_FIELD, encoding="utf-8"
        )
        r = _cli("apply", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "builds" not in (r.stdout + r.stderr), r.stdout + r.stderr
        assert "Dev_read_fields" in (root / FRAG).read_text(encoding="utf-8")
        assert _cli("status", "--check", cwd=root).returncode == 0

    def test_a_record_free_project_is_silent(self, tmp_path):
        assert _cli("new", "rp", cwd=tmp_path).returncode == 0
        root = tmp_path / "rp"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "dev",
                "--module",
                "m",
                "--state",
                "n:int:0",
                cwd=root,
            ).returncode
            == 0
        )
        assert _cli("apply", cwd=root).returncode == 0
        assert _cli("status", "--check", cwd=root).returncode == 0


class TestTheRemedyWorks:
    """What the message tells you to do has to clear what it reports."""

    def test_deleting_the_member_and_reapplying_regenerates_it(
        self, drifted: Path
    ):
        f = drifted / FRAG
        t = f.read_text(encoding="utf-8")
        t = re.sub(
            r"static PyStructSequence_Field Dev_read_fields.*?"
            r"static PyTypeObject \*Dev_read_type = NULL;\n",
            "",
            t,
            flags=re.S,
        )
        t = re.sub(
            r"static PyObject \*\nDev_read\(.*?\n\}\n", "", t, flags=re.S
        )
        t = re.sub(r'\n\s*\{"read",[^\n]*\n(?:[^\n]*\n)*?[^\n]*\},\n', "\n", t)
        f.write_text(t, encoding="utf-8")
        r = _cli("apply", cwd=drifted)
        assert r.returncode == 0, r.stdout + r.stderr
        body = f.read_text(encoding="utf-8")
        block = body[body.index("Dev_read_fields[]") :]
        block = block[: block.index("};")]
        assert '"a"' in block and '"b"' in block, block
        assert _cli("status", "--check", cwd=drifted).returncode == 0


class TestTheDetector:
    """`record_drift` is the one comparison both presentations read."""

    _REF = (
        "static PyStructSequence_Field X_fields[] = {\n"
        '    {"a", NULL},\n    {"b", NULL},\n    {NULL, NULL},\n};\n'
        "static PyStructSequence_Desc X_desc = {\n"
        '    "m.X", "X(a, b)", X_fields, 2\n};\n'
    )

    def test_agreement_is_the_empty_string(self):
        assert record_drift(self._REF, self._REF) == ""

    def test_a_missing_field_is_named(self):
        cur = self._REF.replace('    {"b", NULL},\n', "").replace(
            "X_fields, 2", "X_fields, 1"
        )
        d = record_drift(cur, self._REF)
        assert "X builds 1 field(s) [a]" in d
        assert "manifest declares 2 [a, b]" in d

    def test_a_stale_arity_alone_is_caught(self):
        """The names can match while the count does not — gh-1267's half."""
        cur = self._REF.replace("X_fields, 2", "X_fields, 1")
        assert "n_in_sequence 1 should be 2" in record_drift(cur, self._REF)

    def test_a_renamed_field_alone_is_caught(self):
        """And the count can match while the names do not."""
        cur = self._REF.replace('{"b", NULL}', '{"zz", NULL}')
        assert "[a, zz]" in record_drift(cur, self._REF)

    def test_formatting_is_not_drift(self):
        """These fragments are reformatted on every apply of a `c_style`
        project, so the comparison reads VALUES — the trap gh-1273 documents
        one table over."""
        gnu = self._REF.replace("    {", "  {").replace('    "m.X"', '  "m.X"')
        assert record_drift(gnu, self._REF) == ""

    def test_a_record_absent_from_the_reference_is_not_reported(self):
        """A hand-written record the manifest does not describe is the
        author's, and jm has nothing to compare it against."""
        assert record_drift(self._REF, "") == ""

    def test_a_record_absent_from_the_FRAGMENT_is_not_reported(self):
        """The other direction, and the one with a live path behind it.

        A newly declared record method has no tables in the fragment until
        `transplant_missing_bindings` splices them in. Comparing a reference
        record against nothing and calling the difference drift would fire on
        every such addition -- see
        `TestNoFalsePositive.test_adding_a_new_record_method_is_silent`,
        which is the same claim end to end.
        """
        assert record_drift("", self._REF) == ""
