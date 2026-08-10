from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from derive import default_derivation_sequence, derive
from modelc import compile_spec


BOOT_FLOW = ("flows", "task_flow", "BootInitFlow")
KERNEL_INIT_FLOW = ("flows", "task_flow", "KernelInitFlow")
CPU0_SCHEDULER = ("objects", "scheduler", "Cpu0Scheduler")
BOOT_TASK = ("objects", "task", "BootTask")
KERNEL_INIT_TASK = ("objects", "task", "KernelInitTask")
BOOT_SETUP = ("phases", "start_kernel", "boot_setup", "BootSetup")
BOOT_HANDOFF = ("phases", "start_kernel", "boot_handoff", "BootHandoff")
BOOT_IDLE = ("phases", "start_kernel", "boot_idle", "BootIdle")
KERNEL_INIT_PHASE = ("phases", "kernel_init", "KernelInitPhase")
USER_RUN_PHASE = ("phases", "user_run", "UserRunPhase")
PHASE_TARGETS = {
    ("phases", "arch_head", "ArchHead"),
    ("phases", "start_kernel", "StartKernel"),
    ("phases", "start_kernel", "early_boot", "EarlyBoot"),
    BOOT_SETUP,
    BOOT_HANDOFF,
    BOOT_IDLE,
    KERNEL_INIT_PHASE,
    USER_RUN_PHASE,
}


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)


class BootSchedulerModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = compile_spec(REPOSITORY / "model" / "main.spec")

    def test_scheduler_interface_and_cpu0_overrides_are_explicit(self) -> None:
        module = next(
            item
            for item in self.model.modules
            if item.name == ("objects", "scheduler")
        )
        scheduler_type = module.types[0]
        cpu0_scheduler = module.objects[0]
        type_states = {state.name: state for state in scheduler_type.states}
        object_states = {state.name: state for state in cpu0_scheduler.states}

        self.assertEqual(scheduler_type.initial_state, ("State", "Ready"))
        self.assertEqual(
            set(type_states),
            {
                ("State", "Ready"),
                ("State", "BootTaskRunning"),
                ("State", "KernelInitTaskRunning"),
            },
        )
        enable = type_states[("State", "Ready")].transitions[0]
        self.assertEqual(enable.signal, ("Transition", "Enable"))
        self.assertEqual(enable.target_state, ("State", "BootTaskRunning"))

        for state_name in (
            ("State", "BootTaskRunning"),
            ("State", "KernelInitTaskRunning"),
        ):
            self.assertTrue(type_states[state_name].actions[0].abstract)
            self.assertTrue(object_states[state_name].actions[0].override)
            self.assertFalse(object_states[state_name].actions[0].abstract)
            self.assertFalse(object_states[state_name].transitions[0].override)
        self.assertIsNone(cpu0_scheduler.parent)

    def test_boot_and_user_run_phases_yield_to_the_scheduler(self) -> None:
        boot_flow = next(item for item in self.model.objects if item.name == BOOT_FLOW)
        phases = tuple(
            next(item for item in self.model.objects if item.name == target)
            for target in (BOOT_HANDOFF, USER_RUN_PHASE, BOOT_IDLE)
        )

        self.assertNotIn(
            "yields",
            {block.kind for block in boot_flow.states[0].actions[0].blocks},
        )
        for phase in phases:
            enter = phase.states[0].actions[0]
            yielded = tuple(
                signal
                for block in enter.blocks
                if block.kind == "yields"
                for signal in block.signals
            )
            self.assertEqual(len(yielded), 1)
            self.assertEqual(yielded[0].source, phase.name)
            self.assertEqual(yielded[0].target, CPU0_SCHEDULER)
            self.assertEqual(yielded[0].signal, ("Action", "Schedule"))

    def test_schedule_switches_tasks_in_strict_alternation(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        schedules = tuple(unit for unit in units if unit.event.mode == "yield")

        self.assertEqual(
            tuple(schedule.event.source for schedule in schedules),
            (BOOT_HANDOFF, USER_RUN_PHASE, BOOT_IDLE),
        )
        expected = (
            (
                ("State", "BootTaskRunning"),
                (
                    (BOOT_TASK, ("Transition", "Suspend")),
                    (KERNEL_INIT_TASK, ("Transition", "Resume")),
                    (CPU0_SCHEDULER, ("Transition", "SwitchToKernelInitTask")),
                ),
                ("State", "KernelInitTaskRunning"),
                KERNEL_INIT_FLOW,
            ),
            (
                ("State", "KernelInitTaskRunning"),
                (
                    (KERNEL_INIT_TASK, ("Transition", "Suspend")),
                    (BOOT_TASK, ("Transition", "Resume")),
                    (CPU0_SCHEDULER, ("Transition", "SwitchToBootTask")),
                ),
                ("State", "BootTaskRunning"),
                BOOT_FLOW,
            ),
            (
                ("State", "BootTaskRunning"),
                (
                    (BOOT_TASK, ("Transition", "Suspend")),
                    (KERNEL_INIT_TASK, ("Transition", "Resume")),
                    (CPU0_SCHEDULER, ("Transition", "SwitchToKernelInitTask")),
                ),
                ("State", "KernelInitTaskRunning"),
                KERNEL_INIT_FLOW,
            ),
        )

        for schedule, (before, drives, switched, emitted_flow) in zip(
            schedules, expected, strict=True
        ):
            self.assertEqual(schedule.event.target, CPU0_SCHEDULER)
            self.assertEqual(schedule.state_before, before)
            self.assertEqual(
                tuple((unit.event.target, unit.event.signal) for unit in schedule.drives),
                drives,
            )
            self.assertEqual(schedule.drives[-1].state_after, switched)
            self.assertEqual(len(schedule.emits), 1)
            self.assertEqual(schedule.emits[0].event.target, emitted_flow)
            self.assertEqual(schedule.emits[0].event.signal, ("Action", "Enter"))
            self.assertEqual(schedule.emits[0].event.mode, "emit")

        task_switches = tuple(
            unit.event
            for schedule in schedules
            for unit in schedule.drives
            if unit.event.target in {BOOT_TASK, KERNEL_INIT_TASK}
        )
        self.assertEqual(
            tuple(event.target for event in task_switches),
            (
                BOOT_TASK,
                KERNEL_INIT_TASK,
                KERNEL_INIT_TASK,
                BOOT_TASK,
                BOOT_TASK,
                KERNEL_INIT_TASK,
            ),
        )
        self.assertEqual(
            tuple(
                event.signal
                for event in task_switches
                if event.signal[1] != "Suspend"
            ),
            (("Transition", "Resume"),) * 3,
        )
        self.assertNotIn(
            ("Transition", "Dispatch"),
            {event.signal for event in task_switches},
        )

    def test_kernel_init_resumes_without_repeating_completed_work(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        kernel_flow_enters = tuple(
            unit for unit in units if unit.event.target == KERNEL_INIT_FLOW
        )
        kernel_phase_enters = tuple(
            unit for unit in units if unit.event.target == KERNEL_INIT_PHASE
        )
        user_run_enters = tuple(
            unit for unit in units if unit.event.target == USER_RUN_PHASE
        )

        self.assertEqual(len(kernel_flow_enters), 2)
        self.assertEqual(
            tuple(unit.event.target for unit in kernel_flow_enters[0].drives),
            (KERNEL_INIT_PHASE, USER_RUN_PHASE),
        )
        self.assertEqual(
            tuple(unit.event.target for unit in kernel_flow_enters[1].drives),
            (USER_RUN_PHASE,),
        )
        self.assertEqual(len(kernel_phase_enters), 1)
        self.assertEqual(
            tuple(
                (directive.kind, directive.message)
                for directive in kernel_phase_enters[0].directives
            ),
            (("print", "kernel init"),),
        )
        self.assertEqual(len(user_run_enters), 2)
        self.assertEqual(
            tuple(unit.status for unit in user_run_enters),
            ("yielded", "passed"),
        )
        self.assertEqual(len(user_run_enters[0].yields), 1)
        self.assertEqual(user_run_enters[1].yields, ())

    def test_derivation_finishes_kernel_init_and_keeps_only_boot_idle_suspended(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        states = {item.object: item.state for item in result.final_state}

        self.assertEqual(result.status, "yielded")
        self.assertIsNone(result.failure)
        self.assertEqual(result.facts, ())
        self.assertEqual(states[CPU0_SCHEDULER], ("State", "KernelInitTaskRunning"))
        self.assertEqual(states[BOOT_TASK], ("State", "Online"))
        self.assertEqual(states[KERNEL_INIT_TASK], ("State", "OnCpu"))
        self.assertTrue(
            all(states[target] == ("State", "Online") for target in PHASE_TARGETS)
        )
        self.assertEqual(len(result.continuations), 1)
        continuation = result.continuations[0]
        self.assertEqual(continuation.root, BOOT_FLOW)
        self.assertEqual(continuation.frames[-1].object, BOOT_IDLE)
        self.assertFalse(
            any(
                directive.kind == "panic"
                for unit in units
                for directive in unit.directives
            )
        )


if __name__ == "__main__":
    unittest.main()
