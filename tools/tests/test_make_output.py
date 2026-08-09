from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
EXPECTED_DERIVATION_OUTPUT = """\
Human -> Computer: drives Transition::Preset
  current state: State::Base
  Computer -> QemuVirtPlatform: drives Transition::Preset
    current state: State::Base
    commit state: State::Prepared
  Computer -> OpenSBI: drives Transition::Preset
    current state: State::Base
    commit state: State::Prepared
  Computer -> Kernel: drives Transition::Preset
    current state: State::Base
    commit state: State::Prepared
  Computer -> RootFs: drives Transition::Preset
    current state: State::Base
    commit state: State::Prepared
  commit state: State::Prepared

Human -> Computer: drives Transition::Setup
  current state: State::Prepared
  Computer -> QemuVirtPlatform: drives Transition::Setup
    current state: State::Prepared
    commit state: State::Ready
  Computer -> OpenSBI: drives Transition::Setup
    current state: State::Prepared
    commit state: State::Ready
  Computer -> Kernel: drives Transition::Setup
    current state: State::Prepared
    commit state: State::Ready
  Computer -> RootFs: drives Transition::Setup
    current state: State::Prepared
    commit state: State::Ready
  commit state: State::Ready

Human -> Computer: emits Transition::Enable
  current state: State::Ready
  commit state: State::Online

Computer -> QemuVirtPlatform: emits Transition::Enable
  current state: State::Ready
  commit state: State::Online

QemuVirtPlatform -> OpenSBI: emits Transition::Enable
  current state: State::Ready
  commit state: State::Online

OpenSBI -> Kernel: emits Transition::Enable
  current state: State::Ready
  commit state: State::Online

Kernel -> BootInitFlow: emits Action::Enter
  current state: State::Online
  commit state: unchanged

Derivation passed!
"""


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

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertEqual(completed.stderr, "")

    def test_verbose_run_prints_all_commands_and_derivation_output(self) -> None:
        completed = self._run("VERBOSE=1")

        self.assertEqual(completed.returncode, 0, completed.stderr)
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
        self.assertEqual(completed.stderr, "")

    def test_only_exact_verbose_one_enables_command_echo(self) -> None:
        completed = self._run("VERBOSE=1 0")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
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
