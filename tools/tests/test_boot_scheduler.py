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
CPU0_RUNQ = ("objects", "scheduler", "Cpu0RunQ")
BOOT_TASK_REF = ("objects", "scheduler", "BootTaskRef")
KERNEL_INIT_TASK_REF = ("objects", "scheduler", "KernelInitTaskRef")
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
        yield from _all_units(unit.resumes)


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
        scheduler_type = next(item for item in module.types if item.name[-1] == "Scheduler")
        cpu0_scheduler = next(item for item in module.objects if item.name == CPU0_SCHEDULER)
        type_states = {state.name: state for state in scheduler_type.states}
        object_states = {state.name: state for state in cpu0_scheduler.states}

        self.assertEqual(scheduler_type.initial_state, ("State", "Ready"))
        self.assertEqual(
            set(type_states),
            {
                ("State", "Ready"),
                ("State", "Online"),
            },
        )
        enable = type_states[("State", "Ready")].transitions[0]
        self.assertEqual(enable.signal, ("Transition", "Enable"))
        self.assertEqual(enable.target_state, ("State", "Online"))
        fields = {field.name: field for field in scheduler_type.fields or ()}
        self.assertEqual(set(fields), {"curr", "idle", "runq"})
        self.assertTrue(fields["curr"].mutable)
        self.assertTrue(fields["idle"].mutable)
        self.assertFalse(fields["runq"].mutable)
        self.assertEqual(fields["curr"].default.value, "BootTaskRef")
        self.assertEqual(fields["idle"].default.value, "BootTaskRef")
        actions = {
            action.signal: action
            for action in object_states[("State", "Online")].actions
        }
        self.assertEqual(
            set(actions),
            {
                ("Action", "Enqueue"),
                ("Action", "Schedule"),
                ("Action", "SetCurrentTask"),
                ("Action", "SetIdleTask"),
            },
        )
        self.assertEqual(len(actions[("Action", "Enqueue")].parameters), 1)
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

        self.assertEqual(len(schedules), 1)
        schedule = schedules[0]
        self.assertEqual(schedule.event.source, BOOT_HANDOFF)
        self.assertEqual(schedule.event.target, CPU0_SCHEDULER)
        self.assertEqual(schedule.state_before, ("State", "Online"))
        self.assertEqual(schedule.status, "panic")
        self.assertEqual(schedule.failure.message, "impl sched")
        self.assertEqual(schedule.drives, ())
        self.assertEqual(schedule.emits, ())

    def test_kernel_init_resumes_without_repeating_completed_work(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        enqueues = tuple(
            unit for unit in units if unit.event.signal == ("Action", "Enqueue")
        )
        self.assertEqual(
            tuple(unit.event.target for unit in enqueues),
            (CPU0_SCHEDULER, CPU0_RUNQ),
        )
        self.assertTrue(
            all(
                unit.event.arguments[0].value == "KernelInitTaskRef"
                for unit in enqueues
            )
        )
        self.assertTrue(all(unit.status == "passed" for unit in enqueues))

    def test_derivation_finishes_kernel_init_and_keeps_only_boot_idle_suspended(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        states = {item.object: item.state for item in result.final_state}

        self.assertEqual(result.status, "panic")
        self.assertEqual(result.failure.message, "impl sched")
        self.assertEqual(result.facts, ())
        self.assertEqual(states[CPU0_SCHEDULER], ("State", "Online"))
        self.assertEqual(states[BOOT_TASK], ("State", "OnCpu"))
        self.assertEqual(states[KERNEL_INIT_TASK], ("State", "Online"))
        values = {(item.object, item.field): item.values for item in result.final_values}
        self.assertEqual(values[(CPU0_SCHEDULER, "curr")], (BOOT_TASK_REF,))
        self.assertEqual(values[(CPU0_SCHEDULER, "idle")], (BOOT_TASK_REF,))
        self.assertEqual(values[(CPU0_RUNQ, None)], (KERNEL_INIT_TASK_REF,))
        self.assertNotIn(BOOT_TASK_REF, values[(CPU0_RUNQ, None)])
        self.assertEqual(result.continuations, ())
        self.assertTrue(
            any(
                directive.kind == "panic" and directive.message == "impl sched"
                for unit in units
                for directive in unit.directives
            )
        )


if __name__ == "__main__":
    unittest.main()
