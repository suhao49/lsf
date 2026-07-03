"""PyInstaller entry point -- mirrors the `lsf` console script."""
import sys

from lsf.scheduler import main

if __name__ == "__main__":
    sys.exit(main())
