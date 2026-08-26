from __future__ import annotations

from pathlib import Path
import unittest

from derive import default_derivation_sequence, derive, load_user_runtime_signals
from modelc import compile_spec


REPOSITORY = Path(__file__).resolve().parents[2]


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


class EarlyConsoleModelTests(unittest.TestCase):
    def test_default_boot_binds_sbi_early_console_from_committed_tuples(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
        module = next(
            item for item in model.modules if item.name == ("objects", "early_console")
        )
        self.assertEqual(
            {item.name[-1] for item in module.objects},
            {"BootCommandLine", "EarlyConTable", "EarlyConsole", "SbiConsole"},
        )

        with (REPOSITORY / "tools/signals/parked.signals").open(
            encoding="utf-8"
        ) as stream:
            signals = load_user_runtime_signals(stream)
        result = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=signals,
        )
        self.assertEqual(result.status, "yielded")
        path = result.paths[0]
        units = tuple(_all_units(path.units))

        setup_effects = {
            unit.event.target[-1]: tuple(
                (
                    effect.owner[-1],
                    effect.key.value,
                    effect.value.value[-1]
                    if effect.value.kind == "object"
                    else effect.value.value,
                    effect.status,
                )
                for effect in unit.relation_effects
            )
            for unit in units
            if unit.handler == ("Transition", "Setup")
            and unit.event.target[-1] in {"OpenSBI", "Kernel"}
        }
        self.assertEqual(
            setup_effects,
            {
                "OpenSBI": (),
                "Kernel": (
                    ("BootCommandLine", "earlycon", "sbi", "established"),
                    ("EarlyConTable", "sbi", "SbiConsole", "established"),
                ),
            },
        )

        self.assertFalse(
            any(
                unit.event.target[-1] == "SbiConsole"
                and unit.handler == ("Transition", "Enable")
                for unit in units
            )
        )

        early_boot = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyBoot"
            and unit.handler == ("Action", "Enter")
        )
        self.assertEqual(
            tuple(unit.event.target[-1] for unit in early_boot.drives),
            ("EarlyConsole", "Cpu0Scheduler", "InterruptControl"),
        )

        enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(
            tuple(
                (
                    binding.name,
                    binding.value.value[-1]
                    if binding.value is not None and binding.value.kind == "object"
                    else binding.value.value if binding.value is not None else None,
                )
                for binding in enable.bindings
            ),
            (("value", "sbi"), ("backend", "SbiConsole")),
        )

        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["SbiConsole"], ("State", "Online"))
        self.assertEqual(states["EarlyConsole"], ("State", "Online"))
        backend = next(
            item
            for item in path.final_values
            if item.object[-1] == "EarlyConsole" and item.field == "backend"
        )
        self.assertEqual(backend.values[0][-1], "SbiConsole")

        self.assertEqual(
            tuple(
                (item.owner[-1], item.key.value, item.value.value[-1]
                 if item.value.kind == "object" else item.value.value)
                for item in path.tuples
                if item.owner[-1] in {"BootCommandLine", "EarlyConTable"}
            ),
            (
                ("BootCommandLine", "earlycon", "sbi"),
                ("EarlyConTable", "sbi", "SbiConsole"),
            ),
        )
        binding_fact = next(
            fact
            for fact in path.facts
            if fact.predicate[-1] == "early_console_bound_from_registry"
        )
        self.assertEqual(
            tuple(argument.rsplit("::", 1)[-1] for argument in binding_fact.arguments),
            ("EarlyConsole", "SbiConsole"),
        )


if __name__ == "__main__":
    unittest.main()
