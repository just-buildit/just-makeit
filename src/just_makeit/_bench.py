"""_bench.py — build and run C + Python benchmarks; save dated snapshots.

Workflow
--------
1. Build the project (cmake) so the C bench binaries and the Python
   extension are both current.
2. C side: run every ``bench_<comp>_core`` binary, collect its benchmark
   entries, and merge them into one pytest-benchmark-schema report.
3. Python side: run pytest-benchmark over ``src/`` and load its report.
4. Trim raw per-iteration arrays (``stats.data`` / ``stats.runtimes``)
   from both reports.  pytest-benchmark records every individual timing
   sample; left in, a single run bloats the JSON by orders of magnitude
   (100+ MB).  Only the summary statistics are kept.
5. Write dated snapshots to ``benchmarks/history/``::

       <tag>.json     Python benchmarks
       <tag>-c.json   C benchmarks

   where ``<tag>`` is a UTC timestamp (overridable with ``--tag``).
   Snapshots are immutable and meant to be committed, so perf history
   lives in git.
6. Pretty-print a stats table per side, with a Δ column versus the most
   recent earlier snapshot.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import _config as C


# ── build helpers ─────────────────────────────────────────────────────────────


def _require(exe: str) -> str:
    path = shutil.which(exe)
    if not path:
        print(f"error: '{exe}' not found on PATH.", file=sys.stderr)
        sys.exit(1)
    return path


def _project_python(root: Path) -> str:
    """Return the interpreter for the project's own virtualenv.

    Python benchmarks import the project's built extension and need its
    dependencies (numpy, pytest-benchmark), which live in the project's
    ``.venv`` — not in just-makeit's isolated tool environment, which is
    what ``sys.executable`` points at when jm is run as an installed
    tool.  Falls back to ``sys.executable`` when no project venv exists.
    """
    for rel in ("bin/python", "bin/python3", "Scripts/python.exe"):
        cand = root / ".venv" / rel
        if cand.is_file():
            return str(cand)
    return sys.executable


def _ensure_built(root: Path, build_dir: Path, python: str) -> None:
    """Configure (if needed) and build the project with cmake.

    Configures against *python* so the compiled extension is ABI-matched
    to the interpreter the Python benchmarks will run under.
    """
    cmake = _require("cmake")
    if not (build_dir / "CMakeCache.txt").exists():
        cfg = [
            cmake,
            "-B",
            str(build_dir),
            "-S",
            str(root),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={python}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
        if subprocess.run(cfg, cwd=str(root), timeout=600).returncode != 0:
            sys.exit(1)
    nproc = os.cpu_count() or 4
    build = [cmake, "--build", str(build_dir), "--parallel", str(nproc)]
    if subprocess.run(build, cwd=str(root), timeout=600).returncode != 0:
        sys.exit(1)


def _build_bench_target(root: Path, build_dir: Path, comp: str) -> None:
    """Build the ``bench_<comp>_core`` target (project already configured)."""
    cmake = _require("cmake")
    target = f"bench_{comp}_core"
    nproc = os.cpu_count() or 4
    cmd = [
        cmake,
        "--build",
        str(build_dir),
        "--target",
        target,
        "--parallel",
        str(nproc),
    ]
    r = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        # Surface the error verbosely so the user knows what went wrong.
        print(r.stderr, file=sys.stderr)
        sys.exit(r.returncode)


def _find_bench_binary(build_dir: Path, comp: str) -> Path | None:
    """Return the bench binary path, searching common cmake output spots.

    cmake places the binary in different locations depending on the
    generator and whether it was invoked from a sub-CMakeLists.txt;
    check the canonical spots first, then fall back to a recursive scan.
    """
    stem = f"bench_{comp}_core"
    candidates = [
        build_dir / stem,
        build_dir / f"{stem}.exe",
        build_dir / "Release" / stem,
        build_dir / "Release" / f"{stem}.exe",
        build_dir / "native" / "src" / comp / stem,
        build_dir / "native" / "src" / comp / f"{stem}.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in build_dir.rglob(stem):
        if p.is_file():
            return p
    return None


# ── environment metadata ──────────────────────────────────────────────────────


def _machine_info() -> dict:
    """Host description, mirroring the pytest-benchmark ``machine_info`` key."""
    info: dict = {
        "node": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu": {"count": os.cpu_count()},
    }
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"]["brand_raw"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return info


def _commit_info() -> dict:
    """Current git commit / branch / dirty flag, or {} outside a repo."""

    def _git(*args: str) -> str:
        return (
            subprocess.check_output(
                ["git", *args],
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
            .decode()
            .strip()
        )

    try:
        return {
            "id": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {}


# ── trim ──────────────────────────────────────────────────────────────────────


def _trim(report: dict) -> dict:
    """Drop raw per-iteration arrays from a pytest-benchmark-schema report.

    ``stats.data`` (and the older ``stats.runtimes``) hold every timing
    sample; they dominate the file size and carry no information the
    summary statistics do not.  Mutates and returns *report*.
    """
    for b in report.get("benchmarks", []):
        stats = b.get("stats")
        if isinstance(stats, dict):
            stats.pop("data", None)
            stats.pop("runtimes", None)
    return report


# ── run: C ─────────────────────────────────────────────────────────────────────


def _collect_c(root: Path, build_dir: Path, comps: list[str]) -> dict | None:
    """Build + run each component's bench binary; return one merged report.

    Each binary writes ``bench_<comp>_core.json`` to its working
    directory (see jm_bench.h).  Entry names are prefixed with the
    component so a merged history table is unambiguous.  Returns None
    when no component produced any benchmark.
    """
    benchmarks: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        for comp in comps:
            _build_bench_target(root, build_dir, comp)
            binary = _find_bench_binary(build_dir, comp)
            if binary is None:
                continue
            print(f"  run        bench_{comp}_core", flush=True)
            subprocess.run([str(binary.resolve())], cwd=tmp, timeout=600)
            jf = tmpd / f"bench_{comp}_core.json"
            if not jf.exists():
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                jf.unlink()
                continue
            for entry in data.get("benchmarks", []):
                entry["name"] = f"{comp}::{entry.get('name', '')}"
                benchmarks.append(entry)
            jf.unlink()

    if not benchmarks:
        return None
    return {
        "datetime": datetime.now(timezone.utc).isoformat(),
        "machine_info": _machine_info(),
        "commit_info": _commit_info(),
        "benchmarks": benchmarks,
    }


# ── run: Python ────────────────────────────────────────────────────────────────


def _has_pytest_benchmark(python: str) -> bool:
    return (
        subprocess.run(
            [python, "-c", "import pytest_benchmark"],
            capture_output=True,
            timeout=600,
        ).returncode
        == 0
    )


def _run_python(root: Path, python: str) -> dict | None:
    """Run pytest-benchmark over ``src/``; return its (untrimmed) report.

    Returns None when the pytest-benchmark plugin or any benchmark is
    absent — a project may legitimately ship only C benchmarks.
    """
    if not _has_pytest_benchmark(python):
        print(
            "  Python benchmarks: pytest-benchmark not installed "
            "in the project venv."
        )
        return None
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "py.json"
        cmd = [
            python,
            "-m",
            "pytest",
            "src/",
            "--benchmark-only",
            f"--benchmark-json={report}",
            "-q",
        ]
        print("  run        pytest --benchmark-only", flush=True)
        subprocess.run(cmd, cwd=str(root), timeout=600)
        # No JSON => no pytest-benchmark plugin, or no benchmarks collected.
        if not report.exists():
            return None
        try:
            return json.loads(report.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None


# ── history (dated snapshots) ──────────────────────────────────────────────────


def _history_dir(root: Path) -> Path:
    return root / "benchmarks" / "history"


def _tag() -> str:
    """Default snapshot tag: a sortable UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _snapshot_path(hdir: Path, tag: str, is_c: bool) -> Path:
    return hdir / (f"{tag}-c.json" if is_c else f"{tag}.json")


