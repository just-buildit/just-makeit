"""gh-1426 A: a borrow has a release call, and jm can now name it.

`--borrow` has always stated the contract in prose -- "the view is valid
until the author's own release call" -- and known nothing about that call.
So the count had to be repeated by hand:

    view = buf.wait(512)
    buf.consume(512)          # the 512 said twice, and nothing checks it

`releases = ["wait", "peek"]` on the CONSUMING method closes it: jm records
the count where it builds the view, and the release's count param defaults to
it.

**Declared on the releasing method, listing the borrows** rather than a bare
`release = true`, for two reasons the ring settles:

- a declared relationship can be CHECKED -- naming a method that is not a
  borrow, or not a method at all, is refused;
- one object has TWO releases that differ. `consume(n)` takes a count;
  `reset()` takes none and simply invalidates whatever is outstanding. Under
  a bare flag those two would have to mean different things with nothing in
  the key saying which.

The default is a DEFAULT and never a check: `consume(k)` with `k < n` stays
legal, which is what overlapped frames do.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

EXT = Path("native") / "src" / "ring" / "ring_ext.c"
PYI = Path("src") / "q" / "ring.pyi"


def _ring(tmp_path: Path, *borrows: str) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert (
        run_cli(
            "object",
            "ring",
            "--no-state",
            "--no-step",
            "--init-param",
            "n:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    for b in borrows or ("wait",):
        r = run_cli(
            "method",
            "ring",
            b,
            "--borrow",
            "--param",
            "n:size_t",
            "--return-type",
            "float _Complex",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr
    return proj


def _release(proj: Path, name: str, *extra: str):
    return run_cli(
        "method",
        "ring",
        name,
        "--arg-type",
        "void",
        "--return-type",
        "void",
        *extra,
        cwd=proj,
    )


def _wrapper(src: str, name: str) -> str:
    start = src.index(f"RingObj_{name}(")
    nxt = src.find("\nstatic ", start)
    return src[start : nxt if nxt != -1 else len(src)]


class TestTheCountTravels:
    def test_the_borrow_records_its_count(self, tmp_path):
        proj = _ring(tmp_path)
        assert (
            _release(
                proj, "consume", "--param", "n:size_t", "--releases", "wait"
            ).returncode
            == 0
        )

        src = (proj / EXT).read_text()
        assert "size_t _jm_borrowed;" in src
        # Recorded where the view is BUILT, so nothing re-derives it.
        assert "self->_jm_borrowed = (size_t)(n);" in _wrapper(src, "wait")

    def test_two_borrows_share_one_record(self, tmp_path):
        """What is outstanding is the last view, whichever lent it."""
        proj = _ring(tmp_path, "wait", "peek")
        assert (
            _release(
                proj,
                "consume",
                "--param",
                "n:size_t",
                "--releases",
                "wait,peek",
            ).returncode
            == 0
        )

        src = (proj / EXT).read_text()
        assert src.count("size_t _jm_borrowed;") == 1
        for b in ("wait", "peek"):
            assert "self->_jm_borrowed = (size_t)(n);" in _wrapper(src, b)

    def test_a_borrow_records_even_before_anything_releases_it(self, tmp_path):
        """The store follows the BORROW, not the release.

        Gated on the release, declaration order decided the answer: a
        borrow rendered before anything released it never learned to
        record, and a sacred fragment only gains MISSING members -- so
        declaring the release afterwards left a field read twice, zeroed
        twice and WRITTEN NEVER. Every `wait(n); consume()` then raised at
        runtime, from a project whose every command printed `Done!`.

        Found by doppler against #1428's head. The order here is the order
        that was broken: borrow first, release second.
        """
        proj = _ring(tmp_path, "wait", "peek")
        # ...before any release exists, the borrow already records.
        src = (proj / EXT).read_text()
        assert "self->_jm_borrowed = (size_t)(n);" in _wrapper(src, "wait")
        # ...and the DECLARATION is keyed the same way. The two halves are
        # asserted together because a store without a field is not a wrong
        # answer, it is a compile error, and a text assertion on either one
        # alone passes happily while the generated C does not build.
        assert "size_t _jm_borrowed;" in src

        assert (
            _release(
                proj,
                "consume",
                "--param",
                "n:size_t",
                "--releases",
                "wait,peek",
            ).returncode
            == 0
        )
        src = (proj / EXT).read_text()
        for b in ("wait", "peek"):
            assert "self->_jm_borrowed = (size_t)(n);" in _wrapper(src, b)
        assert "n = self->_jm_borrowed;" in _wrapper(src, "consume")

    def test_an_object_with_no_borrow_has_no_field(self, tmp_path):
        """One store per lend is the cost; an object that lends none pays
        nothing."""
        root = tmp_path / "w"
        root.mkdir()
        assert run_cli("new", "q", cwd=root).returncode == 0
        proj = root / "q"
        assert (
            run_cli(
                "object",
                "ring",
                "--no-state",
                "--no-step",
                "--init-param",
                "n:size_t:16",
                cwd=proj,
            ).returncode
            == 0
        )
        assert "_jm_borrowed" not in (proj / EXT).read_text()

    def test_the_release_resolves_and_clears(self, tmp_path):
        proj = _ring(tmp_path)
        assert (
            _release(
                proj, "consume", "--param", "n:size_t", "--releases", "wait"
            ).returncode
            == 0
        )

        body = _wrapper((proj / EXT).read_text(), "consume")
        # Optional in the PyArg format -- the `|` is what makes `consume()`
        # a legal call at all.
        assert '"|K"' in body
        assert "n = self->_jm_borrowed;" in body
        assert "PyExc_RuntimeError" in body
        assert "self->_jm_borrowed = 0;" in body

    def test_a_release_with_no_count_only_clears(self, tmp_path):
        """`reset()` invalidates whatever is outstanding and takes nothing."""
        proj = _ring(tmp_path)
        assert _release(proj, "wipe", "--releases", "wait").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wipe")
        assert "self->_jm_borrowed = 0;" in body
        # Not `PyExc_RuntimeError`: every wrapper carries the
        # destroyed-handle guard, so that would match a sibling.
        assert "no outstanding borrow" not in body


class TestBothStubWritersAgree:
    """The two `.pyi` producers render this signature from different
    inputs -- one from the render context, one from the manifest -- so they
    are the pair that drifts. Whether an argument is REQUIRED is exactly the
    kind of disagreement a caller would hit before a type checker did."""

    def test_the_object_stub_marks_it_optional(self, tmp_path):
        proj = _ring(tmp_path)
        assert (
            _release(
                proj, "consume", "--param", "n:size_t", "--releases", "wait"
            ).returncode
            == 0
        )
        assert "def consume(self, n: int = ...)" in (proj / PYI).read_text()

    def test_the_module_stub_marks_it_optional(self, tmp_path):
        root = tmp_path / "w"
        root.mkdir()
        assert run_cli("new", "q", cwd=root).returncode == 0
        proj = root / "q"
        assert run_cli("module", "buf", cwd=proj).returncode == 0
        assert (
            run_cli(
                "object",
                "ring",
                "--module",
                "buf",
                "--no-state",
                "--no-step",
                "--init-param",
                "n:size_t:16",
                cwd=proj,
            ).returncode
            == 0
        )
        assert (
            run_cli(
                "method",
                "ring",
                "wait",
                "--module",
                "buf",
                "--borrow",
                "--param",
                "n:size_t",
                "--return-type",
                "float _Complex",
                cwd=proj,
            ).returncode
            == 0
        )
        assert (
            run_cli(
                "method",
                "ring",
                "consume",
                "--module",
                "buf",
                "--arg-type",
                "void",
                "--return-type",
                "void",
                "--param",
                "n:size_t",
                "--releases",
                "wait",
                cwd=proj,
            ).returncode
            == 0
        )
        stub = (proj / "src" / "q" / "buf" / "buf.pyi").read_text()
        assert "def consume(self, n: int = ...)" in stub


class TestTheScaffoldStaysGreen:
    def test_the_generated_demo_is_not_the_raising_call(self, tmp_path):
        """`consume(0)` resolves 0 to "outstanding", finds none, and RAISES.

        The type's zero is the natural literal for a `size_t` param and it is
        exactly the one value this shape rejects, so the synthesized doctest
        would have shipped failing. Every valid CLI sequence must produce a
        scaffold that passes.
        """
        proj = _ring(tmp_path)
        assert (
            _release(
                proj, "consume", "--param", "n:size_t", "--releases", "wait"
            ).returncode
            == 0
        )
        src = (proj / EXT).read_text()
        assert "obj.consume(0)" not in src
        assert "obj.consume(1)" in src


class TestTheKeysSurviveAReplay:
    def test_apply_rebuilds_the_same_binding(self, tmp_path):
        proj = _ring(tmp_path)
        assert (
            _release(
                proj, "consume", "--param", "n:size_t", "--releases", "wait"
            ).returncode
            == 0
        )
        before = (proj / EXT).read_text()

        (proj / EXT).unlink()
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert (proj / EXT).read_text() == before

    def test_script_emits_the_release(self, tmp_path):
        """Dropped, the replay rebuilds a REQUIRED count: a different API."""
        proj = _ring(tmp_path, "wait", "peek")
        assert (
            _release(
                proj,
                "consume",
                "--param",
                "flags:int",
                "--param",
                "n:size_t",
                "--release-count",
                "n",
                "--releases",
                "wait,peek",
            ).returncode
            == 0
        )

        r = run_cli("script", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert "--releases wait,peek" in r.stdout
        assert "--release-count n" in r.stdout


class TestARefusalRatherThanASilentDrop:
    def test_releasing_a_method_that_does_not_exist(self, tmp_path):
        proj = _ring(tmp_path)
        r = _release(proj, "consume", "--releases", "nosuch")
        assert r.returncode != 0
        assert "not a method of this object" in r.stderr

    def test_releasing_something_that_is_not_a_borrow(self, tmp_path):
        proj = _ring(tmp_path)
        assert _release(proj, "plain", "--releases", "wait").returncode == 0
        r = _release(proj, "consume", "--releases", "plain")
        assert r.returncode != 0
        assert "not a borrow" in r.stderr

    def test_releasing_itself(self, tmp_path):
        proj = _ring(tmp_path)
        r = _release(proj, "consume", "--releases", "consume")
        assert r.returncode != 0
        assert "names itself" in r.stderr

    def test_an_ambiguous_count(self, tmp_path):
        """The same rule as `borrow_count`, refused in the same words."""
        proj = _ring(tmp_path)
        r = _release(
            proj,
            "consume",
            "--param",
            "a:size_t",
            "--param",
            "b:size_t",
            "--releases",
            "wait",
        )
        assert r.returncode != 0
        assert "cannot tell which of 2 params is the count" in r.stderr

    def test_release_count_without_releases(self, tmp_path):
        """Inert alone, which is the state this whole issue is about."""
        proj = _ring(tmp_path)
        r = _release(
            proj, "consume", "--param", "n:size_t", "--release-count", "n"
        )
        assert r.returncode != 0
        assert "releases nothing" in r.stderr
