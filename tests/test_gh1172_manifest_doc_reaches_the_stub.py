"""gh-1172: a manifest key reaches the stub, and `status` can see when it
does not.

A `[<obj>] doc` added to the manifest after scaffolding reached a module
object's runtime `tp_doc` and not its `.pyi`. `_stubs` renders it correctly
from that same manifest, so the generator was never the problem — the file
was simply overwritten with a render made from a manifest that no longer had
the key.

`jm apply` replays the project into a throwaway tree reconstructed from CLI
history, then copies the module's pure-generated files over the real ones.
There is no `jm object --doc`, so nothing in that reconstructed history
carried `doc`, the temp manifest had none, and the generic seed was what got
copied. The standalone path escaped it only because gh-1165 bolted a
post-replay re-render from the real cfg onto that one loop.

The fix is in the replay, not in a second post-replay re-render: `doc` joins
`process_global`, `destroy` and `no_reset` in `_apply._object_kwargs` as a
manifest-only key the replay carries explicitly, and `add_component`
re-persists it. One change, both faces, both object shapes.

The half that matters more
--------------------------
`jm status --check` exited **0** on this. Status copies the project, runs the
same replay on the copy, and diffs before against after — so a key the replay
drops is dropped identically on both sides, the two agree, and the drift is
invisible. The checker cannot see a difference it also makes.

gh-1140's `TestNothingGeneratedIsInvisibleToStatus` did not fire because it
asks a different question. It clobbers each file and demands `status` notice,
which proves the file is **compared**; the module `.pyi` is compared, and was
all along. gh-1172 is on the other axis: the file is compared against a
*reference* computed with the key missing. No amount of clobbering finds that.

So the gate here is the missing axis — an **independent oracle**. jm's own
renderers, called directly against the real manifest, are the one reference
that does not go through the replay, and `TestApplyAgreesWithTheDirectRender`
demands every generated `.pyi` on disk equal what they produce. Any manifest
key the replay drops that reaches a stub shows up there immediately, with no
file to register: a stub jm learns to generate is covered on the day it is
generated.

`TestAHandAuthoredKeyReachesTheStub` is the same property driven the way the
bug arrives — one manifest-only object key at a time, written into an
already-applied project. Those are the keys at risk, because they are exactly
the ones the reconstructed CLI history cannot express.

Driven through the CLI against a tree built by running the tool. The residual
is named in gh-1181: this sweeps `[<obj>]`, not the method / property / view /
function / module tables, which have the same exposure.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _glue  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402

MARKER = "MODOBJ_DOC marker."
SOLO_MARKER = "SOLO_DOC marker."
VIEW_MARKER = "VIEW_DOC marker."


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


def _put_object_key(root: Path, fragment: str, line: str) -> None:
    """Write *line* into the `[<fragment>]` table of an object fragment.

    Appending to the file would land the key in whatever sub-table is last
    (`[[o.state]]`, typically), where it means something else entirely — which
    is a real way to mis-reproduce this bug. Anchoring on `no_step`, the last
    always-emitted scalar, puts it on the object table itself.
    """
    p = root / "objects" / f"{fragment}.toml"
    body = p.read_text(encoding="utf-8")
    anchor = 'no_step = "false"\n'
    assert anchor in body, body
    p.write_text(body.replace(anchor, anchor + line + "\n", 1), "utf-8")


def _stub_pairs(root: Path) -> list[tuple[Path, str]]:
    """Every generated `.pyi` in *root*, paired with jm's DIRECT render of it.

    The direct render reads the real manifest and the real (sacred) headers
    through the same two entry points `apply` itself renders stubs with —
    `_stubs.make_module_pyi` for a module, `render_component_pyi` over
    `_glue.component_ctx` for a standalone. What it does *not* go through is
    the replay, which is the whole point: it is the only reference in the
    repo that cannot inherit the replay's own omissions.

    Formatting is not a variable here. `_pyfmt.format_project` is a no-op
    unless the project declares `[project] py_format_command`, and these
    fixtures do not, so both sides are jm's own layout and compare byte for
    byte.
    """
    cfg = C.load(root)
    pkg = C.project_name(cfg)
    pairs: list[tuple[Path, str]] = []
    for mod in C.modules(cfg):
        mp = C.module_paths(mod)
        out_pkg = C.module_package(cfg, mod) or mp.pypath
        p = root / "src" / pkg / out_pkg / f"{mp.leaf}.pyi"
        if p.exists():
            pairs.append((p, S.make_module_pyi(cfg, mod, root)))
    owned = {o for m in C.modules(cfg) for o in C.module_objects(cfg, m)}
    for comp in C.components(cfg):
        if comp in owned:
            continue
        p = root / "src" / pkg / f"{comp}.pyi"
        if not p.exists():
            continue
        ctx = _glue.component_ctx(cfg, comp, pkg, root)
        ctx["extra_include"] = _glue.standalone_extra_include(root, comp)
        pairs.append((p, R.render_component_pyi(ctx)))
    assert pairs, "fixture generated no stubs — the gate would be vacuous"
    return pairs


def _assert_stubs_match_the_direct_render(root: Path) -> None:
    for path, want in _stub_pairs(root):
        got = path.read_text(encoding="utf-8")
        if got != want:
            import difflib

            diff = "".join(
                difflib.unified_diff(
                    got.splitlines(True),
                    want.splitlines(True),
                    f"{path.name} (on disk, via the replay)",
                    f"{path.name} (rendered from the manifest)",
                )
            )
            pytest.fail(
                f"{path} disagrees with jm's own render of the manifest — "
                f"`apply` wrote something the manifest does not say, and "
                f"`status` compares against the same wrong reference:\n{diff}"
            )


@pytest.fixture
def scaffold(tmp_path: Path) -> Path:
    """A project with both object shapes, built by running the tool.

    `o` lives in module `m` (the shape gh-1172 is about, where the `.pyi` is
    the module aggregate) and carries a method, a property and a view so the
    stub has real content around the class docstring. `solo` is standalone —
    the shape gh-1165 already fixed, here so a regression in either is
    visibly not a regression in both.

    No `doc` yet: the tests that need one add it, because "added after
    scaffolding" is the whole condition.
    """
    assert _cli("new", "dm", cwd=tmp_path).returncode == 0
    root = tmp_path / "dm"
    assert _cli("module", "m", cwd=root).returncode == 0
    assert (
        _cli(
            "object",
            "o",
            "--module",
            "m",
            "--state",
            "g:double:1.0",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "method",
            "o",
            "gain2",
            "--module",
            "m",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "property",
            "o",
            "level",
            "--module",
            "m",
            "--type",
            "double",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "view",
            "o",
            "Peek",
            "--module",
            "m",
            "--create-fn",
            "o_create_peek",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli("object", "solo", "--state", "k:double:2.0", cwd=root).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


@pytest.fixture
def documented(scaffold: Path) -> Path:
    """The repro: a `doc` on each shape, added to the manifest by hand.

    Not applied — the tests decide when, because `status` before `apply` is
    half of what gh-1172 is about.
    """
    _put_object_key(scaffold, "o", f'doc = "{MARKER}"')
    _put_object_key(scaffold, "solo", f'doc = "{SOLO_MARKER}"')
    frag = scaffold / "objects" / "o.toml"
    body = frag.read_text(encoding="utf-8")
    anchor = 'class_name = "Peek"\n'
    assert anchor in body, body
    frag.write_text(
        body.replace(anchor, anchor + f'doc = "{VIEW_MARKER}"\n', 1), "utf-8"
    )
    return scaffold


class TestTheDocReachesTheStub:
    """gh-1172's own failure. The `.pyi` is the half that was missing; the
    runtime face is asserted beside it because `_docsync` reaches that one by
    a different route (it renders from the REAL cfg), and a fix that quietly
    swapped which face works would otherwise read as green."""

    def test_a_module_objects_stub_carries_the_manifest_doc(
        self, documented: Path
    ) -> None:
        assert _cli("apply", cwd=documented).returncode == 0
        pyi = (documented / "src/dm/m/m.pyi").read_text(encoding="utf-8")
        assert MARKER in pyi, pyi

    def test_a_module_objects_runtime_doc_carries_it_too(
        self, documented: Path
    ) -> None:
        assert _cli("apply", cwd=documented).returncode == 0
        ext = (documented / "native/src/m/m_ext_o.c").read_text("utf-8")
        assert f'.tp_doc       = "{MARKER}' in ext, ext[:400]

    def test_the_standalone_peer_still_works(self, documented: Path) -> None:
        """gh-1165's case, through the replay rather than the post-replay
        re-render that was carrying it. Both now reach it the same way."""
        assert _cli("apply", cwd=documented).returncode == 0
        pyi = (documented / "src/dm/solo.pyi").read_text(encoding="utf-8")
        assert SOLO_MARKER in pyi, pyi

    def test_a_views_doc_still_works(self, documented: Path) -> None:
        """The view path already forwarded `doc` through the replay
        (`_view.run(doc=...)`), which is what made the object's omission look
        like a one-off rather than the shape it is."""
        assert _cli("apply", cwd=documented).returncode == 0
        pyi = (documented / "src/dm/m/m.pyi").read_text(encoding="utf-8")
        assert VIEW_MARKER in pyi, pyi

    def test_a_second_apply_changes_nothing(self, documented: Path) -> None:
        """A fix that made `apply` rewrite the stub every run would report
        the project stale forever — gh-635's shape, and the reason the
        gh-1165 branch is gated the way it is."""
        assert _cli("apply", cwd=documented).returncode == 0
        again = _cli("apply", cwd=documented)
        assert again.returncode == 0
        assert "already matches" in again.stdout, again.stdout


@pytest.fixture
def module_doc(scaffold: Path) -> Path:
    """ONLY the module object's `doc`, and nothing else's.

    The `documented` fixture carries three, and two of them (the standalone's
    and the view's) already reached the stub before this fix — so a `status`
    assertion made against it goes green on their drift while the module
    object's stays invisible. Measured: with the fix reverted, `status` still
    named `m.pyi`, for the view.
    """
    _put_object_key(scaffold, "o", f'doc = "{MARKER}"')
    return scaffold


class TestStatusSeesTheDrift:
    """The half that let the first half survive.

    `status --check` exited 0 with the manifest and the stub visibly
    disagreeing, because its reference is the replay and the replay had the
    same hole. These assert the two directions that make the check mean
    something: it goes red while the project is behind, and green once
    `apply` has caught it up.
    """

    def test_check_fails_while_the_stub_is_behind(
        self, module_doc: Path
    ) -> None:
        out = _cli("status", "--check", cwd=module_doc)
        assert out.returncode == 1, out.stdout

    def test_status_names_the_stale_stub(self, module_doc: Path) -> None:
        """A finding the author cannot act on is one this repo has paid for
        before, so the report has to name the file, not just fail."""
        out = _cli("status", cwd=module_doc)
        assert "src/dm/m/m.pyi" in out.stdout, out.stdout

    def test_apply_clears_it(self, module_doc: Path) -> None:
        assert _cli("apply", cwd=module_doc).returncode == 0
        out = _cli("status", "--check", cwd=module_doc)
        assert out.returncode == 0, out.stdout


class TestApplyAgreesWithTheDirectRender:
    """The gate: `apply`'s output must equal what jm renders from the
    manifest directly.

    This is the axis gh-1140's clobber gate cannot reach. That one proves a
    file is *compared*; this one proves the thing it is compared against is
    the manifest. The replay is the suspect layer, so the reference must not
    be the replay — and jm's own renderers, called with the real cfg and the
    real headers, are the only reference that isn't.

    Registration-free over files: every generated `.pyi` in the project is
    walked, so a stub jm learns to emit is covered the day it is emitted.
    """

    def test_a_freshly_scaffolded_project_agrees(self, scaffold: Path) -> None:
        """The premise. If this failed the gate would be reporting its own
        formatting differences rather than anything about the manifest."""
        _assert_stubs_match_the_direct_render(scaffold)

    def test_it_still_agrees_after_a_manifest_doc(
        self, documented: Path
    ) -> None:
        assert _cli("apply", cwd=documented).returncode == 0
        _assert_stubs_match_the_direct_render(documented)


class TestTheReplayCarriesTheKey:
    """The mechanism, asserted where it lives.

    Everything above is measured on the `.pyi`, and for a STANDALONE object
    that file is also rewritten by the gh-1165 post-replay branch — so the
    standalone half of this fix can be reverted with every stub assertion
    still green. Measured: removing `doc_=doc` from `_init.run`'s
    `add_component` left all of them passing.

    What that revert actually breaks is the invariant the fix is for: the
    manifest the replay reconstructs must say what the real one says. Every
    file the replay renders comes from it, so a key missing there is a key
    `apply` cannot honour and `status` cannot see — today for one file, and
    for whatever renders from it next.

    Reached through the Python API rather than the CLI because `replay_out`
    is internal (gh-949): it is `status`'s own handle on the replay, and
    there is no flag for it.
    """

    @pytest.mark.parametrize(
        "comp,marker", [("o", MARKER), ("solo", SOLO_MARKER)]
    )
    def test_the_temp_manifest_carries_the_doc(
        self, documented: Path, tmp_path: Path, comp: str, marker: str
    ) -> None:
        import contextlib
        import io

        from just_makeit import _apply

        out = tmp_path / "replay-out"
        with contextlib.redirect_stdout(io.StringIO()):
            _apply.run(documented, replay_out=out)
        replayed = C.load(out)
        assert replayed.get(comp, {}).get("doc") == marker, (
            f"the replay dropped [{comp}] doc — every artefact it renders "
            f"comes from this manifest, and `status` diffs against it"
        )


#: Object-table keys with no CLI flag to carry them. These are the risk set
#: for this whole class: `apply` rebuilds the temp tree from reconstructed CLI
#: history, so a key no command can express reaches the replay only if
#: `_object_kwargs` names it explicitly — and when it does not, `status`
#: inherits the same blind spot. Each value is one a hand-written manifest
#: would plausibly carry.
MANIFEST_ONLY_OBJECT_KEYS = {
    "doc": f'doc = "{MARKER}"',
    "process_global": "process_global = true",
    "opaque_state": 'opaque_state = "true"',
    "step_delegates_to_steps": 'step_delegates_to_steps = "true"',
    "init_post_parse": 'init_post_parse = "/* post parse */"',
    "no_reset": 'no_reset = "true"',
    "extra_link_libs": 'extra_link_libs = ["m"]',
    "extra_include_dirs": 'extra_include_dirs = ["native/inc"]',
}


class TestAHandAuthoredKeyReachesTheStub:
    """The same property, driven the way the bug arrives.

    gh-1172 was written into a manifest by hand, on a project that was
    already applied and already clean. So is each of these. The assertion is
    the oracle above rather than a per-key expectation, because the point is
    not what any one key renders to — it is that whatever the manifest says,
    the file on disk says the same thing.
    """

    @pytest.mark.parametrize("key", sorted(MANIFEST_ONLY_OBJECT_KEYS))
    def test_the_stub_still_matches_the_manifest(
        self, scaffold: Path, key: str
    ) -> None:
        _put_object_key(scaffold, "o", MANIFEST_ONLY_OBJECT_KEYS[key])
        applied = _cli("apply", cwd=scaffold)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        _assert_stubs_match_the_direct_render(scaffold)
