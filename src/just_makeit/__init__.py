"""just-makeit — Python C extensions the easy way."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("just-makeit")
except PackageNotFoundError:
    __version__ = "unknown"
