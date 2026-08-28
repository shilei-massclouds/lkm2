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


def _derive_variant(edits):
    with tempfile.TemporaryDirectory() as directory:
        model_root = Path(directory) / "model"
        shutil.copytree(REPOSITORY / "model", model_root)
        for relative_path, old, new in edits:
            path = model_root / relative_path
            source = path.read_text(encoding="utf-8")
            if old not in source:
                raise AssertionError(f"variant source not found in {relative_path}: {old!r}")
            path.write_text(source.replace(old, new, 1), encoding="utf-8")
        model = compile_spec(model_root / "main.spec")
        return _derive_model(model)


class EarlyConsoleModelTests(unittest.TestCase):
    def assert_console_failure_is_atomic(
        self,
        path,
        *,
        capability_state="Online",
        availability=("sbi_dbcn_available",),
        interrupt_mode="Masked",
    ) -> None:
        units = tuple(_all_units(path.units))
        states = {item.object[-1]: item.state for item in path.final_state}

        self.assertEqual(states["SbiCapability"], ("State", capability_state))
        self.assertEqual(states["SbiConsole"], ("State", "Ready"))
        self.assertEqual(states["EarlyConsole"], ("State", "Ready"))
        self.assertEqual(states["Cpu0Scheduler"], ("State", "Ready"))
        self.assertFalse(
            any(
                item.object[-1] == "EarlyConsole" and item.field == "backend"
                for item in path.final_values
            )
        )
        self.assertEqual(
            tuple(
                fact.predicate[-1]
                for fact in path.facts
                if fact.predicate[-1]
                in {"sbi_dbcn_available", "sbi_v01_console_available"}
            ),
            availability,
        )
        self.assertFalse(
            any(
                fact.predicate[-1]
                in {
                    "early_console_bound_from_registry",
                    "printk_console_registered",
                    "sbi_console_uses_dbcn",
                    "sbi_console_uses_v01",
                }
                for fact in path.facts
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "Cpu0Scheduler"
                and unit.handler == ("Transition", "Enable")
                for unit in units
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "InterruptControl"
                and unit.handler == ("Action", "Unmask")
                for unit in units
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "BootSetup"
                and unit.handler == ("Action", "Enter")
                for unit in units
            )
        )
        self.assertEqual(path.interrupt_controls[0].mode, interrupt_mode)
        self.assertFalse(
            any(
                fact.predicate[-1] == "early_boot_interrupts_enabled"
                for fact in path.facts
            )
        )

    def test_printk_banner_and_console_protocols_are_minimal(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
        printk_module = next(
            item for item in model.modules if item.name == ("objects", "printk")
        )
        printk_objects = {item.name[-1]: item for item in printk_module.objects}
        self.assertEqual(set(printk_objects), {"Banner", "Printk"})

        printk = printk_objects["Printk"]
        self.assertEqual(printk.base_type.name, ("PrintkType",))
        self.assertEqual(printk.initial_state, ("State", "Online"))
        self.assertEqual(printk.parent.value, "Kernel")
        self.assertFalse(
            any(state.transitions or state.actions for state in printk.states)
        )

        banner = printk_objects["Banner"]
        self.assertEqual(banner.base_type.name, ("BannerType",))
        self.assertEqual(banner.initial_state, ("State", "Ready"))
        self.assertEqual(banner.parent.value, "Kernel")
        banner_handlers = tuple(
            transition for state in banner.states for transition in state.transitions
        )
        self.assertEqual(
            tuple(handler.signal for handler in banner_handlers),
            (("Transition", "Enable"),),
        )
        self.assertFalse(any(state.actions for state in banner.states))
        self.assertEqual(
            tuple(block.kind for block in banner_handlers[0].blocks),
            ("ensures",),
        )
        self.assertEqual(len(banner_handlers[0].blocks[0].expressions), 1)

        console_module = next(
            item
            for item in model.modules
            if item.name == ("objects", "early_console")
        )
        self.assertIn("ConsoleType", {item.name[-1] for item in console_module.types})
        self.assertIn(
            "SbiCapabilityType", {item.name[-1] for item in console_module.types}
        )
        self.assertNotIn(
            "EarlyConsoleType", {item.name[-1] for item in console_module.types}
        )
        early_console = next(
            item for item in console_module.objects if item.name[-1] == "EarlyConsole"
        )
        self.assertEqual(early_console.base_type.name, ("ConsoleType",))
        online = next(
            state for state in early_console.states if state.name == ("State", "Online")
        )
        invariant_calls = tuple(
            expression.children[0].value
            for block in online.invariants
            for expression in block
        )
        self.assertEqual(
            invariant_calls,
            (
                "early_console_bound_from_registry",
                "printk_console_registered",
            ),
        )

        predicates = {item.name[-1] for item in console_module.predicates}
        self.assertEqual(
            predicates,
            {
                "early_console_bound_from_registry",
                "printk_console_registered",
                "sbi_dbcn_available",
                "sbi_v01_console_available",
                "sbi_console_uses_dbcn",
                "sbi_console_uses_v01",
            },
        )

        sbi_capability_type = next(
            item
            for item in console_module.types
            if item.name[-1] == "SbiCapabilityType"
        )
        self.assertEqual(sbi_capability_type.initial_state, ("State", "Ready"))

        sbi_capability = next(
            item for item in console_module.objects if item.name[-1] == "SbiCapability"
        )
        self.assertEqual(sbi_capability.base_type.name, ("SbiCapabilityType",))
        self.assertEqual(sbi_capability.initial_state, ("State", "Ready"))
        self.assertEqual(sbi_capability.parent.value, "Kernel")
        capability_handlers = tuple(
            transition
            for state in sbi_capability.states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(
                (handler.signal, handler.target_state)
                for handler in capability_handlers
            ),
            ((("Transition", "Enable"), ("State", "Online")),),
        )
        self.assertFalse(any(state.actions for state in sbi_capability.states))
        self.assertEqual(
            tuple(block.kind for block in capability_handlers[0].blocks),
            ("establishes",),
        )
        availability_effect = capability_handlers[0].blocks[0].expressions[0]
        self.assertEqual(
            availability_effect.children[0].value, "sbi_dbcn_available"
        )
        capability_online = next(
            state
            for state in sbi_capability.states
            if state.name == ("State", "Online")
        )
        self.assertEqual(capability_online.invariants, ())

        sbi_console = next(
            item for item in console_module.objects if item.name[-1] == "SbiConsole"
        )
        self.assertEqual(sbi_console.initial_state, ("State", "Ready"))
        sbi_handlers = tuple(
            transition
            for state in sbi_console.states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple((handler.signal, handler.target_state) for handler in sbi_handlers),
            ((("Transition", "Enable"), ("State", "Online")),),
        )
        self.assertEqual(
            tuple(block.kind for block in sbi_handlers[0].blocks),
            ("depends_on", "establishes"),
        )
        capability_depends = sbi_handlers[0].blocks[0]
        self.assertEqual(len(capability_depends.expressions), 2)
        self.assertEqual(
            capability_depends.expressions[0].children[0].children[0].value,
            "SbiCapability",
        )
        self.assertEqual(capability_depends.expressions[1].value, "||")
        self.assertEqual(
            tuple(
                child.children[0].value
                for child in capability_depends.expressions[1].children
            ),
            ("sbi_dbcn_available", "sbi_v01_console_available"),
        )
        selection = sbi_handlers[0].blocks[1].expressions[0]
        self.assertEqual(selection.children[0].value, "sbi_console_uses_dbcn")

        sbi_online = next(
            state
            for state in sbi_console.states
            if state.name == ("State", "Online")
        )
        self.assertEqual(len(sbi_online.invariants), 1)
        self.assertEqual(
            tuple(expression.value for expression in sbi_online.invariants[0]),
            ("||", "!", "||", "||"),
        )

        enable = next(
            transition
            for state in early_console.states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(block.kind for block in enable.blocks),
            ("depends_on", "binds", "drives", "ensures", "updates", "establishes"),
        )
        depends_on = next(
            block for block in enable.blocks if block.kind == "depends_on"
        )
        self.assertEqual(
            tuple(
                expression.children[0].children[0].value
                for expression in depends_on.expressions
            ),
            ("BootCommandLine", "EarlyConTable"),
        )
        drive = next(
            block.signals[0] for block in enable.blocks if block.kind == "drives"
        )
        self.assertEqual(drive.target.value, "SbiConsole")
        self.assertEqual(drive.signal, ("Transition", "Enable"))
        self.assertEqual(drive.mode, "drive")

    def test_default_boot_binds_sbi_early_console_from_committed_tuples(self) -> None:
        model = compile_spec(REPOSITORY / "model/main.spec")
        console_module = next(
            item for item in model.modules if item.name == ("objects", "early_console")
        )
        self.assertEqual(
            {item.name[-1] for item in console_module.objects},
            {
                "BootCommandLine",
                "EarlyConTable",
                "EarlyConsole",
                "SbiCapability",
                "SbiConsole",
            },
        )

        dtb_module = next(
            item for item in model.modules if item.name == ("objects", "dtb_blob")
        )
        self.assertEqual(
            {item.name[-1] for item in dtb_module.objects},
            {"ChosenBootArgs", "DtbBlob"},
        )
        dtb_objects = {item.name[-1]: item for item in dtb_module.objects}
        dtb_type = next(
            item for item in dtb_module.types if item.name[-1] == "DtbBlobType"
        )
        self.assertEqual(dtb_type.initial_state, ("State", "Ready"))
        self.assertEqual(dtb_objects["DtbBlob"].parent.value, "Kernel")
        self.assertEqual(dtb_objects["ChosenBootArgs"].parent.value, "DtbBlob")
        dtb_enable_model = next(
            transition
            for state in dtb_objects["DtbBlob"].states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(block.kind for block in dtb_enable_model.blocks),
            ("depends_on", "binds", "establishes"),
        )
        dtb_depends = dtb_enable_model.blocks[0].expressions
        self.assertEqual(
            tuple(expression.children[0].value for expression in dtb_depends[2:]),
            (
                "dtb_blob_physical_range_size_at_least",
                "dtb_blob_physical_range_valid",
            ),
        )
        self.assertEqual(dtb_depends[2].children[2].value, 1)

        console_objects = {item.name[-1]: item for item in console_module.objects}
        earlycon_table = console_objects["EarlyConTable"]
        self.assertEqual(earlycon_table.parent.value, "KernelImage")
        self.assertEqual(earlycon_table.initial_state, ("State", "Base"))
        link_handlers = tuple(
            transition
            for state in earlycon_table.states
            for transition in state.transitions
        )
        self.assertEqual(
            tuple(
                (handler.signal, handler.target_state) for handler in link_handlers
            ),
            ((("Transition", "Link"), ("State", "Ready")),),
        )
        self.assertEqual(
            tuple(block.kind for block in link_handlers[0].blocks),
            ("establishes",),
        )
        link_registration = link_handlers[0].blocks[0].expressions[0]
        self.assertEqual(link_registration.children[0].value, "contains")
        self.assertEqual(link_registration.children[1].value, "sbi")
        self.assertEqual(link_registration.children[2].value, "SbiConsole")

        table_ready = next(
            state
            for state in earlycon_table.states
            if state.name == ("State", "Ready")
        )
        self.assertEqual(len(table_ready.invariants), 1)
        self.assertEqual(len(table_ready.invariants[0]), 1)
        ready_registration = table_ready.invariants[0][0]
        self.assertEqual(ready_registration.children[0].value, "contains")
        self.assertEqual(ready_registration.children[1].value, "sbi")
        self.assertEqual(ready_registration.children[2].value, "SbiConsole")

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

        qemu_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "QemuVirtPlatform"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(
            tuple(
                (effect.expression, effect.status)
                for effect in qemu_enable.establishes
            ),
            (
                (
                    "dtb_blob_physical_range_size_at_least(DtbBlob, 1)",
                    "established",
                ),
                ("dtb_blob_physical_range_valid(DtbBlob)", "established"),
            ),
        )

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
                    ("ChosenBootArgs", "earlycon", "sbi", "established"),
                ),
            },
        )
        self.assertFalse(
            any(
                effect.owner[-1] == "BootCommandLine"
                for unit in units
                if unit.handler == ("Transition", "Setup")
                and unit.event.target[-1] in {"OpenSBI", "Kernel"}
                for effect in unit.relation_effects
            )
        )

        kernel_setup = next(
            unit
            for unit in units
            if unit.event.target[-1] == "Kernel"
            and unit.handler == ("Transition", "Setup")
        )
        self.assertEqual(
            tuple(
                (unit.event.target[-1], unit.handler)
                for unit in kernel_setup.drives
            ),
            (("EarlyConTable", ("Transition", "Link")),),
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in kernel_setup.ensures),
            (
                ("EarlyConTable == State::Ready", "passed"),
                ('EarlyConTable.contains("sbi", SbiConsole)', "passed"),
            ),
        )

        link = kernel_setup.drives[0]
        self.assertEqual(
            tuple(
                (
                    effect.owner[-1],
                    effect.key.value,
                    effect.value.value[-1],
                    effect.status,
                )
                for effect in link.relation_effects
            ),
            (("EarlyConTable", "sbi", "SbiConsole", "established"),),
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in link.invariants),
            (('EarlyConTable.contains("sbi", SbiConsole)', "passed"),),
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
                "SbiCapability",
                "EarlyConsole",
                "Cpu0Scheduler",
                "InterruptControl",
            ),
        )

        banner_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "Banner"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in banner_enable.ensures),
            (("Printk == State::Online", "passed"),),
        )

        dtb_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "DtbBlob"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in dtb_enable.depends_on),
            (
                ("ChosenBootArgs == State::Ready", "passed"),
                ("BootCommandLine == State::Ready", "passed"),
                (
                    "dtb_blob_physical_range_size_at_least(DtbBlob, 1)",
                    "passed",
                ),
                ("dtb_blob_physical_range_valid(DtbBlob)", "passed"),
            ),
        )
        self.assertEqual(
            tuple(
                (
                    binding.name,
                    binding.owner[-1],
                    binding.key.value,
                    binding.value.value,
                    binding.status,
                )
                for binding in dtb_enable.bindings
            ),
            (("value", "ChosenBootArgs", "earlycon", "sbi", "passed"),),
        )
        self.assertEqual(
            tuple(
                (
                    effect.owner[-1],
                    effect.key.value,
                    effect.value.value,
                    effect.status,
                )
                for effect in dtb_enable.relation_effects
            ),
            (("BootCommandLine", "earlycon", "sbi", "established"),),
        )
        self.assertEqual(dtb_enable.ensures, ())

        capability_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiCapability"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(capability_enable.status, "passed")
        self.assertEqual(
            tuple(
                (effect.expression, effect.status)
                for effect in capability_enable.establishes
            ),
            (("sbi_dbcn_available(SbiCapability)", "established"),),
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
        self.assertEqual(
            tuple(
                (unit.event.target[-1], unit.handler)
                for unit in enable.drives
            ),
            (("SbiConsole", ("Transition", "Enable")),),
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in enable.ensures),
            (("SbiConsole == State::Online", "passed"),),
        )

        sbi_enable = enable.drives[0]
        self.assertEqual(sbi_enable.status, "passed")
        self.assertEqual(
            tuple((check.expression, check.status) for check in sbi_enable.depends_on),
            (
                ("SbiCapability == State::Online", "passed"),
                (
                    "(sbi_dbcn_available(SbiCapability) || "
                    "sbi_v01_console_available(SbiCapability))",
                    "passed",
                ),
            ),
        )
        self.assertEqual(
            tuple(
                (effect.expression, effect.status)
                for effect in sbi_enable.establishes
            ),
            (("sbi_console_uses_dbcn(SbiConsole)", "established"),),
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in sbi_enable.invariants),
            (
                (
                    "(sbi_console_uses_dbcn(SbiConsole) || "
                    "sbi_console_uses_v01(SbiConsole))",
                    "passed",
                ),
                (
                    "!(sbi_console_uses_dbcn(SbiConsole) && "
                    "sbi_console_uses_v01(SbiConsole))",
                    "passed",
                ),
                (
                    "(!sbi_console_uses_dbcn(SbiConsole) || "
                    "sbi_dbcn_available(SbiCapability))",
                    "passed",
                ),
                (
                    "(!sbi_console_uses_v01(SbiConsole) || "
                    "(sbi_v01_console_available(SbiCapability) && "
                    "!sbi_dbcn_available(SbiCapability)))",
                    "passed",
                ),
            ),
        )

        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["Printk"], ("State", "Online"))
        self.assertEqual(states["Banner"], ("State", "Online"))
        self.assertEqual(states["SbiCapability"], ("State", "Online"))
        self.assertEqual(states["SbiConsole"], ("State", "Online"))
        self.assertEqual(states["DtbBlob"], ("State", "Online"))
        self.assertEqual(states["EarlyConsole"], ("State", "Online"))
        backend = next(
            item
            for item in path.final_values
            if item.object[-1] == "EarlyConsole" and item.field == "backend"
        )
        self.assertEqual(backend.values[0][-1], "SbiConsole")

        self.assertCountEqual(
            tuple(
                (item.owner[-1], item.key.value, item.value.value[-1]
                 if item.value.kind == "object" else item.value.value)
                for item in path.tuples
                if item.owner[-1]
                in {"BootCommandLine", "ChosenBootArgs", "EarlyConTable"}
            ),
            (
                ("BootCommandLine", "earlycon", "sbi"),
                ("ChosenBootArgs", "earlycon", "sbi"),
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
        registration_fact = next(
            fact
            for fact in path.facts
            if fact.predicate[-1] == "printk_console_registered"
        )
        self.assertEqual(
            tuple(
                argument.rsplit("::", 1)[-1]
                for argument in registration_fact.arguments
            ),
            ("Printk", "EarlyConsole"),
        )
        transport_facts = tuple(
            fact
            for fact in path.facts
            if fact.predicate[-1]
            in {
                "sbi_dbcn_available",
                "sbi_v01_console_available",
                "sbi_console_uses_dbcn",
                "sbi_console_uses_v01",
            }
        )
        self.assertEqual(
            tuple(
                (fact.predicate[-1], fact.arguments)
                for fact in transport_facts
            ),
            (
                ("sbi_console_uses_dbcn", ("SbiConsole",)),
                ("sbi_dbcn_available", ("SbiCapability",)),
            ),
        )

    def test_v01_transport_variant_can_enable_and_register_console(self) -> None:
        dbcn_availability = "                    sbi_dbcn_available(self);\n"
        v01_availability = "                    sbi_v01_console_available(self);\n"
        dbcn_selection = "                    sbi_console_uses_dbcn(self);\n"
        v01_selection = "                    sbi_console_uses_v01(self);\n"
        path = _derive_variant(
            (
                (
                    "objects/early_console.spec",
                    dbcn_availability,
                    v01_availability,
                ),
                (
                    "objects/early_console.spec",
                    dbcn_selection,
                    v01_selection,
                ),
            )
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "yielded")
        capability_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiCapability"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(capability_enable.status, "passed")
        self.assertEqual(
            tuple(effect.expression for effect in capability_enable.establishes),
            ("sbi_v01_console_available(SbiCapability)",),
        )
        sbi_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(sbi_enable.status, "passed")
        self.assertEqual(
            tuple(
                (effect.expression, effect.status)
                for effect in sbi_enable.establishes
            ),
            (("sbi_console_uses_v01(SbiConsole)", "established"),),
        )
        transport_facts = tuple(
            fact.predicate[-1]
            for fact in path.facts
            if fact.predicate[-1]
            in {
                "sbi_dbcn_available",
                "sbi_v01_console_available",
                "sbi_console_uses_dbcn",
                "sbi_console_uses_v01",
            }
        )
        self.assertEqual(
            transport_facts,
            ("sbi_console_uses_v01", "sbi_v01_console_available"),
        )
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["SbiCapability"], ("State", "Online"))
        self.assertEqual(states["SbiConsole"], ("State", "Online"))
        self.assertEqual(states["EarlyConsole"], ("State", "Online"))
        self.assertTrue(
            any(
                fact.predicate[-1] == "printk_console_registered"
                for fact in path.facts
            )
        )

    def test_missing_sbi_transport_fails_before_console_commit(self) -> None:
        dbcn_availability = "                    sbi_dbcn_available(self);\n"
        path = _derive_variant(
            (("objects/early_console.spec", dbcn_availability, ""),)
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "depends_on_failed")
        enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyConsole"
            and unit.handler == ("Transition", "Enable")
        )
        sbi_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(enable.status, "stopped")
        self.assertEqual(sbi_enable.status, "depends_on_failed")
        self.assertEqual(sbi_enable.establishes, ())
        self.assertEqual(
            tuple((check.expression, check.status) for check in sbi_enable.depends_on),
            (
                ("SbiCapability == State::Online", "passed"),
                (
                    "(sbi_dbcn_available(SbiCapability) || "
                    "sbi_v01_console_available(SbiCapability))",
                    "failed",
                ),
            ),
        )
        self.assertEqual(sbi_enable.invariants, ())
        self.assert_console_failure_is_atomic(path, availability=())

    def test_simultaneous_transport_capabilities_still_choose_dbcn(self) -> None:
        dbcn_availability = "                    sbi_dbcn_available(self);\n"
        both_availabilities = (
            dbcn_availability
            + "                    sbi_v01_console_available(self);\n"
        )
        path = _derive_variant(
            (
                (
                    "objects/early_console.spec",
                    dbcn_availability,
                    both_availabilities,
                ),
            )
        )

        self.assertEqual(path.status, "yielded")
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["SbiCapability"], ("State", "Online"))
        self.assertEqual(states["SbiConsole"], ("State", "Online"))
        self.assertEqual(states["EarlyConsole"], ("State", "Online"))
        self.assertEqual(
            tuple(
                fact.predicate[-1]
                for fact in path.facts
                if fact.predicate[-1]
                in {
                    "sbi_dbcn_available",
                    "sbi_v01_console_available",
                    "sbi_console_uses_dbcn",
                    "sbi_console_uses_v01",
                }
            ),
            (
                "sbi_console_uses_dbcn",
                "sbi_dbcn_available",
                "sbi_v01_console_available",
            ),
        )
        self.assertTrue(
            any(
                fact.predicate[-1] == "printk_console_registered"
                for fact in path.facts
            )
        )

    def test_ambiguous_sbi_transport_fails_mutual_exclusion_invariant(self) -> None:
        dbcn_selection = "                    sbi_console_uses_dbcn(self);\n"
        both_selections = (
            dbcn_selection + "                    sbi_console_uses_v01(self);\n"
        )
        path = _derive_variant(
            (("objects/early_console.spec", dbcn_selection, both_selections),)
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "invariant_failed")
        sbi_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(sbi_enable.status, "invariant_failed")
        self.assertEqual(
            tuple(effect.status for effect in sbi_enable.establishes),
            ("established", "established"),
        )
        self.assertEqual(
            tuple((check.expression, check.status) for check in sbi_enable.invariants),
            (
                (
                    "(sbi_console_uses_dbcn(SbiConsole) || "
                    "sbi_console_uses_v01(SbiConsole))",
                    "passed",
                ),
                (
                    "!(sbi_console_uses_dbcn(SbiConsole) && "
                    "sbi_console_uses_v01(SbiConsole))",
                    "failed",
                ),
            ),
        )
        self.assert_console_failure_is_atomic(path)

    def test_v01_selection_fails_while_dbcn_is_available(self) -> None:
        dbcn_availability = "                    sbi_dbcn_available(self);\n"
        both_availabilities = (
            dbcn_availability
            + "                    sbi_v01_console_available(self);\n"
        )
        dbcn_selection = "                    sbi_console_uses_dbcn(self);\n"
        v01_selection = "                    sbi_console_uses_v01(self);\n"
        path = _derive_variant(
            (
                (
                    "objects/early_console.spec",
                    dbcn_availability,
                    both_availabilities,
                ),
                (
                    "objects/early_console.spec",
                    dbcn_selection,
                    v01_selection,
                ),
            )
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "invariant_failed")
        sbi_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "SbiConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(sbi_enable.status, "invariant_failed")
        self.assertEqual(
            tuple((check.expression, check.status) for check in sbi_enable.invariants),
            (
                (
                    "(sbi_console_uses_dbcn(SbiConsole) || "
                    "sbi_console_uses_v01(SbiConsole))",
                    "passed",
                ),
                (
                    "!(sbi_console_uses_dbcn(SbiConsole) && "
                    "sbi_console_uses_v01(SbiConsole))",
                    "passed",
                ),
                (
                    "(!sbi_console_uses_dbcn(SbiConsole) || "
                    "sbi_dbcn_available(SbiCapability))",
                    "passed",
                ),
                (
                    "(!sbi_console_uses_v01(SbiConsole) || "
                    "(sbi_v01_console_available(SbiCapability) && "
                    "!sbi_dbcn_available(SbiCapability)))",
                    "failed",
                ),
            ),
        )
        self.assert_console_failure_is_atomic(
            path,
            availability=("sbi_dbcn_available", "sbi_v01_console_available"),
        )

    def test_dtb_rejects_small_or_invalid_qemu_physical_range(self) -> None:
        cases = (
            (
                "smaller than one",
                "dtb_blob_physical_range_size_at_least(DtbBlob, 1);",
                "dtb_blob_physical_range_size_at_least(DtbBlob, 0);",
            ),
            (
                "invalid",
                "dtb_blob_physical_range_valid(DtbBlob);",
                None,
            ),
        )

        for name, requirement, qemu_replacement in cases:
            with self.subTest(name=name):
                edits = tuple(
                    (
                        "systems/qemu_virt_platform.spec",
                        f"{indent}{requirement}\n",
                        "" if qemu_replacement is None
                        else f"{indent}{qemu_replacement}\n",
                    )
                    for indent in ("                    ", "            ")
                )
                path = _derive_variant(edits)
                units = tuple(_all_units(path.units))
                dtb_enable = next(
                    unit
                    for unit in units
                    if unit.event.target[-1] == "DtbBlob"
                    and unit.handler == ("Transition", "Enable")
                )

                self.assertEqual(path.status, "depends_on_failed")
                self.assertEqual(dtb_enable.status, "depends_on_failed")
                self.assertEqual(
                    (
                        dtb_enable.depends_on[-1].expression,
                        dtb_enable.depends_on[-1].status,
                    ),
                    (requirement.removesuffix(";"), "failed"),
                )
                self.assertEqual(dtb_enable.bindings, ())
                self.assertEqual(dtb_enable.relation_effects, ())
                self.assertFalse(
                    any(
                        item.owner[-1] == "BootCommandLine"
                        for item in path.tuples
                    )
                )
                states = {
                    item.object[-1]: item.state for item in path.final_state
                }
                self.assertEqual(states["QemuVirtPlatform"], ("State", "Online"))
                self.assertEqual(states["Banner"], ("State", "Online"))
                self.assertEqual(states["DtbBlob"], ("State", "Ready"))
                self.assert_console_failure_is_atomic(
                    path, capability_state="Ready", availability=()
                )

    def test_ambiguous_bootargs_stops_before_backend_enable(self) -> None:
        chosen_effect = (
            '                    ChosenBootArgs.contains("earlycon", "sbi");\n'
        )
        ambiguous_effects = (
            chosen_effect
            + '                    ChosenBootArgs.contains("earlycon", "other");\n'
        )
        path = _derive_variant(
            (("systems/kernel.spec", chosen_effect, ambiguous_effects),)
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "relation_key_ambiguous")
        dtb_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "DtbBlob"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(dtb_enable.status, "relation_key_ambiguous")
        self.assertEqual(dtb_enable.bindings[0].status, "failed")
        self.assertEqual(
            dtb_enable.bindings[0].failure_code,
            "relation_key_ambiguous",
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "SbiConsole"
                and unit.handler == ("Transition", "Enable")
                for unit in units
            )
        )
        self.assert_console_failure_is_atomic(
            path, capability_state="Ready", availability=()
        )

    def test_unregistered_backend_key_stops_before_backend_enable(self) -> None:
        chosen_effect = (
            '                    ChosenBootArgs.contains("earlycon", "sbi");\n'
        )
        unregistered_effect = (
            '                    ChosenBootArgs.contains("earlycon", "uart");\n'
        )
        path = _derive_variant(
            (("systems/kernel.spec", chosen_effect, unregistered_effect),)
        )
        units = tuple(_all_units(path.units))

        self.assertEqual(path.status, "map_key_missing")
        enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyConsole"
            and unit.handler == ("Transition", "Enable")
        )
        self.assertEqual(enable.status, "map_key_missing")
        self.assertEqual(
            tuple((binding.name, binding.status) for binding in enable.bindings),
            (("value", "passed"), ("backend", "failed")),
        )
        self.assertEqual(enable.bindings[1].failure_code, "map_key_missing")
        self.assertEqual(enable.drives, ())
        self.assert_console_failure_is_atomic(path)

    def test_missing_dtb_bootargs_stops_early_boot_before_console_and_irqs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            kernel_path = model_root / "systems" / "kernel.spec"
            source = kernel_path.read_text(encoding="utf-8")
            declaration = '                    ChosenBootArgs.contains("earlycon", "sbi");\n'
            self.assertIn(declaration, source)
            kernel_path.write_text(source.replace(declaration, "", 1), encoding="utf-8")
            model = compile_spec(model_root / "main.spec")

        with (REPOSITORY / "tools/signals/parked.signals").open(
            encoding="utf-8"
        ) as stream:
            signals = load_user_runtime_signals(stream)
        path = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=signals,
        ).paths[0]
        units = tuple(_all_units(path.units))
        dtb_enable = next(
            unit
            for unit in units
            if unit.event.target[-1] == "DtbBlob"
            and unit.handler == ("Transition", "Enable")
        )

        self.assertEqual(path.status, "relation_key_missing")
        self.assertEqual(dtb_enable.status, "relation_key_missing")
        self.assertEqual(len(dtb_enable.bindings), 1)
        self.assertEqual(dtb_enable.bindings[0].status, "failed")
        self.assertEqual(
            dtb_enable.bindings[0].failure_code,
            "relation_key_missing",
        )
        self.assertEqual(dtb_enable.relation_effects, ())
        self.assertFalse(
            any(item.owner[-1] == "BootCommandLine" for item in path.tuples)
        )
        self.assertFalse(
            any(
                unit.event.target[-1] in {"EarlyConsole", "Cpu0Scheduler"}
                and unit.handler == ("Transition", "Enable")
                for unit in units
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "InterruptControl"
                and unit.handler == ("Action", "Unmask")
                for unit in units
            )
        )
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["Printk"], ("State", "Online"))
        self.assertEqual(states["Banner"], ("State", "Online"))
        self.assertEqual(states["DtbBlob"], ("State", "Ready"))
        self.assertEqual(states["EarlyConsole"], ("State", "Ready"))
        self.assertEqual(states["Cpu0Scheduler"], ("State", "Ready"))
        self.assertEqual(path.interrupt_controls[0].mode, "Masked")
        self.assertFalse(
            any(
                fact.predicate[-1] == "early_boot_interrupts_enabled"
                for fact in path.facts
            )
        )
        self.assert_console_failure_is_atomic(
            path, capability_state="Ready", availability=()
        )

    def test_missing_link_registration_stops_kernel_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            early_console_path = model_root / "objects" / "early_console.spec"
            source = early_console_path.read_text(encoding="utf-8")
            declaration = (
                '                    EarlyConTable.contains("sbi", SbiConsole);\n'
            )
            self.assertIn(declaration, source)
            early_console_path.write_text(
                source.replace(declaration, "", 1), encoding="utf-8"
            )
            model = compile_spec(model_root / "main.spec")

        with (REPOSITORY / "tools/signals/parked.signals").open(
            encoding="utf-8"
        ) as stream:
            signals = load_user_runtime_signals(stream)
        path = derive(
            model,
            default_derivation_sequence(model),
            user_runtime_signals=signals,
        ).paths[0]
        units = tuple(_all_units(path.units))
        kernel_setup = next(
            unit
            for unit in units
            if unit.event.target[-1] == "Kernel"
            and unit.handler == ("Transition", "Setup")
        )
        link = next(
            unit
            for unit in units
            if unit.event.target[-1] == "EarlyConTable"
            and unit.handler == ("Transition", "Link")
        )

        self.assertEqual(path.status, "invariant_failed")
        self.assertEqual(kernel_setup.status, "stopped")
        self.assertEqual(link.status, "invariant_failed")
        self.assertEqual(link.relation_effects, ())
        self.assertEqual(
            tuple((check.expression, check.status) for check in link.invariants),
            (('EarlyConTable.contains("sbi", SbiConsole)', "failed"),),
        )
        states = {item.object[-1]: item.state for item in path.final_state}
        self.assertEqual(states["Kernel"], ("State", "Prepared"))
        self.assertEqual(states["EarlyConTable"], ("State", "Base"))
        self.assertFalse(
            any(item.owner[-1] == "ChosenBootArgs" for item in path.tuples)
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "Kernel"
                and unit.handler == ("Transition", "Enable")
                for unit in units
            )
        )
        self.assertFalse(
            any(
                unit.event.target[-1] == "EarlyBoot"
                and unit.handler == ("Action", "Enter")
                for unit in units
            )
        )
        self.assert_console_failure_is_atomic(
            path,
            capability_state="Ready",
            availability=(),
            interrupt_mode="Unknown",
        )


if __name__ == "__main__":
    unittest.main()
