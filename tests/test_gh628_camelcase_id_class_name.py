"""gh-628: a CamelCase manifest id was mangled in the module-aggregated .pyi.

Two peers derived the Python class name for an object that declares no
``class_name``. The C generators used "upper-case each word's first letter,
leave the rest" (``HalfbandDecimator`` -> ``HalfbandDecimator``); the stub
generator used ``str.title()``, which also *lower-cases* the remainder
(``HalfbandDecimator`` -> ``Halfbanddecimator``).

So the stub declared a class the extension does not define, and omitted the
one it does — while ``__init__.py`` re-exported the real name. A type checker
could not resolve the working import and silently accepted the fictional one.
``jm status --check`` stayed green throughout, because the ``.pyi`` matched
what jm intended to generate; the divergence was only ever visible by
comparing the stub against the binding (see #622).

The two rules agree for every all-lowercase id, which is why this survived:
``fir_filter``, ``nco`` and ``acc_f32`` render identically under both. Only an
id with a capital *after* the first character diverged.

Live instance: doppler's ``HalfbandDecimator``. Its sibling ``hbdecim_q15``
was correct three lines away in the same stub, because it declares an explicit
``class_name`` — which is also the workaround this fix makes unnecessary.
"""

import io
import contextlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._config import default_class_name
from just_makeit._init import _to_title
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._stubs import _title


class TestDerivation:
    """The rule itself, pinned as literals rather than via the constant."""

    @pytest.mark.parametrize(
        ("component", "expected"),
        [
            # Already-capitalised ids: the regression. `.title()` flattened
            # these to Halfbanddecimator / Rateconverter / MyFft.
            ("HalfbandDecimator", "HalfbandDecimator"),
            ("RateConverter", "RateConverter"),
            ("my_FFT", "MyFFT"),
            # Snake_case ids: unchanged by the fix, and the reason it hid.
            ("fir_filter", "FirFilter"),
            ("acc_f32", "AccF32"),
            ("nco", "Nco"),
            ("hbdecim_q15", "HbdecimQ15"),
        ],
    )
    def test_default_class_name(self, component, expected):
        assert default_class_name(component) == expected

    @pytest.mark.parametrize(
        "component",
        ["HalfbandDecimator", "RateConverter", "my_FFT", "fir_filter", "nco"],
    )
    def test_both_generators_agree(self, component):
        """The C side and the stub side must not be able to disagree.

        This is the property that actually failed — either rule alone is
        self-consistent; shipping two of them is the defect.
        """
        assert (
            _to_title(component)
            == _title(component)
            == default_class_name(component)
        )


@pytest.fixture()
def camel_module(tmp_path):
    """A module object whose manifest id is CamelCase and has no class_name."""
    root = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", root, [], [])
        module_run(root, "resample", [])
    m = root / "just-makeit.toml"
    m.write_text(
        m.read_text().replace(
            "[module.resample]\nobjects = []",
            '[module.resample]\nobjects = ["HalfbandDecimator"]',
        )
        + '\n[HalfbandDecimator]\nmodule = "resample"\n'
        'arg_type = "float _Complex"\nreturn_type = "float _Complex"\n'
    )
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(root)
    return root


class TestGeneratedProject:
    """The three faces of one type must name it identically."""

    def test_stub_class_name(self, camel_module):
        pyi = (
            camel_module / "src" / "dsp" / "resample" / "resample.pyi"
        ).read_text()
        assert re.findall(r"^class (\w+)", pyi, re.M) == ["HalfbandDecimator"]

    def test_c_type_name(self, camel_module):
        c = "\n".join(
            p.read_text()
            for p in (camel_module / "native" / "src").rglob("*.c")
        )
        assert "resample.HalfbandDecimator" in c

    def test_package_reexport(self, camel_module):
        init = (
            camel_module / "src" / "dsp" / "resample" / "__init__.py"
        ).read_text()
        assert "import HalfbandDecimator" in init
        assert "Halfbanddecimator" not in init

    def test_no_mangled_name_anywhere(self, camel_module):
        """The mangled spelling must not survive in any generated artifact."""
        for p in camel_module.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".pyi", ".c", ".h"}:
                assert "Halfbanddecimator" not in p.read_text(
                    errors="ignore"
                ), p