def _prev_snapshot(hdir: Path, tag: str, is_c: bool) -> dict | None:
    """Load the most recent snapshot older than *tag*, or None.

    Tags are timestamps, so lexicographic order is chronological; with a
    custom non-timestamp ``--tag`` the comparison degrades gracefully to
    "any earlier-sorting snapshot".
    """
    if not hdir.is_dir():
        return None
    suffix = "-c.json" if is_c else ".json"
    cands: list[Path] = []
    for f in hdir.glob(f"*{suffix}"):
        if not is_c and f.name.endswith("-c.json"):
            continue  # Python glob also matches the C snapshots.
        if f.name[: -len(suffix)] < tag:
            cands.append(f)
    if not cands:
        return None
    latest = max(cands, key=lambda p: p.name)
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_snapshot(
    root: Path, hdir: Path, tag: str, report: dict, is_c: bool
) -> None:
    hdir.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(hdir, tag, is_c)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  saved      {path.relative_to(root)}")


# ── regression gate (gh-141) ───────────────────────────────────────────────────

# Benchmarks whose baseline mean is below this are too jitter-prone on shared
# CI runners to gate on; they are compared and reported but never fail.
_BENCH_NOISE_FLOOR_SEC = 5e-7  # 500 ns


def _baseline_snapshot(
    hdir: Path, is_c: bool, tag: "str | None" = None
) -> "dict | None":
    """Load a baseline snapshot for `--check`: the named *tag* if given, else
    the most recent committed snapshot (latest by sortable timestamp name)."""
    if not hdir.is_dir():
        return None
    if tag is not None:
        p = _snapshot_path(hdir, tag, is_c)
        candidates = [p] if p.is_file() else []
    else:
        suffix = "-c.json" if is_c else ".json"
        candidates = [
            f
            for f in hdir.glob(f"*{suffix}")
            if is_c or not f.name.endswith("-c.json")
        ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.name)
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bench_key(b: dict) -> str:
    """Stable identity for a benchmark entry, for baseline↔current matching.

    pytest-benchmark entries carry a unique ``fullname``
    (``file.py::test[param]``); the bare ``name`` repeats across modules
    (e.g. several ``test_bench_execute_64k`` from different ``bench_*.py``).
    Keying on ``name`` alone collides them and compares unrelated benchmarks
    (gh-141 follow-up). The C side has no ``fullname`` and its ``name``
    (``comp::method``) is already unique, so fall back to it."""
    return b.get("fullname") or b["name"]


