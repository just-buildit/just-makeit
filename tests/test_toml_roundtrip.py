"""
CLI → TOML → jm script round-trip tests.

Strategy: build a project via CLI, run `jm script`, replay every emitted
command into a fresh directory, then compare the two MERGED manifests
(`_jmrun.script_round_trip`). If a flag is correctly stored in TOML and
correctly emitted by `jm script`, the replayed manifest will be identical to
the original.

Merged, not the root ``just-makeit.toml`` alone (gh-1923): `jm new` defaults
to the fragment layout, so every component lives in ``objects/`` and every
module in ``modules/``, and a root-only comparison saw ``[project]`` and
``include`` and nothing else. Every component-level test here passed whether
the replay kept its flag or not.

Known intentional gap:
  - --impl / --replace: intentionally not stored in TOML; tested separately
    in TestImplCLI in test_cli.py.

GATE: every flag a test here declares survives `jm script` and a replay of
      it, compared over the manifest `_config.load` merges.
"""

from __future__ import annotations

from _jmrun import JmRun, run_cli, script_round_trip


def _cli(*args, cwd=None) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


# ── Object flag round-trips ───────────────────────────────────────────────────


class TestObjectFlagsRoundTrip:
    def test_arg_type_void_return_type_complex(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_return_type_void_sink(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "sink",
            "--arg-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_arg_and_return_same_non_default(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "gain",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        # same as arg-type so --return-type should be omitted
        assert "--return-type float" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_default_types_no_type_flags(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "gain", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--arg-type" not in r.stdout
        assert "--return-type" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_no_state_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "gen", "--no-state", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--no-state" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_no_step_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "sink", "--no-step", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--no-step" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_mutable_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--mutable",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--mutable" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_init_param_single(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        r = _cli("script", cwd=dest)
        assert "--init-param n:int:16" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_init_param_multiple(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "gen",
            "--no-state",
            "--init-param",
            "rate:float:1.0",
            "--init-param",
            "order:int:4",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--init-param rate:float:1.0" in r.stdout
        assert "--init-param order:int:4" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_state_var_with_default(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "bpf",
            "--state",
            "cutoff:double:440.0",
            "--state",
            "order:int:4",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--state cutoff:double:440.0" in r.stdout
        assert "--state order:int:4" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_array_arg_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "fir",
            "--arg-type",
            "float _Complex[]",
            "--array-arg",
            "coeffs:float32",
            "--state",
            "gain:float:1.0f",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--array-arg coeffs:float32" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_array_arg_ctype_normalizes_to_dtype(self, tmp_path):
        """C type input (e.g. float) is accepted and stored as dtype name (float32)."""
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "fir",
            "--arg-type",
            "float _Complex[]",
            "--array-arg",
            "coeffs:float",
            "--state",
            "gain:float:1.0f",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--array-arg coeffs:float32" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay


# ── Project flag round-trips ──────────────────────────────────────────────────


class TestProjectFlagsRoundTrip:
    def test_perf_persisted_and_replayed(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--perf")
        r = _cli("script", cwd=dest)
        assert "--perf" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_build_system_make_persisted_and_replayed(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--build-system", "make")
        r = _cli("script", cwd=dest)
        assert "--build-system make" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_non_default_version(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("config", "version", "0.3.0", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "config version 0.3.0" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay


# ── Method flag round-trips ───────────────────────────────────────────────────


class TestMethodFlagsRoundTrip:
    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        return dest

    def test_variable_output(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "execute",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=dest,
        )
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_multi_output(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "execute_ovf",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "uint32_t",
            "--variable-output",
            "--multi-output",
            "uint8_t",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--multi-output uint8_t" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_scalar_params(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "configure",
            "--module",
            "dsp",
            "--param",
            "freq:float",
            "--param",
            "mode:int32_t",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--param freq:float" in r.stdout
        assert "--param mode:int32_t" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_out_type(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "demod",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex",
            "--out-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--out-type float" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_out_divisor(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "demod2",
            "--module",
            "dsp",
            "--arg-type",
            "int16_t",
            "--out-type",
            "float _Complex",
            "--out-divisor",
            "2",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--out-divisor 2" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_out_divisor_1_not_emitted(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "proc",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex",
            "--out-type",
            "float",
            "--out-divisor",
            "1",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--out-divisor" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_batch_round_trip(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "process",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            "--batch",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--batch" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay


# ── Property flag round-trips ─────────────────────────────────────────────────


class TestPropertyFlagsRoundTrip:
    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "nco", cwd=dest)
        return dest

    def test_type_and_writable(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "nco",
            "phase",
            "--type",
            "uint32_t",
            "--writable",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--type uint32_t" in r.stdout
        assert "--writable" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_read_only_no_writable_flag(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("property", "nco", "dropped", "--type", "size_t", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--writable" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_field(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "nco",
            "counter",
            "--type",
            "uint32_t",
            "--field",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--field" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_field_and_writable(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "nco",
            "val",
            "--type",
            "float",
            "--field",
            "--writable",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--field" in r.stdout
        assert "--writable" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay


# ── Function flag round-trips ─────────────────────────────────────────────────


class TestFunctionFlagsRoundTrip:
    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        return dest

    def test_doc_persisted(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "function",
            "dsp_init",
            "--module",
            "dsp",
            "--doc",
            "Initialize DSP tables.",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--doc" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_non_void_return_type(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "function",
            "get_count",
            "--module",
            "dsp",
            "--return-type",
            "int32_t",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--return-type int32_t" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_void_return_not_emitted(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("function", "do_setup", "--module", "dsp", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--return-type" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_params(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "function",
            "apply_window",
            "--module",
            "dsp",
            "--param",
            "n:size_t",
            "--param",
            "beta:float",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--param n:size_t" in r.stdout
        assert "--param beta:float" in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay

    def test_no_doc_not_emitted(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("function", "init", "--module", "dsp", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--doc" not in r.stdout
        orig, replay = script_round_trip(dest, tmp_path / "replay")
        assert orig == replay
