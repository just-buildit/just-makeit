"""gh-1092: two claims on one `.pyi` member name, and what that used to cost.

gh-1079 gave a sizeable `variable_output` method an `out=` buffer and, with
it, the `<m>_max_out()` a caller sizes that buffer WITH. That is a member jm
emits under a name derived from *another* entry's — so a project that had
hand-written `<m>_max_out` under the documented gh-428 `manual_stub`
workaround suddenly had two sources claiming one name.

The visible half was a stub declaring the member twice, the placeholder
shadowing the real signature. The expensive half was silent: `_splice_hand_owned`
transplants a hand-owned member by replacing `_group_span`, and that span is
`min(starts), max(ends)` over *every* node with the name. Consecutive
definitions — a property's getter and setter, an `@overload` run — are exactly
covered by it. Definitions that are NOT consecutive drag whatever sits between
them into the span, and the replacement deletes it.

doppler's `DelayCf64.write` vanished from `delay.pyi` that way on the 0.64.0
bump while staying in `delay_ext_delay.c`'s `PyMethodDef` — the stub and the
extension disagreeing in the direction a type checker cannot catch, since it
rejects a call that works.

Both halves are fixed and tested separately, because either alone leaves a
real hole:

* `TestTheCollisionIsRefused` — the root cause. jm knows it generates the
  name, so a method entry claiming it is refused at declaration.
* `TestTheSpliceNeverSwallows` — the class. A non-contiguous group is the
  condition that makes the span unsafe, whatever route produced it, and the
  splice refuses rather than deleting.

`TestTheExcludedShapeStillWorks` is the fence in the other direction, and the
issue named it explicitly: a gh-412-excluded shape (an array beside other
params) generates no bound at all, so doppler's hand-written
`Farrow.delay_max_out` and `Resampler.execute_ctrl_max_out` are still correct
and must keep working. Reserving every `*_max_out` name would refuse exactly
those.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._builtins import reserved_python_members  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._stubs import (  # noqa: E402
    _splice_manual_stub_bodies,
    noncontiguous_groups,
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


#: The all-scalar shape gh-1079 generates a bound for.
SIZEABLE = dict(arg_type="void", params=[("x", "double")])
#: gh-412's carve-out: an array beside another param. No bound is generated.
EXCLUDED = dict(arg_type="void", params=[("x", "double[]"), ("mu", "double")])


def _project(tmp_path: Path, name: str, **method_kw) -> Path:
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "obj",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("n", "size_t", "16")],
    )
    _quiet(
        method_run,
        root,
        "obj",
        "vo",
        None,
        return_type="double",
        variable_output=True,
        multi_output=[],
        **method_kw,
    )
    return root


class TestTheCollisionIsRefused:
    """jm owns `<m>_max_out`, so a method entry claiming it is refused."""

    def test_the_generated_bound_is_reserved(self, tmp_path):
        root = _project(tmp_path, "r", **SIZEABLE)
        taken = reserved_python_members(C.load(root), "obj")
        assert "vo_max_out" in taken
        holder, hint = taken["vo_max_out"]
        assert "vo()" in holder
        assert "gh-1079" in hint

    def test_declaring_it_exits_nonzero(self, tmp_path):
        """Refused before anything is written — the gh-910 shape: a refused
        name must not leave a half-made tree behind."""
        root = _project(tmp_path, "d", **SIZEABLE)
        with pytest.raises(SystemExit) as e:
            _quiet(
                method_run,
                root,
                "obj",
                "vo_max_out",
                None,
                arg_type="void",
                return_type="size_t",
                variable_output=False,
                multi_output=[],
                manual_stub=True,
            )
        assert e.value.code == 1

    def test_the_message_names_the_source(self, tmp_path, capsys):
        """A refusal that does not say what to do is a failure with extra
        steps. Both the holder and the remedy are derived, not guessed."""
        root = _project(tmp_path, "m", **SIZEABLE)
        with contextlib.redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit):
                method_run(
                    root,
                    "obj",
                    "vo_max_out",
                    None,
                    arg_type="void",
                    return_type="size_t",
                    variable_output=False,
                    multi_output=[],
                    manual_stub=True,
                )
        err = capsys.readouterr().err
        assert "vo_max_out" in err
        assert "vo()" in err
        assert "drop the entry" in err.lower()


class TestTheExcludedShapeStillWorks:
    """The fence the issue asked for by name.

    gh-412 carves an array-beside-other-params method out of the `out=`
    feature, so no bound is generated for it and a hand-written one is still
    the right answer. doppler carries two. A fix that reserved every
    `*_max_out` name would refuse them, which is why the reservation is
    derived from `_outbuf.enabled` rather than from the name's shape.
    """

    def test_no_bound_is_reserved(self, tmp_path):
        root = _project(tmp_path, "x", **EXCLUDED)
        assert "vo_max_out" not in reserved_python_members(C.load(root), "obj")

    def test_the_manual_stub_is_accepted(self, tmp_path):
        root = _project(tmp_path, "a", **EXCLUDED)
        _quiet(
            method_run,
            root,
            "obj",
            "vo_max_out",
            None,
            arg_type="void",
            return_type="size_t",
            variable_output=False,
            multi_output=[],
            manual_stub=True,
        )
        text = (root / "src" / "a" / "obj.pyi").read_text(encoding="utf-8")
        assert text.count("def vo_max_out") == 1
        assert not noncontiguous_groups(text)


class TestTheSpliceNeverSwallows:
    """The class, independent of what produced the duplicate.

    A non-contiguous group is precisely the condition under which
    `_group_span` covers more than the group, so it is the condition the
    splice has to refuse — not "a `_max_out` name", which would fix this
    instance and leave the mechanism intact for the next one.
    """

    #: A class whose two `m` definitions are separated by `other`.
    COLLIDED = (
        "class A:\n"
        "    def m(self) -> int: ...\n"
        "    def other(self) -> None: ...\n"
        "    def m(self) -> int: ...\n"
    )
    #: The legitimate repeat: a property's getter and setter, consecutive.
    PROPERTY = (
        "class A:\n"
        "    @property\n"
        "    def g(self) -> int: ...\n"
        "    @g.setter\n"
        "    def g(self, v: int) -> None: ...\n"
        "    def other(self) -> None: ...\n"
    )

    def test_it_refuses_rather_than_deleting(self):
        with pytest.raises(ValueError) as e:
            _splice_manual_stub_bodies({}, "", self.COLLIDED)
        assert "gh-1092" in str(e.value)

    def test_the_message_names_what_would_be_lost(self):
        """`other` is the member that used to disappear, so it has to be in
        the message — "something would be deleted" is not actionable."""
        with pytest.raises(ValueError) as e:
            _splice_manual_stub_bodies({}, "", self.COLLIDED)
        msg = str(e.value)
        assert "A.m" in msg
        assert "other" in msg

    def test_a_property_pair_is_not_a_collision(self):
        """The false-positive fence, and the whole reason this is keyed on
        adjacency rather than on a repeated name.

        A getter and setter share a name legitimately and are always written
        consecutively. Refusing them would break every object with a
        property — which is most of them.
        """
        assert not noncontiguous_groups(self.PROPERTY)
        assert (
            _splice_manual_stub_bodies({}, "", self.PROPERTY) == self.PROPERTY
        )

    def test_an_overload_run_is_not_a_collision(self):
        src = (
            "from typing import overload\n"
            "class A:\n"
            "    @overload\n"
            "    def m(self, x: int) -> int: ...\n"
            "    @overload\n"
            "    def m(self, x: str) -> str: ...\n"
            "    def other(self) -> None: ...\n"
        )
        assert not noncontiguous_groups(src)

    def test_it_names_every_collision_not_just_the_first(self):
        """A report that stops at the first finding sends the reader round
        the loop once per collision."""
        src = (
            "class A:\n"
            "    def m(self) -> int: ...\n"
            "    def keep_a(self) -> None: ...\n"
            "    def m(self) -> int: ...\n"
            "    def n(self) -> int: ...\n"
            "    def keep_b(self) -> None: ...\n"
            "    def n(self) -> int: ...\n"
        )
        found = noncontiguous_groups(src)
        assert found == {
            ("A", "m"): ["keep_a"],
            ("A", "n"): ["keep_b"],
        }
