- **A dispatched array takes `None` and other ranks as a plain one does**
    (gh-1826). `None` or a 2-D array passed to a `real_type`-dispatched init
    param raised `SystemError: ... returned a result with an exception set`:
    the dtype probe failed, left its error set, and construction carried
    on. The probe now only chooses the constructor; its failure is cleared,
    and the array is then acquired exactly as a plain array param of its
    declared type, so numpy decides what converts. Only a float32
    (`real_type`) 1-D ndarray reaches `real_create_fn`; a list of floats,
    and any input the probe cannot read, takes the default constructor.
