"""Allows `python -m pbix2html ...` as a PATH-independent fallback for the `pbix2html` command."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
