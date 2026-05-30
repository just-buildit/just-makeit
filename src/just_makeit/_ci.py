"""
_ci.py — `just-makeit ci` command.

Generate a CI workflow for a scaffolded project so it is CI-green as fast
as it builds and tests locally. The workflow mirrors the documented local
flow — `make` then `make test` (ctest + pytest/unittest) — on hosted
runners that already ship cmake and a C compiler; it only installs the
Python build/test deps.

Providers:
  - github     -> .github/workflows/ci.yml   (ubuntu + macos matrix)
  - woodpecker -> .woodpecker.yml

The file is written only if absent; pass --force to overwrite an existing
one (CI configs are commonly hand-tuned, so we don't clobber by default).
"""

import sys
from pathlib import Path

from . import _config as C
from . import _render as R

# provider -> (render-template attribute on _render, output path)
_PROVIDERS: dict[str, tuple[str, str]] = {
    "github": ("CI_GITHUB", ".github/workflows/ci.yml"),
    "woodpecker": ("CI_WOODPECKER", ".woodpecker.yml"),
}


def run(root: Path, provider: str = "github", force: bool = False) -> None:
    if provider not in _PROVIDERS:
        allowed = ", ".join(sorted(_PROVIDERS))
        print(
            f"error: unknown CI provider '{provider}'. Allowed: {allowed}.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    tmpl_attr, rel = _PROVIDERS[provider]
    dest = root / rel

    if dest.exists() and not force:
        print(
            f"just-makeit: {rel} already exists — leaving it untouched.\n"
            "Pass --force to overwrite it from the template."
        )
        return

    # pytest projects need pytest in CI; unittest-default projects don't.
    pip_deps = "numpy pytest" if C.is_pytest(cfg) else "numpy"
    ctx = {"package": C.project_name(cfg), "pip_deps": pip_deps}
    content = R.render(getattr(R, tmpl_attr), ctx)

    dest.parent.mkdir(parents=True, exist_ok=True)
    verb = "overwrite" if dest.exists() else "create"
    dest.write_text(content, encoding="utf-8")
    print(f"  {verb}  {dest}")
    print(
        f"Done!  {provider} CI workflow written. It runs `make && make test` "
        f"on every push and PR."
    )
