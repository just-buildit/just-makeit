- **`out=` and `<m>_max_out` for an array beside other params** (gh-1998).
    A `variable_output` method whose array param sits beside a scalar
    (`delay(x, mu)`) or another array (`execute_ctrl(x, ctrl)`) got no
    `out=` buffer and no `<m>_max_out()` -- the gh-412 carve-out -- so a
    project wanting either hand-wrote the binding and a `manual_stub`, and
    `jm adopt` refused the fragment as "binding ahead". Both are generated
    now, in the binding and both `.pyi` faces: `out` follows the params
    (`delay(x, mu, out=None)`), and the buffer is sized from the first array
    param, which is the length `<m>_max_out()` was already given and the
    allocation already fell back to. An interleaved first array
    (`elements_per_sample`, gh-1996) counts the buffer in samples, as the
    rest of the method does, and the overflow and returned-count errors of
    such a method now say "samples of N elements" where they used to say
    "elements" of a count in samples. A `manual_stub` entry for that
    `<m>_max_out` is now refused, with the instruction to drop it: the stub
    it declared is jm's own. Params beside an `arg_type` input still get no
    `out=`, since that parse drops them (gh-1960).
