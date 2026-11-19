- **A scalar `out` param jm cannot bind is refused at load (gh-2683).** On
    a method it was accepted and bound as an input; it is refused now,
    naming the function-only support (gh-2685 tracks methods). On a module
    function an out that is an enum, `path`, `bytes`, capsule or string, or
    has a `default`, and a non-void return without `check_return`, are
    refused with the sentence saying what to declare instead.
