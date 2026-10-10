- **A manifest `doc` that ends in a doctest no longer breaks the stub's
    doctest run** (gh-2176). A text-mode `.pyi` doctest
    (`pytest --doctest-glob='*.pyi'`) reads an example's expected output up
    to the next blank line, and the closing quotes came straight after the
    last line. The example then swallowed the quotes and the declaration
    after them: a parse error for the whole file, or an example that could
    never match. This is gh-691's failure on the path gh-2059 opened, where
    an `extra_methods` row's numpy `doc` can end in `Examples`. Every
    layout of an authored `doc` now leaves a blank line before the closing
    quotes when the text ends inside an example: a member's, a handle
    method's, and a module's. Every other `doc` renders exactly as before.
