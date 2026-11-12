"""The <<package>> package as a whole: it imports, and states its version.

just-makeit writes this test with the project, so ``make test`` runs at least
one test before any component exists -- an empty suite passes and proves
nothing, and unittest on Python 3.12+ fails it outright. Each component adds
its own ``test_<component>.py`` beside this one.
"""

import unittest

import <<package>>

#: ``[project] version`` in just-makeit.toml. ``jm config version`` rewrites
#: this line together with ``__version__`` in the package's ``__init__.py``.
MANIFEST_VERSION = "<<version>>"


class TestProject(unittest.TestCase):
<<version_test>>