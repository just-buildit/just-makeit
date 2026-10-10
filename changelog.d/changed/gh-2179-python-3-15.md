- **Python 3.15 is supported** (gh-2179). It joins jm's CI test matrix on
    every OS, the pre-publish wheel smoke tests run on it, the package
    declares its classifier, and the CI `jm ci` generates for a project
    tests it too. A test now holds the classifiers and both pre-publish
    smoke matrices to the Pythons CI tests as released, so a promotion
    cannot land half done.
