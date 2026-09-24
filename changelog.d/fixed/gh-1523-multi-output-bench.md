- **A multi-output method's scaffolded benchmark builds** (gh-1523).
    gh-600 made each extra `--multi-output` value a trailing `<T> *outN`
    parameter, and the binding passes one, but the generated C benchmark
    still called the method without them. So `jm method w m --param x:double --multi-output int` exited 0 and left a tree whose
    `cmake --build` failed with `too few arguments`. The benchmark now
    declares a local per extra output and passes its address, as the
    binding does. A new stub-conformance shape builds a param, an array
    and a void-return multi-output method with their benchmark.
