from __future__ import annotations

import unittest


class CompactTextTestResult(unittest.TextTestResult):
    """Keep verbose progress compact without changing failure descriptions."""

    def startTest(self, test: unittest.TestCase) -> None:
        unittest.TestResult.startTest(self, test)
        if self.showAll:
            self.stream.write(test._testMethodName)
            self.stream.write(" ... ")
            self.stream.flush()
            self._newline = False
