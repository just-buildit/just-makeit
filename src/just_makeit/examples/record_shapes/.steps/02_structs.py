"""Declare the two record structs in the sacred header.

They are the author's, not jm's: jm writes prototypes that mention them and
never reads a field. Adding them BEFORE declaring the methods is what lets
`--return-type evlog_summary_t` and `--record-dtype evlog_rec_t` resolve.
"""

from pathlib import Path

HEADER = Path("native/inc/collector/collector_core.h")

STRUCTS = """\
/* One row of the log — the element type of what read() hands back. */
typedef struct
{
    uint64_t t;
    double   v;
} evlog_rec_t;

/* The whole-log summary — ONE of these, returned by value. */
typedef struct
{
    uint64_t n;
    double   mean;
} evlog_summary_t;

/* One peak. Every shape names a struct of yours; only the KEY differs. */
typedef struct
{
    size_t index;
    double value;
} evlog_peak_t;

"""


def main() -> None:
    text = HEADER.read_text(encoding="utf-8")
    if "evlog_rec_t" in text:
        return
    # Ahead of the generated state struct, which is the first `typedef
    # struct {` in the file.
    anchor = text.index("typedef struct {")
    HEADER.write_text(
        text[:anchor] + STRUCTS + text[anchor:], encoding="utf-8"
    )


if __name__ == "__main__":
    main()
