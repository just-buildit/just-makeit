"""gh-949: `status` reports WHICH create-only files are behind.

`apply` writes a create-only file when it is absent and never rewrites it, and
`status` works by replaying the manifest over a scratch copy and diffing. So a
create-only file is byte-identical on both sides *because neither run touched
it*: the diff is empty by construction rather than by the file being current,
and every such file is invisible to drift detection however many versions
behind it is.

The detection half compares those files against the replay tree — jm's current
render — and reports the difference as OUTDATED. It cannot compare all of them:
`<comp>_core.c` holds the author's algorithm and differs from its scaffold the
moment the project is real, so a whole-set diff would mark every project
outdated forever. :mod:`just_makeit._createonly` splits them, and the split is
a declared judgement about intent.

**The gate is the point of this file.** A declared judgement rots the instant
someone adds a create-only file and does not think about it, so nothing here
reads the registry as a source of truth. `test_the_create_only_set_is_exactly_
what_is_classified` *derives* it the way the issue was measured — corrupt one
file, run `status`, observe whether it noticed — and asserts set equality
against the registry.

Equality, not containment, and in both directions on purpose. Containment would
catch a new unclassified file and miss the more dangerous case: a file that was
reconciled quietly ceasing to be, which is exactly how coverage disappears with
nobody noticing. Deriving rather than pattern-matching the write sites matters
for the same reason — guarded ``if not path.exists()`` sites account for eight
of the 28 create-only files in a plain project, the rest being written once by
`jm new` and never revisited, so a gate over the write pattern would have
proved completeness over under a third of the set while reading as covering it
all.

`test_a_pristine_project_is_never_outdated` holds the other direction: a rule
wrongly marked `JM` turns a brand-new project red here rather than turning
every user's `status` into permanent noise. `test_no_rule_is_dead` stops the
registry keeping entries for paths jm no longer emits.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _apply, _createonly, _status  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# Enough shapes to reach the files that exist in only some of them: the perf
# headers, the `make` build's Makefile and absent cmake/ tree, a module
# object's nested test and benchmark packages and its `_ext_<obj>.c` fragment,
# the split-layout `objects/*.toml` fragments, and the `.clang-format` a
# c_style project carries.
SHAPES: dict[str, dict] = {
    "plain": {},
    "perf": {"perf": True},
    "make-build": {"build_system": "make"},
    "c_style": {"c_style": "clang-format"},
    "module": {"module": "filter"},
    # Both flags together, because the split layout writes `objects/*.toml`
    # and `modules/*.toml` and only a module project produces the second.
    "fragments": {"fragments": True, "module": "filter"},
}

# gh-958: a `--c-style` project reports its own untouched scaffold as STALE, so
# `status` never says "up to date" over one and the corrupt-and-ask oracle
# cannot run there — every file would read as seen for a reason that has
# nothing to do with the corruption. Excluded from the derivation only; the
# shape still scaffolds for the other gates, which is what keeps
# `.clang-format` classified and out of `test_no_rule_is_dead`.
DERIVABLE = [s for s in sorted(SHAPES) if s != "c_style"]


def _scaffold(root: Path, **shape) -> Path:
    module = shape.pop("module", None)
    perf = shape.get("perf", False)
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, **shape)
        if module:
            module_run(root, module)
        object_run(root, "gain", module, perf=perf)
    return root


def _managed(root: Path) -> list[Path]:
    return sorted(
        f.relative_to(root)
        for f in root.rglob("*")
        if f.is_file() and not _apply.is_skipped(f.relative_to(root))
    )


def _quiet_apply(root: Path, **kw) -> None:
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        _apply.run(root, **kw)


def _corrupt(p: Path) -> None:
    """Change *p* in a way that cannot break how anything parses it.

    A comment, not junk, and appended rather than replacing the file. Replacing
    it outright makes `apply` *refuse*: gutting `native/src/<c>/CMakeLists.txt`
    trips the "already holds a module-style core lib" guard and the command
    exits before doing anything, which would read here as the file being
    create-only. The measurement has to change the bytes without changing what
    the file is.
    """
    lead = "// " if p.suffix in (".c", ".h") else "# "
    p.write_text(
        p.read_text(encoding="utf-8") + f"\n{lead}gh949 probe\n",
        encoding="utf-8",
    )


def _derive_create_only(tmp_path: Path, name: str, **shape) -> set[str]:
    """The create-only set of a scaffolded project, measured not declared.

    For each manifest-owned file: change it, run `status --check` on a fresh
    copy, and ask whether `status` noticed. A file whose change is invisible is
    create-only — that is precisely what "invisible to drift detection" means,
    so this asks the property itself rather than a proxy for it.
    """
    pristine = _scaffold(tmp_path / f"{name}-pristine", **dict(shape))
    baseline, _ = _status_out(pristine, check=True)
    assert "OK — up to date" in baseline, (
        f"the {name} shape is not clean before corruption, so the oracle "
        f"measures nothing:\n{baseline}"
    )
    blind: set[str] = set()
    for rel in _managed(pristine):
        work = tmp_path / f"{name}-work"
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(pristine, work)
        _corrupt(work / rel)
        out, rc = _status_out(work, check=True)
        assert rc is not None, f"status refused over a corrupted {rel}:\n{out}"
        if "OK — up to date" in out:
            blind.add(rel.as_posix())
    return blind


def _replay_of(tmp_path: Path, root: Path, name: str) -> Path:
    """jm's current render of *root*, as `status` obtains it."""
    work = tmp_path / f"{name}-replaysrc"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(root, work)
    replay = tmp_path / f"{name}-replay"
    shutil.rmtree(replay, ignore_errors=True)
    _quiet_apply(work, honor_status_allow=False, replay_out=replay)
    return replay


