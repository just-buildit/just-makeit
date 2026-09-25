- **`jm adopt`'s usage names every form it accepts** (gh-1569). The first
    line now reads `adopt --check [--module <id> | --all]`, in the command's
    own usage and in `jm --help`: `--check` surveys the whole project unless
    `--module` narrows it. The parser reads its options from that usage text,
    so an option cannot be accepted without being advertised. `adopt --check   <obj>` is refused: it used to ignore the name and survey every object.
