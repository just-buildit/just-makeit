"""gh-2356: a composer's second __init__ must not leak (or free twice) its state.

``Mix.__init__`` built a new state with ``self->state = NULL`` as its first
statement, so calling it again on a live composer dropped the pointer to the
old one: a leak, and after a failing second ``__init__`` a composer with a NULL
state that ``execute`` then dereferenced. The handle kind settled the contract
(gh-178, gh-2346, gh-2272) and the composer now follows it, with one
improvement:

- **refused while the state is lent** (``_capsule`` was read): a borrower holds
  the pointer and there is one slot, so the old state can be neither freed
  nor kept;
- **the new state is built first**, so a refused or failing re-init leaves the
  composer exactly as it was (a handle's is left closed);
- **then the old state is released**: new calls refused, a GIL-free call in
  flight waited for, and only if it is still live -- ``close()`` frees without
  clearing the pointer, so a closed composer's must not be freed again.

The gate counts what the author's C is actually asked: the bundled
``composer_seams`` project, with its ``create`` and ``destroy`` printing an
event to stderr, driven through every sequence above. Every other count is
derived from those events, not from the generated text.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from test_gh2346_composer_capsule import (
    _cmake,
    _pool_project,
    _run,
    _same_second_as_the_build,
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Printed by the patched author's C, one line per call that matters.
CREATE = "JM_CREATE"
DESTROY = "JM_DESTROY"

_PROBE = """\
import sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track


def say(*a):
    print(*a, file=sys.stderr, flush=True)


m = Mix([Track(dur=4)])
say("built")
m.__init__([Track(dur=2)])
say("reinit-live")
try:
    m.__init__("not segments")
except Exception as e:
    say("bad-reinit", type(e).__name__)
say("still-works", len(m.execute(1)) == 1)
m.close()
say("closed")
m.__init__([Track(dur=3)])
say("reinit-closed")
m.close()
say("closed-again")
m.__init__([Track(dur=3)])
say("reinit-once-more")
cap = m._capsule
try:
    m.__init__([Track(dur=1)])
except RuntimeError as e:
    say("refused-while-lent", "lent" in str(e))
del m
del cap  # the capsule keeps the object alive; the last owner frees
import gc

gc.collect()
say("dealloc")
"""

#: The events the author's C sees, in order, with the Python markers between.
_EXPECT = [
    CREATE,
    "built",
    # a live re-init: the new state is built BEFORE the old one is released
    CREATE,
    DESTROY,
    "reinit-live",
    # a failing re-init changes nothing: no create, no destroy, still usable
    "bad-reinit TypeError",
    "still-works True",
    DESTROY,
    "closed",
    # re-init after close(): the closed state is NOT freed a second time
    CREATE,
    "reinit-closed",
    DESTROY,
    "closed-again",
    CREATE,
    "reinit-once-more",
    # lent: refused, nothing built, nothing freed
    "refused-while-lent True",
    DESTROY,
    "dealloc",
]


def _patch_author_c(proj: Path) -> None:
    """Make the example's create and destroy print an event to stderr."""
    core = proj / "native" / "src" / "backing" / "playlist_core.c"
    text = core.read_text(encoding="utf-8")
    head = "playlist_create (const track_t *tracks, size_t n, int repeat, int continuous)\n{\n  playlist_state_t *st;\n"
    assert text.count(head) == 1, text[:1500]
    text = text.replace(
        head, head + f'  fprintf (stderr, "{CREATE}\\n");\n', 1
    )
    dhead = "playlist_destroy (playlist_state_t *state)\n{\n  if (!state)\n    return;\n"
    assert text.count(dhead) == 1, text[-800:]
    text = text.replace(
        dhead, dhead + f'  fprintf (stderr, "{DESTROY}\\n");\n', 1
    )
    if "#include <stdio.h>" not in text:
        text = "#include <stdio.h>\n" + text
    core.write_text(text, encoding="utf-8")


def _rebuild(proj: Path) -> None:
    """Rebuild after `_patch_author_c`, from clean.

    gh-2362, measured on macOS: make 3.81 compares mtimes to the second, so a
    file patched within the second of the first build keeps its old object
    and the probe runs the UNPATCHED C -- every `JM_CREATE` / `JM_DESTROY`
    line missing while the Python lines are all there (this test's first CI
    run on macOS). `--clean-first` is what survives it, as in
    `test_gh2346_composer_capsule._pool_project`.
    """
    _cmake(proj, "--build", "build", "--parallel", "4", "--clean-first")
    built = [
        f
        for f in (proj / "build").rglob("*")
        if f.is_file() and CREATE.encode() in f.read_bytes()
    ]
    assert built, (
        "the rebuild did not pick up the patched author C: no built file "
        f"carries {CREATE!r} (a stale object, gh-2362)"
    )


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_second_init_frees_what_it_replaces_exactly_once(tmp_path):
    proj = _pool_project(tmp_path)
    _patch_author_c(proj)
    _same_second_as_the_build(proj)
    _rebuild(proj)
    out = _run(proj, _PROBE)
    got = out.stderr.split("\n")[:-1]
    assert out.returncode == 0, (out.returncode, out.stderr[-2500:])
    assert got == _EXPECT, "\n".join(got)


_LEND_IN_CONVERSION = """\
import sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track


def say(*a):
    print(*a, file=sys.stderr, flush=True)


m = Mix([Track(dur=4)])
say("built")
held = []


class Lender:
    # Converting the segments runs this, AFTER __init__ looked at `lent`.
    def __iter__(self):
        held.append(m._capsule)
        return iter([Track(dur=1)])


try:
    m.__init__(Lender())
    say("returned")
except RuntimeError as e:
    say("refused", "lent" in str(e))
say("borrower-reads", len(held))
del held[:]
del m
import gc

gc.collect()
say("dealloc")
"""

#: The new state is built (conversion done) and then thrown away; the old
#: one, which the borrower holds, is freed once, by the last owner.
_EXPECT_LEND_IN_CONVERSION = [
    CREATE,
    "built",
    CREATE,
    DESTROY,
    "refused True",
    "borrower-reads 1",
    DESTROY,
    "dealloc",
]


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_lend_during_conversion_is_refused_not_freed_under_the_borrower(
    tmp_path,
):
    """The top check ran before the conversion lent the state (gh-2273's
    shape): the swap rechecks, destroys what it built and refuses."""
    proj = _pool_project(tmp_path)
    _patch_author_c(proj)
    _same_second_as_the_build(proj)
    _rebuild(proj)
    out = _run(proj, _LEND_IN_CONVERSION)
    got = out.stderr.split("\n")[:-1]
    assert out.returncode == 0, (out.returncode, out.stderr[-2500:])
    assert got == _EXPECT_LEND_IN_CONVERSION, "\n".join(got)


def test_the_lent_refusal_is_only_rendered_for_a_composer_that_lends():
    from test_gh2346_composer_capsule import _cfg

    from just_makeit import _composer

    sys.path  # noqa: B018 - the import above resolves tests/ first
    with_cap = _composer.render_ext(_cfg(), "wc")
    without = _composer.render_ext(_cfg(key=""), "wc")
    assert "__init__ would free it under the borrower" in with_cap
    assert "__init__ would free it" not in without
    # ...but the swap (build first, release after) is everyone's.
    for ext in (with_cap, without):
        assert "wfm_compose_state_t *_jm_new = " in ext
        assert ext.index("_jm_new = ") < ext.index(
            "wfm_compose_destroy(_jm_old)"
        )
        assert "self->state = NULL;\n    self->destroyed = 0;" not in ext
