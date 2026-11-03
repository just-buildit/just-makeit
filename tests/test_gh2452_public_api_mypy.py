"""gh-2452: a type checker reads the lazy public names as what they are.

`just_makeit.__getattr__` returns `object`, so the lazy re-exports --
``components``, ``Refusal``, ``__version__`` -- read to mypy as `object`
unless the package declares them under ``TYPE_CHECKING``. Without that a
caller's ``components(cfg)`` is "not callable", ``except Refusal`` "must
derive from BaseException" and ``v: str = __version__`` an incompatible
assignment. This type-checks such a caller and requires exit 0.

It needs mypy, a dev-group tool, so like ``test_gh1724_array_stub_mypy``
it runs on the PROJECT_ENV_TESTS path (Makefile, gh-1442) and imports
mypy unconditionally: absent, it fails loudly rather than skipping.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from mypy import api

import just_makeit

#: A script of a project's own, using every lazy public name.
_CALLER = textwrap.dedent(
    """\
    import just_makeit
    from just_makeit import Refusal, components, load_manifest

    version: str = just_makeit.__version__
    try:
        names: list[str] = components(load_manifest("."))
    except Refusal as exc:
        errors: tuple[str, ...] = exc.errors
    try:
        also: list[str] = just_makeit.components({})
    except just_makeit.Refusal:
        pass
    """
)


def test_a_caller_type_checks(tmp_path: Path, monkeypatch) -> None:
    """mypy reads the package SOURCE, through ``MYPYPATH``: jm ships no
    ``py.typed``, so an installed jm reads as untyped (`Any`) and would
    pass whatever the package declared. ``--follow-imports=silent`` keeps
    the verdict on the caller, not on jm's own internals."""
    script = tmp_path / "use.py"
    script.write_text(_CALLER, encoding="utf-8")
    monkeypatch.setenv(
        "MYPYPATH", str(Path(just_makeit.__file__).parent.parent)
    )
    out, err, status = api.run(
        [
            "--no-incremental",
            "--follow-imports=silent",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(script),
        ]
    )
    # Armed: mypy read the file and found just_makeit, not a usage error.
    assert "Success" in out or "Found" in out, out + err
    assert status == 0, out + err
