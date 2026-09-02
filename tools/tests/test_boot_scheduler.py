from __future__ import annotations

from pathlib import Path
from io import StringIO
import sys
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from derive import (
    default_derivation_sequence,
    derive,
    load_user_runtime_signals,
)
from model_ir import ModelExpression
from modelc import compile_spec


BOOT_FLOW = ("flows", "task_flow", "BootInitFlow")
KERNEL_INIT_FLOW = ("flows", "task_flow", "KernelInitFlow")
CPU0_SCHEDULER = ("objects", "scheduler", "Cpu0Scheduler")
BOOT_TASK = ("objects", "task", "BootTask")
KERNEL_INIT_TASK = ("objects", "task", "KernelInitTask")
USER_APP_RUNTIME = (
    *KERNEL_INIT_TASK,
    "UserAppRuntime",
)
KERNEL = ("systems", "kernel", "Kernel")
BOOT_HANDOFF = ("phases", "start_kernel", "boot_handoff", "BootHandoff")
EARLY_BOOT = ("phases", "start_kernel", "early_boot", "EarlyBoot")
BOOT_SETUP = ("phases", "start_kernel", "boot_setup", "BootSetup")
USER_RUN_PHASE = ("phases", "user_run", "UserRunPhase")
BOOT_CPU = ("objects", "cpu", "BootCPU")
EARLY_CONSOLE = ("objects", "early_console", "EarlyConsole")
DTB_BLOB = ("objects", "dtb_blob", "DtbBlob")
MEMBLOCK = ("objects", "memblock", "MemBlock")
MEMBLOCK_MEMORY = ("objects", "memblock", "MemBlockMemory")
MEMBLOCK_RESERVED = ("objects", "memblock", "MemBlockReserved")
SWAPPER_PAGE_TABLE = ("objects", "vm", "SwapperPageTable")
SBI_CAPABILITY = ("objects", "early_console", "SbiCapability")
BANNER = ("objects", "printk", "Banner")


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


def _target_name(expression):
    parts = []
    cursor = expression
    while cursor.kind in {"member", "path"}:
        parts.append(cursor.value)
        cursor = cursor.children[0]
    parts.append(cursor.value)
    return tuple(reversed(parts))


class BootSchedulerModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = compile_spec(REPOSITORY / "model" / "main.spec")

    def test_scheduler_policy_uses_sched_core_and_runtime_selectors(self) -> None:
        module = next(
            item for item in self.model.modules if item.name == ("objects", "scheduler")
        )
        scheduler_type = next(item for item in module.types if item.name[-1] == "Scheduler")
        scheduler = next(item for item in module.objects if item.name == CPU0_SCHEDULER)

        self.assertTrue(scheduler_type.sched_core)
        self.assertEqual(scheduler_type.fields, ())
        self.assertEqual(_target_name(scheduler.idle_task), ("BootTask",))
        online = next(state for state in scheduler.states if state.name == ("State", "Online"))
        self.assertEqual(tuple(action.signal for action in online.actions), (("Action", "Schedule"),))
        blocks = online.actions[0].blocks
        self.assertEqual(tuple(block.kind for block in blocks), ("drives", "switches", "drives"))
        self.assertEqual(blocks[0].signals[0].target.value, "CurrentTaskRef")
        self.assertEqual(blocks[1].switches, "next_task_ref")
        self.assertEqual(blocks[2].signals[0].target.value, "next_task_ref")

    def test_cpu_and_event_flow_protocols_are_explicit(self) -> None:
        cpu_module = next(
            item for item in self.model.modules if item.name == ("objects", "cpu")
        )
        cpu_type = next(item for item in cpu_module.types if item.name[-1] == "CPU")
        boot_cpu = next(item for item in cpu_module.objects if item.name == BOOT_CPU)
        scheduler = next(item for item in self.model.objects if item.name == CPU0_SCHEDULER)

        self.assertTrue(cpu_type.cpu_core)
        self.assertEqual(boot_cpu.logical_id, 0)
        self.assertEqual(_target_name(scheduler.parent), ("BootCPU",))
        syscall_action = next(
            action
            for state in cpu_type.states
            for action in state.actions
            if action.signal == ("Action", "OnSyscallExit")
        )
        self.assertEqual(syscall_action.parameters[0].name, "status")
        self.assertEqual(
            _target_name(syscall_action.blocks[0].signals[0].target),
            ("self", "SyscallExitFlowRef"),
        )
        for action_name, selector in (
            ("OnInterrupt", "InterruptFlowRef"),
            ("OnException", "ExceptionFlowRef"),
        ):
            action = next(
                action
                for state in cpu_type.states
                for action in state.actions
                if action.signal == ("Action", action_name)
            )
            self.assertEqual(
                _target_name(action.blocks[0].signals[0].target),
                ("self", selector),
            )

        interrupt_control = next(
            item for item in cpu_module.types if item.name[-1] == "InterruptControl"
        )
        self.assertEqual(
            tuple(action.signal for action in interrupt_control.states[0].actions),
            (
                ("Action", "ClearPending"),
                ("Action", "MaskAll"),
                ("Action", "Unmask"),
            ),
        )

        event_module = next(
            item
            for item in self.model.modules
            if item.name == ("flows", "event_flow")
        )
        event_types = {item.name[-1]: item for item in event_module.types}
        self.assertTrue(event_types["EventFlow"].event_flow)
        for name in ("InterruptFlow", "ExceptionFlow"):
            self.assertTrue(event_types[name].event_flow)
            self.assertTrue(event_types[name].continuation)
        self.assertEqual(event_module.objects, ())

        flow_module = next(
            item
            for item in self.model.modules
            if item.name == ("flows", "syscall_exit_flow")
        )
        flow_type = flow_module.types[0]
        self.assertTrue(flow_type.continuation)
        self.assertTrue(flow_type.event_flow)
        self.assertTrue(flow_type.syscall_exit_flow)
        self.assertEqual(flow_module.objects, ())

        task_flow_module = next(
            item
            for item in self.model.modules
            if item.name == ("flows", "task_flow")
        )
        cpu_ref = next(
            field
            for field in task_flow_module.types[0].fields
            if field.name == "cpu_ref"
        )
        self.assertTrue(cpu_ref.mutable)

    def test_task_resume_declares_its_resume_target_entry(self) -> None:
        task_module = next(
            item for item in self.model.modules if item.name == ("objects", "task")
        )
        task_type = next(item for item in task_module.types if item.name[-1] == "Task")
        boot_task = next(item for item in task_module.objects if item.name == BOOT_TASK)

        for owner in (task_type, boot_task):
            resume = next(
                transition
                for state in owner.states
                for transition in state.transitions
                if transition.signal == ("Transition", "Resume")
            )
            signals = tuple(
                signal
                for block in resume.blocks
                if block.kind == "resumes"
                for signal in block.signals
            )
            self.assertEqual(len(signals), 1)
            self.assertEqual(
                _target_name(signals[0].target),
                ("self", "ResumeTargetRef"),
            )
            self.assertEqual(signals[0].signal, ("Action", "Enter"))
            self.assertEqual(signals[0].mode, "resume")

    def test_boot_handoff_yields_to_the_scheduler(self) -> None:
        phase = next(item for item in self.model.objects if item.name == BOOT_HANDOFF)
        signal = next(
            signal
            for block in phase.states[0].actions[0].blocks
            if block.kind == "yields"
            for signal in block.signals
        )
        self.assertEqual(_target_name(signal.target), CPU0_SCHEDULER)
        self.assertEqual(signal.signal, ("Action", "Schedule"))

    def test_swapper_page_table_is_vm_child_and_paging_init_is_absent(self) -> None:
        vm_module = next(
            item for item in self.model.modules if item.name == ("objects", "vm")
        )
        swapper = next(item for item in vm_module.objects if item.name == SWAPPER_PAGE_TABLE)
        self.assertEqual(_target_name(swapper.parent), ("Vm",))
        self.assertEqual(swapper.initial_state, ("State", "Ready"))
        self.assertEqual(
            tuple(item.name[-1] for item in self.model.objects if item.name[-1] == "PagingInit"),
            (),
        )
        early_boot = next(item for item in self.model.objects if item.name == EARLY_BOOT)
        self.assertEqual(
            {action.signal[-1] for action in early_boot.states[0].actions},
            {"Enter"},
        )

    def test_early_boot_enter_owns_m0_m1_scheduler_and_unmask_order(self) -> None:
        early_boot = next(
            item for item in self.model.objects if item.name == EARLY_BOOT
        )
        boot_setup = next(
            item for item in self.model.objects if item.name == BOOT_SETUP
        )
        actions = {
            action.signal: action for action in early_boot.states[0].actions
        }
        early_action = actions[("Action", "Enter")]
        setup_action = boot_setup.states[0].actions[0]
        early_drives = next(
            block.signals for block in early_action.blocks if block.kind == "drives"
        )
        setup_drives = next(
            block.signals for block in setup_action.blocks if block.kind == "drives"
        )

        self.assertEqual(
            tuple((_target_name(signal.target), signal.signal) for signal in early_drives),
            (
                (BANNER, ("Transition", "Enable")),
                (DTB_BLOB, ("Transition", "Enable")),
                (MEMBLOCK_MEMORY, ("Transition", "Enable")),
                (SBI_CAPABILITY, ("Transition", "Enable")),
                (EARLY_CONSOLE, ("Transition", "Enable")),
                (MEMBLOCK_RESERVED, ("Transition", "Enable")),
                (MEMBLOCK, ("Transition", "Enable")),
                (SWAPPER_PAGE_TABLE, ("Transition", "Enable")),
                (("objects", "memory_node", "MemoryNode"), ("Transition", "Enable")),
                (CPU0_SCHEDULER, ("Transition", "Enable")),
                (
                    ("CurrentCPU", "InterruptControlRef"),
                    ("Action", "Unmask"),
                ),
            ),
        )
        self.assertEqual(
            tuple(
                (_target_name(signal.target), signal.signal)
                for signal in setup_drives
            ),
            (
                (KERNEL_INIT_TASK, ("Transition", "Preset")),
                (KERNEL_INIT_TASK, ("Transition", "Setup")),
                (KERNEL_INIT_TASK, ("Transition", "Enable")),
            ),
        )

    def test_early_boot_handoff_gates_boot_setup(self) -> None:
        path = derive(
            self.model,
            default_derivation_sequence(self.model),
            user_runtime_signals=load_user_runtime_signals(StringIO("")),
        ).paths[0]
        units = tuple(_all_units(path.units))
        early_boot = next(
            unit for unit in units
            if unit.event.target == EARLY_BOOT and unit.handler == ("Action", "Enter")
        )
        boot_setup = next(unit for unit in units if unit.event.target == BOOT_SETUP)

        self.assertTrue(
            all(check.status == "passed" for check in early_boot.depends_on)
        )
        self.assertTrue(all(check.status == "passed" for check in early_boot.ensures))
        self.assertTrue(
            {
                "Banner == State::Online",
                "DtbBlob == State::Online",
                "MemBlockMemory == State::Online",
                "SbiCapability == State::Online",
                "EarlyConsole == State::Online",
                "MemBlockReserved == State::Online",
                "MemBlock == State::Online",
                "SwapperPageTable == State::Online",
                "MemoryNode == State::Online",
                "Cpu0Scheduler == State::Online",
                "early_console_bound_from_registry(EarlyConsole, SbiConsole)",
                "printk_console_registered(Printk, EarlyConsole)",
                'BootCommandLine.has_key("earlycon")',
                "swapper_fixmap_established()",
                "swapper_linear_map_established()",
                "swapper_kernel_map_established()",
                "swapper_fixmap_cleared()",
                "swapper_satp_switched()",
                "swapper_tlb_flush_completed()",
                "swapper_late_paging_mode_selected()",
            }.issubset({check.expression for check in early_boot.ensures})
        )
        self.assertNotIn(
            'BootCommandLine.contains("earlycon", "sbi")',
            {check.expression for check in early_boot.ensures},
        )
        self.assertEqual(
            tuple(unit.event.signal for unit in early_boot.drives),
            (
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Transition", "Enable"),
                ("Action", "Unmask"),
            ),
        )
        self.assertTrue(all(check.status == "passed" for check in early_boot.ensures))
        self.assertEqual(
            tuple(check.expression for check in early_boot.establishes),
            ("early_boot_interrupts_enabled()",),
        )
        self.assertTrue(
            all(check.status == "passed" for check in boot_setup.depends_on)
        )
        self.assertEqual(path.interrupt_controls[0].mode, "Unmasked")

    def test_failed_unmask_does_not_publish_handoff_or_run_boot_setup(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        early_boot = next(item for item in model.objects if item.name == EARLY_BOOT)
        enter = next(
            action for action in early_boot.states[0].actions
            if action.signal == ("Action", "Enter")
        )
        drives = next(
            block
            for block in enter.blocks
            if block.kind == "drives"
        )
        unmask = drives.signals[-1]
        self.assertEqual(unmask.signal, ("Action", "Unmask"))

        # Bypass compiler validation to inject a runtime failure at the exact
        # Unmask drive without changing the repository model.
        object.__setattr__(
            unmask,
            "arguments",
            (ModelExpression("integer", 0),),
        )
        path = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(StringIO("")),
        ).paths[0]
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "unhandled_signal")
        self.assertEqual(path.interrupt_controls[0].mode, "Masked")
        self.assertFalse(
            any(
                fact.predicate[-1] == "early_boot_interrupts_enabled"
                for fact in path.facts
            )
        )
        self.assertFalse(any(unit.event.target == BOOT_SETUP for unit in units))

    def test_boot_setup_rejects_a_missing_early_boot_handoff_fact(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        early_boot = next(item for item in model.objects if item.name == EARLY_BOOT)
        action = next(
            action for action in early_boot.states[0].actions
            if action.signal == ("Action", "Enter")
        )
        object.__setattr__(
            action,
            "blocks",
            tuple(block for block in action.blocks if block.kind != "establishes"),
        )
        path = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=load_user_runtime_signals(StringIO("")),
        ).paths[0]
        units = tuple(_all_units(path.units))
        boot_setup = next(unit for unit in units if unit.event.target == BOOT_SETUP)

        self.assertEqual(path.status, "depends_on_failed")
        self.assertEqual(boot_setup.depends_on[-1].status, "failed")
        self.assertEqual(
            boot_setup.depends_on[-1].expression,
            "early_boot_interrupts_enabled()",
        )
        self.assertEqual(boot_setup.drives, ())

    def test_user_run_prepares_runtime_then_yields_to_its_enter_action(self) -> None:
        phase = next(item for item in self.model.objects if item.name == USER_RUN_PHASE)
        blocks = phase.states[0].actions[0].blocks

        self.assertEqual(
            tuple(block.kind for block in blocks),
            ("drives", "drives", "drives", "yields"),
        )
        signals = tuple(block.signals[0] for block in blocks)
        self.assertTrue(
            all(
                _target_name(signal.target)
                == ("CurrentTaskRef", "UserAppRuntimeRef")
                for signal in signals
            )
        )
        self.assertEqual(
            tuple(signal.signal for signal in signals),
            (
                ("Transition", "Preset"),
                ("Transition", "Setup"),
                ("Transition", "Enable"),
                ("Action", "Enter"),
            ),
        )
        self.assertEqual(
            tuple(signal.mode for signal in signals),
            ("drive", "drive", "drive", "yield"),
        )

    def test_kernel_boots_exclusively_through_boot_task_resume(self) -> None:
        kernel = next(item for item in self.model.objects if item.name == KERNEL)
        enable = next(
            transition
            for state in kernel.states
            for transition in state.transitions
            if transition.signal == ("Transition", "Enable")
        )
        signals = tuple(
            signal for block in enable.blocks for signal in block.signals
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(_target_name(signals[0].target), BOOT_TASK)
        self.assertEqual(signals[0].signal, ("Transition", "Resume"))
        self.assertEqual(signals[0].mode, "drive")

        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        boot_resume = next(
            unit
            for unit in units
            if unit.event.target == BOOT_TASK
            and unit.event.signal == ("Transition", "Resume")
        )
        self.assertEqual(boot_resume.state_before, ("State", "Online"))
        self.assertEqual(boot_resume.state_after, ("State", "OnCpu"))
        self.assertFalse(
            any(
                unit.event.target == BOOT_TASK
                and unit.event.signal == ("Transition", "Resume")
                and unit.state_before == ("State", "OnCpu")
                for unit in units
            )
        )
        self.assertEqual(tuple(unit.event.target for unit in boot_resume.resumes), (BOOT_FLOW,))

    def test_default_derivation_delivers_exit_without_rescheduling(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.paths), 1)
        path = result.paths[0]
        self.assertEqual(path.status, "panic")
        self.assertEqual(path.failure.message, "Attempted to kill init!")
        units = tuple(_all_units(path.units))
        schedules = tuple(
            unit
            for unit in units
            if unit.event.target == CPU0_SCHEDULER
            and unit.event.signal == ("Action", "Schedule")
        )

        self.assertEqual(len(schedules), 1)
        self.assertTrue(all(unit.status == "passed" for unit in schedules))
        self.assertEqual(
            tuple(switch.task for unit in schedules for switch in unit.switches),
            (KERNEL_INIT_TASK,),
        )
        self.assertEqual(
            tuple(
                switch.idle_fallback
                for unit in schedules
                for switch in unit.switches
            ),
            (False,),
        )
        queue_actions = tuple(
            unit.event.signal
            for unit in units
            if unit.event.target == CPU0_SCHEDULER
            and unit.event.signal in {
                ("Action", "Enqueue"),
                ("Action", "Dequeue"),
            }
        )
        self.assertEqual(
            queue_actions,
            (("Action", "Enqueue"),),
        )
        cpu_exit = next(
            unit
            for unit in units
            if unit.event.target == BOOT_CPU
            and unit.event.signal == ("Action", "OnSyscallExit")
        )
        flow_units = tuple(_all_units((cpu_exit,)))
        self.assertFalse(
            any(
                unit.event.signal
                in {
                    ("Transition", "Suspend"),
                    ("Action", "Schedule"),
                }
                for unit in flow_units
            )
        )

    def test_default_final_scheduler_context_and_continuation(self) -> None:
        program = load_user_runtime_signals(StringIO(""))
        path = derive(
            self.model,
            default_derivation_sequence(self.model),
            user_runtime_signals=program,
        ).paths[0]
        states = {item.object: item.state for item in path.final_state}

        self.assertEqual(path.status, "yielded")
        self.assertIsNone(path.failure)
        self.assertEqual(states[CPU0_SCHEDULER], ("State", "Online"))
        self.assertEqual(states[BOOT_TASK], ("State", "Online"))
        self.assertEqual(states[KERNEL_INIT_TASK], ("State", "OnCpu"))
        self.assertEqual(states[USER_APP_RUNTIME], ("State", "Online"))
        self.assertEqual(len(path.schedulers), 1)
        scheduler = path.schedulers[0]
        self.assertEqual(scheduler.scheduler, CPU0_SCHEDULER)
        self.assertEqual(scheduler.idle_task, BOOT_TASK)
        self.assertEqual(path.current_task_ref, KERNEL_INIT_TASK)
        self.assertEqual(path.current_cpu_ref, BOOT_CPU)
        self.assertEqual(scheduler.runq, (KERNEL_INIT_TASK,))
        self.assertEqual(
            tuple(continuation.root for continuation in path.continuations),
            (BOOT_HANDOFF, USER_RUN_PHASE),
        )

        units = tuple(_all_units(path.units))
        runtime_entries = tuple(
            unit
            for unit in units
            if unit.event.target == USER_APP_RUNTIME
            and unit.event.signal == ("Action", "Enter")
        )
        self.assertEqual(len(runtime_entries), 1)
        self.assertEqual(runtime_entries[0].event.mode, "yield")
        self.assertEqual(runtime_entries[0].drives, ())
        self.assertFalse(
            any(unit.event.target[-1] == "BootIdle" for unit in units)
        )
        self.assertFalse(
            any(
                directive.kind == "panic"
                and directive.message == "boot idle repeated!"
                for unit in units
                for directive in unit.directives
            )
        )
        self.assertFalse(
            any(
                unit.event.signal
                in {("Transition", "Disable"), ("Transition", "Cleanup")}
                for unit in units
            )
        )


if __name__ == "__main__":
    unittest.main()
