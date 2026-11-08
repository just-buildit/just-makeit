- **Scaffolded Python passes ruff's isort rule** (gh-2571). A project that
    selects `I` (doppler does) got `I001` on almost every generated file:
    the test put `import unittest`, `import numpy as np` and
    `from <pkg> import X` in one block, the benchmark glued `time` to numpy,
    both `.pyi` stubs glued `from typing` to numpy, and a package's
    re-exports kept the order the objects were added in. Every test,
    benchmark and stub now lists its imports in isort's sections (stdlib,
    third-party, the project's package, relative), one blank line apart,
    with the names in each `from` import sorted. In a package
    `__init__.py` jm sorts only its own relative re-exports, and leaves the
    author's imports as written. An existing project's files move to that
    order on its next `jm apply`.
