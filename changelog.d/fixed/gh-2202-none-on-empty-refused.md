- **`none_on_empty` is refused on a method with no empty result**
    (gh-2202). Only a `--variable-output` result and a `--borrow` read it;
    on a scalar, status, record or fixed-output method it was stored and
    did nothing. `jm method` and `apply` now refuse it there, naming the
    shapes that read it.
