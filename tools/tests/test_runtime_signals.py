from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import shutil
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools" / "src"))

from derive import (  # noqa: E402
    UserRuntimeSignalValidationError,
    default_derivation_sequence,
    derive,
    load_user_runtime_signals,
)
from derive.cli import main as derive_main  # noqa: E402
from modelc import compile_spec  # noqa: E402


BOOT_CPU = ("objects", "cpu", "BootCPU")
KERNEL_INIT_TASK = ("objects", "task", "KernelInitTask")
CPU0_SCHEDULER = ("objects", "scheduler", "Cpu0Scheduler")


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


class UserRuntimeSignalParserTests(unittest.TestCase):
    def test_repository_signal_programs_are_valid(self) -> None:
        signals_directory = REPOSITORY / "tools" / "signals"
        with (signals_directory / "default.signals").open(encoding="utf-8") as stream:
            default_program = load_user_runtime_signals(stream)
        with (signals_directory / "parked.signals").open(encoding="utf-8") as stream:
            parked_program = load_user_runtime_signals(stream)

        self.assertEqual(
            tuple(item.qualified_name for item in default_program.signals),
            ("syscall.exit",),
        )
        self.assertEqual(default_program.signals[0].arguments, (0,))
        self.assertEqual(parked_program.signals, ())

    def test_comments_empty_lines_and_all_families_parse(self) -> None:
        program = load_user_runtime_signals(
            StringIO(
                "# signals\n\ninterrupt.timer <local> # tick\n"
                "exception.page_fault 0 -1 +2\n"
            )
        )
        self.assertEqual(
            tuple(item.qualified_name for item in program.signals),
            ("interrupt.timer", "exception.page_fault"),
        )
        self.assertEqual(program.signals[1].arguments, (-1, 2))

    def test_exit_requires_one_i32_and_must_be_last(self) -> None:
        invalid = (
            "syscall.exit <local>\n",
            "syscall.exit <local> 2147483648\n",
            "syscall.exit <local> 0\ninterrupt.timer <local>\n",
        )
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaises(UserRuntimeSignalValidationError):
                    load_user_runtime_signals(StringIO(source))

    def test_diagnostics_include_stream_line_and_column(self) -> None:
        stream = StringIO("\n  syscall.Exit <local> 0\n")
        stream.name = "signals.txt"
        with self.assertRaisesRegex(
            UserRuntimeSignalValidationError,
            r"signals\.txt:2:11: signal name must be a lowercase identifier",
        ):
            load_user_runtime_signals(stream)


class UserRuntimeSignalEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = compile_spec(REPOSITORY / "model" / "main.spec")
        cls.sequence = default_derivation_sequence(cls.model)

    def _derive(self, text: str):
        return derive(
            self.model,
            self.sequence,
            user_runtime_signals=load_user_runtime_signals(StringIO(text)),
        )

    def _derive_with_interrupt_control(
        self, text: str, *, clear: bool = False, nested: bool = False
    ):
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            human_spec = model_root / "systems" / "human.spec"
            source = human_spec.read_text(encoding="utf-8")
            source = source.replace(
                "\n}\n",
                "\n    drives RuntimeSignalControl.Action::Apply;\n}\n",
                1,
            )
            clear_signal = (
                "CurrentCPU.InterruptControlRef.Action::ClearPending;"
                if clear
                else ""
            )
            source += f"""

type RuntimeSignalControlType {{
    initial_state: State::Online;
    state State::Online {{ actions {{
        on Action::Apply {{ drives {{
            {clear_signal}
            CurrentCPU.InterruptControlRef.Action::Unmask;
        }} }}
    }} }}
}}
object RuntimeSignalControl: RuntimeSignalControlType {{}}
"""
            human_spec.write_text(source, encoding="utf-8")
            if nested:
                flow_spec = model_root / "flows" / "event_flow.spec"
                flow_source = flow_spec.read_text(encoding="utf-8")
                flow_source = flow_source.replace(
                    "predicate event_flow_handled<F>",
                    "use model::objects::cpu::BootCPU;\n\npredicate event_flow_handled<F>",
                    1,
                ).replace(
                    "on Action::Enter {\n                establishes",
                    """on Action::Enter {
                drives BootCPU.Action::OnException;
                establishes""",
                    1,
                )
                flow_spec.write_text(flow_source, encoding="utf-8")
            model = compile_spec(model_root / "main.spec")
        return derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(StringIO(text)),
        )

    def test_empty_program_parks_without_cpu_or_scheduler_entry(self) -> None:
        path = self._derive("").paths[0]
        self.assertEqual(path.status, "yielded")
        self.assertEqual(path.current_cpu_ref, BOOT_CPU)
        self.assertEqual(path.event_flows, ())
        units = tuple(_all_units(path.units))
        self.assertEqual(
            sum(
                unit.event.target == CPU0_SCHEDULER
                and unit.event.signal == ("Action", "Schedule")
                for unit in units
            ),
            1,
        )

    def test_explicit_cpu_and_status_are_preserved_through_exit_chain(self) -> None:
        path = self._derive("syscall.exit 0 7\n").paths[0]
        self.assertEqual(path.status, "panic")
        units = tuple(_all_units(path.units))
        chain = tuple(
            unit
            for unit in units
            if unit.event.signal
            in {
                ("Action", "OnSyscallExit"),
                ("Action", "Exit"),
            }
            or unit.event.target[-1].startswith("SyscallExitFlow")
        )
        self.assertEqual(
            tuple(unit.event.arguments[0].value for unit in chain),
            (7, 7, 7),
        )
        self.assertEqual(path.event_flows[0].cpu, BOOT_CPU)
        self.assertEqual(path.event_flows[0].outcome, "terminal")
        states = {item.object: item.state for item in path.final_state}
        self.assertEqual(states[KERNEL_INIT_TASK], ("State", "OnCpu"))
        self.assertEqual(path.schedulers[0].runq, (KERNEL_INIT_TASK,))

    def test_unknown_cpu_and_unsupported_syscall_fail_at_consumption(self) -> None:
        cases = (
            ("syscall.exit 9 0\n", "unknown_cpu_target"),
            ("syscall.write <local> 1\n", "unsupported_runtime_signal"),
        )
        for source, code in cases:
            with self.subTest(source=source):
                path = self._derive(source).paths[0]
                self.assertEqual(path.status, code)
                self.assertEqual(path.event_flows, ())

    def test_masked_interrupt_is_pending_and_exception_returns_locally(self) -> None:
        interrupt = self._derive("interrupt.timer <local>\n").paths[0]
        self.assertEqual(interrupt.status, "yielded")
        self.assertEqual(interrupt.event_flows, ())
        self.assertEqual(interrupt.interrupt_controls[0].mode, "Masked")
        self.assertEqual(
            interrupt.interrupt_controls[0].pending,
            ("interrupt.timer",),
        )

        exception = self._derive("exception.page_fault <local>\n").paths[0]
        self.assertEqual(exception.status, "yielded")
        self.assertEqual(exception.current_task_ref, KERNEL_INIT_TASK)
        self.assertEqual(exception.event_flows[0].cpu, BOOT_CPU)
        self.assertEqual(exception.event_flows[0].signal, "exception.page_fault")
        self.assertEqual(exception.event_flows[0].outcome, "returned")
        self.assertTrue(
            any(fact.predicate[-1] == "event_flow_handled" for fact in exception.facts)
        )

    def test_clear_pending_discards_and_unmask_delivers_fifo(self) -> None:
        program = "interrupt.timer <local>\ninterrupt.external <local>\n"
        cleared = self._derive_with_interrupt_control(
            program, clear=True
        ).paths[0]
        self.assertEqual(cleared.status, "yielded")
        self.assertEqual(cleared.event_flows, ())
        self.assertEqual(cleared.interrupt_controls[0].mode, "Unmasked")
        self.assertEqual(cleared.interrupt_controls[0].pending, ())

        delivered = self._derive_with_interrupt_control(program).paths[0]
        self.assertEqual(delivered.status, "yielded")
        self.assertEqual(
            tuple(flow.signal for flow in delivered.event_flows),
            ("interrupt.timer", "interrupt.external"),
        )
        self.assertTrue(
            all(flow.outcome == "returned" for flow in delivered.event_flows)
        )
        self.assertEqual(delivered.interrupt_controls[0].mode, "Unmasked")
        self.assertEqual(delivered.interrupt_controls[0].pending, ())

    def test_nested_event_flow_is_rejected(self) -> None:
        path = self._derive_with_interrupt_control(
            "interrupt.timer <local>\n", nested=True
        ).paths[0]
        self.assertEqual(path.status, "nested_event_flow")
        self.assertEqual(path.event_flows, ())

    def test_existing_nonlocal_cpu_is_rejected_for_syscall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            cpu_spec = model_root / "objects" / "cpu.spec"
            cpu_spec.write_text(
                cpu_spec.read_text(encoding="utf-8")
                + "\nobject SecondaryCPU: CPU { logical_id: 1; parent: Kernel; }\n",
                encoding="utf-8",
            )
            model = compile_spec(model_root / "main.spec")
        result = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(
                StringIO("syscall.exit 1 0\n")
            ),
        )
        self.assertEqual(result.paths[0].status, "invalid_syscall_cpu_target")
        exception = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(
                StringIO("exception.page_fault 1\n")
            ),
        )
        self.assertEqual(
            exception.paths[0].status,
            "invalid_exception_cpu_target",
        )

    def test_interrupt_fails_while_control_mode_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            arch_head = model_root / "phases" / "arch_head.spec"
            arch_head.write_text(
                arch_head.read_text(encoding="utf-8").replace(
                    "CurrentCPU.InterruptControlRef.Action::MaskAll;\n",
                    "",
                    1,
                ),
                encoding="utf-8",
            )
            model = compile_spec(model_root / "main.spec")
        result = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(
                StringIO("interrupt.timer <local>\n")
            ),
        )
        path = result.paths[0]
        self.assertEqual(path.status, "unknown_interrupt_mode")
        self.assertEqual(path.interrupt_controls[0].mode, "Unknown")

    def test_repeated_derivations_restart_cursor_and_flow_number(self) -> None:
        program = load_user_runtime_signals(StringIO("syscall.exit <local> -7\n"))
        first = derive(
            self.model,
            self.sequence,
            user_runtime_signals=program,
        )
        second = derive(
            self.model,
            self.sequence,
            user_runtime_signals=program,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.event_flows[0].flow[-1], "SyscallExitFlow0")

    def test_cli_reports_unreadable_signal_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.signals"
            stderr = StringIO()
            with redirect_stderr(stderr):
                status = derive_main(
                    ["--user-runtime-signals", str(missing)],
                    default_model=REPOSITORY / "model" / "main.spec",
                )
            self.assertEqual(status, 1)
            self.assertIn("missing.signals", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
