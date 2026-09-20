"""gh-1418 part 2: a NULL from a borrow has more than one meaning.

A borrow reports failure by returning NULL, and that is the *whole* signal --
no count to inspect, no rc to print. jm answered it with one blanket
`ValueError("<name> failed")`, which is wrong for the shape a borrow exists
for: a blocking `wait(n)` gives up for reasons the caller must tell apart.
End-of-stream is what a consumer loop CATCHES, and a Ctrl-C is not bad input.

So the author names one C function that owns the precedence (`status_fn`,
called with the state AND the borrow's count, because "never satisfiable" is
a property of both) and rows mapping its answers to exceptions.

The rows COMPOSE with `none_on_empty` rather than replacing it: on NULL the
binding checks signals, looks the status up, and a status with no row falls
through to `none_on_empty` if set, else to the blanket raise. That is what
lets one ring's `wait` and `peek` share a table and differ only in the row
they decline to write -- the arrangement doppler settled on in the issue
thread.

`PyErr_CheckSignals()` is emitted for EVERY borrow, table or not: a kernel
that blocked with the GIL released is exactly where a pending signal
accumulates, and gating it behind the table would leave a borrow that
declares none swallowing Ctrl-C.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

EXT = Path("native") / "src" / "ring" / "ring_ext.c"
TOML = Path("objects") / "ring.toml"


def _ring(tmp_path: Path) -> Path:
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

    Every member here returns NULL somewhere; a whole-file `in` check would
    pass on another member's body. The anchor-the-match lesson, inherited
    from this issue's part-1 file.
    """
    start = src.index(f"RingObj_{name}(")
    nxt = src.find("\nstatic ", start)
    return src[start : nxt if nxt != -1 else len(src)]


#: The full table doppler declares, as the issue settles it.
TABLE = (
    "--status-fn",
    "ring_wait_status",
    "--status-error",
    "RING_TOO_LARGE:ValueError:n exceeds capacity",
    "--status-error",
    "RING_CLOSED:EOFError",
    "--status-error",
    "RING_INTERRUPTED:KeyboardInterrupt",
)


