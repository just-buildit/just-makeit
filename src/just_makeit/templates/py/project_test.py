"""The <<package>> package as a whole: it imports, and states its version.

just-makeit writes this test with the project, so ``make test`` runs at least
one test before any component exists -- an empty suite passes and proves
nothing, and a newer unittest fails it outright. Each component adds
its own ``test_<component>.py`` beside this one.
"""

import unittest

import <<package>>
<<version_const>>

class TestProject(unittest.TestCase):
<<version_test>>