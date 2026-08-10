from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from derive import DerivationFact, default_derivation_sequence, derive
from modelc import compile_spec


BOOT_FLOW = ("flows", "task_flow", "BootInitFlow")
CPU0_SCHEDULER = ("objects", "scheduler", "Cpu0Scheduler")
IDENTITY_PREDICATE = (
    "objects",
    "scheduler",
    "scheduler_identity_schedule_committed",
)
BOOT_SETUP = ("phases", "start_kernel", "boot_setup", "BootSetup")
BOOT_HANDOFF = ("phases", "start_kernel", "boot_handoff", "BootHandoff")
PHASE_TARGETS = {
    ("phases", "arch_head", "ArchHead"),
    ("phases", "start_kernel", "StartKernel"),
    ("phases", "start_kernel", "early_boot", "EarlyBoot"),
    BOOT_SETUP,
    BOOT_HANDOFF,
    ("phases", "start_kernel", "boot_idle", "BootIdle"),
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

        type_handlers = {
            handler.signal: handler
            for state in scheduler_type.states
            for handler in (*state.transitions, *state.actions)
        }
        object_handlers = {
            handler.signal: handler
            for state in cpu0_scheduler.states
            for handler in (*state.transitions, *state.actions)
        }

        self.assertEqual(scheduler_type.initial_state, ("State", "Ready"))
        self.assertTrue(type_handlers[("Transition", "Enable")].abstract)
        self.assertTrue(type_handlers[("Action", "Schedule")].abstract)
        self.assertTrue(object_handlers[("Transition", "Enable")].override)
        self.assertTrue(object_handlers[("Action", "Schedule")].override)
        self.assertFalse(object_handlers[("Transition", "Enable")].abstract)
        self.assertFalse(object_handlers[("Action", "Schedule")].abstract)
        self.assertIsNone(cpu0_scheduler.parent)

    def test_boot_flow_owns_the_yield_and_handoff_does_not(self) -> None:
        boot_flow = next(item for item in self.model.objects if item.name == BOOT_FLOW)
        boot_handoff = next(
            item for item in self.model.objects if item.name == BOOT_HANDOFF
        )
        enter = boot_flow.states[0].actions[0]
        handoff_enable = next(
            transition
            for state in boot_handoff.states
            for transition in state.transitions
            if transition.signal == ("Transition", "Enable")
        )

        yielded = tuple(
            signal
            for block in enter.blocks
            if block.kind == "yields"
            for signal in block.signals
        )
        self.assertEqual(len(yielded), 1)
        self.assertEqual(yielded[0].source, BOOT_FLOW)
        self.assertEqual(yielded[0].target, CPU0_SCHEDULER)
        self.assertEqual(yielded[0].signal, ("Action", "Schedule"))
        self.assertNotIn("yields", {block.kind for block in handoff_enable.blocks})

    def test_identity_schedule_resumes_once_and_reaches_quiescence(self) -> None:
        result = derive(self.model, default_derivation_sequence(self.model))
        units = tuple(_all_units(result.units))
        states = {item.object: item.state for item in result.final_state}

        self.assertEqual(result.status, "passed")
        self.assertIsNone(result.failure)
        self.assertEqual(result.continuations, ())
        self.assertEqual(states[CPU0_SCHEDULER], ("State", "Online"))
        self.assertEqual(
            result.facts,
            (
                DerivationFact(
                    IDENTITY_PREDICATE,
                    ("Cpu0Scheduler", "BootInitFlow"),
                ),
            ),
        )

        scheduler_enable = next(
            unit
            for unit in units
            if unit.event.source == BOOT_SETUP
            and unit.event.target == CPU0_SCHEDULER
            and unit.event.signal == ("Transition", "Enable")
        )
        self.assertEqual(scheduler_enable.state_before, ("State", "Ready"))
        self.assertEqual(scheduler_enable.state_after, ("State", "Online"))

        yields = tuple(unit for unit in units if unit.event.mode == "yield")
        self.assertEqual(len(yields), 1)
        schedule = yields[0]
        self.assertEqual(schedule.event.source, BOOT_FLOW)
        self.assertEqual(schedule.event.target, CPU0_SCHEDULER)
        self.assertEqual(schedule.establishes[0].status, "established")
        self.assertEqual(len(schedule.emits), 1)

        resumed = schedule.emits[0]
        self.assertEqual(resumed.event.source, CPU0_SCHEDULER)
        self.assertEqual(resumed.event.target, BOOT_FLOW)
        self.assertEqual(resumed.event.signal, ("Action", "Enter"))
        self.assertEqual(resumed.event.mode, "emit")
        self.assertEqual(resumed.ensures[0].status, "passed")
        self.assertIsNotNone(resumed.yield_token_consumed)
        self.assertEqual(resumed.drives, ())

        suspended = next(
            unit
            for unit in units
            if unit.event.target == BOOT_FLOW
            and unit.event.signal == ("Action", "Enter")
            and unit.status == "yielded"
        )
        self.assertEqual(suspended.yields, (schedule,))
        self.assertEqual(
            suspended.yield_token_created, resumed.yield_token_consumed
        )

        phase_enables = tuple(
            unit
            for unit in units
            if unit.event.target in PHASE_TARGETS
            and unit.event.signal == ("Transition", "Enable")
        )
        self.assertEqual(len(phase_enables), len(PHASE_TARGETS))
        self.assertEqual(
            {unit.event.target for unit in phase_enables}, PHASE_TARGETS
        )


if __name__ == "__main__":
    unittest.main()
