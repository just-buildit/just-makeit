import sys

sys.path.insert(0, "src")
from my_filter import Filter

f = Filter(gain=1.0)
assert f.step(2.0) == 2.0

f.configure(gain=0.5)
assert f.step(2.0) == 1.0

# positional also accepted (PyArg_ParseTupleAndKeywords handles both)
f.configure(2.0)
assert f.step(1.0) == 2.0

# no args: gain unchanged
f.configure()
assert f.step(1.0) == 2.0

print("configure: PASSED")
