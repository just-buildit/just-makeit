"""A sacred fragment missing a safety guard fails ``status --check`` (gh-2581).

A module object's binding fragment is sacred: ``apply`` adds a member it
lacks and never rewrites one it has. So a guard jm adds to a wrapper after
the fragment was rendered never arrives. ``apply`` named it in a warning,
and ``jm status --check`` -- the gate a downstream CI runs -- exited 0.
doppler carried two capture fragments without the output-size checks that
way.

Each per-member marker `_docsync` reads is classified once, on its row in
`_docsync.MARKERS`: **safety** (a guard -- memory, or a silent wrong answer)
gates as ``GUARD``, never suppressible; **advisory** stays an UNRECONCILED
reason. `apply`'s warning reads the same row and is marked gating.

GATE, registration-free over the fixture's fresh render: every marker a
member carries is removed in turn, by deleting the one guard block that
holds it, and ``status --check`` goes red naming it exactly when its row
says safety. A cosmetic edit, and a fresh render, stay green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _docsync

FRAG = ("native", "src", "m", "m_ext_o.c")

#: Methods whose fresh render carries the guards: an ``out=`` /
#: ``variable_output`` result with a declared sentinel, the same released
#: from the GIL (its inflight count, and the teardown's wait), and a scalar
#: with a declared negative refusal, a ranged integer and a byte array.
METHODS = [
    ["rd", "--arg-type", "float", "--return-type", "float",
     "--variable-output", "--error-sentinel", "SIZE_MAX",
     "--error", "OSError", "--error-message", "bad"],
    ["ng", "--arg-type", "float", "--return-type", "float",
     "--variable-output", "--nogil"],
    ["sc", "--param", "k:int8_t", "--param", "b:uint8_t[]",
     "--return-type", "int", "--error-negative"],
]  # fmt: skip

#: A guard jm emits is an ``if (<test>) { <refuse> }`` block with no brace
#: inside it -- or, for a test folded into one condition with others (the
#: ``out=`` contiguity), that whole block. Removing the block is what a
#: fragment rendered before the guard looks like.
_IF_BLOCK = re.compile(r"\n[ \t]*if\s*\([^{};]*\)\s*\{[^{}]*\}")

#: An advisory marker is no guard: a result shape is a statement
#: (``Py_RETURN_NONE;``), and it is removed as one.
_STATEMENT = re.compile(r"\n[ \t]*[^{}\n;]*;")

#: A `status_errors` row is a `case <STATUS>:` arm of the status switch; a
#: fragment rendered before the row lacks its label.
_CASE_ARM = re.compile(r"\n[ \t]*case\s+[A-Za-z_]\w*\s*:")

#: A guard that is a CALL rather than a block: gh-1700's converter, which a
#: fragment rendered before it spells as numpy's own.
_CALL_SWAPS = (("jm_array_arg(", "PyArray_FROM_OTF("),)


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> "tuple[Path, str]":
    """A project at rest, and its fragment as jm renders it today."""
    where = tmp_path_factory.mktemp("gh2581")
    _ok("new", "p", cwd=where)
    proj = where / "p"
    _ok("module", "m", cwd=proj)
    _ok(
        "object", "o", "--module", "m",
        "--arg-type", "float", "--return-type", "float",
        cwd=proj,
    )  # fmt: skip
    for m in METHODS:
        _ok("method", "o", *m, cwd=proj)
    _rich(proj)
    _ok("apply", cwd=proj)
    return proj, proj.joinpath(*FRAG).read_text(encoding="utf-8")


#: The second object's fragment: the safety rows `o`'s shapes do not reach.
RICH = ("native", "src", "m", "m_ext_r.c")

#: Every fragment the sweep walks.
FRAGS = (FRAG, RICH)

#: The rows no fixture here renders: `single` and `record_dtype` result
#: shapes, both advisory. Only these rely on the table test at the end;
#: every other row is proved on a real render by the sweep.
UNRENDERED = {"PyStructSequence_New", "PyArray_NewFromDescr"}

#: Methods declared in TOML: a capsule param both retained and required, a
#: byte array with a `str_hint`, and a list-of-records method with its
#: consumed-input function and (in the header, below) its capacity.
_RICH_TOML = """
[[r.methods]]
name = "attach"
return_type = "int"
status_return = true
params = [{ name = "t", type = "p_tt_state_t *", capsule = "p.tt", \
header = "p/tt/tt_core.h", retain = true, required = true }]

