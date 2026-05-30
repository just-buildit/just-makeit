"""Unit tests for just_makeit._cli_new."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_new

    _cli_new.run(args)


class TestCliNew:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_object_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--object"])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--module"])

    def test_build_system_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--build-system"])

    def test_build_system_invalid_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--build-system", "ninja"])

    def test_arg_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type"])

    def test_return_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type"])

    def test_return_type_array_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type", "float[]"])

    def test_arg_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type", "notatype"])

    def test_arg_type_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type", "notatype[]"])

    def test_return_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type", "notatype"])

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--unknown"])

    def test_flag_like_unknown_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--bogus-flag"])

    def test_minimal_call(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == "myproj"

    def test_object_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "fir"])
            args, _ = mock_run.call_args
            assert args[2] == ["fir"]

    def test_multiple_objects(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "fir", "--object", "biquad"])
            args, _ = mock_run.call_args
            assert set(args[2]) == {"fir", "biquad"}

    def test_module_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--module", "signal"])
            _, kwargs = mock_run.call_args
            assert kwargs["modules"] == ["signal"]

    def test_build_system_cmake(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--build-system", "cmake"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "cmake"

    def test_build_system_make(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--build-system", "make"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "make"

    def test_basic_flag_deprecated(self, capsys):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--basic"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "make"
        assert "deprecated" in capsys.readouterr().err

    def test_no_step_flag(self):
        """v0.14 foot-gun #3: `jm new --object X --no-step` used to error
        with 'unexpected argument'; the flag now threads through to the
        object scaffold so reader-shaped objects scaffold in one command."""
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "src", "--no-step"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_step"] is True

    def test_no_state_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "src", "--no-state"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_state"] is True

    def test_no_state_and_state_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            _run(
                [
                    "myproj",
                    "--object",
                    "x",
                    "--no-state",
                    "--state",
                    "z:float:0",
                ]
            )

    def test_perf_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--perf"])
            _, kwargs = mock_run.call_args
            assert kwargs["perf"] is True

    def test_pytest_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--pytest"])
            _, kwargs = mock_run.call_args
            assert kwargs["pytest_"] is True

    def test_pytest_benchmark_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--pytest-benchmark"])
            _, kwargs = mock_run.call_args
            assert kwargs["pytest_benchmark_"] is True

    def test_mutable_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--mutable"])
            _, kwargs = mock_run.call_args
            assert kwargs["mutable"] is True

    def test_arg_type_float(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--arg-type", "float"])
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float"

    def test_arg_type_array(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--arg-type", "float[]"])
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float[]"

    def test_return_type_float(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--return-type", "float"])
            _, kwargs = mock_run.call_args
            assert kwargs["return_type"] == "float"

    def test_state_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--state", "gain:double:1.0"])
            args, _ = mock_run.call_args
            assert args[3] is not None

    def test_dest_positional(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "/tmp/dest"])
            args, _ = mock_run.call_args
            from pathlib import Path

            assert args[1] == Path("/tmp/dest")

    def test_no_objects_passes_none(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            args, _ = mock_run.call_args
            assert args[2] is None

    def test_no_state_passes_none(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            args, _ = mock_run.call_args
            assert args[3] is None

    def test_find_package_forwarded(self):
        """Phase 2: --find-package NAME (repeatable) lands in [project]
        find_packages and is picked up by jm apply's _splice_cmake_external_deps."""
        with patch("just_makeit._new.run") as mock_run:
            _run(
                [
                    "myproj",
                    "--find-package",
                    "Doppler",
                    "--find-package",
                    "Threads",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["find_packages"] == ["Doppler", "Threads"]

    def test_pkg_module_forwarded(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--pkg-module", "doppler"])
            _, kwargs = mock_run.call_args
            assert kwargs["pkg_modules"] == ["doppler"]

    def test_c_dep_forwarded(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--c-dep", "libfoo"])
            _, kwargs = mock_run.call_args
            assert kwargs["c_deps"] == ["libfoo"]

    def test_find_package_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--find-package"])

    def test_pkg_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--pkg-module"])

    def test_c_dep_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--c-dep"])

    def test_external_deps_persisted_in_toml(self, tmp_path):
        """End-to-end: --find-package / --pkg-module / --c-dep land in
        the just-makeit.toml [project] section."""
        from just_makeit._new import run as new_run

        new_run(
            "demo",
            tmp_path / "demo",
            find_packages=["Doppler"],
            pkg_modules=["fftw3"],
            c_deps=["vendored_lib"],
        )
        toml_text = (tmp_path / "demo" / "just-makeit.toml").read_text(encoding="utf-8")
        assert 'find_packages = ["Doppler"]' in toml_text
        assert 'pkg_modules = ["fftw3"]' in toml_text
        assert 'c_deps = ["vendored_lib"]' in toml_text
