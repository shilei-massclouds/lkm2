from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
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


def _target_name(expression):
    parts = []
    cursor = expression
    while cursor.kind in {"member", "path"}:
        parts.append(cursor.value)
        cursor = cursor.children[0]
    parts.append(cursor.value)
    return tuple(reversed(parts))


def _derive_model(model):
    with (REPOSITORY / "tools/signals/parked.signals").open(
        encoding="utf-8"
    ) as stream:
        signals = load_user_runtime_signals(stream)
    return derive(
        model,
        default_derivation_sequence(model),
        user_runtime_signals=signals,
    ).paths[0]


def _derive_without_qemu_fact(fact):
    with tempfile.TemporaryDirectory() as directory:
        model_root = Path(directory) / "model"
        shutil.copytree(REPOSITORY / "model", model_root)
        path = model_root / "systems/qemu_virt_platform.spec"
        source = path.read_text(encoding="utf-8")
        declaration = f"{fact}(DtbBlob);"
        count = source.count(declaration)
        if count != 2:
            raise AssertionError(
                f"expected two QEMU declarations for {fact}, found {count}"
            )
        path.write_text(source.replace(f"{declaration}\n", ""), encoding="utf-8")
        return _derive_model(compile_spec(model_root / "main.spec"))


class MemBlockModelTests(unittest.TestCase):
    def test_memblock_objects_have_independent_minimal_lifecycles(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
        module = next(
            item for item in model.modules if item.name == ("objects", "memblock")
        )
        types = {item.name[-1]: item for item in module.types}
        objects = {item.name[-1]: item for item in module.objects}

        self.assertEqual(
            set(types),
            {"MemBlockType", "MemBlockMemoryType", "MemBlockReservedType"},
        )
        self.assertEqual(
            set(objects), {"MemBlock", "MemBlockMemory", "MemBlockReserved"}
        )
        for item in types.values():
            self.assertEqual(item.fields, ())
            self.assertEqual(item.initial_state, ("State", "Ready"))
            handlers = tuple(
                transition
                for state in item.states
                for transition in state.transitions
            )
            self.assertEqual(
                tuple((handler.signal, handler.target_state) for handler in handlers),
                ((("Transition", "Enable"), ("State", "Online")),),
            )

        self.assertEqual(objects["MemBlock"].parent.value, "Kernel")
        self.assertEqual(objects["MemBlockMemory"].parent.value, "MemBlock")
        self.assertEqual(objects["MemBlockReserved"].parent.value, "MemBlock")
        for item in objects.values():
            self.assertEqual(item.initial_state, ("State", "Ready"))
            self.assertEqual(item.attrs, ())
            self.assertEqual(item.base_type.arguments, ())
            self.assertNotIn(
                item.base_type.name[-1],
                {"Relation", "Map", "Collection", "Range", "Region", "Segment"},
            )

        memblock_enable = next(
            transition
            for state in objects["MemBlock"].states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(block.kind for block in memblock_enable.blocks),
            ("depends_on",),
        )
        self.assertEqual(
            tuple(
                (
                    _target_name(expression.children[0].children[0])[-1],
                    _target_name(expression.children[1]),
                )
                for expression in memblock_enable.blocks[0].expressions
            ),
            (
                ("MemBlockMemory", ("State", "Online")),
                ("MemBlockReserved", ("State", "Online")),
            ),
        )

        memory_enable = next(
            transition
            for state in objects["MemBlockMemory"].states
            for transition in state.transitions
        )
        reserved_enable = next(
            transition
            for state in objects["MemBlockReserved"].states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(block.kind for block in memory_enable.blocks),
            ("depends_on", "establishes"),
        )
        self.assertEqual(
            tuple(block.kind for block in reserved_enable.blocks),
            ("depends_on", "establishes"),
        )
        self.assertEqual(
            {item.name[-1] for item in module.predicates},
            {
                "memblock_memory_derived_from_dtb",
                "memblock_required_reservations_complete",
            },
        )
        self.assertFalse(
            any(
                name in item.name[-1].lower()
                for item in (*module.types, *module.objects)
                for name in ("range", "region", "segment", "collection")
            )
        )

    def test_default_qemu_boot_stages_memblock_across_paging_init(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
        path = _derive_model(model)
        units = tuple(_all_units(path.units))

        qemu_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "QemuVirtPlatform"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(
            tuple(effect.expression for effect in qemu_enable.establishes),
            (
                "dtb_blob_physical_range_size_at_least(DtbBlob, 1)",
                "dtb_blob_physical_range_valid(DtbBlob)",
                "dtb_blob_describes_nonempty_valid_physical_memory(DtbBlob)",
                "dtb_blob_reserve_map_and_reserved_memory_valid(DtbBlob)",
            ),
        )
        self.assertTrue(
            all(effect.status == "established" for effect in qemu_enable.establishes)
        )

        early_boot = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyBoot"
            and unit.handler == ("Action", "Enter")
        )
        self.assertEqual(
            tuple(unit.event.target[-1] for unit in early_boot.drives),
            (
                "Banner",
                "DtbBlob",
                "MemBlockMemory",
                "SbiCapability",
                "EarlyConsole",
            ),
        )
        paging_init = next(
            unit
            for unit in units
            if unit.event.target[-1] == "PagingInit"
            and unit.handler == ("Action", "Enter")
        )
        self.assertEqual(
            tuple(unit.event.target[-1] for unit in paging_init.drives),
            (
                "MemBlockReserved",
                "MemBlock",
                "FinalPageTable",
                "Cpu0Scheduler",
                "InterruptControl",
            ),
        )
        memblock_enable = paging_init.drives[1]
        self.assertEqual(memblock_enable.drives, ())
        self.assertTrue(
            all(check.status == "passed" for check in memblock_enable.depends_on)
        )

        states = {item.object[-1]: item.state for item in path.final_state}
        for name in ("DtbBlob", "MemBlockMemory", "MemBlockReserved", "MemBlock"):
            self.assertEqual(states[name], ("State", "Online"))

        facts = {fact.predicate[-1]: fact for fact in path.facts}
        memory_fact = facts["memblock_memory_derived_from_dtb"]
        self.assertEqual(
            tuple(argument.rsplit("::", 1)[-1] for argument in memory_fact.arguments),
            ("MemBlockMemory", "DtbBlob"),
        )
        reservation_fact = facts["memblock_required_reservations_complete"]
        self.assertEqual(
            tuple(
                argument.rsplit("::", 1)[-1]
                for argument in reservation_fact.arguments
            ),
            ("MemBlockReserved", "KernelImage", "DtbBlob"),
        )
        self.assertNotIn("memblock_ready_for_allocation", facts)
        self.assertNotIn("ready_for_allocation", facts)

    def assert_paging_failure_stops_later_boot(self, path) -> None:
        units = tuple(_all_units(path.units))
        self.assertFalse(
            any(
                (unit.event.target[-1] == "FinalPageTable")
                or (
                    unit.event.target[-1] == "Cpu0Scheduler"
                    and unit.handler == ("Transition", "Enable")
                )
                or (
                    unit.event.target[-1] == "InterruptControl"
                    and unit.handler == ("Action", "Unmask")
                )
                or unit.event.target[-1] == "BootSetup"
                for unit in units
            )
        )
        self.assertEqual(path.interrupt_controls[0].mode, "Masked")
        self.assertFalse(
            any(
                fact.predicate[-1] == "early_boot_interrupts_enabled"
                for fact in path.facts
            )
        )

    def test_missing_valid_memory_description_commits_no_memblock_state(self) -> None:
        path = _derive_without_qemu_fact(
            "dtb_blob_describes_nonempty_valid_physical_memory"
        )
        units = tuple(_all_units(path.units))
        memory_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "MemBlockMemory"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(path.status, "depends_on_failed")
        self.assertEqual(memory_enable.status, "depends_on_failed")
        self.assertEqual(memory_enable.depends_on[-1].status, "failed")
        self.assertFalse(
            any(unit.event.target[-1] == "MemBlockReserved" for unit in units)
        )
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["DtbBlob"], ("State", "Online"))
        self.assertEqual(states["MemBlockMemory"], ("State", "Ready"))
        self.assertEqual(states["MemBlockReserved"], ("State", "Ready"))
        self.assertEqual(states["MemBlock"], ("State", "Ready"))
        self.assertFalse(
            any(
                fact.predicate[-1] == "memblock_memory_derived_from_dtb"
                for fact in path.facts
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] in {"SbiCapability", "EarlyConsole", "PagingInit"}
                for unit in units
            )
        )
        self.assert_paging_failure_stops_later_boot(path)

    def test_missing_reservation_description_keeps_early_boot_online(self) -> None:
        path = _derive_without_qemu_fact(
            "dtb_blob_reserve_map_and_reserved_memory_valid"
        )
        units = tuple(_all_units(path.units))
        reserved_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "MemBlockReserved"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(path.status, "depends_on_failed")
        self.assertEqual(reserved_enable.status, "depends_on_failed")
        self.assertEqual(reserved_enable.depends_on[-1].status, "failed")
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["MemBlockMemory"], ("State", "Online"))
        self.assertEqual(states["MemBlockReserved"], ("State", "Ready"))
        self.assertEqual(states["MemBlock"], ("State", "Ready"))
        self.assertEqual(states["SbiCapability"], ("State", "Online"))
        self.assertEqual(states["SbiConsole"], ("State", "Online"))
        self.assertEqual(states["EarlyConsole"], ("State", "Online"))
        facts = {fact.predicate[-1] for fact in path.facts}
        self.assertIn("memblock_memory_derived_from_dtb", facts)
        self.assertNotIn("memblock_required_reservations_complete", facts)
        self.assert_paging_failure_stops_later_boot(path)


if __name__ == "__main__":
    unittest.main()
