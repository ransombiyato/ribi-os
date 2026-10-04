#!/usr/bin/env python3
"""Ribi OS ISO builder -- thin launcher.

The implementation lives in the ``ribi`` package (see ``ribi/``). This entrypoint
preserves the original command-line interface of the single-file builder.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ribi.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
