"""Assemble README.md from .steps/*.md (and their embedded script files).

Usage:
    python3 assemble.py          # writes README.md next to this file
    python3 assemble.py --check  # exits non-zero if README.md is stale

Fenced-block syntax in step .md files:
    ```{filename}
    ```
The assembler replaces this with the file's content in a fence whose language
is inferred from the file extension (.sh→sh, .py→python, .c→c).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"
OUT = HERE / "README.md"

_EXT_LANG = {".sh": "sh", ".py": "python", ".c": "c", ".h": "c"}
_REF_RE = re.compile(r"```\{([^}]+)\}\s*\n```")


def _resolve(md: str, step_dir: Path) -> str:
    def _sub(m: re.Match) -> str:
        fname = m.group(1)
        fpath = step_dir / fname
        lang = _EXT_LANG.get(fpath.suffix, "")
        content = fpath.read_text(encoding="utf-8").rstrip("\n")
        return f"```{lang}\n{content}\n```"

    return _REF_RE.sub(_sub, md)


def assemble() -> str:
    parts: list[str] = []
    for md_file in sorted(STEPS.glob("*.md")):
        parts.append(_resolve(md_file.read_text(encoding="utf-8").rstrip(), STEPS))
    return "\n\n---\n\n".join(parts) + "\n"


def main() -> None:
    check = "--check" in sys.argv
    result = assemble()
    if check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != result:
            print("README.md is stale — run: python3 assemble.py", file=sys.stderr)
            sys.exit(1)
        print("README.md is up to date.")
    else:
        OUT.write_text(result, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
