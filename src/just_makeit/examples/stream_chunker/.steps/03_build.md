## 3. Build and test

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```
