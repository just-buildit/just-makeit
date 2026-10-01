"""gh-1777: a deliberate refusal reads as one ``error:`` line, not a crash.

An object declared both in ``objects/gen.toml`` and in the central manifest
is refused by ``_config.load`` with a good message -- the file, what is
wrong, what to do. It reached the author under a dozen stack frames, so the
author's own mistake read as a crash in jm. The same was true of a composer's
render-time refusals under ``jm upgrade``: gh-1711's partial owned-pointer
set and gh-1739's two prototypes for one seam function.

The CLI boundary (``_cli.main``) now catches :class:`_report.Refusal` -- and
only that -- printing ``error: <message>`` and exiting 1. These drive it
in-process through ``run_cli``; ``test_gh1777_refusal_in_a_real_process``
repeats the decisive cases in a real interpreter, because ``run_cli`` itself
turns an escaping exception into a traceback and exit 1.

GATE: a load refusal and a render refusal each print exactly one ``error:``
line, no traceback, and exit 1; ``JM_DEBUG=1`` brings the traceback back; a
plain ``ValueError`` or ``RuntimeError`` (a bug) still tracebacks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import JmRun, run_cli  # noqa: E402

#: The issue's trigger: a second declaration of an object whose fragment
#: already exists, appended to the central manifest.
DUPLICATE_OBJECT = '\n[[gen.methods]]\nname = "foo"\n'

#: A composer whose renderer refuses the source fields spliced in at
#: ``{fields}``. The project loads; only rendering it refuses.
COMPOSER = """
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]

[module.playlist.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"
{fields}
[module.playlist.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "2"
"""

_FRAME = """
[[module.playlist.source.fields]]
name = "{name}"
type = "desc_t *"
copy_fn = "desc_copy"
free_fn = "desc_free"
parse_fn = "desc_parse"
"""

#: Each render refusal, with what its one ``error:`` line must say.
RENDER_REFUSALS = {
    # gh-1711: an owned pointer naming three of its four host functions.
    "partial_owned_ptr": (
        _FRAME.format(name="frame"),
        "names all four host functions",
    ),
    # gh-1739: two keys naming one seam function with two prototypes.
    "seam_disagreement": (
        _FRAME.format(name="frame")
        + 'format_fn = "desc_format"\n'
        + _FRAME.format(name="frame2")
        + 'format_fn = "desc_format"\nparse_why = true\n',
        "would declare it differently",
    ),
}


def _ok(r: JmRun) -> None:
    assert r.returncode == 0, r.stderr


def _append(manifest: Path, text: str) -> None:
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture(scope="module")
def load_refused(tmp_path_factory) -> Path:
    """A split-layout project whose manifest ``load`` refuses."""
    where = tmp_path_factory.mktemp("gh1777_load")
    _ok(run_cli("new", "proj", cwd=where))
    root = where / "proj"
    _ok(run_cli("object", "gen", cwd=root))
    assert (root / "objects" / "gen.toml").is_file()
    _append(root / "just-makeit.toml", DUPLICATE_OBJECT)
    return root


@pytest.fixture(scope="module", params=sorted(RENDER_REFUSALS))
def render_refused(request, tmp_path_factory) -> "tuple[Path, str]":
    """A project that loads, and whose composer the renderer refuses."""
    fields, needle = RENDER_REFUSALS[request.param]
    where = tmp_path_factory.mktemp(f"gh1777_{request.param}")
    _ok(run_cli("new", "studio", cwd=where))
    root = where / "studio"
    _ok(
        run_cli(
            "object",
            "clip",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=root,
        )
    )
    _append(root / "just-makeit.toml", COMPOSER.format(fields=fields))
    return root, needle


def _error_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("error:")]


def _assert_refused(r: JmRun, needle: str) -> None:
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    errors = _error_lines(r.stderr)
    assert len(errors) == 1, r.stderr
    assert needle in errors[0], r.stderr


# -- a refusal is one line ---------------------------------------------------


@pytest.mark.parametrize("cmd", ["apply", "status", "script", "upgrade"])
def test_a_load_refusal_is_one_error_line(
    load_refused: Path, cmd: str
) -> None:
    """Every command reads the manifest through ``load``; each one that
    reached the refusal printed the traceback."""
    _assert_refused(run_cli(cmd, cwd=load_refused), "'gen' already exists")


def test_a_render_refusal_is_one_error_line(
    render_refused: "tuple[Path, str]",
) -> None:
    """``jm upgrade`` renders the composer with no handler of its own, so
    the refusal reaches the boundary -- ``apply`` has always caught it."""
    root, needle = render_refused
    _assert_refused(run_cli("upgrade", cwd=root), needle)


# -- the traceback is still there when it is wanted ---------------------------


def test_jm_debug_brings_the_traceback_back(
    load_refused: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JM_DEBUG", "1")
    r = run_cli("status", cwd=load_refused)
    assert r.returncode == 1
    assert "Traceback" in r.stderr, r.stderr
    assert "Refusal: " in r.stderr, r.stderr


def test_jm_debug_zero_is_off(
    load_refused: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JM_DEBUG", "0")
    _assert_refused(
        run_cli("status", cwd=load_refused), "'gen' already exists"
    )


# -- a bug is not a refusal --------------------------------------------------


@pytest.mark.parametrize("exc", [ValueError, RuntimeError])
def test_a_genuine_bug_still_tracebacks(
    load_refused: Path, monkeypatch: pytest.MonkeyPatch, exc: type
) -> None:
    """A ``ValueError`` jm did not raise on purpose is a bug, and the
    boundary must not dress it as an author's mistake."""
    from just_makeit import _config

    def boom(*_a, **_k):
        raise exc("gh1777 genuine bug")

    monkeypatch.setattr(_config, "load", boom)
    r = run_cli("status", cwd=load_refused)
    assert r.returncode == 1
    assert "Traceback" in r.stderr, r.stderr
    assert f"{exc.__name__}: gh1777 genuine bug" in r.stderr, r.stderr
    assert not _error_lines(r.stderr), r.stderr
