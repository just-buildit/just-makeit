"""_bench.py — build and run C benchmarks, display results as a table.

Workflow
--------
1. Configure + build just the bench target(s) via cmake.
2. Run each bench binary from the project root; it writes
   bench_<comp>_core.json to CWD (see jm_bench.h).
3. Pretty-print a pytest-benchmark-style ASCII table.
4. Save result to .benchmarks/c/<comp>.json; the previous run is kept
   as .benchmarks/c/<comp>.prev.json so successive calls automatically
   show a Δ column.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import _config as C


# ── build helpers ─────────────────────────────────────────────────────────────

def _require(exe: str) -> str:
    path = shutil.which(exe)
    if not path:
        print(f"error: '{exe}' not found on PATH.", file=sys.stderr)
        sys.exit(1)
    return path


def _cmake_configure(root: Path, build_dir: Path) -> None:
    cmake = _require("cmake")
    cmd = [
        cmake,
        "-B", str(build_dir),
        "-S", str(root),
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DPython3_EXECUTABLE={sys.executable}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    r = subprocess.run(cmd, cwd=str(root))
    if r.returncode != 0:
        sys.exit(r.returncode)


def _build_bench_target(root: Path, build_dir: Path, comp: str) -> None:
    """Configure (if needed) then build the bench target for one component."""
    cmake = _require("cmake")
    if not (build_dir / "CMakeCache.txt").exists():
        print(f"  configure  {build_dir.name}/", flush=True)
        _cmake_configure(root, build_dir)

    target = f"bench_{comp}_core"
    nproc = os.cpu_count() or 4
    cmd = [
        cmake, "--build", str(build_dir),
        "--target", target,
        "--parallel", str(nproc),
    ]
    print(f"  build      {target}", flush=True)
    r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        # Surface the error verbosely so the user knows what went wrong.
        print(r.stderr, file=sys.stderr)
        sys.exit(r.returncode)


def _find_bench_binary(build_dir: Path, comp: str) -> Path | None:
    """Return the bench binary path, searching common cmake output locations.

    cmake places the binary in different spots depending on the generator
    and whether cmake was invoked from a sub-CMakeLists.txt.  Check the
    canonical locations first, then fall back to a recursive search.
    """
    stem = f"bench_{comp}_core"
    candidates = [
        build_dir / stem,
        build_dir / f"{stem}.exe",
        build_dir / "Release" / stem,
        build_dir / "Release" / f"{stem}.exe",
        # Sub-CMakeLists.txt puts the binary under native/src/<comp>/
        build_dir / "native" / "src" / comp / stem,
        build_dir / "native" / "src" / comp / f"{stem}.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    # Recursive fallback for unusual generator layouts.
    for p in build_dir.rglob(stem):
        if p.is_file():
            return p
    return None


# ── run + JSON ────────────────────────────────────────────────────────────────

def _run_bench_binary(root: Path, binary: Path, comp: str) -> dict:
    """Run the bench binary (from root) and return parsed JSON."""
    json_path = root / f"bench_{comp}_core.json"
    if json_path.exists():
        json_path.unlink()

    r = subprocess.run([str(binary)], cwd=str(root))
    if r.returncode != 0:
        sys.exit(r.returncode)

    if not json_path.exists():
        print(
            f"error: bench binary did not write {json_path.name}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    json_path.unlink()  # remove from project root; we'll store it properly below
    return data


# ── history (save / load prev) ────────────────────────────────────────────────

def _history_dir(root: Path) -> Path:
    return root / ".benchmarks" / "c"


def _load_prev(root: Path, comp: str) -> dict | None:
    """Return the most-recent saved result for comp, or None if not yet saved."""
    cur = _history_dir(root) / f"{comp}.json"
    if cur.exists():
        return json.loads(cur.read_text(encoding="utf-8"))
    return None


def _save_result(root: Path, comp: str, data: dict) -> None:
    hdir = _history_dir(root)
    hdir.mkdir(parents=True, exist_ok=True)
    cur = hdir / f"{comp}.json"
    prev = hdir / f"{comp}.prev.json"
    if cur.exists():
        cur.replace(prev)
    cur.write_text(json.dumps(data, indent=2), encoding="utf-8")


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

def _display_table(comp: str, data: dict, prev: dict | None) -> None:
    benches = data.get("benchmarks", [])
    if not benches:
        print(f"\n  {comp}: no benchmarks recorded.")
        return

    # Choose a consistent time unit for the table based on the fastest min.
    unit = _pick_unit([b["stats"]["min"] for b in benches])

    # Build a lookup of previous ops values keyed by benchmark name.
    prev_ops: dict[str, float] = {}
    if prev:
        for b in prev.get("benchmarks", []):
            prev_ops[b["name"]] = b["stats"]["ops"]

    # Assemble rows.
    rows: list[dict[str, str]] = []
    for b in benches:
        s = b["stats"]
        row: dict[str, str] = {
            "name":   f"{b['name']}()",
            "min":    _fmt_time(s["min"],    unit),
            "max":    _fmt_time(s["max"],    unit),
            "mean":   _fmt_time(s["mean"],   unit),
            "stddev": _fmt_time(s["stddev"], unit),
            "median": _fmt_time(s["median"], unit),
            "iqr":    _fmt_time(s["iqr"],    unit),
            "ops":    _fmt_ops(s["ops"]),
        }
        if b["name"] in prev_ops:
            delta = (s["ops"] - prev_ops[b["name"]]) / prev_ops[b["name"]] * 100.0
            row["delta"] = f"{delta:+.1f}%"
        rows.append(row)

    has_delta = any("delta" in r for r in rows)
    headers = ["Name", "Min", "Max", "Mean", "StdDev", "Median", "IQR", "Throughput"]
    keys    = ["name", "min", "max", "mean", "stddev", "median", "iqr", "ops"]
    if has_delta:
        headers.append("Δ vs prev")
        keys.append("delta")

    widths = [len(h) for h in headers]
    for r in rows:
        for i, k in enumerate(keys):
            widths[i] = max(widths[i], len(r.get(k, "")))

    sep    = "  "
    hline  = sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    rule   = "─" * len(hline)

    s0     = benches[0]["stats"]
    iters  = s0.get("iterations", 0)
    rounds = s0.get("rounds", 0)
    mi     = data.get("machine_info", {})
    sys_str = mi.get("system", "")
    node_str = mi.get("node", "")
    info   = f"  ({sys_str} / {node_str})" if sys_str and node_str else ""

    print(f"\n=== {comp} benchmark{info} ===")
    print(f"block = {iters:,} samples  ·  {rounds} rounds\n")
    print(f"  {hline}")
    print(f"  {rule}")
    for r in rows:
        print(
            "  "
            + sep.join(r.get(k, "").ljust(widths[i]) for i, k in enumerate(keys))
        )
    print()


# ── public entry point ────────────────────────────────────────────────────────

def run(
    root: Path,
    components: list[str] | None = None,
    build_dir: Path | None = None,
) -> None:
    """Build and run C benchmarks for the project at *root*.

    Parameters
    ----------
    root : Path
        Project root (must contain just-makeit.toml).
    components : list[str] or None
        Components to bench.  None → all standalone components.
    build_dir : Path or None
        cmake build directory.  None → root / "build".
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

    if not target_comps:
        print("error: no standalone components found in just-makeit.toml.",
              file=sys.stderr)
        sys.exit(1)

    bdir = build_dir or (root / "build")

    for comp in target_comps:
        _build_bench_target(root, bdir, comp)
        binary = _find_bench_binary(bdir, comp)
        if binary is None:
            print(
                f"error: could not find bench_{comp}_core binary in {bdir}",
                file=sys.stderr,
            )
            sys.exit(1)

        prev = _load_prev(root, comp)
        data = _run_bench_binary(root, binary, comp)
        _save_result(root, comp, data)
        _display_table(comp, data, prev)
