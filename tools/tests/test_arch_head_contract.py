from __future__ import annotations

import json
from pathlib import Path
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
        for target, handler in expected_handlers:
            matches = tuple(
                unit
                for unit in units
                if unit.event.target[-1] == target and unit.handler == handler
            )
            self.assertEqual(len(matches), 1, (target, handler))
            unit = matches[0]
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


if __name__ == "__main__":
    unittest.main()
