"""gh-595: an unrecognised `return_type` was SILENTLY dropped.

A `[[module.X.functions]]` entry declaring `return_type = "long"` generated a
binding that called the C function, threw the result away and emitted
`Py_RETURN_NONE` -- no error, no warning, and code that compiles cleanly. The
failure surfaced only at runtime, as a `None` where a number was expected.

The root cause is a lookup miss falling through to the void path:
`_render._py_wrapper_for_function` does `_CTYPE_META.get(return_type)` and, on
`None`, takes the branch that discards the call's value. `long` is not a
manifest key (its width is platform-dependent, and a binding's PyArg format
char has to match an exact width), but ANY unrecognised spelling behaved the
same way -- so the dangerous part is the silence, not the one missing type.

The fix is validation at `jm apply`, mirroring what the `jm function` /
`jm method` front-ends have always done for `--return-type`. Both front-ends
and the manifest walk now share ONE predicate (`_types.is_supported_return_type`)
instead of the three copies of the same rule that existed before.

Two exemptions matter and are pinned below, because getting either wrong turns
this check into a false positive that blocks legitimate projects:

- `result_fields` names a user-defined record struct (`peaks_result_t`), which
  is deliberately not a registered scalar. Likewise `codec` / `manual_stub` /
  `varargs` methods, whose `return_type` is an inert placeholder.
- an array return (`"float _Complex[]"`) is valid in a MANIFEST (capsule and
  block methods use it) but has always been rejected by the `--return-type`
  flags, which are scalar-or-void only. The predicate keeps that asymmetry
  explicit via `allow_array` rather than quietly widening the CLI.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run
from just_makeit._types import (
    is_supported_return_type,
    unsupported_return_type_help,
)


class TestPredicate:
    """The single shared rule behind all three call sites."""

    @pytest.mark.parametrize(
        "rt", ["void", "size_t", "int", "double", "float _Complex", "uint8_t"]
    )
    def test_accepts_void_and_registered_scalars(self, rt):
        assert is_supported_return_type(rt)

    @pytest.mark.parametrize(
        "rt",
        [
            "long",
            "unsigned",
            "ssize_t",
            "uint",
            "float complex",  # the display form, not the stored key
            "peaks_result_t",
            "",
        ],
    )
    def test_rejects_unregistered_spellings(self, rt):
        assert not is_supported_return_type(rt)

    def test_array_return_is_cli_invalid_but_manifest_valid(self):
        # The asymmetry is intentional -- see the module docstring.
        assert not is_supported_return_type("float _Complex[]")
        assert is_supported_return_type("float _Complex[]", allow_array=True)

    def test_array_of_unregistered_element_still_rejected(self):
        assert not is_supported_return_type("long[]", allow_array=True)

    def test_help_suggests_the_fixed_width_equivalent(self):
        msg = unsupported_return_type_help("long")
        assert "int64_t" in msg
        assert "platform-dependent width" in msg

    def test_help_lists_supported_types_without_a_hint(self):
        msg = unsupported_return_type_help("totally_made_up")
        assert "Supported: void," in msg
        assert "Did you mean" not in msg


class TestManifestWalk:
    """`C.return_type_errors` finds every offending table, and only those."""

    def test_flags_the_issue_manifest(self):
        cfg = {
            "module": {
                "ber": {
                    "functions": [
                        {"name": "ber_lock_symbol", "return_type": "long"}
                    ]
                }
            }
        }
        errors = C.return_type_errors(cfg)
        assert len(errors) == 1
        assert "module 'ber' function 'ber_lock_symbol'" in errors[0]
        assert "unknown return_type 'long'" in errors[0]

    def test_clean_manifest_yields_no_errors(self):
        cfg = {
            "module": {
                "ber": {
                    "functions": [
                        {"name": "ok", "return_type": "int64_t"},
                        {"name": "novoid", "return_type": "void"},
                        {"name": "defaulted", "name_only": True},
                    ]
                }
            }
        }
        assert C.return_type_errors(cfg) == []

    def test_result_fields_record_struct_is_exempt(self):
        # The doppler shape from the issue: a user struct + result_fields.
        cfg = {
            "module": {
                "ber": {
                    "functions": [
                        {
                            "name": "scan",
                            "return_type": "ber_align_t",
                            "result_fields": [{"name": "lag", "type": "int"}],
                        }
                    ]
                }
            }
        }
        assert C.return_type_errors(cfg) == []

    @pytest.mark.parametrize(
        "key,value",
        [
            ("codec", "sigmf"),
            ("manual_stub", True),
            ("varargs", True),
        ],
    )
    def test_placeholder_return_type_shapes_are_exempt(self, key, value):
        cfg = {
            "meter": {
                "methods": [
                    {"name": "m", "return_type": "whatever_t", key: value}
                ]
            }
        }
        assert C.return_type_errors(cfg) == []

    def test_function_out_type_makes_return_type_inert(self):
        # fn_c_decl forces the C return to void when out_type is set, so
        # return_type is never emitted -- flagging it would be a false alarm.
        cfg = {
            "module": {
                "m": {
                    "functions": [
                        {
                            "name": "f",
                            "return_type": "long",
                            "out_type": "float",
                        }
                    ]
                }
            }
        }
        assert C.return_type_errors(cfg) == []

    def test_array_return_on_a_method_is_accepted(self):
        cfg = {
            "module": {
                "ddc_fn": {
                    "methods": [
                        {"name": "execute", "return_type": "float _Complex[]"}
                    ]
                }
            }
        }
        assert C.return_type_errors(cfg) == []

    def test_component_and_view_methods_are_walked(self):
        cfg = {
            "meter": {
                "methods": [{"name": "a", "return_type": "long"}],
                "views": [
                    {
                        "class_name": "MeterView",
                        "methods": [{"name": "b", "return_type": "unsigned"}],
                    }
                ],
            }
        }
        errors = C.return_type_errors(cfg)
        assert len(errors) == 2
        assert "'meter' method 'a'" in errors[0]
        assert "'meter' view 'MeterView' method 'b'" in errors[1]


class TestApplyRefuses:
    """End-to-end: `jm apply` exits non-zero instead of generating None."""

    @pytest.fixture
    def project(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj, modules=["ber"])
        return proj

    def _declare_function(self, root, return_type):
        """Hand-author the issue's function entry (the TOML-only path)."""
        manifest = root / C.FILENAME
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\n[[module.ber.functions]]\n"
            + 'name = "ber_lock_symbol"\n'
            + f'return_type = "{return_type}"\n',
            encoding="utf-8",
        )

    def test_apply_refuses_and_explains(self, project, capsys):
        self._declare_function(project, "long")
        with pytest.raises(SystemExit) as exc:
            apply_run(project)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "unknown return_type 'long'" in err
        assert "int64_t" in err

    def test_apply_does_not_emit_a_value_discarding_binding(self, project):
        """The actual gh-595 symptom must never reach a generated file."""
        self._declare_function(project, "long")
        with pytest.raises(SystemExit):
            apply_run(project)
        ext = project / "native" / "src" / "ber" / "ber_ext.c"
        assert "_bind_ber_lock_symbol" not in ext.read_text(encoding="utf-8")

    def test_supported_return_type_still_applies(self, project):
        """Sabotage guard: the check must not block a valid manifest."""
        self._declare_function(project, "int64_t")
        apply_run(project)
        ext = (project / "native" / "src" / "ber" / "ber_ext.c").read_text(
            encoding="utf-8"
        )
        # The value is returned, not discarded -- the inverse of the bug.
        assert "_bind_ber_lock_symbol" in ext
        body = ext.split("_bind_ber_lock_symbol(")[1].split("\n}")[0]
        assert "Py_RETURN_NONE" not in body
