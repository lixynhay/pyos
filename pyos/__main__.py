"""Позволяет запускать пакет как ``python -m pyos``."""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
