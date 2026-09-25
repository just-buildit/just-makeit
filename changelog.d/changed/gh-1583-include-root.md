- **Internal: one owner of the header layout** (gh-1583, part 1 of 3). The
    include root and every `#include` of a jm-generated header -- about 75
    hand spellings across jm's modules and every C template -- now come from
    `_incpath`, so moving headers under the package is a flag rather than an
    edit at each. Generated projects are byte-identical to before.
