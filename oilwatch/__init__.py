"""OilWatch package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__", "package_version"]


def package_version() -> str:
    """The installed distribution's version, or a marker when run from source.

    One source, because there were three that disagreed: a literal here, another
    in ``pyproject.toml``, and whichever one the MCP framework supplied for a
    server that never declared its own.
    """
    try:
        return version("oilwatch")
    except PackageNotFoundError:
        return "0.0.0+source"


__version__ = package_version()
