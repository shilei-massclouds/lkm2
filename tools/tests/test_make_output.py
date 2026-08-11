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
  BootInitFlow -> ArchHead: drives Action::Enter
    current state: State::Online
    ArchHead -> StartKernel: drives Action::Enter
      current state: State::Online
      StartKernel -> EarlyBoot: drives Action::Enter
        current state: State::Online
        print: here
        commit state: unchanged
      StartKernel -> BootSetup: drives Action::Enter
        current state: State::Online
        BootSetup -> Cpu0Scheduler: drives Transition::Enable
          current state: State::Ready
          commit state: State::BootTaskRunning
        BootSetup -> KernelInitTask: drives Transition::Preset
          current state: State::Base
          commit state: State::Prepared
        BootSetup -> KernelInitTask: drives Transition::Setup
          current state: State::Prepared
          commit state: State::Ready
        BootSetup -> KernelInitTask: drives Transition::Enable
          current state: State::Ready
          commit state: State::Online
        commit state: unchanged
      StartKernel -> BootHandoff: drives Action::Enter
        current state: State::Online
        BootHandoff -> Cpu0Scheduler: yields Action::Schedule
          current state: State::BootTaskRunning
          Cpu0Scheduler -> BootTask: drives Transition::Suspend
            current state: State::OnCpu
            commit state: State::Online
          Cpu0Scheduler -> KernelInitTask: drives Transition::Resume
            current state: State::Online
            commit state: State::OnCpu
          Cpu0Scheduler -> Cpu0Scheduler: drives Transition::SwitchToKernelInitTask
            current state: State::BootTaskRunning
            commit state: State::KernelInitTaskRunning
          commit state: unchanged
        Cpu0Scheduler -> KernelInitFlow: emits Action::Enter
          current state: State::Online
          KernelInitFlow -> KernelInitPhase: drives Action::Enter
            current state: State::Online
            print: kernel init
            commit state: unchanged
          KernelInitFlow -> UserRunPhase: drives Action::Enter
            current state: State::Online
            UserRunPhase -> Cpu0Scheduler: yields Action::Schedule
              current state: State::KernelInitTaskRunning
              Cpu0Scheduler -> KernelInitTask: drives Transition::Suspend
                current state: State::OnCpu
                commit state: State::Online
              Cpu0Scheduler -> BootTask: drives Transition::Resume
                current state: State::Online
                commit state: State::OnCpu
              Cpu0Scheduler -> Cpu0Scheduler: drives Transition::SwitchToBootTask
                current state: State::KernelInitTaskRunning
                commit state: State::BootTaskRunning
              commit state: unchanged
            Cpu0Scheduler -> BootInitFlow: emits Action::Enter
              current state: State::Online
              commit state: unchanged
        commit state: unchanged
      StartKernel -> BootIdle: drives Action::Enter
        current state: State::Online
        BootIdle -> Cpu0Scheduler: yields Action::Schedule
          current state: State::BootTaskRunning
          Cpu0Scheduler -> BootTask: drives Transition::Suspend
            current state: State::OnCpu
            commit state: State::Online
          Cpu0Scheduler -> KernelInitTask: drives Transition::Resume
            current state: State::Online
            commit state: State::OnCpu
          Cpu0Scheduler -> Cpu0Scheduler: drives Transition::SwitchToKernelInitTask
            current state: State::BootTaskRunning
            commit state: State::KernelInitTaskRunning
          commit state: unchanged
        Cpu0Scheduler -> KernelInitFlow: emits Action::Enter
          current state: State::Online
          KernelInitFlow -> UserRunPhase: drives Action::Enter
            current state: State::Online
            commit state: unchanged
          commit state: unchanged

Derivation yielded!
"""

# The default model intentionally stops at the Scheduler placeholder panic.
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

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertIn("Error 1", completed.stderr)

    def test_verbose_run_prints_all_commands_and_derivation_output(self) -> None:
        completed = self._run("VERBOSE=1")

        self.assertEqual(completed.returncode, 2)
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

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, EXPECTED_DERIVATION_OUTPUT)
        self.assertIn("Error 1", completed.stderr)

    def test_quiet_run_keeps_error_diagnostics_visible(self) -> None:
        completed = self._run("MODEL=missing-model.spec")

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error:", completed.stderr)
        self.assertIn("missing-model.spec", completed.stderr)
        self.assertNotIn("tools/bin/derive", completed.stderr)


if __name__ == "__main__":
    unittest.main()
