from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
EXPECTED_DERIVATION_OUTPUT = (REPOSITORY / "tools/tests/expected.default.stdout").read_text(
    encoding="utf-8"
)


class MakeRunOutputTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "GNUMAKEFLAGS",
            "MAKEFLAGS",
            "MAKELEVEL",
            "MFLAGS",
            "VERBOSE",
        ):
            environment.pop(name, None)
        return subprocess.run(
            ["make", "run", *arguments],
            cwd=REPOSITORY,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_default_run_only_prints_derivation_output(self) -> None:
        completed = self._run()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertIn("Error 1", completed.stderr)

    def test_verbose_run_prints_all_commands_and_derivation_output(self) -> None:
        completed = self._run("VERBOSE=1")

        self.assertNotEqual(completed.returncode, 0)
        for command in (
            "make --no-print-directory -C tools run",
            "-m compileall -q src tests",
            "-m modelc.build_cache --repository",
            "-m derive.sequence_builder",
            "/tools/bin/derive ",
        ):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)
        self.assertTrue(completed.stdout.endswith(EXPECTED_DERIVATION_OUTPUT))
        self.assertIn("Error 1", completed.stderr)

    def test_only_exact_verbose_one_enables_command_echo(self) -> None:
        completed = self._run("VERBOSE=1 0")

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertIn("Error 1", completed.stderr)

    def test_empty_user_runtime_signal_program_yields_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signals = Path(directory) / "empty.signals"
            signals.write_text("", encoding="utf-8")
            completed = self._run(f"USER_RUNTIME_SIGNALS={signals}")

        self.assertEqual(completed.returncode, 0)
        self.assertTrue(completed.stdout.endswith("Derivation yielded!\n"))
        self.assertEqual(completed.stderr, "")

    def test_quiet_run_keeps_error_diagnostics_visible(self) -> None:
        completed = self._run("MODEL=missing-model.spec")

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error:", completed.stderr)
        self.assertIn("missing-model.spec", completed.stderr)
        self.assertNotIn("tools/bin/derive", completed.stderr)


if __name__ == "__main__":
    unittest.main()
