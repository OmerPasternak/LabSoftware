"""Launch the separate motor–camera scan window."""

import multiprocessing

from .gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
