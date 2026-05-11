## 6. Development install

```{06_dev_install.sh}
```

`pip install -e .` installs the package in editable mode using the
[just-buildit](https://github.com/just-buildit/just-buildit) PEP 517 backend.
The `.so` built in step 5 is used directly — no rebuild needed when you only
edit Python files.

After this, `from iqfile.conv import Cf32ToQ15, Q15ToCf32` works from anywhere.
