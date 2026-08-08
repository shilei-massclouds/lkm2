from __future__ import annotations

import argparse
from pathlib import Path
import unittest

from _compact_result import CompactTextTestResult


TESTS_DIRECTORY = Path(__file__).resolve().parent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run model tool unit tests")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()
    suite = unittest.TestLoader().discover(TESTS_DIRECTORY)
    result = unittest.TextTestRunner(
        verbosity=1 if arguments.quiet else 2,
        resultclass=CompactTextTestResult,
    ).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