[[r.methods]]
name = "hint"
return_type = "int"
params = [{ name = "b", type = "uint8_t[]", str_hint = "pass bytes" }]

[[r.methods]]
name = "rec"
arg_type = "float"
return_type = "div_t"
result_fields = [{name = "quot", type = "int"}, {name = "rem", type = "int"}]
consumed_fn = "p_r_rec_used"
"""


def _rich(proj: Path) -> None:
    """Object `r`, whose render carries the borrow, release, status-table,
    strict, capsule, str_hint and records guards (gh-2581 review)."""
    _ok("object", "tt", "--module", "m", "--state", "k:int:0", cwd=proj)
    _ok(
        "property", "tt", "_capsule", "--module", "m",
        "--type", "capsule", "--capsule", "p.tt",
        cwd=proj,
    )  # fmt: skip
    _ok("object", "r", "--module", "m", "--state", "used:size_t:0", cwd=proj)
    _ok(
        "method", "r", "wait", "--module", "m", "--borrow",
        "--param", "n:size_t", "--return-type", "float _Complex",
        "--status-fn", "r_status",
        "--status-error", "A:ValueError:bad",
        "--status-error", "B:EOFError:end",
        cwd=proj,
    )  # fmt: skip
    _ok(
        "method", "r", "consume", "--module", "m", "--arg-type", "void",
        "--return-type", "void", "--param", "n:size_t",
        "--releases", "wait",
        cwd=proj,
    )  # fmt: skip
    _ok(
        "method", "r", "sx", "--module", "m", "--arg-type", "float[]",
        "--return-type", "bool", "--strict",
        cwd=proj,
    )  # fmt: skip
    toml = proj / "objects" / "r.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + _RICH_TOML, encoding="utf-8"
    )
    # The author's half of the records method: what it consumed, and the
    # capacity that sizes each call -- in the sacred header.
    (h,) = proj.glob("native/inc/**/r_core.h")
    text = h.read_text(encoding="utf-8")
    cut = text.rindex("#ifdef __cplusplus")
    decls = (
        "size_t p_r_rec_used(const p_r_state_t *state);\n"
        "size_t p_r_rec_max_out(p_r_state_t *state, size_t n_in);\n"
    )
    h.write_text(text[:cut] + decls + text[cut:], encoding="utf-8")


def _markers(text: str, member: str) -> frozenset:
    """Both axes' labels for *member*, as `signature_drift_details` reads."""
    return _docsync._method_feature_symbols(text).get(
        member, frozenset()
    ) | _docsync._method_return_shapes(text).get(member, frozenset())


def _without(text: str, member: str, label: str) -> "str | None":
    """*text* with the one guard that carries *label* in *member* removed.

    The first ``if`` block -- or, failing one, statement or call swap -- whose
    removal drops *label* while the member still has a body. ``None`` when
    neither does: the caller says which label it could not remove.
    """
    body = _docsync._row_bodies(text)[member][0]
    edits = [
        body[: m.start()] + body[m.end() :]
        for rx in (_IF_BLOCK, _STATEMENT, _CASE_ARM)
        for m in rx.finditer(body)
    ]

    # A guard jm emits on each of a member's paths (the `out=` one and the
    # allocating one) is missing from every path of an older fragment: every
    # block with the same TEST -- its refusal releases what that path holds
    # -- or the same statement, at any indentation, removed everywhere.
    def _key(text: str) -> list:
        return text[: text.index("{")].split() if "{" in text else text.split()

    for rx in (_IF_BLOCK, _STATEMENT):
        for m in rx.finditer(body):
            key = _key(m.group(0))
            edits.append(
                rx.sub(
                    lambda o, key=key: (
                        "" if _key(o.group(0)) == key else o.group(0)
                    ),
                    body,
                )
            )
    edits += [body.replace(a, b, 1) for a, b in _CALL_SWAPS if a in body]
    for new in edits:
        out = text.replace(body, new, 1)
        if _docsync._row_bodies(out).get(member, ("",))[0] and (
            label not in _markers(out, member)
        ):
            return out
    return None


