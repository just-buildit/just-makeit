- **A documented struct member called `name` no longer fails a manifest write**
    (gh-1932). The declared-name walk read every `name` key in the config,
    including inside `_doc_blocks`, the derived table of Doxygen text for C
    struct members, so a member `name` with the comment `e.g. "agc.gain_db".`
    was refused as a name and `jm adopt` exited 1 with nothing written. The
    walk now skips `_`-prefixed runtime state, the way `_strip_private`
    already does; a genuinely bad declared name is still refused.
