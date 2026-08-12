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
KERNEL = ("systems", "kernel", "Kernel")
BOOT_HANDOFF = ("phases", "start_kernel", "boot_handoff", "BootHandoff")
USER_RUN_PHASE = ("phases", "user_run", "UserRunPhase")


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
    while cursor.kind == "path":
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

    def test_boot_and_user_run_yield_to_the_scheduler(self) -> None:
        for name in (BOOT_HANDOFF, USER_RUN_PHASE):
            phase = next(item for item in self.model.objects if item.name == name)
            signal = next(
                signal
                for block in phase.states[0].actions[0].blocks
                if block.kind == "yields"
                for signal in block.signals
            )
            self.assertEqual(_target_name(signal.target), CPU0_SCHEDULER)
            self.assertEqual(signal.signal, ("Action", "Schedule"))

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
        self.assertEqual(boot_resume.state_before, ("State", "OnCpu"))
        self.assertEqual(boot_resume.state_after, ("State", "OnCpu"))
        self.assertEqual(tuple(unit.event.target for unit in boot_resume.resumes), (BOOT_FLOW,))

    def test_default_derivation_runs_full_task_switch_lifecycle(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        self.assertEqual(result.status, "yielded")
        self.assertEqual(len(result.paths), 1)
        path = result.paths[0]
        units = tuple(_all_units(path.units))
        schedules = tuple(
            unit
            for unit in units
            if unit.event.target == CPU0_SCHEDULER
            and unit.event.signal == ("Action", "Schedule")
        )

        self.assertEqual(len(schedules), 2)
        self.assertTrue(all(unit.status == "passed" for unit in schedules))
        self.assertEqual(
            tuple(switch.task for unit in schedules for switch in unit.switches),
            (KERNEL_INIT_TASK, KERNEL_INIT_TASK),
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
            (
                ("Action", "Enqueue"),
                ("Action", "Dequeue"),
                ("Action", "Enqueue"),
                ("Action", "Dequeue"),
            ),
        )

    def test_default_final_scheduler_context_and_continuation(self) -> None:
        path = derive(
            self.model, default_derivation_sequence(self.model)
        ).paths[0]
        states = {item.object: item.state for item in path.final_state}

        self.assertEqual(path.status, "yielded")
        self.assertEqual(states[CPU0_SCHEDULER], ("State", "Online"))
        self.assertEqual(states[BOOT_TASK], ("State", "Online"))
        self.assertEqual(states[KERNEL_INIT_TASK], ("State", "OnCpu"))
        self.assertEqual(len(path.schedulers), 1)
        scheduler = path.schedulers[0]
        self.assertEqual(scheduler.scheduler, CPU0_SCHEDULER)
        self.assertEqual(scheduler.idle_task, BOOT_TASK)
        self.assertEqual(scheduler.current_task, KERNEL_INIT_TASK)
        self.assertEqual(scheduler.runq, ())
        self.assertEqual(tuple(item.root for item in path.continuations), (BOOT_HANDOFF,))


if __name__ == "__main__":
    unittest.main()
