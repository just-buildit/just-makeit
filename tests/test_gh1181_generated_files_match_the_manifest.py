"""gh-1181: the drift oracle covers every table and every reconciled file.

gh-1172 established the shape. `jm apply` rebuilds a throwaway tree from
*reconstructed CLI history* and renders from that tree's manifest, so a key no
CLI command carries reaches the replay only if `_apply` names it explicitly —
and when it does not, `jm status --check` inherits the same blind spot,
because status copies the project, runs the *same* replay on the copy, and
diffs. The checker cannot see a difference it also makes.

Its gate swept the `[<obj>]` table and compared `.pyi` files. That left two
gaps, and this closes both.

The tables
----------
`_apply` reconstructs each kind of manifest row from its own hand-written key
list: `_object_kwargs`, `_replay_method`, `_replay_property`, the `_view.run`
and `_function.run` calls, and the `[module.X]` metadata copy-down. Five
lists, five chances to forget a key — gh-663, gh-645, gh-542, gh-1117 and
gh-1172 are five instances of exactly that. `TestEveryTableReachesTheArtefact`
sweeps all of them, one manifest-only key at a time, written into an
already-applied and already-clean project the way a hand edit arrives.

The files
---------
Two oracles, because they answer different questions and neither subsumes the
other:

**Direct render** (`TestApplyAgreesWithTheDirectRender`) — jm's own renderers,
called against the *real* manifest and the *real* headers, are the one
reference that does not go through the replay. This is what catches a dropped
key. It covers the reconciled files an independent entry point exists for: the
`.pyi` stubs and both shapes of `_ext.c`.

**Rebuild** (`TestARebuildFromTheManifestAgrees`) — the same project built from
its manifest alone must be byte-identical. Both sides go through the replay,
so this cannot see a dropped key; what it sees is the other failure, where
`apply`'s *incremental* path and its *materialize* path never converge on the
same file. That is a real bug it found: `jm new` writes the minimal package
`__init__.py`, the first standalone object then finds the file present and
splices into it, and the full template carrying the Windows DLL-directory
preamble was never reached. Every project built the ordinary way — a
`--windows` one included — shipped a package `__init__.py` with no
`os.add_dll_directory` call, and `status --check` said clean, because `apply`
MERGES that file and the difference was never between the two sides it
compares.

Scope, derived rather than listed
---------------------------------
Both oracles ask `_createonly.classify` which files `apply` is answerable for.
`RECONCILED` is rewritten wholesale, `PARTIAL` is spliced into; `AUTHOR` and
`JM` are create-only and legitimately diverge. Nothing here carries a path
list of its own, so a file jm learns to generate is classified — and covered —
by the same table `status` already reads.

The sweep is `RECONCILED`-only on purpose. A manifest key added after
scaffolding may well imply a *structural* change to a sacred file (a new state
field in `_core.h`, a function body's return type), and `apply` deliberately
does not make those — that is what `jm regenerate` is for. Asserting on them
would be asserting against the sacred/glue contract itself.

Residual, measured here and filed rather than skipped: gh-1183 (a fragment
whose `.tp_doc` slot was deleted never gets one back — `status` classifies it
AUTHOR-OWNED, so `--check` stays 0) and gh-1185 (`manual_stub` on a method a
view inherits aborts `apply` with a traceback, so that key's sweep case is
unmeasured rather than passing).
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _createonly as CO  # noqa: E402
from just_makeit import _glue  # noqa: E402
from just_makeit import _object as O  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402

#: Never compared: build output and bytecode are not the manifest's.
_SKIP_PARTS = {"build", "__pycache__", ".git"}
_SKIP_SUFFIXES = {".pyc", ".so", ".pyd"}


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


def _diff(got: str, want: str, name: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            got.splitlines(True),
            want.splitlines(True),
            f"{name} (on disk)",
            f"{name} (from the manifest)",
        )
    )


# --------------------------------------------------------------------------
# Oracle 1 — jm's own renderers, called against the real manifest.
# --------------------------------------------------------------------------


def _direct_render_pairs(root: Path) -> list[tuple[Path, str]]:
    """Every reconciled file an INDEPENDENT render exists for.

    "Independent" is the whole point: each of these entry points is the one
    `apply` itself renders the file with, handed the real cfg and the real
    root instead of the replay's reconstruction. A second implementation
    written here would drift into disagreeing with the first, and the gate
    would then report jm's own divergence rather than the manifest's — which
    is why `render_module_ext_c` was extracted (gh-1181) rather than copied.

    Formatting is not a variable: `_pyfmt`/`_cfmt` are no-ops unless the
    project declares a format command, and these fixtures do not.
    """
    cfg = C.load(root)
    pkg = C.project_name(cfg)
    pairs: list[tuple[Path, str]] = []
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        # A handle/capsule/composer module is materialized straight from the
        # manifest with no object-group scaffold, so these two entry points
        # are not its renderers. gh-1181's own residual.
        if (
            C.is_capsule_module(cfg, mod)
            or C.is_composer_module(cfg, mod)
            or C.is_handle_module(cfg, mod)
        ):
            continue
        mp = C.module_paths(mod)
        out_pkg = C.module_package(cfg, mod) or mp.pypath
        p = root / "src" / pkg / out_pkg / f"{mp.leaf}.pyi"
        if p.exists():
            pairs.append((p, S.make_module_pyi(cfg, mod, root)))
        p = root / "native" / "src" / mp.cname / f"{mp.cname}_ext.c"
        if p.exists():
            pairs.append((p, O.render_module_ext_c(root, cfg, mod, pkg)))
    owned = {o for m in C.modules(cfg) for o in C.module_objects(cfg, m)}
    for comp in C.components(cfg):
        if comp in owned:
            continue
        ctx = _glue.component_ctx(cfg, comp, pkg, root)
        ctx["extra_include"] = _glue.standalone_extra_include(root, comp)
        p = root / "src" / pkg / f"{comp}.pyi"
        if p.exists():
            pairs.append((p, R.render_component_pyi(ctx)))
        p = root / "native" / "src" / comp / f"{comp}_ext.c"
        if p.exists():
            pairs.append((p, R.render(R.COMPONENT_EXT_C, ctx)))
    assert pairs, "the fixture generated nothing — the gate would be vacuous"
    return pairs


def assert_matches_the_direct_render(root: Path) -> None:
    for path, want in _direct_render_pairs(root):
        got = path.read_text(encoding="utf-8")
        if got != want:
            pytest.fail(
                f"{path.relative_to(root)} disagrees with jm's own render of "
                f"the manifest — `apply` wrote something the manifest does "
                f"not say, and `status` compares against the same wrong "
                f"reference:\n" + _diff(got, want, path.name)
            )


# --------------------------------------------------------------------------
# Oracle 2 — the same project, built from its manifest alone.
# --------------------------------------------------------------------------


def _tree(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or set(rel.parts) & _SKIP_PARTS:
            continue
        if p.suffix in _SKIP_SUFFIXES:
            continue
        out[rel.as_posix()] = p.read_bytes()
    return out


def assert_a_rebuild_agrees(root: Path, kinds: tuple[str, ...]) -> None:
    """Rebuild *root* from its manifest alone and compare.

    *kinds* names which of `_createonly`'s four classifications must agree.
    `RECONCILED` is what `apply` rewrites wholesale and `PARTIAL` what it
    splices into; `AUTHOR` and `JM` are create-only, so a difference there is
    the author's content or an older jm, not a defect.
    """
    from just_makeit import _apply

    with tempfile.TemporaryDirectory() as td:
        fresh = Path(td) / "fresh"
        fresh.mkdir()
        shutil.copy2(root / C.FILENAME, fresh / C.FILENAME)
        for d in ("objects", "modules"):
            if (root / d).is_dir():
                shutil.copytree(root / d, fresh / d)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _apply.run(fresh)
        before, after = _tree(root), _tree(fresh)
        for rel in sorted(set(before) | set(after)):
            if before.get(rel) == after.get(rel):
                continue
            rule = CO.classify(rel)
            if rule is None or rule.kind not in kinds:
                continue
            pytest.fail(
                f"{rel} ({rule.kind}) differs from the same project rebuilt "
                f"from its manifest alone — `apply`'s incremental path and "
                f"its materialize path do not converge, and `status` cannot "
                f"see it because both of its sides take the incremental "
                f"one:\n"
                + _diff(
                    before.get(rel, b"").decode("utf-8", "replace"),
                    after.get(rel, b"").decode("utf-8", "replace"),
                    rel,
                )
            )


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """One project carrying a row of every table the replay reconstructs.

    Module object with a method, a property and a view; a module-level
    function; and a standalone object with its own method and property, so a
    finding about the module path is visibly not a finding about both. Built
    by running the tool — a hand-written fixture would be a fixture of what I
    think jm emits, which is the thing under test.
    """
    assert _cli("new", "sw", cwd=tmp_path).returncode == 0
    root = tmp_path / "sw"
    steps = [
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        (
            "method",
            "o",
            "gain2",
            "--module",
            "m",
            "--arg-type",
            "double",
            "--return-type",
            "double",
        ),
        ("property", "o", "level", "--module", "m", "--type", "double"),
        ("view", "o", "Peek", "--module", "m", "--create-fn", "o_create_peek"),
        ("function", "calc", "--module", "m"),
        ("object", "solo", "--state", "k:double:2.0"),
        (
            "method",
            "solo",
            "scale",
            "--arg-type",
            "double",
            "--return-type",
            "double",
        ),
        ("property", "solo", "bias", "--type", "double"),
    ]
    for step in steps:
        out = _cli(*step, cwd=root)
        assert out.returncode == 0, f"{step}: {out.stdout}{out.stderr}"
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


class TestApplyAgreesWithTheDirectRender:
    def test_the_baseline_project_agrees(self, project: Path) -> None:
        """The premise. If this failed, every assertion below would be
        reporting the oracle's own disagreement rather than a manifest's."""
        assert_matches_the_direct_render(project)


class TestARebuildFromTheManifestAgrees:
    """The property jm's manifest-is-SSOT design claims outright.

    Every file `apply` is answerable for — rewritten wholesale or spliced
    into — must be what a from-scratch materialize would write. `PARTIAL` is
    included because this fixture has no hand edits, which is exactly the
    condition that makes the two paths comparable at all.
    """

    def test_every_reconciled_and_spliced_file_agrees(
        self, project: Path
    ) -> None:
        assert_a_rebuild_agrees(project, (CO.RECONCILED, CO.PARTIAL))


class TestThePackageInitGuardsItsDLLs:
    """gh-1181's own finding, kept as a named assertion beside the general
    one — the rebuild oracle says *some file* diverged, and this says which
    one and why it mattered."""

    PREAMBLE = "_os.add_dll_directory"

    def test_a_standalone_objects_package_carries_the_preamble(
        self, project: Path
    ) -> None:
        init = (project / "src/sw/__init__.py").read_text(encoding="utf-8")
        assert self.PREAMBLE in init, init

    def test_a_fresh_object_gets_it_before_any_apply(
        self, tmp_path: Path
    ) -> None:
        """The scaffold path on its own. `apply` fixes this file too, and the
        `project` fixture runs one — so without this the splice half of the
        fix can be reverted with the general assertions still green."""
        assert _cli("new", "fp", cwd=tmp_path).returncode == 0
        root = tmp_path / "fp"
        assert (
            _cli("object", "a", "--state", "g:double:1.0", cwd=root).returncode
            == 0
        )
        init = (root / "src/fp/__init__.py").read_text(encoding="utf-8")
        assert self.PREAMBLE in init, init
        assert "from .a import A  # noqa: E402" in init, init

    def test_the_reexports_are_marked_noqa(self, project: Path) -> None:
        """They follow statements now, so a project that lints its own
        generated Python would fail E402 on jm's output."""
        init = (project / "src/sw/__init__.py").read_text(encoding="utf-8")
        assert "from .solo import Solo  # noqa: E402" in init, init

    def test_a_second_object_is_marked_too(self, project: Path) -> None:
        """The splice that ADDS a re-export runs when the preamble is already
        there, and an early return on "preamble present" left exactly that
        line unannotated."""
        assert (
            _cli(
                "object", "extra", "--state", "z:double:3.0", cwd=project
            ).returncode
            == 0
        )
        init = (project / "src/sw/__init__.py").read_text(encoding="utf-8")
        assert "from .extra import Extra  # noqa: E402" in init, init

    def test_a_module_only_package_does_not_get_it(
        self, tmp_path: Path
    ) -> None:
        """A package with no extension beside it has no DLLs to find, and
        adding the block there would churn every such project for nothing.
        The guard is a re-export line, so this stays a plain docstring."""
        assert _cli("new", "mo", cwd=tmp_path).returncode == 0
        root = tmp_path / "mo"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert _cli("object", "o", "--module", "m", cwd=root).returncode == 0
        init = (root / "src/mo/__init__.py").read_text(encoding="utf-8")
        assert self.PREAMBLE not in init, init

    def test_an_existing_project_catches_up_on_apply(
        self, project: Path
    ) -> None:
        """The half that reaches projects already on disk. `apply` merges this
        file rather than overwriting it, and merging only ever spliced
        imports — so gating the preamble on a NEW import would have left
        exactly the projects that need it without one, forever."""
        init = project / "src/sw/__init__.py"
        init.write_text('"""sw package."""\nfrom .solo import Solo\n', "utf-8")
        assert _cli("status", "--check", cwd=project).returncode == 1
        assert _cli("apply", cwd=project).returncode == 0
        assert self.PREAMBLE in init.read_text(encoding="utf-8")
        out = _cli("status", "--check", cwd=project)
        assert out.returncode == 0, out.stdout