def _status_out(root: Path, **kw) -> tuple[str, int | None]:
    buf, rc = io.StringIO(), None
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit):
            rc = _status.run(root, **kw)
    return buf.getvalue(), rc


# ── The gates ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", DERIVABLE)
def test_the_create_only_set_is_exactly_what_is_classified(tmp_path, name):
    """Derived, not declared, and equal in both directions.

    Sabotage: drop a rule and its file arrives unclassified on the left;
    change any `RECONCILED` entry to another kind and it appears on the right.
    """
    measured = _derive_create_only(tmp_path, name, **SHAPES[name])
    root = tmp_path / f"{name}-pristine"
    declared = set()
    for rel in _managed(root):
        rule = _createonly.classify(rel.as_posix())
        assert rule is not None, (
            f"{rel.as_posix()} has no rule in _createonly.RULES — classify it "
            "JM (jm's content, so it can be behind), AUTHOR (the author's, so "
            "it cannot), PARTIAL (apply splices managed blocks into it) or "
            "RECONCILED (apply rewrites it)."
        )
        if rule.kind != _createonly.RECONCILED:
            declared.add(rel.as_posix())

    # The issue's measurement: create-only is most of the tree, not a handful
    # of files. A collapse here means the oracle broke, not that jm started
    # reconciling everything.
    assert len(measured) > 20, f"derivation looks broken: {sorted(measured)}"
    assert measured == declared, (
        "measured create-only set != what _createonly.RULES declares.\n"
        f"  create-only but classified RECONCILED: "
        f"{sorted(measured - declared)}\n"
        f"  classified create-only but `status` sees drift in it: "
        f"{sorted(declared - measured)}"
    )


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_a_pristine_project_is_never_outdated(tmp_path, name):
    """A wrong ``versioned=True`` fails here, not in every user's terminal.

    A freshly scaffolded project is by definition on this jm's render of
    everything, so nothing in it can be behind. A rule that marks an
    author-owned or partially-reconciled file versioned shows up as a project
    reporting itself outdated the second it is created.
    """
    root = _scaffold(tmp_path / name, **dict(SHAPES[name]))
    replay = _replay_of(tmp_path, root, name)
    assert _createonly.outdated(root, replay) == []


def test_no_rule_is_dead(tmp_path):
    """Every rule matches something a real scaffold produces.

    A registry that keeps rules for paths jm no longer emits reads as broader
    coverage than it has, and the unmatched entry is exactly where a stale
    judgement hides.
    """
    seen: set[str] = set()
    for name, shape in SHAPES.items():
        root = _scaffold(tmp_path / f"live-{name}", **dict(shape))
        seen.update(p.as_posix() for p in _managed(root))
    dead = [
        rule.pattern
        for rule in _createonly.RULES
        if not any(_createonly.classify(p) is rule for p in seen)
    ]
    assert not dead, f"rules matching no scaffolded file: {dead}"


# ── Behaviour ────────────────────────────────────────────────────────────────


def _stale_makefile(root: Path) -> None:
    """The reported scenario: a Makefile without v0.58's `tidy` target."""
    mk = root / "Makefile"
    body = mk.read_text(encoding="utf-8")
    assert "\ntidy:" in body, "fixture assumes the tidy target exists"
    i = body.index("\ntidy:")
    j = body.index("\n\nbuild:", i)
    mk.write_text(body[:i] + body[j:], encoding="utf-8")


