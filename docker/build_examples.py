"""Build and test all bundled just-makeit examples into DEST.

Usage:
    python3 build_examples.py /path/to/dest

Each example's test.py run() function is called with DEST as the root, so
each scaffolded project lands at DEST/<project-name>/. After each example
builds successfully, pytest is run against all newly created project
directories so the generated Python test suites are exercised too.
"""

import importlib.util
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: build_examples.py <dest>", file=sys.stderr)
    sys.exit(1)

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)

from just_makeit._example import _EXAMPLES, _find


def _run_pytest(proj: Path) -> bool:
    """Run pytest against the generated Python test suite in proj/src/.

    The compiled extension .so/.pyd files land in src/<pkg>/ after the cmake
    build, so PYTHONPATH=src makes them importable without a pip install step.
    Returns True if pytest passed or if no compiled extension is present
    (unbuilt scaffold-only projects are skipped).
    """
    src_dir = proj / "src"
    if not src_dir.is_dir():
        return True
    # Only run if the C extension was actually compiled; scaffold-only
    # examples have no .so/.pyd and can't be imported.
    extensions = list(proj.rglob("*.so")) + list(proj.rglob("*.pyd"))
    if not extensions:
        print(
            f"    (no compiled extension in {proj.name}, skipping pytest)",
            flush=True,
        )
        return True
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(src_dir),
            "--tb=short",
            "-q",
            "--no-header",
        ],
        env=env,
        cwd=proj,
    )
    # 0 = passed; 5 = no tests collected (e.g. a functions-only module that
    # builds an extension but generates no pytest suite) — not a failure.
    return r.returncode in (0, 5)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from welcome import describe  # noqa: E402

# What each example actually put on disk, and what it is for. Collected as we
# go so the sandbox's welcome page can be generated from it rather than
# hand-listed — the hand-listed version advertised a `my_corr/` no example
# produces and omitted ~25 that exist.
#
# Recorded to a manifest here and RENDERED by `welcome.py` in the next Docker
# step, so this script observes and that one writes prose. The renderer is then
# pure enough for a test to drive it with a mapping no image contains — the
# only way to prove the page follows its input rather than the tree it runs in.
built: dict[str, list[str]] = {}
descriptions: dict[str, str] = {}

failed = []
for name in _EXAMPLES:
    ex_dir = _find(name)
    if ex_dir is None:
        print(f"  {name}: skipped (not found)", flush=True)
        continue
    descriptions[name] = describe(ex_dir)
    spec = importlib.util.spec_from_file_location(
        f"jm_ex_{name}", ex_dir / "test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"  [{name}] building...", flush=True)

    before = {d for d in dest.iterdir() if d.is_dir()}
    try:
        mod.run(dest)
        print(f"  [{name}] build+ctest PASSED", flush=True)
    except Exception:
        print(f"  [{name}] FAILED:", flush=True)
        traceback.print_exc()
        failed.append(name)
        continue

    # Run pytest against every project directory created by this example.
    after = {d for d in dest.iterdir() if d.is_dir()}
    built[name] = sorted(p.name for p in (after - before))
    for proj in sorted(after - before):
        print(f"  [{name}] pytest {proj.name}/src ...", flush=True)
        if _run_pytest(proj):
            print(f"  [{name}] pytest {proj.name} PASSED", flush=True)
        else:
            print(f"  [{name}] pytest {proj.name} FAILED", flush=True)
            failed.append(f"{name}:pytest:{proj.name}")

if failed:
    print(f"\nFailed: {', '.join(failed)}", file=sys.stderr)
    sys.exit(1)

# What was built, for `welcome.py --render` to turn into the README in a later
# layer. JSON rather than the finished page so the two concerns stay in the two
# layers that should own them.
manifest = dest.parent / ".jm-built.json"
manifest.write_text(
    json.dumps({"built": built, "descriptions": descriptions}, indent=2),
    encoding="utf-8",
)
n_projects = sum(len(v) for v in built.values())
print(f"  wrote {manifest} ({n_projects} projects)", flush=True)

print(f"\nAll {len(_EXAMPLES)} examples built and tested in {dest}")
