- **A function app's C tool and Python script agree on its flags**
    (gh-2560). The C tool started every parameter at zero and enforced
    none, so a run missing a parameter called the function with 0. It also
    ignored a declared default (gh-240). The Python faces, meanwhile,
    required every flag, even a defaulted one. Both now read the flag
    model constructor flags use. A parameter without a default is required
    on both faces: the C tool exits 2 with `--x is required`, as argparse
    does. A defaulted one starts at its default on both. One defaulted to
    a C constant (`INFINITY`) starts there in C, and the Python call passes
    it only when given. The Python faces call the function as
    `result = f(...)` then `print(result)`.
