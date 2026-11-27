- **An interleaved array whose length is not a whole number of samples is
    refused** (gh-1999). `elements_per_sample = E` divided an array's
    element count into the kernel's sample count with nothing checking that
    it divides, so a 5-element `int16_t[]` with `E = 2` reached the kernel
    as 2 samples and the fifth element was dropped without a word. A method
    param, a module function param and a constructor `init_param` now
    raise a `ValueError` naming the param, its length and E before the
    kernel runs. The generated docstring examples pass whole samples
    (`4 * E` elements). An `out=` buffer is not held to the multiple:
    spare room there is legal.