def _bench_metric(b: dict) -> float:
    """Wall-time used for regression comparison: the **min** sample.

    pytest-benchmark's `min` is the most stable cross-run statistic (best-case
    timing, least perturbed by scheduler jitter / noisy neighbours), so it is
    the right basis for regression detection — `mean` is far noisier on shared
    CI runners. Falls back to `mean` when `min` is absent."""
    s = b["stats"]
    return s["min"] if "min" in s else s["mean"]


def _compare_reports(
    current: "dict | None",
    baseline: "dict | None",
    threshold: float,
    floor_sec: float = _BENCH_NOISE_FLOOR_SEC,
    allow: "set[str] | None" = None,
) -> list[dict]:
    """Per-benchmark regression comparison (pure; no I/O).

    Compares each benchmark's mean wall-time against the baseline. A higher
    mean is slower; ``delta_pct`` > ``threshold*100`` is a regression. Returns
    one record per current benchmark with ``status`` in:
      - ``regressed``    slower than baseline by more than the threshold
      - ``ok``           within threshold
      - ``new``          no baseline entry for this name
      - ``allowed``      name in *allow* — reported, never fails
      - ``below_floor``  baseline mean < ``floor_sec`` — too noisy to gate
    """
    allow = allow or set()
    base = {
        _bench_key(b): _bench_metric(b)
        for b in (baseline or {}).get("benchmarks", [])
    }
    out: list[dict] = []
    for b in (current or {}).get("benchmarks", []):
        key = _bench_key(b)
        name = b["name"]
        cur = _bench_metric(b)
        if key not in base:
            out.append(
                {
                    "name": name,
                    "id": key,
                    "baseline_ns": None,
                    "current_ns": cur * 1e9,
                    "delta_pct": None,
                    "status": "new",
                }
            )
            continue
        bm = base[key]
        delta = (cur - bm) / bm if bm else 0.0
        if name in allow or key in allow:
            status = "allowed"
        elif bm < floor_sec:
            status = "below_floor"
        elif delta > threshold:
            status = "regressed"
        else:
            status = "ok"
        out.append(
            {
                "name": name,
                "id": key,
                "baseline_ns": bm * 1e9,
                "current_ns": cur * 1e9,
                "delta_pct": delta * 100.0,
                "status": status,
            }
        )
    return out


# ── formatting helpers ────────────────────────────────────────────────────────