def _cases(text: str) -> list:
    """Every (member, label) the fresh render carries -- derived, never
    listed, so a guard jm starts emitting for these shapes is covered."""
    return sorted(
        (n, lab)
        for n in _docsync._row_bodies(text)
        for lab in _markers(text, n)
    )


def _status(proj: Path, *flags: str):
    return run_cli("status", "--check", *flags, cwd=proj)


def test_the_fixture_carries_the_guards_in_the_issue(fresh):
    """Armed: the render holds the issue's guards and an advisory one, so
    the sweep below cannot pass by finding nothing to remove."""
    _proj, text = fresh
    labels = {lab for _n, lab in _cases(text)}
    for want in (
        "output-size",
        "returned-count",
        "refusal-value:SIZE_MAX",
        "nogil-counted",
        "teardown-waits",
        "int-range",
        "raises",
        "nogil",
        "Py_RETURN_NONE",
    ):
        assert want in labels, (want, sorted(labels))


def test_a_fresh_render_is_green(fresh):
    proj, _text = fresh
    r = _status(proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GUARD (" not in r.stdout


def test_each_guard_removed_in_turn_gates_and_only_a_guard(fresh):
    """The class: every marker, removed alone, is GUARD iff its row says
    safety -- and ``status --check`` exits non-zero exactly then."""
    proj, _text = fresh
    seen: dict = {True: set(), False: set()}
    present: set = set()
    for rel in FRAGS:
        frag = proj.joinpath(*rel)
        text = frag.read_text(encoding="utf-8")
        present |= {lab for _n, lab in _cases(text)}
        try:
            for member, label in _cases(text):
                safety = _docsync.marker(label).safety
                edited = _without(text, member, label)
                if edited is None:
                    # A marker no single block, statement or call carries
                    # in this member -- `raises` where several refusals
                    # each raise, a `Py_None` per return -- is proved on
                    # another member; the assertions after the loop hold
                    # every label to at least one.
                    continue
                frag.write_text(edited, encoding="utf-8")
                r = run_cli("status", "--json", cwd=proj)
                found = {
                    (g["path"], g["member"], lab)
                    for g in json.loads(r.stdout)["missing_guards"]
                    for lab in g["guards"]
                }
                guard = ("/".join(rel), member, label) in found
                assert guard == safety, (rel, member, label, sorted(found))
                if safety:
                    # Gates: the exit code is the drift count.
                    assert r.returncode != 0, (member, label)
                seen[safety].add(label)
        finally:
            frag.write_text(text, encoding="utf-8")
    # Every safety label the renders carry was proved at least once, and
    # an advisory one too, so neither half of the classification went
    # untested. (`nogil` is a macro pair, not a statement: the test below
    # removes it.)
    assert "Py_RETURN_NONE" in seen[False], seen
    want_safety = {lab for lab in present if _docsync.marker(lab).safety}
    assert seen[True] == want_safety, (
        f"never removed: {sorted(want_safety - seen[True])}; teach "
        "_without its shape"
    )
    # ...and the renders carry EVERY safety row, so each is proved on real
    # code rather than only against the table.
    rows = {lab.partition(":")[0] for lab in seen[True]}
    safety_rows = {k for k, v in _docsync.MARKERS.items() if v.safety}
    assert rows == safety_rows, sorted(safety_rows - rows)


def test_the_unrendered_rows_are_exactly_these(fresh):
    proj, _text = fresh
    present = {
        lab.partition(":")[0]
        for rel in FRAGS
        for _n, lab in _cases(proj.joinpath(*rel).read_text(encoding="utf-8"))
    }
    assert set(_docsync.MARKERS) - present == UNRENDERED, present


def test_a_fragment_that_never_releases_has_no_teardown_to_wait(fresh):
    """A fragment rendered before gh-2259 holds the GIL in every member, so
    its teardown has nothing to wait for: no GUARD on `destroy` or
    `__exit__` (gh-2581 review). `nogil` itself stays advisory."""
    proj, text = fresh
    frag = proj.joinpath(*FRAG)
    old = re.sub(
        r"Py_BEGIN_ALLOW_THREADS([\s\S]*?)Py_END_ALLOW_THREADS", r"\1", text
    )
    old = re.sub(r"\n[ \t]*jm_inflight_(?:enter|leave)\([^;]*\);", "", old)
    old = re.sub(
        r"\n[ \t]*if\s*\(\s*jm_inflight_wait\([^{}]*\)\s*\{[^{}]*\}", "", old
    )
    assert "jm_inflight_wait" not in old and "ALLOW_THREADS" not in old
    try:
        frag.write_text(old, encoding="utf-8")
        # Premise: the render's teardown waits and this one does not.
        feat = _docsync._method_feature_symbols
        assert "teardown-waits" in feat(text)["destroy"]
        assert "teardown-waits" not in feat(old)["destroy"]
        assert not any("nogil" in m for m in feat(old).values())
        r = run_cli("status", "--json", cwd=proj)
        assert json.loads(r.stdout)["missing_guards"] == [], r.stdout
        r = _status(proj)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        frag.write_text(text, encoding="utf-8")


def test_a_fragment_that_releases_still_waits(fresh):
    """The other half: while any member releases, a teardown without the
    wait is a GUARD on both teardowns."""
    proj, text = fresh
    frag = proj.joinpath(*FRAG)
    old = re.sub(
        r"\n[ \t]*if\s*\(\s*jm_inflight_wait\([^{}]*\)\s*\{[^{}]*\}", "", text
    )
    assert old != text
    try:
        frag.write_text(old, encoding="utf-8")
        r = run_cli("status", "--json", cwd=proj)
        found = {
            (g["member"], lab)
            for g in json.loads(r.stdout)["missing_guards"]
            for lab in g["guards"]
        }
        assert found == {
            ("destroy", "teardown-waits"),
            ("__exit__", "teardown-waits"),
        }, found
    finally:
        frag.write_text(text, encoding="utf-8")


def test_an_advisory_difference_alone_stays_green(fresh):
    """Holding the GIL is slower, never unsafe: `nogil` alone does not gate,
    and the fragment is still listed as unreconciled."""
    proj, text = fresh
    frag = proj.joinpath(*FRAG)
    body = _docsync._row_bodies(text)["ng"][0]
    # Every released block, as a fragment rendered before gh-2259 has it.
    held, n = re.subn(
        r"Py_BEGIN_ALLOW_THREADS([\s\S]*?)Py_END_ALLOW_THREADS", r"\1", body
    )
    assert n, body
    try:
        frag.write_text(text.replace(body, held, 1), encoding="utf-8")
        assert "nogil" in _docsync.signature_drift_details(
            frag.read_text(encoding="utf-8"), text
        ).get("ng", "")
        r = _status(proj)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "GUARD (" not in r.stdout
    finally:
        frag.write_text(text, encoding="utf-8")


def test_a_cosmetic_edit_stays_green(fresh):
    """A comment and a reflowed line differ from the render and lack no
    guard: unreconciled, never GUARD."""
    proj, text = fresh
    frag = proj.joinpath(*FRAG)
    body = _docsync._row_bodies(text)["rd"][0]
    cosmetic = body.replace("{\n", "{\n    /* the author's note */\n", 1)
    try:
        frag.write_text(text.replace(body, cosmetic, 1), encoding="utf-8")
        r = _status(proj)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "GUARD (" not in r.stdout
    finally:
        frag.write_text(text, encoding="utf-8")


def test_the_issue_case_json_allow_and_apply(fresh):
    """doppler's shape: both output-size checks gone. JSON names them,
    ``--allow`` does not excuse them, and `apply` marks its warning gating
    and keeps the body."""
    proj, text = fresh
    frag = proj.joinpath(*FRAG)
    edited = _without(text, "rd", "output-size")
    edited = _without(edited, "rd", "returned-count")
    try:
        frag.write_text(edited, encoding="utf-8")
        r = run_cli("status", "--json", cwd=proj)
        assert r.returncode != 0
        found = json.loads(r.stdout)["missing_guards"]
        rd = [g for g in found if g["member"] == "rd"]
        assert rd and rd[0]["path"] == "/".join(FRAG), found
        assert {"output-size", "returned-count"} <= set(rd[0]["guards"])
        assert all(rd[0]["why"]), rd

        r = _status(proj, "--allow", "/".join(FRAG))
        assert r.returncode != 0, r.stdout + r.stderr
        assert "GUARD (" in r.stdout
        # The remedy says what clears it: jm's own form of each guard,
        # since an equivalent spelled otherwise is not recognised (gh-2589).
        assert (
            "jm's form: if (<x>_need > (size_t)NPY_MAX_INTP) {...}" in r.stdout
        ), r.stdout
        assert "gh-2589" in r.stdout, r.stdout
        assert all(rd[0]["forms"]), rd

        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
        assert re.search(
            r"warning !: \S*m_ext_o\.c: binding no longer matches",
            r.stderr,
        ), r.stderr
        assert "Missing safety guard(s) in rd" in r.stderr
        assert "GUARD, gh-2581" in r.stderr
        # Sacred: the body is the author's, guard or no guard.
        assert frag.read_text(encoding="utf-8") == edited
    finally:
        frag.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("label", sorted(UNRENDERED))
def test_an_unrendered_row_is_read_for_its_severity(label, monkeypatch):
    """`missing_guards` decides by the row, for the rows (`UNRENDERED`) no
    fixture here renders; the sweep proves every other on real code."""
    text = (
        "static PyObject *f(PyObject *s, PyObject *a) { return NULL; }\n"
        "static PyMethodDef M_methods[] = {\n"
        '    {"f", (PyCFunction)f, METH_NOARGS, NULL},\n'
        "    {NULL, NULL, 0, NULL}\n};\n"
    )
    assert "f" in _docsync._row_bodies(text)
    full = f"{label}:X" if label in ("case", "refusal-value") else label
    monkeypatch.setattr(
        _docsync, "_missing_markers", lambda _e, _r: {"f": {full}}
    )
    got = _docsync.missing_guards(text, text)
    assert bool(got) == _docsync.MARKERS[label].safety, got
    if got:
        assert got["f"][full], got


#: The guards the maintainer's decision on gh-2581 names, by their row:
#: output size, declared refusals, NULL / destroyed state, the GIL-free
#: inflight accounting, and the borrow / retain checks. The sweep above
#: proves `status` reads each row; this pins the rows the issue decided, so
#: a row flipped to advisory goes red here and not only in an example.
DECIDED_SAFETY = {
    "output-size",
    "returned-count",
    "refusal-value",
    "case",
    "raises",
    "strict-input",
    "capsule-required",
    "deferred-free",
    "nogil-counted",
    "teardown-waits",
    "borrow-release",
    "retain",
}


@pytest.mark.parametrize("label", sorted(DECIDED_SAFETY))
def test_the_guards_the_issue_names_are_safety(label):
    assert _docsync.MARKERS[label].safety, label


def test_every_safety_row_shows_what_clears_it():
    """GUARD's remedy is jm's own form of the guard (gh-2581 review): a
    safety row without one would leave the reader nothing to write."""
    bare = [k for k, v in _docsync.MARKERS.items() if v.safety and not v.form]
    assert not bare, bare


def test_only_shape_and_nogil_rows_are_advisory():
    """A result's Python shape and holding the GIL are not guards; every
    other row is (the PR body justifies each)."""
    advisory = {k for k, v in _docsync.MARKERS.items() if not v.safety}
    shapes = set(_docsync._RETURN_SHAPE_MARKERS) - {"raises"}
    assert advisory == shapes | {"nogil"}, advisory
