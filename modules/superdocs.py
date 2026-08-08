"""modules.superdocs (deprecated)

The in-app Superdocs generator and viewer were removed in favour of the
online help site:

    https://docs.supervertaler.com/

This module remains as a tiny shim to prevent accidental imports in older
code paths.
"""


def __getattr__(name: str):
    raise ImportError(
        "The 'modules.superdocs' module has been removed. Use the online help at "
        "https://docs.supervertaler.com/"
    )


__all__: list[str] = []
