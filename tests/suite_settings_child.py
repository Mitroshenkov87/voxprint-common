"""Write one suite.json key from a second process."""

from __future__ import annotations

import sys

from voxprint_common.suite_settings import set_value


def main() -> None:
    """Store ``argv[1]`` as a bool or as text."""
    key = sys.argv[1]
    raw = sys.argv[2]
    if raw == "true":
        value: object = True
    elif raw == "false":
        value = False
    else:
        value = raw
    set_value(key, value)


if __name__ == "__main__":
    main()
