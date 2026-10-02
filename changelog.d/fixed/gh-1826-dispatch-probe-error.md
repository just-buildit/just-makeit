- **A dispatched array refuses `None` and the wrong rank by name**
    (gh-1826). `None`, a 2-D array or a 0-d scalar passed to a
    `real_type`-dispatched init param raised
    `SystemError: ... returned a result with an exception set`: the dtype
    probe failed, left its error set, and construction carried on. The probe
    only chooses the constructor now, and the array's acquisition reports a
    bad argument: `ValueError: taps must be a 1-D array`. Only a float32
    (`real_type`) ndarray reaches `real_create_fn`; a list of floats takes
    the default constructor.
