## 6. Run the tests

| Visual Studio                              | Command line                     |
| ------------------------------------------ | -------------------------------- |
| **Test > Test Explorer**, then **Run All** | `ctest --preset windows-release` |
| **View > Terminal**, then the line below   | the Python suite                 |

Test Explorer lists the CTest tests the build registered. The same run:

```{06_ctest.ps1}
```

The Python suite imports the `.pyd` from `src\`, through the `.venv`
interpreter:

```{07_python.ps1}
```

The generated tests run under `unittest` as written; with pytest installed in
the `.venv`, `.venv\Scripts\python -m pytest src` runs them too.

If the configure step fails with `Could NOT find Python3` and names
`NumPy`, the `.venv` has no numpy: run the last line of step 3 again, then
**Project > Delete Cache and Reconfigure**.