#: One manifest-only key per table jm reconstructs from a hand-written list.
#:
#: Keyed by the fragment file and the row to write into, because that is how
#: the bug arrives: an author opens the manifest and adds a key to a row that
#: is already scaffolded, already applied and already clean.
#:
#: Every one of these is a key `_apply` can only carry by naming it — there is
#: no CLI flag for it, so nothing in the reconstructed history holds it.
SWEEP: dict[str, tuple[str, str, str]] = {
    # [[<obj>.methods]] — `_replay_method`
    "method.max_results": ("o", 'name = "gain2"', 'max_results = "16"'),
    "method.none_on_empty": ("o", 'name = "gain2"', "none_on_empty = true"),
    "method.sink_fn": ("o", 'name = "gain2"', 'sink_fn = "o_sink"'),
    "method.nogil": ("o", 'name = "gain2"', "nogil = true"),
    "method.doc": ("o", 'name = "gain2"', 'doc = "METHOD marker."'),
    "method.py_return_type": (
        "o",
        'name = "gain2"',
        'py_return_type = "float"',
    ),
    # [[<obj>.properties]] — `_replay_property`
    "property.doc": ("o", 'name = "level"', 'doc = "PROP marker."'),
    "property.ctype": ("o", 'name = "level"', 'ctype = "double"'),
    "property.writable": ("o", 'name = "level"', "writable = true"),
    "property.default": ("o", 'name = "level"', 'default = "1.5"'),
    "property.field": ("o", 'name = "level"', 'field = "g"'),
    # [[<obj>.views]] — the `_view.run` call
    "view.doc": ("o", 'class_name = "Peek"', 'doc = "VIEW marker."'),
    "view.exclude_methods": (
        "o",
        'class_name = "Peek"',
        'exclude_methods = ["gain2"]',
    ),
    "view.exclude_properties": (
        "o",
        'class_name = "Peek"',
        'exclude_properties = ["level"]',
    ),
    "view.create_error": (
        "o",
        'class_name = "Peek"',
        'create_error = "ValueError"\ncreate_error_message = "VIEW_ERR"',
    ),
    # [[module.X.functions]] — the `_function.run` call
    "function.doc": ("m", 'name = "calc"', 'doc = "FN marker."'),
    "function.check_return": (
        "m",
        'name = "calc"',
        'check_return = "negative"',
    ),
    "function.inline": ("m", 'name = "calc"', "inline = true"),
    "function.return_type": ("m", 'name = "calc"', 'return_type = "double"'),
    # [module.X] — `_module.run` plus the metadata copy-down
    "module.doc": ("m", 'objects = ["o"]', 'doc = "MODULE marker."'),
    "module.extra_types": ("m", 'objects = ["o"]', 'extra_types = ["Foo"]'),
    "module.reexports": ("m", 'objects = ["o"]', 'reexports = ["O"]'),
    "module.functions_in_core": (
        "m",
        'objects = ["o"]',
        "functions_in_core = true",
    ),
    "module.serializable": ("m", 'objects = ["o"]', "serializable = true"),
    # the standalone peers of the two member tables
    "solo.method.doc": ("solo", 'name = "scale"', 'doc = "SOLO_M marker."'),
    "solo.property.doc": ("solo", 'name = "bias"', 'doc = "SOLO_P marker."'),
    "solo.method.none_on_empty": (
        "solo",
        'name = "scale"',
        "none_on_empty = true",
    ),
}