class TestTheTableReachesTheBinding:
    def test_each_row_raises_its_own_exception(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert "switch (ring_wait_status(self->handle, n))" in body
        # Each row, with its category -- the point of the feature is that
        # these are THREE different exceptions, so asserting one would pass
        # on a binding that collapsed them.
        assert "case RING_TOO_LARGE:" in body
        assert "PyExc_ValueError" in body
        assert "case RING_CLOSED:" in body
        assert "PyExc_EOFError" in body
        assert "case RING_INTERRUPTED:" in body
        assert "PyExc_KeyboardInterrupt" in body

    def test_the_declared_message_is_used_and_others_name_the_status(
        self, tmp_path
    ):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert '"n exceeds capacity"' in body
        # A row with no message still says WHICH status fired, rather than
        # falling back to the blanket text and losing the finding.
        assert '"wait failed (RING_CLOSED)"' in body

    def test_the_status_fn_is_passed_the_borrow_count(self, tmp_path):
        """`TOO_LARGE` is a property of (state, n), not of the state."""
        proj = _ring(tmp_path)
        r = run_cli(
            "method",
            "ring",
            "wait",
            "--borrow",
            "--param",
            "flags:int",
            "--param",
            "how_many:size_t",
            "--borrow-count",
            "how_many",
            "--return-type",
            "float _Complex",
            *TABLE,
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr

        body = _wrapper((proj / EXT).read_text(), "wait")
        # The COUNT param, not the first param and not the state alone.
        assert "ring_wait_status(self->handle, how_many)" in body

    def test_signals_are_checked_before_the_table(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert body.index("PyErr_CheckSignals") < body.index("switch (")
        assert body.index("if (!_p)") < body.index("PyErr_CheckSignals")

    def test_signals_are_checked_for_a_borrow_with_no_table(self, tmp_path):
        """Generic: the blocking kernel is where a signal accumulates."""
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", "--nogil").returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        assert "PyErr_CheckSignals" in body
        assert "switch (" not in body


class TestItComposesWithNoneOnEmpty:
    def test_an_unrowed_status_falls_through_to_none(self, tmp_path):
        """`peek` shares the table and declines the PENDING row."""
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "peek",
            "--none-on-empty",
            "--status-fn",
            "ring_wait_status",
            "--status-error",
            "RING_CLOSED:EOFError",
        )
        assert r.returncode == 0, r.stderr

        body = _wrapper((proj / EXT).read_text(), "peek")
        assert "case RING_CLOSED:" in body
        # Every case returns, so falling out of the switch IS the fallback.
        assert body.index("default: break;") < body.index("Py_RETURN_NONE")

    def test_without_none_on_empty_the_fallback_still_raises(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0

        body = _wrapper((proj / EXT).read_text(), "wait")
        tail = body[body.index("default: break;") :]
        assert '"wait failed"' in tail
        assert "Py_RETURN_NONE" not in tail


class TestTheKeysSurviveAReplay:
    def test_script_emits_the_table(self, tmp_path):
        """A replay that dropped it rebuilds a method with the OLD meaning."""
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0

        r = run_cli("script", cwd=proj)
        assert r.returncode == 0, r.stderr
        assert "--status-fn ring_wait_status" in r.stdout
        # Quoted, because the message has a space in it -- an unquoted
        # spec would replay as two arguments and lose the message.
        assert (
            '--status-error "RING_TOO_LARGE:ValueError:n exceeds capacity"'
            in r.stdout
        )
        assert "--status-error RING_CLOSED:EOFError" in r.stdout
        assert "--status-error RING_INTERRUPTED:KeyboardInterrupt" in r.stdout

    def test_apply_rebuilds_the_same_binding(self, tmp_path):
        proj = _ring(tmp_path)
        assert _borrow(proj, "wait", *TABLE).returncode == 0
        before = (proj / EXT).read_text()

        (proj / EXT).unlink()
        assert run_cli("apply", cwd=proj).returncode == 0
        assert (proj / EXT).read_text() == before


class TestARefusalRatherThanASilentDrop:
    def test_rows_without_a_status_fn(self, tmp_path):
        proj = _ring(tmp_path)
        r = _borrow(proj, "wait", "--status-error", "RING_CLOSED:EOFError")
        assert r.returncode != 0
        assert "nothing to read the status FROM" in r.stderr

    def test_a_status_fn_with_no_rows(self, tmp_path):
        """jm would call it and discard the answer."""
        proj = _ring(tmp_path)
        r = _borrow(proj, "wait", "--status-fn", "ring_wait_status")
        assert r.returncode != 0
        assert "no `status_errors` row reads it" in r.stderr

    def test_a_row_for_zero(self, tmp_path):
        """0 is SUCCESS, and a borrow only asks after a NULL."""
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "wait",
            "--status-fn",
            "ring_wait_status",
            "--status-error",
            "0:ValueError",
        )
        assert r.returncode != 0
        assert "could never fire" in r.stderr

    def test_an_exception_jm_does_not_emit(self, tmp_path):
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "wait",
            "--status-fn",
            "ring_wait_status",
            "--status-error",
            "RING_CLOSED:NoSuchError",
        )
        assert r.returncode != 0
        assert "which jm does not emit" in r.stderr

    def test_one_status_mapped_twice(self, tmp_path):
        """Two `case` labels of one value is a compile error downstream."""
        proj = _ring(tmp_path)
        r = _borrow(
            proj,
            "wait",
            "--status-fn",
            "ring_wait_status",
            "--status-error",
            "RING_CLOSED:EOFError",
            "--status-error",
            "RING_CLOSED:ValueError",
        )
        assert r.returncode != 0
        assert "twice" in r.stderr

    def test_the_keys_on_a_method_that_is_not_a_borrow(self, tmp_path):
        """The gh-1418 shape itself: accepted, recorded, and dropped."""
        proj = _ring(tmp_path)
        r = run_cli(
            "method",
            "ring",
            "drain",
            "--variable-output",
            "--param",
            "n:size_t",
            "--return-type",
            "float _Complex",
            "--status-fn",
            "ring_wait_status",
            "--status-error",
            "RING_CLOSED:EOFError",
            cwd=proj,
        )
        assert r.returncode != 0
        assert "not a borrow" in r.stderr

    def test_a_refusal_writes_nothing(self, tmp_path):
        """A refused declaration must not leave half a method behind."""
        proj = _ring(tmp_path)
        before = (proj / TOML).read_text()
        assert _borrow(proj, "wait", "--status-fn", "ring_wait_status")
        assert (proj / TOML).read_text() == before
