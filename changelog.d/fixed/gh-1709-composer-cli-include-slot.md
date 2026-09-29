- **A composer's c-face CLI can be generated again in a project using the
    `native/inc/<pkg>/` layout** (found while fixing gh-1709). The CLI copies
    `jm app`'s sample-type block, and the block's `#include` slot was left
    unfilled, so `apply` refused to write `<module>_cli.c`.