class TestEveryTableReachesTheArtefact:
    """The sweep. One key, one row, one `apply`, both oracles.

    The assertion is deliberately not a per-key expectation about what the
    key renders to. What is being held is weaker and more useful: whatever
    the manifest says, the generated file says the same thing — so a key
    added to jm tomorrow is covered by adding one line here, not by working
    out what its output should look like.

    `RECONCILED` only for the rebuild arm. A key added after scaffolding may
    imply a structural change to a sacred file, and `apply` deliberately does
    not make those.
    """

    @staticmethod
    def _inject(root: Path, fragment: str, anchor: str, line: str) -> None:
        for sub in ("objects", "modules"):
            p = root / sub / f"{fragment}.toml"
            if p.exists():
                break
        else:  # pragma: no cover - a typo in SWEEP, not a jm defect
            raise AssertionError(f"no fragment file for {fragment!r}")
        body = p.read_text(encoding="utf-8")
        assert body.count(anchor + "\n") == 1, (fragment, anchor, body)
        p.write_text(
            body.replace(anchor + "\n", anchor + "\n" + line + "\n", 1),
            encoding="utf-8",
        )

    @pytest.mark.parametrize("case", sorted(SWEEP))
    def test_the_key_reaches_every_generated_file(
        self, project: Path, case: str
    ) -> None:
        self._inject(project, *SWEEP[case])
        applied = _cli("apply", cwd=project)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert_matches_the_direct_render(project)
        assert_a_rebuild_agrees(project, (CO.RECONCILED,))