def test_a_stale_makefile_is_named(tmp_path):
    root = _scaffold(tmp_path / "p")
    _stale_makefile(root)
    out, _ = _status_out(root, check=True)
    assert "OUTDATED (1)" in out
    assert "↑ Makefile" in out


def test_it_is_printed_under_check(tmp_path):
    """The whole feature.

    The reported failure is a reader running `status --check` before
    migrating, seeing OK and concluding there is nothing to do. Collapsing
    this finding into the one-line summary would reproduce that exactly.
    """
    root = _scaffold(tmp_path / "p")
    _stale_makefile(root)
    out, _ = _status_out(root, check=True)
    assert "OUTDATED (1)" in out
    assert "; 1 outdated." in out


def test_it_does_not_gate(tmp_path):
    """`apply` cannot fix this, so failing CI on it offers no way out.

    gh-767's precedent for `unreconciled`, unchanged: qualify the line,
    leave the exit code alone.
    """
    root = _scaffold(tmp_path / "p")
    _stale_makefile(root)
    out, rc = _status_out(root, check=True)
    assert rc == 0
    assert "OK — up to date" in out


def test_the_authors_own_files_are_never_outdated(tmp_path):
    """The failure mode that would make this feature worse than nothing.

    `_core.c` and the C test are create-only too, and they differ from their
    scaffold as soon as anyone writes real code. Diffing them against the
    template would report every project as outdated forever.
    """
    root = _scaffold(tmp_path / "p")
    core = root / "native" / "src" / "gain" / "gain_core.c"
    core.write_text(
        core.read_text(encoding="utf-8") + "\n/* my algorithm */\n",
        encoding="utf-8",
    )
    test_c = root / "native" / "tests" / "test_gain_core.c"
    test_c.write_text(
        test_c.read_text(encoding="utf-8") + "\n/* my tests */\n",
        encoding="utf-8",
    )
    readme = root / "README.md"
    readme.write_text("# my project\n", encoding="utf-8")
    pyproj = root / "pyproject.toml"
    pyproj.write_text(
        pyproj.read_text(encoding="utf-8") + '\n[tool.mine]\nx = "1"\n',
        encoding="utf-8",
    )

    out, rc = _status_out(root, check=True)
    # `OUTDATED (` — the section header. The bare word also appears in the OK
    # line's note, which explains the category; asserting on it would pass
    # over a report that did fire.
    assert "OUTDATED (" not in out, out
    assert rc == 0


def test_status_allow_suppresses_it(tmp_path):
    """A project that has taken its Makefile over says so once.

    Suppressible, unlike the gh-426 dropped symbol beside it: nothing is being
    lost here, and adopting jm's render is the author's call either way. Still
    listed, so the exemption stays visible.
    """
    root = _scaffold(tmp_path / "p")
    _stale_makefile(root)
    out, rc = _status_out(root, check=True, allow=("Makefile",))
    assert "↑ Makefile [status_allow]" in out
    assert rc == 0


def test_json_carries_it(tmp_path):
    """A CI consumer must see the same finding a human does."""
    root = _scaffold(tmp_path / "p")
    _stale_makefile(root)
    out, _ = _status_out(root, as_json=True)
    payload = json.loads(out)
    assert payload["outdated"] == [{"path": "Makefile", "allowed": False}]
    # Never in the gating count, for the same reason it does not gate above.
    assert payload["drift"] == 0


def test_a_pristine_project_reports_nothing(tmp_path):
    root = _scaffold(tmp_path / "p")
    out, rc = _status_out(root, check=True)
    assert "OUTDATED (" not in out
    assert "; 0 outdated" not in out and "; 1 outdated" not in out
    assert rc == 0


def test_the_ok_line_still_says_what_it_did_not_compare(tmp_path):
    """The reporting half (gh-949, #952/#957) must survive this one.

    Detection took half that note's job — jm's own create-only files are
    compared now — and none of the other half: the author-owned ones are still
    invisible, and there are far more of them than the note first named.
    """
    root = _scaffold(tmp_path / "p")
    out, _ = _status_out(root, check=True)
    assert "create-only files:" in out
    assert "NOT compared" in out
    for name in ("_core.c", "README", "pyproject.toml"):
        assert name in out, f"{name} not named as uncompared"
