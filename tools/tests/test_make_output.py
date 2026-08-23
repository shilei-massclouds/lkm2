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


class MakeDeriveOutputTests(unittest.TestCase):
    def _make(self, target: str, *arguments: str) -> subprocess.CompletedProcess[str]:
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
            ["make", target, *arguments],
            cwd=REPOSITORY,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def _derive(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self._make("derive", *arguments)

    def test_default_derive_only_prints_derivation_output(self) -> None:
        completed = self._derive()

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertEqual(completed.stderr, "")

    def test_verbose_derive_prints_all_commands_and_derivation_output(self) -> None:
        completed = self._derive("VERBOSE=1")

        self.assertEqual(completed.returncode, 0)
        for command in (
            "make --no-print-directory -C tools run",
            "-m compileall -q src tests",
            "-m modelc.build_cache --repository",
            "-m derive.sequence_builder",
            "/tools/bin/derive ",
            '--user-runtime-signals "'
            + str(REPOSITORY / "tools/signals/parked.signals")
            + '"',
        ):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)
        self.assertTrue(completed.stdout.endswith(EXPECTED_DERIVATION_OUTPUT))
        self.assertEqual(completed.stderr, "")

    def test_only_exact_verbose_one_enables_command_echo(self) -> None:
        completed = self._derive("VERBOSE=1 0")

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertEqual(completed.stderr, "")

    def test_absolute_custom_signal_program_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signals = Path(directory) / "custom.signals"
            signals.write_text("syscall.exit <local> 0\n", encoding="utf-8")
            completed = self._derive(f"USER_RUNTIME_SIGNALS={signals}")

        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(completed.stdout.endswith("stopped: panic\n"))
        self.assertIn("Attempted to kill init!", completed.stdout)
        self.assertIn("Error 1", completed.stderr)

    def test_explicit_empty_signal_variable_uses_in_memory_default(self) -> None:
        completed = self._derive("VERBOSE=1", "USER_RUNTIME_SIGNALS=")

        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("--user-runtime-signals", completed.stdout)
        self.assertIn("Attempted to kill init!", completed.stdout)
        self.assertTrue(completed.stdout.endswith("stopped: panic\n"))
        self.assertIn("Error 1", completed.stderr)

    def test_quiet_derive_keeps_error_diagnostics_visible(self) -> None:
        completed = self._derive("MODEL=missing-model.spec")

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error:", completed.stderr)
        self.assertIn("missing-model.spec", completed.stderr)
        self.assertNotIn("tools/bin/derive", completed.stderr)

    def test_root_run_is_silent_no_op(self) -> None:
        completed = self._make(
            "run",
            "VERBOSE=1",
            "TOOLS_MAKE=false",
            "MODEL=missing-model.spec",
            "SEQUENCE=missing-sequence.json",
            "USER_RUNTIME_SIGNALS=missing-signals.txt",
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
