"""gh-442: lint when an init_param's manifest default disagrees with the
sacred header's own `@param name ... (default: X)` doc.

`_build_class_docstring` already juxtaposes both sources when rendering the
`.pyi`'s numpydoc `Parameters` section: the type/default signature line from
the manifest, the prose body from the header's `@param` text. When only one
side gets edited out-of-band (a manifest default retuned without updating
the hand-written header doc, or vice versa), the generated stub silently
reads as a scale/corruption bug even though each half is individually
correct for its own source of truth (this is exactly what gh-441's original
report turned out to be — a doppler-side doc-rot issue, not a jm bug).

`jm apply` warns (non-fatal — jm has no way to know which side is stale);
`jm status`/`jm status --check` promote the same check to a CI-gating DRIFT
section, always shown and never suppressed by `--allow`/`status_allow`,
mirroring the gh-426 DROPPED-symbol precedent.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import init_param_drift, run as object_run
from just_makeit._apply import run as apply_run
from just_makeit._docstring import header_default
from just_makeit import _config as C
from just_makeit import _status


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        result = fn(*a, **k)
    return result, buf.getvalue()


def _scaffold(tmp_path: Path) -> Path:
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    object_run(
        dest,
        "burst",
        module=None,
        init_params=[("carrier", "double", "0.05")],
    )
    return dest


def _hand_document_create(dest: Path, default_text: str = "0.05") -> None:
    """Replace the scaffold-trivial create() doc with a real hand-written
    one (a non-template @brief, or `_is_scaffold_brief` filters the whole
    block — including its @param defaults — as jm's own boilerplate)."""
    header = dest / "native" / "inc" / "burst" / "burst_core.h"
    text = header.read_text(encoding="utf-8")
    text = text.replace(
        "@brief Create a burst instance.",
        "@brief Allocate and initialize a burst detector.",
    )
    text = text.replace(
        "@param carrier  carrier (default: 0.05).",
        f"@param carrier  Carrier offset in Hz (default: {default_text}).",
    )
    header.write_text(text, encoding="utf-8")


def _retune_manifest_default(dest: Path, new_default: str) -> None:
    cfg = C.load(dest)
    cfg["burst"]["init_params"][0]["default"] = new_default
    C.save(dest, cfg)


class TestInitParamDrift:
    def test_no_drift_when_defaults_match(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == []

    def test_drift_detected_when_manifest_retuned(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        _retune_manifest_default(dest, "0.01")
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == [
            ("carrier", "0.01", "0.05")
        ]

    def test_no_false_positive_before_header_is_hand_documented(
        self, tmp_path
    ):
        # A freshly scaffolded header's create() doc is jm's own boilerplate
        # (_is_scaffold_brief) — filtered out entirely, so a manifest-only
        # retune with no human-authored header doc yet is not drift.
        dest = _scaffold(tmp_path)
        _retune_manifest_default(dest, "0.01")
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == []

    def test_no_false_positive_when_header_has_no_parseable_default(
        self, tmp_path
    ):
        dest = _scaffold(tmp_path)
        header = dest / "native" / "inc" / "burst" / "burst_core.h"
        text = header.read_text(encoding="utf-8")
        text = text.replace(
            "@brief Create a burst instance.",
            "@brief Allocate and initialize a burst detector.",
        )
        text = text.replace(
            "@param carrier  carrier (default: 0.05).",
            "@param carrier  Carrier offset in Hz.",
        )
        header.write_text(text, encoding="utf-8")
        _retune_manifest_default(dest, "0.01")
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == []

    def test_no_false_positive_when_manifest_default_is_empty(self, tmp_path):
        # A required init_param (no default) has nothing to compare —
        # skipped rather than treated as a mismatch against 0.05.
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        cfg = C.load(dest)
        cfg["burst"]["init_params"][0]["default"] = ""
        C.save(dest, cfg)
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == []

    def test_no_false_positive_when_header_default_is_not_numeric(
        self, tmp_path
    ):
        # A recognizable (default: X) suffix whose X isn't a number (e.g.
        # a symbolic/expression default) can't be compared — skipped.
        dest = _scaffold(tmp_path)
        _hand_document_create(dest, default_text="M_PI / 4")
        cfg = C.load(dest)
        assert init_param_drift(cfg, dest, "burst") == []


class TestHeaderDefault:
    def test_none_for_missing_description(self):
        assert header_default(None) is None
        assert header_default("") is None

    def test_none_for_description_without_default_suffix(self):
        assert header_default("Carrier offset in Hz.") is None

    def test_extracts_the_default_value(self):
        assert header_default("Initial carrier (default: 0.05).") == "0.05"


class TestApplyWarns:
    def test_apply_prints_warning_and_stays_non_fatal(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        _retune_manifest_default(dest, "0.01")

        with contextlib.redirect_stdout(io.StringIO()) as buf:
            apply_run(dest)  # must not raise / sys.exit
        out = buf.getvalue()
        assert (
            "warning: burst.carrier default mismatch: "
            "manifest='0.01' header='0.05'" in out
        )

    def test_apply_silent_when_no_drift(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)

        with contextlib.redirect_stdout(io.StringIO()) as buf:
            apply_run(dest)
        assert "default mismatch" not in buf.getvalue()


class TestStatusGatesOnDrift:
    def test_status_check_fails_and_shows_drift_section(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        _retune_manifest_default(dest, "0.01")
        apply_run(dest, honor_status_allow=False)  # sync everything else

        rc, out = _silent(_status.run, dest, check=True)
        assert rc >= 1
        assert "DRIFT (1)" in out
        assert "burst.carrier: manifest='0.01' header='0.05'" in out

    def test_status_clean_after_realigning_defaults(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        apply_run(dest, honor_status_allow=False)

        rc, out = _silent(_status.run, dest, check=True)
        assert rc == 0
        assert "DRIFT" not in out

    def test_status_json_includes_param_default_drift(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        _retune_manifest_default(dest, "0.01")
        apply_run(dest, honor_status_allow=False)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _status.run(dest, as_json=True)
        payload = json.loads(buf.getvalue())
        assert payload["param_default_drift"] == [
            {
                "object": "burst",
                "param": "carrier",
                "manifest_default": "0.01",
                "header_default": "0.05",
            }
        ]

    def test_drift_not_suppressed_by_allow(self, tmp_path):
        dest = _scaffold(tmp_path)
        _hand_document_create(dest)
        _retune_manifest_default(dest, "0.01")
        apply_run(dest, honor_status_allow=False)

        rc, _ = _silent(_status.run, dest, check=True, allow=("**/*.pyi",))
        assert rc >= 1
