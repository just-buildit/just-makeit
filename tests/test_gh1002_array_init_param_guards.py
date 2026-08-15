"""Array init-params: the three shapes jm used to accept and miscompile.

Three issues, one manifest surface, and they are worth reading together
because two of them are only reachable by *not* knowing the third exists.

- **gh-1002** — an init-param named `<array>_len` collided with the length
  parameter jm derives for that array, and both reached `create()`:
  `obj_create(const uint8_t *sync, size_t sync_len, size_t sync_len)`. A
  `redefinition of parameter` error in a file the author did not write, from a
  command that reported success.
- **gh-1004** — `optional = true` with no `create_fn` interpolated the empty
  string into the call, emitting `self->handle = (args);` — a comma expression
  with no callee. Also silent, also uncompilable.
- **gh-1005** — two `optional` arrays emitted one `if/else` each, both
  assigning `self->handle`. The second overwrote the first, so the caller's
  array was discarded and the first allocation leaked. **That one compiles**,
  which is why nothing caught it: it produces a wrong object at runtime.

The refusals point at `default = "[]"`, which is the spelling that actually
composes (gh-611): every absent array reaches `create()` as `NULL` with length
`0`, in a *single* call, for any number of arrays. gh-1003 was filed asking for
that feature to be built — it already existed, and the class below is the
control proving it, because a guard that refused the working spelling too would
satisfy every refusal test on its own.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

_HEAD = (
    '[frame]\narg_type = "void"\nreturn_type = "uint8_t"\nmutable = "true"\n\n'
)


def _apply(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "proj"
    frag = tmp_path / "frag.toml"
    frag.write_text(_HEAD + body, encoding="utf-8")
    new_run("proj", root)
    apply_run(root, fragment=frag)
    return root


class TestRefusedShapes:
    def test_a_param_named_after_an_arrays_derived_len(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _apply(
                tmp_path,
                '[[frame.init_params]]\nname = "sync"\n'
                'type = "uint8_t[]"\n\n'
                '[[frame.init_params]]\nname = "sync_len"\n'
                'type = "size_t"\ndefault = "0"\n',
            )
        # ONE readouterr(): it consumes the buffer, so a second call
        # returns empty and the assertion below passes on nothing.
        out = capsys.readouterr()
        assert "sync_len" in out.out + out.err

    @pytest.mark.parametrize(
        "decl",
        ['type = "uint8_t[]"\n', 'type = "uint8_t[]"\ndefault = "[]"\n'],
        ids=["required-array", "omittable-array"],
    )
    def test_the_collision_covers_every_array_kind(self, tmp_path, decl):
        """`<name>_len` is derived for an omittable array too.

        Not a hypothetical: narrowing the guard to required arrays alone
        leaves `frame_create(const uint8_t *sync, size_t sync_len, size_t
        sync_len)` for the `default = "[]"` spelling — measured — and every
        other test here stays green, because they all use a required array.
        """
        with pytest.raises(SystemExit):
            _apply(
                tmp_path,
                f'[[frame.init_params]]\nname = "sync"\n{decl}\n'
                '[[frame.init_params]]\nname = "sync_len"\n'
                'type = "size_t"\ndefault = "0"\n',
            )

    def test_optional_without_create_fn(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _apply(
                tmp_path,
                '[[frame.init_params]]\nname = "preamble"\n'
                'type = "uint8_t[]"\noptional = true\n',
            )

    def test_two_optional_arrays(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _apply(
                tmp_path,
                '[[frame.init_params]]\nname = "preamble"\n'
                'type = "uint8_t[]"\noptional = true\n'
                'create_fn = "frame_create_pre"\n\n'
                '[[frame.init_params]]\nname = "sync"\n'
                'type = "uint8_t[]"\noptional = true\n'
                'create_fn = "frame_create_sync"\n',
            )

    @pytest.mark.parametrize(
        "body,needle",
        [
            (
                '[[frame.init_params]]\nname = "sync"\n'
                'type = "uint8_t[]"\n\n'
                '[[frame.init_params]]\nname = "sync_len"\n'
                'type = "size_t"\ndefault = "0"\n',
                "sync_nbits",
            ),
            (
                '[[frame.init_params]]\nname = "preamble"\n'
                'type = "uint8_t[]"\noptional = true\n',
                'default = "[]"',
            ),
            (
                '[[frame.init_params]]\nname = "preamble"\n'
                'type = "uint8_t[]"\noptional = true\n'
                'create_fn = "a"\n\n'
                '[[frame.init_params]]\nname = "sync"\n'
                'type = "uint8_t[]"\noptional = true\n'
                'create_fn = "b"\n',
                'default = "[]"',
            ),
        ],
        ids=["derived-len", "optional-no-create-fn", "two-optional"],
    )
    def test_the_message_says_what_to_do_instead(
        self, tmp_path, capsys, body, needle
    ):
        """A refusal without a route out is a papercut, not a fix.

        Each of these is a shape someone reached for on purpose, so the
        message has to name the working spelling — two of the three exist
        *because* `default = "[]"` was not findable.
        """
        with pytest.raises(SystemExit):
            _apply(tmp_path, body)
        out = capsys.readouterr()
        assert needle in (out.out + out.err)


class TestTheWorkingSpellingStillWorks:
    """The control, and the reason it is not optional.

    Every test above passes on a guard that refuses *all* array init-params.
    These pin the two shapes that must keep working: `default = "[]"` for any
    number of omittable arrays (gh-611, and what gh-1003 asked to have built),
    and a single `optional` + `create_fn` for genuine dispatch.
    """

    _OMITTABLE = (
        '[[frame.init_params]]\nname = "preamble"\n'
        'type = "uint8_t[]"\ndefault = "[]"\n\n'
        '[[frame.init_params]]\nname = "sync"\n'
        'type = "uint8_t[]"\ndefault = "[]"\n\n'
        '[[frame.init_params]]\nname = "payload"\n'
        'type = "uint8_t[]"\ndefault = "[]"\n\n'
        '[[frame.init_params]]\nname = "crc"\ntype = "int"\n'
        'default = "0"\n'
    )

    def test_three_omittable_arrays_make_one_create_call(self, tmp_path):
        """The property dispatch could not have: they compose.

        One `create()`, with a `NULL`/`0` pair per absent array — not one
        constructor per combination, and not one assignment per array
        overwriting the last (which is gh-1005).
        """
        root = _apply(tmp_path, self._OMITTABLE)
        ext = (root / "native/src/frame/frame_ext.c").read_text(
            encoding="utf-8"
        )
        assert ext.count("self->handle = frame_create") == 1
        assert '"|OOOi"' in ext
        for n in ("preamble", "sync", "payload"):
            assert (
                f"{n}_arr ? (const uint8_t *)PyArray_DATA({n}_arr) : NULL"
                in ext
            )

    def test_the_stub_shows_them_omittable(self, tmp_path):
        root = _apply(tmp_path, self._OMITTABLE)
        pyi = (root / "src/proj/frame.pyi").read_text(encoding="utf-8")
        for n in ("preamble", "sync", "payload"):
            assert f"{n}: npt.ArrayLike = ..." in pyi

    def test_single_optional_with_create_fn_is_untouched(self, tmp_path):
        """Dispatch itself is sound and stays supported — one array, one
        alternate constructor. Only the shapes with no coherent output go."""
        root = _apply(
            tmp_path,
            '[[frame.init_params]]\nname = "preamble"\n'
            'type = "uint8_t[]"\noptional = true\n'
            'create_fn = "frame_create_pre"\n\n'
            '[[frame.init_params]]\nname = "crc"\ntype = "int"\n'
            'default = "0"\n',
        )
        ext = (root / "native/src/frame/frame_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle = frame_create_pre(" in ext
        assert "self->handle = frame_create(crc);" in ext


@_needs_cc
class TestTheAcceptedTreeCompiles:
    """The tier that would have caught all three.

    Every one of these bugs was a *string* jm was happy to emit; only a
    compiler had an opinion. gh-1005 is the reason this is not enough on its
    own — it compiled fine and still built the wrong object — which is what
    the one-create-call assertion above is for.
    """

    def test_omittable_arrays_compile(self, tmp_path):
        numpy = pytest.importorskip("numpy")
        root = _apply(tmp_path, TestTheWorkingSpellingStillWorks._OMITTABLE)
        for rel in (
            "native/src/frame/frame_core.c",
            "native/src/frame/frame_ext.c",
        ):
            proc = subprocess.run(
                [
                    _CC,
                    "-fsyntax-only",
                    "-std=gnu99",
                    f"-I{root / 'native' / 'inc'}",
                    f"-I{sysconfig.get_paths()['include']}",
                    f"-I{numpy.get_include()}",
                    str(root / rel),
                ],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, f"{rel}: {proc.stderr}"
