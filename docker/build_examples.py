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
import sys
import traceback
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: build_examples.py <dest>", file=sys.stderr)
    sys.exit(1)

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)

from just_makeit._example import _EXAMPLES, _find


# gh-1089: the implementation moved into the package so `make test-examples`
# can run the same check. It lived only here, and this script is not a required
# check — the image build was red on `main` for 14 consecutive runs while every
# PR gate stayed green. One implementation, two callers.
from just_makeit._build import run_generated_pytest as _run_pytest  # noqa: E402


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

    # Run pytest against every project this example created -- found by its
    # manifest, at any depth, as tests/test_examples.py finds them: the two
    # Visual Studio examples both scaffold `my_vs`, so each scaffolds it into
    # a folder named for itself (gh-2610).
    after = {d for d in dest.iterdir() if d.is_dir()}
    built[name] = sorted(p.name for p in (after - before))
    projects = sorted(
        m.parent for d in after - before for m in d.rglob("just-makeit.toml")
    )
    for proj in projects:
        rel = proj.relative_to(dest).as_posix()
        print(f"  [{name}] pytest {rel}/src ...", flush=True)
        if _run_pytest(proj):
            print(f"  [{name}] pytest {rel} PASSED", flush=True)
        else:
            print(f"  [{name}] pytest {rel} FAILED", flush=True)
            failed.append(f"{name}:pytest:{rel}")

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