def _pick_unit(values: list[float]) -> str:
    mn = min(values) if values else 1e-6
    if mn >= 1.0:
        return "s"
    if mn >= 1e-3:
        return "ms"
    if mn >= 1e-6:
        return "μs"
    return "ns"


def _fmt_time(sec: float, unit: str) -> str:
    if unit == "ns":
        return f"{sec * 1e9:.1f} ns"
    if unit == "μs":
        return f"{sec * 1e6:.1f} μs"
    if unit == "ms":
        return f"{sec * 1e3:.1f} ms"
    return f"{sec:.4f} s"


def _fmt_ops(ops: float) -> str:
    if ops >= 1e9:
        return f"{ops / 1e9:.2f} GSa/s"
    if ops >= 1e6:
        return f"{ops / 1e6:,.0f} MSa/s"
    return f"{ops / 1e3:,.0f} kSa/s"


# ── table display ─────────────────────────────────────────────────────────────


def _display_table(label: str, data: dict, prev: dict | None) -> None:
    benches = data.get("benchmarks", [])
    if not benches:
        print(f"\n  {label}: no benchmarks recorded.")
        return

    # Choose a consistent time unit from the fastest min.
    unit = _pick_unit([b["stats"]["min"] for b in benches])

    # Previous ops keyed by benchmark name, for the Δ column.
    prev_ops: dict[str, float] = {}
    if prev:
        for b in prev.get("benchmarks", []):
            prev_ops[b["name"]] = b["stats"]["ops"]

    rows: list[dict[str, str]] = []
    for b in benches:
        s = b["stats"]
        row: dict[str, str] = {
            "name": b["name"],
            "min": _fmt_time(s["min"], unit),
            "max": _fmt_time(s["max"], unit),
            "mean": _fmt_time(s["mean"], unit),
            "stddev": _fmt_time(s["stddev"], unit),
            "median": _fmt_time(s["median"], unit),
            "ops": _fmt_ops(s["ops"]),
        }
        if b["name"] in prev_ops and prev_ops[b["name"]]:
            delta = (s["ops"] - prev_ops[b["name"]]) / prev_ops[b["name"]]
            row["delta"] = f"{delta * 100.0:+.1f}%"
        rows.append(row)

    has_delta = any("delta" in r for r in rows)
    headers = ["Name", "Min", "Max", "Mean", "StdDev", "Median", "Throughput"]
    keys = ["name", "min", "max", "mean", "stddev", "median", "ops"]
    if has_delta:
        headers.append("Δ vs prev")
        keys.append("delta")

    widths = [len(h) for h in headers]
    for r in rows:
        for i, k in enumerate(keys):
            widths[i] = max(widths[i], len(r.get(k, "")))

    sep = "  "
    hline = sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    rule = "─" * len(hline)

    mi = data.get("machine_info", {})
    sys_str, node_str = mi.get("system", ""), mi.get("node", "")
    info = f"  ({sys_str} / {node_str})" if sys_str and node_str else ""

    print(f"\n=== {label}{info} ===")
    print(f"  {hline}")
    print(f"  {rule}")
    for r in rows:
        print(
            "  "
            + sep.join(
                r.get(k, "").ljust(widths[i]) for i, k in enumerate(keys)
            )
        )
    print()


# ── public entry point ────────────────────────────────────────────────────────


