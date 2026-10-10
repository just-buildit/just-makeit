- **A release's explicit count of `0` reaches C as `0`, instead of releasing
    the whole outstanding borrow** (gh-2191). The generated release tested
    the parsed value, and `0` was both its "omitted" default and a count a
    caller can mean, so `peek(64); consume(0)` released 64 samples nobody
    had processed (doppler-dsp/doppler#2014), and `consume(0)` with nothing
    outstanding raised. Omitted is now decided by whether the argument was
    passed, positionally or by keyword. A bare `consume()` still takes the
    borrow's count, and still raises `RuntimeError` with nothing
    outstanding.
