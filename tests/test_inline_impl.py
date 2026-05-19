"""Tests for inline `impl` / `impl_file` / `replace` on object and
method TOML sections, consumed by `jm apply`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import (
    _interpolate,
    _resolve_impl,
    _validate_fragment_impl_keys,
    run as apply_run,
)
from just_makeit._new import run as new_run


def _write_manifest_with_include(root: Path) -> None:
    """Prepend an include glob to the existing manifest."""
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        'include = ["objects/*.toml"]\n\n' + text, encoding="utf-8"
    )


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    new_run("proj", root)
    return root


class TestInterpolate:
    def test_replaces_known_placeholder(self):
        assert (
            _interpolate("hello {Component}!", {"Component": "Foo"})
            == "hello Foo!"
        )

    def test_unknown_placeholder_passes_through(self):
        assert (
            _interpolate("loop {missing} count", {}) == "loop {missing} count"
        )

    def test_literal_braces_pass_through(self):
        """C-style literal braces are NOT identifier placeholders and so
        survive intact: `{0}`, `{ static int x; }`, `if (...) { ... }`."""
        body = "int xs[3] = {0};\nif (n) { return -1; }"
        assert _interpolate(body, {"Component": "X"}) == body


class TestResolveImpl:
    def test_returns_none_when_neither_set(self, tmp_path):
        assert _resolve_impl({}, {}, tmp_path, "x") is None

    def test_inline_with_interpolation(self, tmp_path):
        section = {"impl": "/* {Component} */ return x;"}
        body = _resolve_impl(section, {"Component": "Agc"}, tmp_path, "agc")
        assert body == "/* Agc */ return x;"

    def test_impl_file_with_replace(self, tmp_path):
        ref = tmp_path / "ref.c"
        ref.write_text(
            "float REF_step(state_t *s, float x) {\n"
            "    /* TODO */\n"
            "    return x;\n"
            "}\n"
        )
        section = {
            "impl_file": "ref.c::REF_step",
            "replace": {"TODO": "done", "REF_step": "agc_step"},
        }
        body = _resolve_impl(section, {}, tmp_path, "agc")
        assert "/* done */" in body
        assert "return x;" in body

    def test_mutex_error(self, tmp_path):
        section = {"impl": "return x;", "impl_file": "ref.c::foo"}
        with pytest.raises(ValueError, match="mutually exclusive"):
            _resolve_impl(section, {}, tmp_path, "agc")

    def test_impl_file_requires_funcname(self, tmp_path):
        section = {"impl_file": "ref.c"}
        with pytest.raises(ValueError, match="path::funcname"):
            _resolve_impl(section, {}, tmp_path, "agc")


class TestValidateFragmentImplKeys:
    def test_clean_fragment_passes(self):
        _validate_fragment_impl_keys({"agc": {"impl": "x"}}, "agc.toml")

    def test_object_mutex_violation(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            _validate_fragment_impl_keys(
                {"agc": {"impl": "x", "impl_file": "r.c::f"}},
                "agc.toml",
            )

    def test_method_mutex_violation(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            _validate_fragment_impl_keys(
                {
                    "agc": {
                        "methods": [
                            {
                                "name": "exec",
                                "impl": "x",
                                "impl_file": "r.c::f",
                            }
                        ]
                    }
                },
                "agc.toml",
            )


_OBJECT_FRAGMENT = """\
[scaler]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

impl = '''
/* {Component} scales x by state->gain. */
return (float _Complex)(state->gain * x);
'''

[[scaler.state]]
name = "gain"
type = "float"
default = "1.0f"
"""


class TestApplyInjectsInlineImpl:
    def test_inline_impl_lands_in_core_h(self, project, tmp_path):
        frag = tmp_path / "scaler.toml"
        frag.write_text(_OBJECT_FRAGMENT)
        apply_run(project, fragment=frag)

        core_h = (
            project / "native" / "inc" / "scaler" / "scaler_core.h"
        ).read_text(encoding="utf-8")
        assert "Scaler scales x by state->gain" in core_h
        assert "(float _Complex)(state->gain * x)" in core_h

    def test_no_unreplaced_placeholder_remains(self, project, tmp_path):
        frag = tmp_path / "scaler.toml"
        frag.write_text(_OBJECT_FRAGMENT)
        apply_run(project, fragment=frag)
        core_h = (
            project / "native" / "inc" / "scaler" / "scaler_core.h"
        ).read_text(encoding="utf-8")
        assert "{Component}" not in core_h


class TestApplyMutexAborts:
    def test_mutex_violation_aborts_before_copy(self, project, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text(
            '[bad]\narg_type = "float"\nreturn_type = "float"\n'
            'mutable = "false"\nno_state = "false"\nno_step = "false"\n'
            'impl = "return x;"\nimpl_file = "x.c::y"\n'
        )
        with pytest.raises(SystemExit):
            apply_run(project, fragment=bad)
        # Fragment must NOT have been copied; manifest must NOT have
        # gained an `include`.
        assert not (project / "objects" / "bad.toml").exists()
        assert "include" not in C.load_manifest(project)
