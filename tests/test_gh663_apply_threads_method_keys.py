"""gh-663: `jm apply` must thread every manifest method key it can.

`count_default` (gh-657) worked through `jm method` and was silently dropped by
`jm apply` — the flow doppler actually uses, where a manifest is authored by
hand and every binding is regenerated. The gh-657 tests all drove
``_method.run`` directly, so they passed while the feature did not work.

The cause is structural rather than a typo: ``_apply._replay_method``
enumerates the keys it forwards to ``_method.run`` one by one, so **any** new
manifest key is dropped until someone remembers to add a line. gh-244 was the
same shape. The second test here is the guard for the class — it compares the
keys the replay actually reads against the manifest keys the config layer
knows, so the next omission fails a test instead of reaching a user.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _apply  # noqa: E402
from just_makeit._config import _KNOWN_METHOD_KEYS  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# Keys `_replay_method` legitimately does not forward as a plain kwarg.
# Anything not listed here MUST appear as `m.get("<key>")` in the replay.
_EXEMPT = {
    # Identity / structural, handled by the call's positional arguments.
    "name",
    "arg_type",
    "return_type",
    # Read under either spelling and passed as `params=`.
    "extra_args",
    "params",
    # Documentation-only manifest keys with no _method.run parameter.
    "bench",
}


def _scaffold(tmp_path: Path, *, count_default: str) -> Path:
    root = tmp_path / "dsp"
    new_run("dsp", root)
    object_run(
        root,
        "delay",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="double _Complex",
        return_type="double _Complex",
    )
    method_run(
        root,
        "delay",
        "ptr",
        None,
        "void",
        "double _Complex",
        True,
        [],
        pass_capacity=True,
        count_default=count_default,
    )
    return root


class TestApplyRoundTrip:
    def test_count_default_survives_delete_and_apply(self, tmp_path):
        root = _scaffold(tmp_path, count_default="state->num_taps")
        frag = root / "native" / "src" / "delay" / "delay_ext.c"

        # The CLI path has always worked.
        assert "(Py_ssize_t)(state->num_taps)" in frag.read_text()

        # Regenerating from the manifest is the flow that lost it.
        frag.unlink()
        _apply.run(root)
        regenerated = frag.read_text(encoding="utf-8")
        assert "(Py_ssize_t)(state->num_taps)" in regenerated
        assert "delay_state_t *state = self->handle;" in regenerated
        assert "Py_ssize_t n = 1;" not in regenerated

    def test_absent_key_still_regenerates_the_plain_seed(self, tmp_path):
        root = _scaffold(tmp_path, count_default="")
        frag = root / "native" / "src" / "delay" / "delay_ext.c"
        frag.unlink()
        _apply.run(root)
        assert "Py_ssize_t n = 1;" in frag.read_text()


class TestReplayForwardsEveryKnownKey:
    """The guard for the class, not just for count_default."""

    @staticmethod
    def _keys_read_by_replay() -> set[str]:
        """Every ``m.get("<key>")`` literal inside ``_replay_method``."""
        src = Path(_apply.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_replay_method"
            ):
                break
        else:  # pragma: no cover - the function is the subject of the test
            raise AssertionError("_replay_method not found in _apply.py")

        keys: set[str] = set()
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "m"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
            ):
                keys.add(sub.args[0].value)
        return keys

    def test_no_manifest_method_key_is_silently_dropped(self):
        read = self._keys_read_by_replay()
        missing = sorted(_KNOWN_METHOD_KEYS - read - _EXEMPT)
        assert not missing, (
            "jm apply regenerates a method's binding from the manifest, but "
            "_apply._replay_method never reads these declared keys, so a "
            "project driving `jm apply` silently loses them: "
            f"{missing}. Forward them to _method.run, or add them to _EXEMPT "
            "with the reason."
        )

    def test_exempt_list_has_no_stale_entries(self):
        # An exemption for a key that no longer exists hides the next gap.
        stale = sorted(_EXEMPT - _KNOWN_METHOD_KEYS)
        assert not stale, f"_EXEMPT names unknown method keys: {stale}"
