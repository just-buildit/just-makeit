- **A package's `__all__` names only what its import lines bind**
    (gh-2376). In a package two modules share (gh-523 `package =`), a
    `reexports` entry naming the sibling module cut that sibling's
    `from .<sibling> import` line back to the declared names, while
    `__all__` kept the sibling's other names. Since gh-2372 those included
    its published enums, so `from pkg import *` raised `AttributeError`. A
    re-export of a sibling now only adds to the sibling's line, so its
    enums are importable from the package.
