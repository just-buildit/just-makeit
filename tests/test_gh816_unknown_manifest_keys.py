"""gh-816: a wrong-kind manifest key is accepted, but no longer in silence.

The reported case: `check_return` is a `jm function` key. Written on an object
method it was accepted, round-tripped, and never acted on — the generated
binding did not raise, while the method's own `doc` promised it would. jm said
nothing and exited 0, so the only way to find out was to build the extension
and call it.

Two halves are tested here, and the second is the one that matters:

1. the warning fires, names the kind the key IS valid for, and suggests the
   method spelling of the same intent;
2. it does NOT fire on anything jm itself generates. A key registry that
   drifts behind the emitters turns every scaffold into noise, and a warning
   channel that cries wolf is worse than the silence it replaced — so the
   no-false-positive half is asserted over the same broad shapes
   `test_manifest_wiring_gate` uses for the three manifest writers.
"""

from __future__ import annotations

# tomllib is stdlib only on 3.11+; jm supports down to 3.9 and guards this the
# same way in _config.py. A bare `import tomllib` is a collection error on the
# 3.9/3.10 legs, which fail-fast then reports as an 18-job matrix wipeout.
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import pytest

from just_makeit import _config as C
from just_makeit._keys import (
    KIND_KEYS,
    unknown_keys,
    warn_unknown_keys,
)

from test_manifest_wiring_gate import SHAPES


def _msgs(cfg: dict) -> list[str]:
    return [u.message() for u in unknown_keys(cfg)]


class TestTheReportedCases:
    """Both instances named in gh-816 / gh-805 §G."""

    def test_check_return_on_a_method_is_reported(self):
        cfg = {
            "meter": {
                "arg_type": "float",
                "methods": [{"name": "set_truth", "check_return": True}],
            }
        }
        (msg,) = _msgs(cfg)
        assert "meter.set_truth" in msg
        assert "`check_return`" in msg

    def test_it_names_the_kind_the_key_belongs_to(self):
        """The clause the reporter called most of the value."""
        cfg = {
            "meter": {"methods": [{"name": "m", "check_return": True}]},
        }
        (msg,) = _msgs(cfg)
        assert "it is a function key" in msg

    def test_it_suggests_the_method_spelling(self):
        cfg = {
            "meter": {"methods": [{"name": "m", "check_return": True}]},
        }
        (msg,) = _msgs(cfg)
        assert "status_return" in msg

    def test_create_error_under_init_params_is_reported(self):
        """gh-805 §G's first instance: TOML binds a key written below
        `[[obj.init_params]]` into that param table, where jm never looks."""
        cfg = {
            "widget": {
                "init_params": [
                    {"name": "rate", "type": "double", "create_error": "V"}
                ]
            }
        }
        (msg,) = _msgs(cfg)
        assert "widget.rate" in msg
        assert "`create_error`" in msg
        assert "[<object>]" in msg

    def test_a_key_of_no_kind_says_so_plainly(self):
        cfg = {"meter": {"methods": [{"name": "m", "notakey": 1}]}}
        (msg,) = _msgs(cfg)
        assert "does not read it anywhere" in msg
        # No misleading "it is a <kind> key" clause when there is no kind.
        assert "it is a" not in msg


class TestTomlBindsATrailingKeyIntoTheLastTable:
    """The mechanism behind both reported cases, asserted directly.

    A bare key written *after* a `[[x.y]]` header belongs to that table, not
    to `[x]` — which is why the misplacement is so easy to make and so hard
    to see. This is the property the warning exists to surface.
    """

    def test_a_key_after_a_state_header_lands_on_the_state_entry(self):
        cfg = tomllib.loads(
            '[acq]\narg_type = "float"\n\n'
            '[[acq.state]]\nname = "n"\ntype = "int"\n\n'
            'depends_on = [ { name = "fft", link = true } ]\n'
        )
        assert "depends_on" not in cfg["acq"]
        assert "depends_on" in cfg["acq"]["state"][0]
        # ...and that is exactly what jm now reports.
        (msg,) = _msgs(cfg)
        assert "unknown state key `depends_on`" in msg


class TestNoFalsePositives:
    """A registry that lags the emitters makes every scaffold noisy."""

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_a_generated_project_reports_nothing(self, shape, tmp_path):
        root = SHAPES[shape](tmp_path)
        assert _msgs(C.load(root)) == []

    def test_every_declared_key_of_every_kind_is_accepted(self):
        """Guard against a key being listed under one kind and read as
        unknown by another kind's walk."""
        for kind, keys in KIND_KEYS.items():
            assert keys, f"{kind} has no declared keys"

    def test_app_enum_and_codec_are_not_objects(self):
        """`[app]`, `[[enum]]` and `[codec.X]` are top-level sections, not
        components. Walking them as objects reported every one of their keys
        as unknown — `C.components` is the canonical exclusion list."""
        cfg = {
            "project": {"name": "p"},
            "app": {"commands": [], "target": "x"},
            "codec": {"frame": {"kw": 1}},
        }
        assert _msgs(cfg) == []


class TestScope:
    """Tables jm cannot characterise are skipped, not guessed at."""

    def test_a_handle_module_is_left_alone(self):
        """`kind = "handle"` methods have their own vocabulary (`returns`,
        `out_len_fn`). Warning on them would be a false positive."""
        cfg = {
            "module": {
                "dev": {
                    "kind": "handle",
                    "functions": [{"name": "f", "out_len_fn": "n"}],
                }
            }
        }
        assert _msgs(cfg) == []

    def test_a_plain_module_function_is_checked(self):
        cfg = {
            "module": {
                "dsp": {"functions": [{"name": "lerp", "status_return": True}]}
            }
        }
        (msg,) = _msgs(cfg)
        assert "dsp.lerp" in msg
        assert "it is a method key" in msg

    def test_transient_underscore_keys_are_skipped(self):
        cfg = {"meter": {"_doc_blocks": {}, "methods": [{"name": "m"}]}}
        assert _msgs(cfg) == []


class TestWarningEmission:
    def test_it_writes_to_the_stream_and_dedupes(self, capsys):
        import just_makeit._keys as K

        K._SEEN.clear()
        cfg = {"meter": {"methods": [{"name": "m", "check_return": True}]}}
        first = warn_unknown_keys(cfg)
        second = warn_unknown_keys(cfg)
        assert len(first) == 1
        assert second == [], "the same warning was printed twice"
        assert "warning:" in capsys.readouterr().err

    def test_load_reports_it(self, tmp_path, capsys):
        """The whole point: an ordinary command surfaces it."""
        import just_makeit._keys as K

        (tmp_path / C.FILENAME).write_text(
            '[project]\nname = "p"\n\n[meter]\narg_type = "float"\n\n'
            '[[meter.methods]]\nname = "set_truth"\ncheck_return = true\n'
        )
        K._SEEN.clear()
        C.load(tmp_path)
        assert "check_return" in capsys.readouterr().err

    def test_the_key_still_round_trips(self, tmp_path):
        """gh-257's tolerance is unchanged — warning is not rejecting."""
        (tmp_path / C.FILENAME).write_text(
            '[project]\nname = "p"\n\n[meter]\narg_type = "float"\n\n'
            '[[meter.methods]]\nname = "set_truth"\ncheck_return = true\n'
        )
        cfg = C.load(tmp_path)
        C.save(tmp_path, cfg)
        assert C.load(tmp_path)["meter"]["methods"][0]["check_return"] is True
