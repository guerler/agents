"""The harness. Its modules read `settings` and `subject` from the repository root, put on the path here."""

import pathlib
import sys

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
