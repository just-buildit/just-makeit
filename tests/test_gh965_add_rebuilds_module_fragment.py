"""gh-965: `jm add` on a module object must reach the constructor.

A module object's binding lives in a per-object fragment (`<mod>_ext_<obj>.c`,
gh-729), and that fragment was not in the set `jm regenerate` rebuilds.
Everything downstream is member-level and **additive** — `_docsync` transplants
docs and splices in bindings the manifest gained — so nothing could rewrite the
one function a structural change actually alters: `<Obj>_init`, which carries
the constructor's `kwlist`.

So `jm add --state bias` left `kwlist[] = {"gain", NULL}` while the manifest,
the header's `gain_create(double, double)` and the `.pyi` all had two.

**jm was not silent about it, and that is worth stating precisely** — the
issue was filed as if it were. `_docsync.warn_init_kwargs_drift` printed a
warning to stderr naming the file, the exact change and two remedies, and
gh-823 made it *gate* `jm status --check`. The guard was right to refuse: a
kwlist kept under a freshly rendered body binds each keyword to the
neighbouring variable, which compiles, runs, and puts the caller's values in
the wrong fields.

What was wrong is that the remedy it named — "reconcile the manifest with the
binding, or keep the hand-written constructor in an _extra.c" — is written for
an author who hand-wrote that constructor. Here jm wrote it, and there was
nothing for the author to reconcile.

The fix is scoped to `discard`, the flag whose prompt already reads "This
discards hand-written bodies (e.g. in _core.c)": a hand-written binding in the
fragment is now given up under the same warning and the same confirmation.
Plain `jm regenerate` preserves and does not touch the fragment — asserted
below, because that is the property gh-770 exists to protect.

gh-2073: the same for every VIEW over the object (gh-504). A view without its
own `init_params` takes the parent's, and `apply` re-scaffolds its
`create_fn` in the rebuilt core at the new arity; its fragment was left out
of the set, so its `<View>_init` called that function with the old
arguments and the module did not compile.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


def _project(tmp_path: Path) -> Path:
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    assert _cli("module", "filt", cwd=proj).returncode == 0
    assert _cli("object", "gain", "--module", "filt", cwd=proj).returncode == 0
    return proj


def _kwlist(proj: Path) -> list[str]:
    text = (proj / "native/src/filt/filt_ext_gain.c").read_text(
        encoding="utf-8"
    )
    m = re.search(r"kwlist\[\] = \{([^}]*)\}", text)
    assert m, "no kwlist in the fragment"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_add_reaches_the_module_constructor(tmp_path):
    """The defect itself.

    Sabotage: drop the fragment from the delete set in `_regenerate.run` and
    the kwlist stays `["gain"]`.
    """
    proj = _project(tmp_path)
    assert _kwlist(proj) == ["gain"]
    r = _cli(
        "add",
        "--object",
        "gain",
        "--state",
        "bias:double:0.0",
        "--force",
        cwd=proj,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert _kwlist(proj) == ["gain", "bias"], (
        "the constructor's kwlist did not gain the new state field, so the "
        "compiled object rejects the keyword its own .pyi advertises"
    )


def test_the_tree_is_clean_afterwards(tmp_path):
    """`status --check` gated on this (gh-612/gh-823), so it is the proof."""
    proj = _project(tmp_path)
    assert (
        _cli(
            "add",
            "--object",
            "gain",
            "--state",
            "bias:double:0.0",
            "--force",
            cwd=proj,
        ).returncode
        == 0
    )
    r = _cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout


def test_the_kwargs_warning_no_longer_fires(tmp_path):
    """There is nothing left to warn about — and that is the whole point.

    The warning is correct when a refresh *would* leave the kwlist behind. Once
    the fragment is rebuilt there is no drift, so a warning here would mean the
    repair did not happen.
    """
    proj = _project(tmp_path)
    r = _cli(
        "add",
        "--object",
        "gain",
        "--state",
        "bias:double:0.0",
        "--force",
        cwd=proj,
    )
    assert "constructor's keyword arguments" not in r.stderr, r.stderr


def test_plain_regenerate_still_preserves_the_fragment(tmp_path):
    """The property gh-770 exists to protect, and the reason for the scoping.

    `jm regenerate` without `--discard` promises to lift and splice back
    hand-written bodies. It must not have gained a fragment deletion — losing
    hand-written C is the unrecoverable direction.
    """
    proj = _project(tmp_path)
    frag = proj / "native/src/filt/filt_ext_gain.c"
    frag.write_text(
        frag.read_text(encoding="utf-8") + "\n/* MY_HANDWRITTEN */\n",
        encoding="utf-8",
    )
    assert _cli("regenerate", "gain", "--force", cwd=proj).returncode == 0
    assert "MY_HANDWRITTEN" in frag.read_text(encoding="utf-8"), (
        "plain `jm regenerate` deleted the module fragment — that is the "
        "`--discard` behaviour and it discards hand-written C"
    )


def test_a_standalone_object_is_unaffected(tmp_path):
    """`module` is None there, so the new branch must not fire."""
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    assert _cli("object", "solo", cwd=proj).returncode == 0
    assert (
        _cli(
            "add",
            "--object",
            "solo",
            "--state",
            "bias:double:0.0",
            "--force",
            cwd=proj,
        ).returncode
        == 0
    )
    ext = (proj / "native/src/solo/solo_ext.c").read_text(encoding="utf-8")
    assert re.search(r'kwlist\[\] = \{[^}]*"bias"', ext)
    assert _cli("status", "--check", cwd=proj).returncode == 0


#: Every verb that changes a module object's constructor keywords, each
#: through `regenerate --discard`. `remove-state` takes the LAST field away,
#: which `status` cannot see (no kwlist on the fresh side), so only this
#: test sees it; `add-state` is gh-2073's trigger, which the gh-2057 gate
#: runs on its `view` shape on the full-matrix leg alone.
_CTOR_VERBS = {
    "add-state": ("add", "--object", "gain", "--state", "bias:double:0.0"),
    "remove-state": ("remove", "state", "gain", "--object", "gain"),
}


@pytest.mark.parametrize("verb", sorted(_CTOR_VERBS))
def test_a_views_constructor_follows_its_parents(tmp_path, verb):
    """gh-2073: the view's binding calls its `create_fn` with the arguments
    the rebuilt header declares, not the ones it had before.

    Read off the two files a compiler reads, so the assertion is the build
    error itself: "too few arguments to function 'gain_create_wide'".

    Sabotage: keep only the object's own fragment in `_regenerate.run`'s
    delete set (`binding_sources(...)[:1]`) and both go red.
    """
    proj = _project(tmp_path)
    view = ("view", "gain", "Wide", "--module", "filt")
    r = _cli(*view, "--create-fn", "gain_create_wide", cwd=proj)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    r = _cli(*_CTOR_VERBS[verb], "--force", cwd=proj)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

    (core_h,) = proj.glob("native/inc/**/gain_core.h")
    proto = re.search(
        r"\*gain_create_wide\(([^)]*)\);", core_h.read_text(encoding="utf-8")
    )
    assert proto, "the view's create_fn is not declared"
    declared = [
        p.split()[-1] for p in proto.group(1).split(",") if p.strip() != "void"
    ]
    frag = proj / "native/src/filt/filt_ext_wide.c"
    call = re.search(
        r"= gain_create_wide\(([^)]*)\);", frag.read_text(encoding="utf-8")
    )
    assert call, "the view's constructor does not call its create_fn"
    passed = [a.strip() for a in call.group(1).split(",") if a.strip()]
    assert passed == declared, (
        f"{frag.name} calls gain_create_wide({', '.join(passed)}) but the "
        f"header declares ({', '.join(declared) or 'void'}): the view's "
        "constructor was not rebuilt with its parent's"
    )
