- **`jm app` builds and runs for a required or `default_raw` init
    param** (gh-2557). A constructor flag took its default from `default`
    alone, so the C tool declared `size_t size = ;` (it did not compile) and
    the console and PEP 723 scripts passed `noise_hi=None` to the
    constructor whenever the flag was not given. Now a required param is a
    flag the run must be given (`required=True` in Python; the C tool exits
    2 with `--size is required`, as argparse does). A `default_raw` param
    starts at that expression in C (with `<math.h>` in scope, as it is in
    the binding). A param whose default Python cannot spell is left out of
    the Python call unless the flag is given, so the binding applies its
    own default.
