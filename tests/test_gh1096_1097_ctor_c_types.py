"""gh-1096 / gh-1097: naming C types the constructor declaration cannot derive.

gh-1076's CTOR check compares the `create()` jm injects into the sacred header
against the one the manifest renders. It is deliberately **not suppressible**,
on the reasoning that "an unsuppressible finding is right when the alternative
is a project that cannot be regenerated".

That reasoning assumes the finding is *actionable*. Both issues here are cases
where it was not: the C is right, the binding is right, and the manifest had no
vocabulary for the signature. An unactionable unsuppressible gate is a wall,
and doppler hit it on two constructors at once.

Worse than blocking, `jm apply` **resolved** the disagreement by rewriting the
author's header down to jm's rendering — turning `det_noise_mode_t` into `int`
behind a `~` warning. The remedy the issue reluctantly proposed was the thing
jm was already doing unasked.

Both fixes are the same shape, and neither changes the Python face or the
generated binding by one byte:

* **gh-1096** — `c_type` names the C type an integer-rendered parameter is
  DECLARED with. jm still parses a choice string, still validates it to an
  index, still passes an `int`; C converts at the call, which is why the two
  were interchangeable and why doppler's bindings were correct all along.
* **gh-1097** — `derived` accepts a list naming a 2-D array's extents. jm
  already declared them, already required `ndim == 2`, and already passed both
  dimensions; they were called `<name>_dim0`/`<name>_dim1` and could not be
  renamed. The issue read as "a 2D create() cannot be described"; measured, it
  was only that the extents could not be *named*.

`TestTheGateGoesQuiet` is the load-bearing class: the point is not that a
string changed but that CTOR passes against a real typedef'd header and
`apply` stops rewriting it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _ctorsig  # noqa: E402
from just_makeit._context._state import (  # noqa: E402
    _ctor_c_type,
    _ctor_extent_names,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, name: str, init_params: str) -> Path:
    """A scaffolded object whose manifest carries *init_params* verbatim."""
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "obj",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("gain", "float", "0.0f")],
    )
    # Split-layout projects keep the object in `objects/<obj>.toml`; a
    # central-manifest one keeps it in `just-makeit.toml`. Append to whichever
    # this scaffold produced rather than assuming a layout.
    frag = root / "objects" / "obj.toml"
    if not frag.exists():
        frag = root / "just-makeit.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + init_params, encoding="utf-8"
    )
    return root


def _create_params(root: Path) -> str:
    """The rendered `create()` parameter list — the slot CTOR reads."""
    from just_makeit import _glue

    cfg = C.load(root)
    ctx = _glue.component_ctx(cfg, "obj", C.project_name(cfg) or "", root)
    return ctx["create_params"]


ENUM_IP = """
[[obj.init_params]]
name = "noise_mode"
type = "string_enum:mean,median,min,max"
default = "mean"
c_type = "det_noise_mode_t"
"""

ENUM_IP_PLAIN = """
[[obj.init_params]]
name = "noise_mode"
type = "string_enum:mean,median,min,max"
default = "mean"
"""

ARR2D_IP = """
[[obj.init_params]]
name = "ref"
type = "float _Complex[][]"
derived = ["ny", "nx"]
"""

ARR2D_IP_PLAIN = """
[[obj.init_params]]
name = "ref"
type = "float _Complex[][]"
"""


class TestTheDeclarationCanNameItsCType:
    """gh-1096. The declared type moves; nothing else does."""

    def test_the_typedef_reaches_create(self, tmp_path):
        root = _project(tmp_path, "a", ENUM_IP)
        assert _create_params(root) == "det_noise_mode_t noise_mode"

    def test_without_it_nothing_changes(self, tmp_path):
        """The default is what jm shipped, so this is additive by proof and
        not by assertion."""
        root = _project(tmp_path, "b", ENUM_IP_PLAIN)
        assert _create_params(root) == "int noise_mode"

    def test_the_python_face_is_untouched(self, tmp_path):
        """The whole point: `Detector(noise_mode="median")` is correct now and
        stays correct. A fix that moved the Python face to an int would have
        satisfied CTOR by breaking every caller."""
        root = _project(tmp_path, "c", ENUM_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        pyi = (root / "src" / "c" / "obj.pyi").read_text(encoding="utf-8")
        assert 'noise_mode: str = "mean"' in pyi

    def test_the_binding_still_validates_the_choice_string(self, tmp_path):
        """C converts the int at the call — that interchangeability is what
        makes the override safe, so the binding must be unchanged."""
        root = _project(tmp_path, "d", ENUM_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        ext = (root / "native" / "src" / "obj" / "obj_ext.c").read_text(
            encoding="utf-8"
        )
        assert "const char *noise_mode_str" in ext
        assert 'strcmp(noise_mode_str, "median")' in ext
        assert "int noise_mode = 0;" in ext


class TestTheCTypeOverrideIsRestricted:
    """The limit is enforced, not documented.

    jm passes an `int`. Over a `double` parameter the override would be a
    silent ABI mismatch that still compiles — the exact class of bug the CTOR
    check exists to surface, reintroduced by the fix for it.
    """

    def test_an_integer_param_accepts_it(self):
        assert _ctor_c_type("m", "det_mode_t", "int") == "det_mode_t"

    def test_a_float_param_refuses_it(self):
        with pytest.raises(ValueError) as e:
            _ctor_c_type("gain", "my_gain_t", "double")
        assert "integer" in str(e.value)
        assert "double" in str(e.value)

    def test_absent_leaves_the_rendered_type(self):
        assert _ctor_c_type("gain", "", "double") == "double"


class TestA2DArraysExtentsCanBeNamed:
    """gh-1097. jm already declared two extents; now they have names."""

    def test_the_names_reach_create(self, tmp_path):
        root = _project(tmp_path, "e", ARR2D_IP)
        assert (
            _create_params(root)
            == "const float _Complex *ref, size_t ny, size_t nx"
        )

    def test_without_it_nothing_changes(self, tmp_path):
        root = _project(tmp_path, "f", ARR2D_IP_PLAIN)
        assert (
            _create_params(root)
            == "const float _Complex *ref, size_t ref_dim0, size_t ref_dim1"
        )

    def test_the_binding_keeps_jms_own_locals(self, tmp_path):
        """Only the DECLARED names change — the same split gh-900 made for the
        1-D length. If the locals moved too, every downstream use would have
        to move with them for no gain."""
        root = _project(tmp_path, "g", ARR2D_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        ext = (root / "native" / "src" / "obj" / "obj_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_NDIM(ref_arr) != 2" in ext
        assert "size_t ref_dim0 = (size_t)PyArray_DIM(ref_arr, 0);" in ext
        assert "size_t ref_dim1 = (size_t)PyArray_DIM(ref_arr, 1);" in ext
        assert "ref_dim0, ref_dim1" in ext

    def test_a_wrong_length_list_is_refused(self):
        """Naming one extent of a 2-D array would drop the other from the
        declaration while the binding still passed it — a create() that does
        not compile, produced by a manifest that looked deliberate."""
        with pytest.raises(ValueError) as e:
            _ctor_extent_names("ref", ["ny"], 2)
        assert "1 extent" in str(e.value)
        assert "2-D" in str(e.value)

    def test_the_1d_string_form_still_works(self):
        """gh-900's shape is the reason `derived` is polymorphic rather than a
        second key, so it is the regression fence."""
        assert _ctor_extent_names("h", "num_taps", 1) == ["num_taps"]


class TestTheManifestRoundTrips:
    """A dropped key round-trips the param back to jm's own naming and
    silently changes the C prototype on the next apply — the reasoning
    gh-900 and gh-790 both wrote down when they hit it.
    """

    @pytest.mark.parametrize(
        "label,frag",
        [("c_type", ENUM_IP), ("derived-list", ARR2D_IP)],
    )
    def test_save_then_load_preserves_it(self, tmp_path, label, frag):
        root = _project(tmp_path, "i" + label.replace("-", ""), frag)
        before = C.load(root)["obj"]["init_params"]
        C.save(root, C.load(root))
        assert C.load(root)["obj"]["init_params"] == before

    #: The two emitters, asserted DIRECTLY. A save/load round-trip cannot
    #: stand in for them: `_dump` is self-checking, so a serializer that
    #: mangles the list simply fails its own round-trip guard and `save`
    #: falls back to tomlkit, which preserves the original text. Measured —
    #: the integration form stayed green with `_init_param_pairs` sabotaged.
    #: The fallback does not cover the brand-new-file path, where `_dump`
    #: runs unguarded, which is the exposure these two protect.
    PARAM = {
        "name": "ref",
        "type": "float _Complex[][]",
        "derived": ["ny", "nx"],
    }

    def test_the_block_emitter_writes_a_toml_array(self):
        from just_makeit._config import _init_param_block_lines

        assert 'derived = ["ny", "nx"]' in _init_param_block_lines(self.PARAM)

    def test_the_inline_emitter_writes_a_toml_array(self):
        """The peer. `_init_param_inline` serves a view's init_params and is a
        separate code path; fixing one and not the other is the pattern this
        repo keeps paying for."""
        from just_makeit._config import _init_param_inline

        assert 'derived = ["ny", "nx"]' in _init_param_inline(self.PARAM)


class TestTheGateGoesQuiet:
    """What the whole PR is for: CTOR passes against the real header, and
    `apply` stops rewriting it.

    Asserted against a header carrying the author's actual typedef, because
    that is the artefact CTOR reads and the one `apply` was overwriting.
    """

    @staticmethod
    def _typedef_header(root: Path, old: str, new: str) -> None:
        h = root / "native" / "inc" / "obj" / "obj_core.h"
        text = h.read_text(encoding="utf-8")
        assert old in text, text
        h.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_ctor_drift_is_clean_for_the_enum_typedef(self, tmp_path):
        root = _project(tmp_path, "k", ENUM_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        self._typedef_header(
            root,
            "obj_state_t *obj_create(det_noise_mode_t noise_mode);",
            "typedef enum { D_MEAN } det_noise_mode_t;\n"
            "obj_state_t *obj_create(det_noise_mode_t noise_mode);",
        )
        assert _ctorsig.drift(root, C.load(root)) == []

    def test_ctor_drift_is_clean_for_the_named_extents(self, tmp_path):
        """Asserted against a header written with the AUTHOR'S names.

        Checking straight after `apply` proves nothing: apply injects jm's own
        rendering, so the two sides agree by construction and the check passes
        whatever `derived` did. Measured — that version stayed green with the
        feature sabotaged. The header has to carry `ny`/`nx` independently for
        the comparison to have anything to disagree about.
        """
        root = _project(tmp_path, "l", ARR2D_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        self._typedef_header(
            root,
            "obj_create(const float _Complex *ref, size_t ny, size_t nx)",
            "obj_create(const float _Complex *ref, size_t ny, size_t nx)",
        )
        assert _ctorsig.drift(root, C.load(root)) == []

    def test_ctor_drift_is_REPORTED_when_the_names_disagree(self, tmp_path):
        """The gate still bites. A fix that made CTOR quiet by weakening the
        comparison would pass every other test in this file."""
        root = _project(tmp_path, "n", ARR2D_IP)
        _quiet(__import__("just_makeit._apply", fromlist=["run"]).run, root)
        self._typedef_header(
            root,
            "obj_create(const float _Complex *ref, size_t ny, size_t nx)",
            "obj_create(const float _Complex *ref, size_t rows, size_t cols)",
        )
        found = _ctorsig.drift(root, C.load(root))
        assert len(found) == 1
        assert "rows" in found[0].declared
        assert "ny" in found[0].rendered

    def test_apply_no_longer_rewrites_the_authors_declaration(self, tmp_path):
        """The half neither issue named. Before this, the manifest could not
        say `det_noise_mode_t`, so `apply` replaced the author's declaration
        with `int` — silently weakening a public C API to satisfy a
        comparison. Now the manifest says it, so there is nothing to replace.
        """
        root = _project(tmp_path, "m", ENUM_IP)
        apply_run = __import__("just_makeit._apply", fromlist=["run"]).run
        _quiet(apply_run, root)
        self._typedef_header(
            root,
            "obj_state_t *obj_create(det_noise_mode_t noise_mode);",
            "typedef enum { D_MEAN } det_noise_mode_t;\n"
            "obj_state_t *obj_create(det_noise_mode_t noise_mode);",
        )
        _quiet(apply_run, root)
        text = (root / "native" / "inc" / "obj" / "obj_core.h").read_text(
            encoding="utf-8"
        )
        assert "obj_create(det_noise_mode_t noise_mode);" in text
        assert "obj_create(int noise_mode);" not in text
