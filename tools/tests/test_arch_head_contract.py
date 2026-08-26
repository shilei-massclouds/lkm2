from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from derive import (
    default_derivation_sequence,
    derive,
    load_user_runtime_signals,
)
from modelc import compile_spec


REPOSITORY = Path(__file__).resolve().parents[2]
EXPECTED_CONTRACT = REPOSITORY / "tools/tests/expected.arch_head_contract.json"


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


def _checks(checks):
    return [
        {"expression": check.expression, "status": check.status}
        for check in checks
    ]


class ArchHeadContractGoldenTests(unittest.TestCase):
    def test_opensbi_arch_head_start_kernel_contract(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
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
        self.assertEqual(len(result.paths), 1)

        path = result.paths[0]
        selected = []
        expected_handlers = (
            ("OpenSBI", ("Transition", "Enable")),
            ("ArchHead", ("Action", "Enter")),
            ("StartKernel", ("Action", "Enter")),
        )
        units = tuple(_all_units(path.units))
        matched_units = {}
        for target, handler in expected_handlers:
            matches = tuple(
                unit
                for unit in units
                if unit.event.target[-1] == target and unit.handler == handler
            )
            self.assertEqual(len(matches), 1, (target, handler))
            unit = matches[0]
            matched_units[target] = unit
            selected.append(
                {
                    "depends_on": _checks(unit.depends_on),
                    "ensures": _checks(unit.ensures),
                    "establishes": _checks(unit.establishes),
                    "handler": "::".join(handler),
                    "target": target,
                }
            )

        facts = sorted(
            "::".join(fact.predicate)
            for fact in path.facts
            if fact.predicate[-1] == "opensbi_kernel_entry_handoff_ready"
            or fact.predicate[-1].startswith("arch_head_")
        )
        actual = json.dumps(
            {"facts": facts, "handlers": selected},
            indent=2,
            sort_keys=True,
        ) + "\n"
        self.maxDiff = None
        self.assertEqual(
            actual,
            EXPECTED_CONTRACT.read_text(encoding="utf-8"),
        )

        arch_head = matched_units["ArchHead"]
        self.assertEqual(
            tuple(
                (unit.event.target[-1], unit.event.signal)
                for unit in arch_head.drives
            ),
            (
                ("InterruptControl", ("Action", "MaskAll")),
                ("InterruptControl", ("Action", "ClearPending")),
                ("KernelImage", ("Action", "ClearBss")),
                ("BootTask", ("Action", "ResetCurrent")),
                ("BootStack", ("Transition", "Preset")),
                ("Vm", ("Transition", "Preset")),
                ("Vm", ("Transition", "Setup")),
                ("BootStack", ("Transition", "Setup")),
            ),
        )
        self.assertEqual(path.current_task_ref[-1], "KernelInitTask")
        self.assertEqual(path.current_cpu_ref[-1], "BootCPU")
        self.assertEqual(path.interrupt_controls[0].mode, "Unmasked")
        self.assertEqual(path.interrupt_controls[0].pending, ())
        states = {state.object[-1]: state.state for state in path.final_state}
        self.assertEqual(states["KernelImage"], ("State", "Ready"))
        self.assertEqual(states["BootStack"], ("State", "Ready"))
        self.assertEqual(states["Vm"], ("State", "Ready"))

    def test_arch_head_object_lifecycles_reject_wrong_order(self) -> None:
        replacements = {
            "kernel-image-repeat": (
                "KernelImage.Action::ClearBss;",
                "KernelImage.Action::ClearBss;\n                    KernelImage.Action::ClearBss;",
            ),
            "boot-stack-setup-before-preset": (
                "BootStack.Transition::Preset;\n                    Vm.Transition::Preset;\n                    Vm.Transition::Setup;\n                    BootStack.Transition::Setup;",
                "BootStack.Transition::Setup;\n                    Vm.Transition::Preset;\n                    Vm.Transition::Setup;\n                    BootStack.Transition::Preset;",
            ),
            "vm-setup-before-preset": (
                "Vm.Transition::Preset;\n                    Vm.Transition::Setup;",
                "Vm.Transition::Setup;\n                    Vm.Transition::Preset;",
            ),
        }
        for name, (old, new) in replacements.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                model_root = Path(directory) / "model"
                shutil.copytree(REPOSITORY / "model", model_root)
                arch_head = model_root / "phases" / "arch_head.spec"
                source = arch_head.read_text(encoding="utf-8")
                self.assertIn(old, source)
                arch_head.write_text(source.replace(old, new, 1), encoding="utf-8")
                model = compile_spec(model_root / "main.spec")
                with (REPOSITORY / "tools/signals/parked.signals").open(
                    encoding="utf-8"
                ) as stream:
                    signals = load_user_runtime_signals(stream)
                result = derive(
                    model,
                    default_derivation_sequence(model),
                    user_runtime_signals=signals,
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.paths[0].status, "unhandled_signal")


if __name__ == "__main__":
    unittest.main()
