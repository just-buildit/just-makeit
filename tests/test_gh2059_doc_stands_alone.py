"""gh-2059: a `doc` that is a member's whole docstring duplicates nothing.

`jm status --check` failed on a `[[<obj>.extra_methods]]` row whose `doc`
carries a numpy section (`Parameters` / `----------`). The `DOC !` finding
said "jm generates the numpy sections itself". It does not, there:
`_extramethods.pyi_member` writes the row's `doc` as the whole stub body
and `method_def_row` writes it as the whole `ml_doc`, with nothing generated
beside either. jm does not know a hand-written function's signature, so it
has nothing to document. The remedy the finding printed (Doxygen in
`_core.h`) could not apply either, because the method has no core
declaration.

The cause is a rule written for one table and applied to every table.
`_docstring.manifest_docs_with_sections` walks every key named `doc`, and
exempted only a module's own `doc`. A sectioned `doc` was rendered in every
position, and the same false positive held for each table whose renderer
writes the `doc` alone: an object's and a view's properties, a capsule's
methods and properties, a handle's getters, a composer's serializers and
computed properties, and the composer's own `extra_methods` (gh-1190). They
are named once, in `_docstring.DOC_STANDS_ALONE`.

GATE: a manifest `doc` is reported as a duplicated numpy section exactly
      when jm generates a section beside it, measured on the rendered faces
      of every `doc` position.

- `TestTheIssue`: the issue's row, on a standalone object, a module object
  and a composer, passes `status --check`, and the stub and the `ml_doc`
  each carry its `Parameters` section once.
- `TestEveryPosition` is the oracle. Every `doc` position of a fixture that
  spans every face gets a sectioned value, and the tree is rendered. For
  each position that renders, "reported" must equal "jm wrote a section
  beside it on some face". The test needs no list of positions. A new table
  that renders its `doc` alone fails until it is in `DOC_STANDS_ALONE`. A
  renderer that starts generating sections beside a listed table fails
  until its entry goes. `RATCHET` pins the rows whose renderer decides per
  row (gh-2103).
- `TestEveryFace` (gh-2104) reads the same render for layout: every stub
  docstring keeps its indent, and a `doc` on the stub is on the runtime
  face too.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docstring as D  # noqa: E402
from _jmrun import run_cli  # noqa: E402
from test_capsule_apply import _capsule_module  # noqa: E402
from test_codec_pack import _CODEC, _METHOD  # noqa: E402
from test_composer_apply import _composer_module  # noqa: E402
from test_handle_apply import _ring_module  # noqa: E402

#: A reST section underline, read independently of the detector's own
#: regex: this is the oracle, so it must not share the code it checks.
_RULE = re.compile(r"^[ \t]*-{3,}[ \t]*$")

#: A C string literal, escapes included.
_C_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')

#: The issue's `doc`, verbatim (doppler `dp_tlm`, doppler#1446).
ISSUE_DOC = (
    "{summary}\n\n"
    "Parameters\n"
    "----------\n"
    "n : int, optional\n"
    "    Records wanted.\n"
)

#: Rows that render their `doc` alone yet are reported, because their
#: renderer generates a section for SOME rows of the table and a table-level
#: exemption cannot say which. It may only shrink.
RATCHET = {
    # A codec-pack method: `_codec.render_method_pyi` writes the doc alone.
    "fir.methods.add_kw.doc": 2103,
    # A module function with no header Doxygen and no documented param.
    "module.dsp.functions.fnop.doc": 2103,
    "module.dsp.functions.fone.doc": 2103,
    # A handle factory with no init_params; a handle method with no params
    # and a None return.
    "module.ringbuf.factories.zzfactory.doc": 2103,
    "module.ringbuf.methods.clear.doc": 2103,
}


def _jm(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}:\n{r.stdout}{r.stderr}"


def _headings(doc: str) -> "list[str]":
    """Every line of *doc* underlined by a reST rule, in order."""
    lines = doc.split("\n")
    return [
        lines[i - 1].strip()
        for i in range(1, len(lines))
        if _RULE.match(lines[i])
        and lines[i - 1].strip()
        and not _RULE.match(lines[i - 1])
    ]


def _docstrings(root: Path) -> "list[tuple[str, str]]":
    """``(face, text)`` for every docstring jm wrote under *root*.

    The stub face is each ``.pyi`` docstring. The runtime face is each run
    of adjacent C string literals, decoded: every ``ml_doc``, getset doc
    and ``tp_doc`` is written that way.
    """
    out = []
    for p in sorted((root / "src").rglob("*.pyi")):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r'"""(.*?)"""', text, re.S):
            out.append((p.name, m.group(1)))
    for p in sorted((root / "native").rglob("*.c")):
        text = p.read_text(encoding="utf-8")
        run: list[str] = []
        end = -1
        for m in _C_LITERAL.finditer(text):
            if run and text[end : m.start()].strip():
                out.append((p.name, "".join(run)))
                run = []
            run.append(m.group(1))
            end = m.end()
        if run:
            out.append((p.name, "".join(run)))
    return [
        (
            face,
            s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\"),
        )
        for face, s in out
    ]


def _objects_project(base: Path) -> Path:
    """A project with a standalone and a module object, plus a composer."""
    _jm("new", "pp", "--no-c-prefix", cwd=base)
    root = base / "pp"
    _jm("object", "solo", cwd=root)
    _jm("module", "filt", cwd=root)
    _jm("object", "fir", "--module", "filt", cwd=root)
    return root


def _add_composer(cfg: dict, **tables: "list[dict]") -> dict:
    cfg.setdefault("enum", []).append(
        {"name": "wfm_type", "values": ["tone", "noise", "pn"]}
    )
    mod = _composer_module()
    mod.update(copy.deepcopy(tables))
    cfg.setdefault("module", {})["wfm_compose"] = mod
    return mod


#: Every owner an `extra_methods` row has, and the row's `fn` on it.
OWNERS = {
    "solo": "Solo_read_dict",
    "fir": "Fir_read_dict",
    "wfm_compose": "Composer_read_dict",
}


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """The issue's row on a standalone object, a module object and a
    composer, applied."""
    root = _objects_project(tmp_path_factory.mktemp("issue"))
    cfg = C.load(root)
    rows = {
        owner: [
            {
                "name": "read_dict",
                "fn": fn,
                "flags": "METH_VARARGS | METH_KEYWORDS",
                "args": "n: int = 0",
                "returns": "dict[str, int]",
                "doc": ISSUE_DOC.format(summary=f"Drains {owner}."),
            }
        ]
        for owner, fn in OWNERS.items()
    }
    cfg["solo"]["extra_methods"] = rows["solo"]
    cfg["fir"]["extra_methods"] = rows["fir"]
    _add_composer(cfg, extra_methods=rows["wfm_compose"])
    C.save(root, cfg)
    _jm("apply", cwd=root)
    return root


class TestTheIssue:
    """The issue's row, on every owner an `extra_methods` row has."""

    def test_status_check_passes(self, project: Path) -> None:
        r = run_cli("status", "--check", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "extra_methods" not in r.stdout, r.stdout

    @pytest.mark.parametrize("owner", sorted(OWNERS))
    @pytest.mark.parametrize("face", [".pyi", ".c"])
    def test_each_face_carries_the_section_once(
        self, project: Path, owner: str, face: str
    ) -> None:
        """One docstring per face holds the row, with ONE `Parameters`."""
        found = [
            text
            for name, text in _docstrings(project)
            if name.endswith(face) and f"Drains {owner}." in text
        ]
        assert len(found) == 1, (face, found)
        assert _headings(found[0]) == ["Parameters"], found[0]


#: The handle getter whose own `doc` is left to render: its one field gets
#: no `doc`, so the getter's documents the field (`_handle`, gh-374).
_GETTER_FN = "ringbuf_zzstats"


def _every_position_project(base: Path) -> Path:
    """Every face jm renders a `doc` on, in one tree."""
    root = _objects_project(base)
    _jm(
        "object",
        "eng",
        "--state",
        "gain:double:1.0",
        "--init-param",
        "n:int:4",
        cwd=root,
    )
    _jm("object", "bare", "--no-state", "--no-step", cwd=root)
    for args in (
        (
            "method",
            "eng",
            "exec",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            "--param",
            "k:int",
        ),
        (
            "method",
            "eng",
            "tick",
            "--arg-type",
            "void",
            "--return-type",
            "void",
        ),
        (
            "method",
            "eng",
            "one",
            "--arg-type",
            "double",
            "--single",
            "--return-type",
            "pt_t",
            "--result-field",
            "a:int",
            "--result-field",
            "b:double",
            "--record-name",
            "One",
        ),
        (
            "method",
            "eng",
            "recs",
            "--arg-type",
            "double",
            "--variable-output",
            "--record-dtype",
            "pt_t",
            "--result-field",
            "x:double",
        ),
        ("property", "eng", "span", "--type", "int", "--field"),
        ("property", "eng", "level", "--type", "double"),
        (
            "view",
            "fir",
            "FirView",
            "--module",
            "filt",
            "--create-fn",
            "fir_view_create",
            "--init-param",
            "m:int",
        ),
        ("property", "fir", "vlvl", "--type", "double", "--view", "FirView"),
        (
            "method",
            "fir",
            "vm",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            "--view",
            "FirView",
        ),
        ("module", "dsp"),
        (
            "function",
            "fmap",
            "--module",
            "dsp",
            "--param",
            "b:int",
            "--return-type",
            "int",
        ),
        ("function", "fnop", "--module", "dsp", "--return-type", "void"),
        ("function", "fone", "--module", "dsp", "--return-type", "int"),
    ):
        _jm(*args, cwd=root)
    cfg = C.load(root)
    # gh-2104: a record type's own doc is authored text too, read by a face
    # `_inject` does not reach (the key is not `doc`).
    for m in cfg["eng"]["methods"]:
        if m["name"] == "one":
            m["record_doc"] = "One record.\n\nIts second paragraph."
    cfg["codec"] = copy.deepcopy(_CODEC)
    cfg["fir"].setdefault("methods", []).append(copy.deepcopy(_METHOD))
    for comp in ("solo", "fir", "bare"):
        cfg[comp]["extra_methods"] = [
            {"name": "hand", "fn": f"{comp.title()}_hand", "returns": "int"}
        ]
    ring = _ring_module()
    ring["getters"] = list(ring.get("getters") or []) + [
        {
            "fn": _GETTER_FN,
            "out": "ringbuf_zz_t",
            "fields": [{"name": "zzfield", "type": "double"}],
        }
    ]
    ring["factories"] = [
        {"name": "zzfactory", "create_fn": "ringbuf_zz"},
        {
            "name": "zzfac2",
            "create_fn": "ringbuf_zz2",
            "init_params": [{"name": "cap", "type": "int"}],
        },
    ]
    cfg["module"]["ringbuf"] = ring
    cfg["module"]["ddc_fn"] = _capsule_module()
    composer = _add_composer(
        cfg,
        extra_methods=[{"name": "zzextra", "fn": "Composer_zz"}],
        serializers=[{"name": "zzser", "fn": "wfm_zz_ser"}],
        settings=[
            {
                "name": "zzset",
                "type": "int",
                "setter_fn": "wfm_compose_set_zz",
                "getter_fn": "wfm_compose_get_zz",
            }
        ],
    )
    composer["source"]["computed"] = [
        {"name": "zzcomp", "type": "double", "fn": "wfm_zzcomp"}
    ]
    C.save(root, cfg)
    return root


def _inject(cfg: dict) -> "dict[str, tuple[str, tuple[str, ...]]]":
    """A sectioned `doc` on every table and row; ``{marker: (where, keys)}``.

    Each `doc` gets its own heading (``ZZH001``), so a heading in a rendered
    docstring is the author's exactly when it matches one, and any other
    heading is jm's. *keys* is the table path without list rows, the same
    unit `DOC_STANDS_ALONE` is keyed on.
    """
    marks: dict[str, tuple[str, tuple[str, ...]]] = {}

    def walk(node: object, path: tuple, keys: tuple) -> None:
        if isinstance(node, dict):
            # The two name -> table mappings hold tables, not a doc.
            if (
                path
                and path[0] != "project"
                and keys
                not in (
                    ("module",),
                    ("codec",),
                )
            ):
                n = len(marks) + 1
                head = f"ZZH{n:03d}"
                node["doc"] = (
                    f"Summary ZZD{n:03d}.\n\n{head}\n{'-' * len(head)}\n"
                    f"Body ZZD{n:03d}."
                )
                marks[f"ZZD{n:03d}"] = (".".join(path + ("doc",)), keys)
            for key, value in list(node.items()):
                if key != "doc" and not key.startswith("_"):
                    walk(value, path + (key,), keys + (key,))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                label = (
                    item.get("name")
                    if isinstance(item, dict) and item.get("name")
                    else str(i)
                )
                walk(item, path + (str(label),), keys)

    walk(cfg, (), ())
    for g in cfg["module"]["ringbuf"]["getters"]:
        if g["fn"] == _GETTER_FN:
            g["fields"][0].pop("doc")
    return marks


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> "tuple[Path, dict]":
    """Every `doc` position given a sectioned, multi-line value, applied:
    ``(root, marks)``, *marks* as `_inject` returns them."""
    root = _every_position_project(tmp_path_factory.mktemp("every"))
    cfg = C.load(root)
    marks = _inject(cfg)
    C.save(root, cfg)
    _jm("apply", cwd=root)
    return root, marks


@pytest.fixture(scope="module")
def measured(rendered: "tuple[Path, dict]") -> "dict[str, tuple]":
    """``{where: (reported, beside, (owner, table))}`` per rendered doc.

    *beside* is every heading jm wrote in a docstring that carries the doc,
    on any face; *reported* is whether the detector named it.
    """
    root, marks = rendered
    cfg = C.load(root)
    reported = {
        m.group(0)
        for d in D.manifest_docs_with_sections(C.load(root))
        for m in [re.search(r"ZZD\d{3}", d.summary)]
        if m
    }
    docs = _docstrings(root)
    out = {}
    for mk, (where, keys) in marks.items():
        faces = [text for _, text in docs if f"Summary {mk}." in text]
        if not faces:
            continue  # rendered nowhere: nothing to measure
        beside = sorted(
            {
                h
                for text in faces
                for h in _headings(text)
                if not re.fullmatch(r"ZZH\d{3}", h)
            }
        )
        if keys[:1] == ("module",):
            owner = C.module_kind(cfg, keys[1]) or "module"
            table = keys[2:]
        else:
            owner, table = "object", keys[1:]
        out[where] = (mk in reported, beside, (owner, table))
    return out


class TestEveryPosition:
    def test_reported_exactly_where_jm_generates_a_section(
        self, measured: dict
    ) -> None:
        wrong = {
            where
            for where, (reported, beside, _) in measured.items()
            if reported != bool(beside)
        }
        false_pos = sorted(w for w in wrong - set(RATCHET) if measured[w][0])
        false_neg = sorted(
            w for w in wrong - set(RATCHET) if not measured[w][0]
        )
        assert not false_pos, (
            "reported as a duplicated section, but jm generates no section "
            "beside it on any face -- if its whole table renders the doc "
            "alone, add the table to _docstring.DOC_STANDS_ALONE:\n  "
            + "\n  ".join(false_pos)
        )
        assert not false_neg, (
            "jm generates a section beside it, and it is not reported -- "
            "its table is in _docstring.DOC_STANDS_ALONE and should not be:"
            "\n  " + "\n  ".join(f"{w}: {measured[w][1]}" for w in false_neg)
        )
        fixed = sorted(set(RATCHET) - wrong)
        assert not fixed, (
            "no longer a false positive: delete its RATCHET entry\n  "
            + "\n  ".join(fixed)
        )

    def test_the_positives_still_report(self, measured: dict) -> None:
        """An object method's sectioned doc is the finding gh-1493 left."""
        for where in ("eng.methods.exec.doc", "eng.methods.tick.doc"):
            reported, beside, _ = measured[where]
            assert reported and beside, (where, beside)

    def test_every_exempt_table_is_measured(self, measured: dict) -> None:
        """A `DOC_STANDS_ALONE` entry this fixture never renders is a claim
        nothing checks: add a row of that table to the fixture."""
        rendered = {ot for _, _, ot in measured.values()}
        unmeasured = sorted(
            (owner, table)
            for owner, tables in D.DOC_STANDS_ALONE.items()
            for table in tables
            if (owner, table) not in rendered
        )
        assert not unmeasured, unmeasured


#: A `.pyi` docstring: its opening line's indent, and its text.
_STUB_DOCSTRING = re.compile(r'^([ \t]*)[rR]?"""(.*?)"""', re.S | re.M)


class TestEveryFace:
    """gh-2104: every face carries a `doc`, laid out as written.

    A codec-pack method pasted its `doc` raw into the stub, so every line
    after the first sat in column 0. Its `ml_doc` was a fixed line that never
    read the `doc` (gh-1113's shape: a key honoured on one face). Both are
    checked over every position this fixture renders, not over the one
    table: a raw paste anywhere fails the first test, and a `doc` honoured on
    the stub alone fails the second. Every injected `doc` has four lines,
    so a raw paste cannot pass by being one line long.
    """

    def test_every_stub_docstring_keeps_its_indent(
        self, rendered: "tuple[Path, dict]"
    ) -> None:
        root, _ = rendered
        flush = [
            f"{p.name}: {ln!r}"
            for p in sorted((root / "src").rglob("*.pyi"))
            for m in _STUB_DOCSTRING.finditer(p.read_text(encoding="utf-8"))
            for ln in m.group(2).split("\n")[1:]
            if ln.strip() and len(ln) - len(ln.lstrip()) < len(m.group(1))
        ]
        assert not flush, (
            "a docstring line left of its opening quotes -- a `doc` pasted "
            "raw; lay it out with `_docstring.authored_docstring`:\n  "
            + "\n  ".join(flush)
        )

    def test_a_doc_on_the_stub_is_on_the_runtime_face(
        self, rendered: "tuple[Path, dict]"
    ) -> None:
        root, marks = rendered
        docs = _docstrings(root)

        def on(mk: str, suffix: str) -> bool:
            return any(
                f"Summary {mk}." in text
                for name, text in docs
                if name.endswith(suffix)
            )

        stub_only = sorted(
            where
            for mk, (where, _) in marks.items()
            if on(mk, ".pyi") and not on(mk, ".c")
        )
        assert not stub_only, (
            "in the stub, but `help()` on the built type will not say it -- "
            "write the runtime doc from the same text "
            "(`_docstring.authored_c_doc`):\n  " + "\n  ".join(stub_only)
        )
