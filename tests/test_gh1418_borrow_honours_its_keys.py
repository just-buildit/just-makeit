"""gh-1418: a borrow honours the keys it accepts, or it should not accept them.

Found adopting 0.79.1 for doppler's ring buffer. Two keys were recorded in
the manifest, accepted with exit 0, and then **dropped** when the method was
a `borrow`:

- **`nogil`** -- and for a blocking borrow this is correctness, not speed. A
  ring's `wait(n)` sleeps until a producer supplies `n` samples; with the GIL
  held a Python producer thread can never run, so a threaded
  producer/consumer does not fail, it HANGS.
- **`none_on_empty`** -- the non-blocking twin (`peek`) answers NULL for "not
  yet", which is a normal answer. It raised `ValueError("peek failed")`
  instead, and the stub promised a non-optional array.

The shape of the bug matters more than either instance: *a dropped key is
indistinguishable from an honoured one* until it deadlocks or raises. The
cause was a third inline copy of the kernel-call emitter, written for the
borrow, that never learned about `nogil` -- so the fix is one emitter with
three callers (`_nogil_call`), not a fourth branch.

`none_on_empty` also had **no CLI flag at all**, so the only way to reach it
was hand-editing the manifest -- the foot-gun `_recorddecl` states the rule
against ("every shape jm supports has a command that produces it") -- and
`_script` had no emitter for it, so a replayed script rebuilt a method that
RAISES where the original returned `None`: exit 0, different project.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

EXT = Path("native") / "src" / "ring" / "ring_ext.c"
PYI = Path("src") / "q" / "ring.pyi"


def _ring(tmp_path: Path) -> Path:
    """An opaque, header-only-ish component to hang borrows off."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    r = run_cli(
        "object",
        "ring",
        "--no-state",
        "--no-step",
        "--init-param",
        "n:size_t:16",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    return proj


def _borrow(proj: Path, name: str, *extra: str):
    return run_cli(
        "method",
        "ring",
        name,
        "--borrow",
        "--param",
        "n:size_t",
        "--return-type",
        "float _Complex",
        *extra,
        cwd=proj,
    )


def _wrapper(src: str, name: str) -> str:
    """Just the one wrapper, so an assertion cannot match a sibling.

    Every member of this object returns NULL somewhere and several return
    None; a whole-file `in` check would pass on another member's body. That
    is the anchor-the-match lesson, and it bit while writing this file.
    """
    start = src.index(f"RingObj_{name}(")
    nxt = src.find("\nstatic ", start)
    return src[start : nxt if nxt != -1 else len(src)]


class TestNogilOnABlockingBorrow:
    def test_the_gil_is_released_across_the_kernel(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", "--nogil").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert "Py_BEGIN_ALLOW_THREADS" in body
        assert "Py_END_ALLOW_THREADS" in body

    def test_the_pointer_is_declared_outside_the_block(self, tmp_path):
        """It has to outlive the released section to be checked at all."""
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", "--nogil").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        # `_p;` not `*_p;`: the house-style pass reformats the declarator
        # to `float _Complex * _p;`, so an anchor that includes the star
        # matches the EMITTER's spelling rather than the file's.
        decl = body.index("_p;")
        assert decl < body.index("Py_BEGIN_ALLOW_THREADS")
        # ...and the NULL test runs AFTER the GIL is back, since it raises.
        assert body.index("Py_END_ALLOW_THREADS") < body.index("if (!_p)")

    def test_without_nogil_the_call_stays_one_line(self, tmp_path):
        """The negative half: the block appears because the key asked."""
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert "ALLOW_THREADS" not in body
        assert "_p = ring_wait(" in body


class TestNoneOnEmptyOnABorrow:
    def test_null_returns_none_rather_than_raising(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "peek", "--none-on-empty").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "peek")
        assert "Py_RETURN_NONE" in body
        assert "peek failed" not in body

    def test_the_stub_says_optional(self, tmp_path):
        """A caller who cannot see `| None` writes peek(n).mean()."""
        proj = _ring(tmp_path)
        assert _borrow(proj, "peek", "--none-on-empty").returncode == 0

        pyi = (proj / PYI).read_text()
        assert "def peek(self, n: int) -> NDArray[np.complex64] | None:" in pyi

    def test_without_it_a_null_still_raises(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "peek").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "peek")
        assert "peek failed" in body
        pyi = (proj / PYI).read_text()
        assert "def peek(self, n: int) -> NDArray[np.complex64]:" in pyi


class TestItIsReachableAndReplayable:
    def test_the_cli_can_produce_it(self, tmp_path):
        """It was manifest-only, so the shape had no command."""
        proj = _ring(tmp_path)
        r = _borrow(proj, "peek", "--none-on-empty")
        assert r.returncode == 0, r.stderr
        assert (
            "none_on_empty = true"
            in (proj / "objects" / "ring.toml").read_text()
        )

    def test_a_replayed_script_rebuilds_the_same_methods(self, tmp_path):
        """The emitter was missing, so replay produced a DIFFERENT project.

        Compared as a SET of lines: `_dump` writes keys in canonical order
        and the property under test is that nothing is lost, not that the
        file is byte-identical to a hand-ordered one.
        """
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", "--nogil").returncode == 0
        assert _borrow(proj, "peek", "--none-on-empty").returncode == 0

        out = run_cli("script", cwd=proj)
        assert out.returncode == 0, out.stderr
        assert "--none-on-empty" in out.stdout
        assert "--nogil" in out.stdout

        replay = tmp_path / "replay"
        replay.mkdir()
        import subprocess

        done = subprocess.run(
            ["bash", "-s"],
            input=out.stdout,
            cwd=replay,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr + done.stdout

        def _keys(p: Path) -> set:
            return {
                ln.strip()
                for ln in p.read_text().splitlines()
                if ln.strip() and "jm_version" not in ln
            }

        assert _keys(proj / "objects" / "ring.toml") == _keys(
            replay / "q" / "objects" / "ring.toml"
        )