def run(
    root: Path,
    components: list[str] | None = None,
    build_dir: Path | None = None,
    tag: str | None = None,
    do_c: bool = True,
    do_python: bool = True,
    check: bool = False,
    threshold: float = 0.10,
    baseline: str | None = None,
    as_json: bool = False,
    allow: tuple[str, ...] = (),
) -> None:
    """Build, benchmark, and snapshot the project at *root*.

    Parameters
    ----------
    root : Path
        Project root (must contain just-makeit.toml).
    components : list[str] or None
        C components to bench.  None → all standalone components.
    build_dir : Path or None
        cmake build directory.  None → ``root / "build"``.
    tag : str or None
        Snapshot tag.  None → a UTC timestamp.
    do_c, do_python : bool
        Select which benchmark sides to run.
    check : bool
        Regression-gate mode (gh-141): compare against a baseline snapshot and
        exit non-zero on regression, instead of saving a new snapshot.
    threshold : float
        Fractional slowdown that counts as a regression (0.10 = 10%).
    baseline : str or None
        Baseline snapshot tag.  None → the most recent committed snapshot.
    as_json : bool
        In check mode, emit the comparison as JSON.
    allow : tuple[str, ...]
        Benchmark names exempt from the gate (reported, never fail).
    """
    cfg = C.load(root)
    if not cfg:
        print("error: no just-makeit.toml found.", file=sys.stderr)
        sys.exit(1)

    all_comps = C.components(cfg)
    if components:
        unknown = [c for c in components if c not in all_comps]
        if unknown:
            print(
                f"error: unknown component(s): {', '.join(unknown)}",
                file=sys.stderr,
            )
            sys.exit(1)
        target_comps = list(components)
    else:
        target_comps = list(all_comps)

    bdir = build_dir or (root / "build")
    tag = tag or _tag()
    hdir = _history_dir(root)
    python = _project_python(root)

    # One build covers the C bench binaries and the Python extension.
    print("  build      project", flush=True)
    _ensure_built(root, bdir, python)

    if check:
        _run_check(
            root,
            bdir,
            target_comps,
            python,
            do_c,
            do_python,
            hdir,
            threshold,
            baseline,
            as_json,
            set(allow),
        )
        return

    if do_c:
        creport = _collect_c(root, bdir, target_comps)
        if creport:
            _trim(creport)
            prev = _prev_snapshot(hdir, tag, is_c=True)
            _save_snapshot(root, hdir, tag, creport, is_c=True)
            _display_table("C benchmarks", creport, prev)
        else:
            print("  C benchmarks: none found.")

    if do_python:
        preport = _run_python(root, python)
        if preport and preport.get("benchmarks"):
            _trim(preport)
            prev = _prev_snapshot(hdir, tag, is_c=False)
            _save_snapshot(root, hdir, tag, preport, is_c=False)
            _display_table("Python benchmarks", preport, prev)
        else:
            print("  Python benchmarks: none found.")


def _run_check(
    root: Path,
    bdir: Path,
    target_comps: list[str],
    python: str,
    do_c: bool,
    do_python: bool,
    hdir: Path,
    threshold: float,
    baseline: str | None,
    as_json: bool,
    allow: set[str],
) -> None:
    """Compare current benchmarks against a baseline snapshot and exit
    non-zero on regression. Does not save a snapshot (a gate is not a record).
    """
    sides: list[tuple[str, dict | None, dict | None]] = []
    if do_c:
        cur = _collect_c(root, bdir, target_comps)
        if cur:
            _trim(cur)
            sides.append(("C", cur, _baseline_snapshot(hdir, True, baseline)))
    if do_python:
        cur = _run_python(root, python)
        if cur and cur.get("benchmarks"):
            _trim(cur)
            sides.append(
                ("Python", cur, _baseline_snapshot(hdir, False, baseline))
            )

    rows: list[dict] = []
    missing_baseline: list[str] = []
    for label, cur, base in sides:
        if base is None:
            missing_baseline.append(label)
            continue
        for r in _compare_reports(cur, base, threshold, allow=allow):
            r["side"] = label
            rows.append(r)

    if as_json:
        print(
            json.dumps(
                {
                    "threshold_pct": threshold * 100.0,
                    "baseline": baseline or "latest",
                    "missing_baseline": missing_baseline,
                    "results": rows,
                },
                indent=2,
            )
        )
    else:
        for label in missing_baseline:
            print(
                f"  {label}: no baseline snapshot found — run `jm bench` "
                "and commit it first; skipping gate."
            )
        regressed = [r for r in rows if r["status"] == "regressed"]
        for r in rows:
            if r["status"] in ("regressed", "new"):
                d = (
                    f"{r['delta_pct']:+.1f}%"
                    if r["delta_pct"] is not None
                    else "new"
                )
                label = r.get("id") or r["name"]
                print(f"  [{r['status']}] {r['side']}:{label}  {d}")
        n = len(rows)
        if not regressed:
            print(
                f"OK — no regression > {threshold * 100:.0f}% "
                f"({n} benchmark(s) checked)."
            )
        else:
            print(
                f"REGRESSION — {len(regressed)} benchmark(s) slower than "
                f"baseline by > {threshold * 100:.0f}%."
            )

    if any(r["status"] == "regressed" for r in rows):
        sys.exit(1)
