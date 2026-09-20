"""The same object must document itself the same way on every producer.

**This gate exists because notes did not work.** gh-805 §H took four releases
to become correct, and every one of the four was the same habit: a value wired
to one surface and not its sibling.

===========  ======================  ==================================
release      wired                   not wired
===========  ======================  ==================================
0.54.0       the context builder     the ``jm apply`` replay
0.54.1       the standalone replay   the module replay
0.54.2       the emitted body        the ``.pyi`` ``Raises``
0.54.3       one doc-face producer   the module-aggregated ``.pyi``
===========  ======================  ==================================

Each time the tests passed, because each test exercised the surface that had
just been wired. And the rule was already written down -- *"count the
BUILDERS, not just the producers; jm has FIVE .pyi producers, and fixing one
is how gh-747 happened"* -- in a note, where it did nothing.

So the invariant is gated rather than described: **scaffold one object twice,
standalone and inside a module, and require both stubs to document every
shared member identically.** It is registration-free by construction -- no
list to extend, no new feature to remember -- so the next producer wired in
one place and not its sibling fails here rather than downstream.

`_KNOWN_DIVERGENT` is a ratchet, not an excuse: it holds what was already
broken when the gate landed, each entry pointing at its issue, and it may
only shrink.

GATE: an object documents itself identically standalone and inside a module.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

# Members whose two producers already disagreed when this gate landed.
# Each entry is a BUG, not a waiver: the module-aggregated stub renders a
# brief-only docstring where the standalone renders the full numpy block --
# the same object documented less because it lives in a module. This is
# gh-642's defect (brief-only on one face) surviving between the two *stub*
# producers rather than between runtime and stub.
#
# Tracked as gh-867. The set may only shrink; deleting an entry is the fix
# landing, and `test_the_ratchet_only_holds_real_divergence` fails if an entry
# stops diverging, so this cannot rust into a permanent allowlist.
_KNOWN_DIVERGENT: set[str] = set()


# gh-1271: one defaulted parameter per literal kind, on both the constructor
# and a method. Each of these reached the stub as the C token at some point --
# `0U` and `1.5f` are SyntaxErrors in a `.pyi`, `true` and `NULL` are
# NameErrors -- and the nullable string is the one whose ANNOTATION also has
# to move, to `str | None`.
_INIT_PARAMS = [
    ("gain", "float", "1.0"),
    ("tag", "const char *", "NULL"),
]

_DEFAULTED_PARAMS = [
    ("count", "uint64_t", "0U"),
    ("ratio", "float", "1.5f"),
    ("loud", "bool", "true"),
    ("label", "const char *", "NULL"),
]


def _docstrings(pyi: Path) -> dict[str, str]:
    """``{member_name: docstring}`` for every method of every class in *pyi*.

    Parsed with `ast` rather than grepped because a docstring's *content* is
    the thing under test, and a regex over stub source cannot tell a section
    header from prose that happens to look like one.
    """
    tree = ast.parse(pyi.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(fn)
            if doc is not None:
                out[fn.name] = doc
    return out


# gh-881: the I/O shapes an object can have. The gate compared exactly ONE
# of these — `float`/`float` — for its whole life, and it is the only one that
# was clean: the other four each diverged, on `step`, `steps`, or both. An
# empty `_KNOWN_DIVERGENT` therefore read as "no divergence anywhere" while
# measuring one case in five. Coverage is the first thing to check about a
# gate that keeps reporting green.
SHAPES = [
    ("float", "float"),
    ("void", "float"),
    ("void", "void"),
    ("float", "void"),
    ("float[]", "float[]"),
]


def _signatures(pyi: Path) -> dict[str, str]:
    """``{member_name: normalised signature}`` for every method in *pyi*.

    The gate compared docstrings only, and that is a real blind spot: a
    member can be documented identically on both faces while the two
    producers disagree about its *parameters*. gh-805 §E is the worked
    example — `_stubs.py`'s module-aggregated producer offered `out=` on a
    `record_dtype` method while the standalone producer and the binding both
    refused it, so the module stub type-checked a call that raised at
    runtime. Every docstring matched throughout.

    Normalised through `ast.unparse` rather than compared as source text, so
    a line break is not a divergence. Adding a parameter wraps a signature
    across lines, and a gate that failed on formatting would be turned off
    the first time it cried wolf.

    `ast.unparse` needs 3.9+, which is this project's floor.
    """
    tree = ast.parse(pyi.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = ast.unparse(fn.args)
            returns = ast.unparse(fn.returns) if fn.returns else ""
            out[fn.name] = f"({args}) -> {returns}"
    return out


def _scaffold(
    root: Path,
    module: str | None,
    arg_type: str = "float",
    return_type: str = "float",
) -> Path:
    """One object, built standalone or into *module*; returns its ``.pyi``."""
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, arg_type=arg_type, return_type=return_type)
    method_run(
        root,
        "w",
        "close",
        module,
        "void",
        "int",
        False,
        [],
        status_return=True,
        error="ValueError",
        error_message="the capture has a hole",
    )
    cfg = C.load(root)
    C.set_destroy_spec(cfg, "w", {"returns": "int", "exit": "close"})
    C.save(root, cfg)
    apply_run(root)
    return (
        root / "src" / "p" / module / f"{module}.pyi"
        if module
        else root / "src" / "p" / "w.pyi"
    )


@pytest.fixture(scope="module")
def faces(tmp_path_factory) -> tuple[dict[str, str], dict[str, str]]:
    """The default `float`/`float` shape, for the tests that name members."""
    base = tmp_path_factory.mktemp("parity")
    standalone = _docstrings(_scaffold(base / "sa", None))
    module = _docstrings(_scaffold(base / "mo", "m"))
    return standalone, module


@pytest.fixture(scope="module")
def faces_by_shape(tmp_path_factory) -> dict:
    """Both faces for every shape in `SHAPES`, built once for the module."""
    base = tmp_path_factory.mktemp("parity_shapes")
    out = {}
    for at, rt in SHAPES:
        tag = f"{at}_{rt}".replace("[]", "arr")
        out[(at, rt)] = (
            _docstrings(_scaffold(base / tag / "sa", None, at, rt)),
            _docstrings(_scaffold(base / tag / "mo", "m", at, rt)),
        )
    return out


@pytest.fixture(scope="module")
def sigs_by_shape(tmp_path_factory) -> dict:
    """Signatures, per shape, both faces. Scaffolded once for the module."""
    base = tmp_path_factory.mktemp("parity_sigs")
    out = {}
    for at, rt in SHAPES:
        tag = f"{at}_{rt}".replace("[]", "arr")
        out[(at, rt)] = (
            _signatures(_scaffold(base / tag / "sa", None, at, rt)),
            _signatures(_scaffold(base / tag / "mo", "m", at, rt)),
        )
    return out


class TestBothStubProducersAgree:
    """The standalone and module-aggregated `.pyi` document members alike."""

    def test_the_two_trees_actually_built(self, faces):
        # A gate that silently compared two empty dicts would pass forever.
        standalone, module = faces
        assert standalone, "standalone stub produced no documented members"
        assert module, "module stub produced no documented members"

    def test_they_share_the_members_under_test(self, faces):
        standalone, module = faces
        shared = set(standalone) & set(module)
        assert {"__exit__", "__enter__", "destroy"} <= shared, sorted(shared)

    def test_every_shared_member_documents_identically(self, faces):
        standalone, module = faces
        divergent = sorted(
            name
            for name in set(standalone) & set(module)
            if standalone[name] != module[name]
            and name not in _KNOWN_DIVERGENT
        )
        assert not divergent, (
            "these members document differently depending on whether the "
            f"object is standalone or in a module: {divergent}. A second "
            "producer was wired in one place and not the other — see this "
            "file's docstring for why that keeps happening."
        )

    @pytest.mark.parametrize(("arg_type", "return_type"), SHAPES)
    def test_every_shape_documents_identically(
        self, faces_by_shape, arg_type, return_type
    ):
        """gh-881: the same claim, for every I/O shape rather than one.

        `_KNOWN_DIVERGENT` is deliberately NOT consulted here. It exists to
        ratchet down a known set, and seeding it with these would be adding
        entries to a set whose whole rule is that it may only shrink — so the
        divergences were fixed instead, and this arrives with nothing to
        waive.
        """
        standalone, module = faces_by_shape[(arg_type, return_type)]
        assert standalone and module, (
            f"{arg_type} -> {return_type} produced no documented members on "
            f"one of the two faces, so this comparison would pass vacuously"
        )
        divergent = sorted(
            name
            for name in set(standalone) & set(module)
            if standalone[name] != module[name]
        )
        assert not divergent, (
            f"for an object with arg_type={arg_type!r} "
            f"return_type={return_type!r}, these members document differently "
            f"depending on whether it is standalone or in a module: "
            f"{divergent}."
        )

    @pytest.mark.parametrize(("arg_type", "return_type"), SHAPES)
    def test_every_shape_has_the_same_signature(
        self, sigs_by_shape, arg_type, return_type
    ):
        """The blind spot this gate had: it compared docstrings only.

        A member can be documented identically on both faces while the two
        producers disagree about its PARAMETERS, and that disagreement is the
        more damaging kind — a docstring difference misinforms, a signature
        difference makes a type checker bless a call that raises.

        Both instances found when this was added were real:

        - `steps(n: int = 1)` standalone against `steps(n: int)` in a module,
          so `obj.steps()` type-checked on one face and failed on the other.
          gh-527 fixed that default on one producer and not its sibling.
        - `__init__` advertising `gain: float = 0.0` standalone and
          `gain: float = ...` in a module — the same object documented less
          for living in a module, which is gh-642's defect again.
        - and gh-805 §E, where the module face offered an `out=` the binding
          refused outright.

        `_KNOWN_DIVERGENT` is deliberately not consulted: the divergences were
        fixed rather than waived, so this arrives with nothing to allow.
        """
        standalone, module = sigs_by_shape[(arg_type, return_type)]
        assert standalone and module

        divergent = sorted(
            f"{name}\n      standalone: {standalone[name]}"
            f"\n      module:     {module[name]}"
            for name in set(standalone) & set(module)
            if standalone[name] != module[name]
        )
        assert not divergent, (
            f"for arg_type={arg_type!r} return_type={return_type!r} these "
            f"members have different SIGNATURES depending on whether the "
            f"object is standalone or in a module:\n    "
            + "\n    ".join(divergent)
        )

    def test_every_shape_documents_steps_at_all(self, faces_by_shape):
        """A member present on both faces but documented on neither passes
        `test_every_shape_documents_identically` while saying nothing.

        The blockwise shape did exactly that: its standalone `steps()` was a
        bare `...` with no docstring, so the members simply did not appear in
        either mapping and equality never had anything to compare.
        """
        missing = [
            f"{at} -> {rt} ({name})"
            for (at, rt), faces_pair in faces_by_shape.items()
            for name, face in zip(("standalone", "module"), faces_pair)
            if "steps" not in face
        ]
        assert not missing, (
            f"steps() is undocumented on at least one face for: "
            f"{sorted(set(missing))}"
        )

    def test_the_ratchet_only_holds_real_divergence(self, faces):
        # An entry that no longer diverges must be DELETED, or the ratchet
        # rusts into a permanent allowlist that hides the next regression.
        standalone, module = faces
        stale = sorted(
            name
            for name in _KNOWN_DIVERGENT
            if name in standalone
            and name in module
            and standalone[name] == module[name]
        )
        assert not stale, (
            f"{stale} no longer diverge — remove them from "
            "_KNOWN_DIVERGENT so the gate keeps its teeth."
        )


def _scaffold_defaults(root: Path, module: str | None) -> Path:
    """One object whose constructor AND one method carry defaults.

    A second axis, not a second gate. `SHAPES` above varies the I/O types and
    every method it builds takes no parameter -- so a defaulted parameter, and
    every way of getting one wrong, was outside the tree this file walks. That
    is how gh-1271 stayed invisible here: the module-aggregated producer
    discarded every defaulted init-param's literal (`gain: float = ...`
    against the standalone `gain: float = 1.0`), and BOTH producers emitted
    the declared C literal verbatim into the stub.
    """
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, init_params=_INIT_PARAMS)
    method_run(
        root,
        "w",
        "tune",
        module,
        "void",
        "void",
        False,
        [],
        params=_DEFAULTED_PARAMS,
    )
    apply_run(root)
    return (
        root / "src" / "p" / module / f"{module}.pyi"
        if module
        else root / "src" / "p" / "w.pyi"
    )


@pytest.fixture(scope="module")
def default_faces(tmp_path_factory) -> tuple[dict[str, str], dict[str, str]]:
    base = tmp_path_factory.mktemp("parity_defaults")
    return (
        _signatures(_scaffold_defaults(base / "sa", None)),
        _signatures(_scaffold_defaults(base / "mo", "m")),
    )


class TestDefaultedParamsAgreeAcrossFaces:
    """gh-1271: a default is part of the surface, so it is part of parity.

    Signatures rather than docstrings, deliberately. A parameterised method's
    *docstring* still diverges -- the module face renders the brief alone
    where the standalone renders the full numpy block -- and that is gh-1292,
    measured on unmodified `main` and filed rather than waived here. Asserting
    it would mean putting an entry into `_KNOWN_DIVERGENT`, whose whole rule
    is that it may only shrink.
    """

    def test_both_faces_actually_built_the_method(self, default_faces):
        """Two empty dicts would agree perfectly and prove nothing."""
        sa, mo = default_faces
        assert "tune" in sa, sorted(sa)
        assert "tune" in mo, sorted(mo)
        assert "__init__" in sa and "__init__" in mo

    def test_the_defaulted_method_signs_identically(self, default_faces):
        sa, mo = default_faces
        assert sa["tune"] == mo["tune"]

    def test_the_constructor_signs_identically(self, default_faces):
        """The peer that was wrong: `= ...` in a module, the literal alone."""
        sa, mo = default_faces
        assert sa["__init__"] == mo["__init__"]

    def test_no_c_literal_reaches_either_face(self, default_faces):
        """`0U` and `1.5f` are SyntaxErrors in a `.pyi`, `true`/`NULL` names.

        Named individually rather than trusting the signature comparison
        above: two producers with the SAME defect agree with each other, and
        agreeing on a stub that does not parse is not parity.
        """
        sa, mo = default_faces
        for face, sigs in (("standalone", sa), ("module", mo)):
            blob = sigs["tune"] + sigs["__init__"]
            for token in ("0U", "1.5f", "true", "NULL"):
                assert token not in blob, f"{face}: C literal {token!r}"

    def test_the_nullable_string_is_annotated_as_nullable(self, default_faces):
        """The binding parses it with `z`, so the stub must say `| None`."""
        sa, mo = default_faces
        for sigs in (sa, mo):
            assert "label: str | None=None" in sigs["tune"].replace(" =", "=")
            assert "tag: str | None=None" in sigs["__init__"].replace(
                " =", "="
            )


class TestLifecycleGlueSpecifically:
    """The surface §H broke four times, pinned on both producers at once."""

    @pytest.mark.parametrize("member", ["__enter__", "__exit__", "destroy"])
    def test_glue_docs_match_across_producers(self, faces, member):
        standalone, module = faces
        assert standalone[member] == module[member]

    def test_exit_describes_finalizing_on_both(self, faces):
        # gh-864 defect 3: the module stub said "releasing" over a body that
        # finalizes, and agreed with nothing.
        for face in faces:
            assert "finalizing" in face["__exit__"]
            assert "releasing" not in face["__exit__"]

    def test_destroy_raises_the_inherited_class_on_both(self, faces):
        # gh-864 defect 1: the stub named RuntimeError over a ValueError body.
        for face in faces:
            assert "ValueError" in face["destroy"]
            assert "RuntimeError\n" not in face["destroy"]


# ── gh-1292: a member with a PARAMETER, on all four faces ────────────────────
#
# Every other fixture in this file builds members with no parameter, and a
# brief-only docstring and a full one are the same string when there is no
# `Parameters` block to lose. So the gate reported green while a module
# object's undocumented method collapsed to `Tune.` in its stub and listed its
# parameters in `help()` -- and while the runtime face spelled the summary
# `tune.` on both paths. Four faces rather than two, because the stub-vs-stub
# comparison above could not have caught the second half at all.

from test_gh642_runtime_doc_parity import _runtime_doc, _stub_doc  # noqa: E402

_AUTHORED_TUNE = (
    " * @brief Retune it.\n *\n * @param gain The new gain, linear."
)


def _tune_project(root: Path, module: str | None, authored: bool) -> Path:
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, arg_type="float", return_type="float")
    method_run(
        root,
        "w",
        "tune",
        module,
        "void",
        "void",
        False,
        [],
        params=[("gain", "double", "")],
    )
    if authored:
        h = next((root / "native" / "inc").rglob("w_core.h"))
        t = h.read_text(encoding="utf-8")
        assert t.count(" * @brief tune.") == 1, "scaffold no longer seeds it"
        h.write_text(t.replace(" * @brief tune.", _AUTHORED_TUNE), "utf-8")
    apply_run(root)
    return root


def _four_faces(root: Path, module: str | None) -> dict[str, list[str]]:
    """``{face: doc lines}`` for `tune`, up to any runtime Examples section."""
    pyi = (
        root / "src" / "p" / (f"{module}/{module}.pyi" if module else "w.pyi")
    )
    (ext,) = [
        p
        for p in (root / "native" / "src").rglob("*_ext*.c")
        if '{"tune",' in p.read_text(encoding="utf-8")
    ]
    runtime = _runtime_doc(ext.read_text(encoding="utf-8"), "tune")
    assert runtime[0].startswith("tune("), runtime[0]
    runtime = runtime[2:]
    if "Examples" in runtime:
        runtime = runtime[: runtime.index("Examples")]
    stub = _stub_doc(pyi.read_text(encoding="utf-8"), "tune")
    tag = "module" if module else "standalone"
    return {
        f"{tag} stub": [ln for ln in stub if ln.strip()],
        f"{tag} runtime": [ln for ln in runtime if ln.strip()],
    }


@pytest.mark.parametrize(
    "authored", [False, True], ids=["undocumented", "authored"]
)
def test_a_parameterised_method_documents_alike_on_all_four_faces(
    tmp_path, authored
):
    faces = {
        **_four_faces(_tune_project(tmp_path / "sa", None, authored), None),
        **_four_faces(_tune_project(tmp_path / "mo", "m", authored), "m"),
    }
    reference = faces["standalone stub"]
    # The case this exists for: the section is there to lose.
    assert "Parameters" in reference, reference
    divergent = {k: v for k, v in faces.items() if v != reference}
    assert not divergent, (reference, divergent)


def test_an_undocumented_free_function_spells_its_summary_alike(tmp_path):
    """The same capitalisation split, on the module-function faces."""
    from just_makeit._function import run as function_run

    root = tmp_path / "p"
    new_run("p", root, [], [])
    module_run(root, "m")
    function_run(root, "scale_it", "m", params=[("gain", "double")])
    apply_run(root)
    ext = (root / "native/src/m/m_ext.c").read_text(encoding="utf-8")
    pyi = (root / "src/p/m/m.pyi").read_text(encoding="utf-8")
    stub = re.search(r'def scale_it\(.*?\n    """(.*?)"""', pyi, re.S)
    assert stub, pyi
    assert _runtime_doc(ext, "scale_it")[0] == stub.group(1) == "Scale it."
