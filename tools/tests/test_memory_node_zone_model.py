from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from checkpointgen import build_checkpoints, load_mapping  # noqa: E402
from modelc import compile_spec  # noqa: E402


ZONE_PREDICATES = {
    "zone_bound_to_unique_memory_node",
    "dma32_zone_bounded_by_32bit_dma_limit",
    "normal_zone_base_bounds_follow_dma32_and_node_limit",
    "movable_zone_empty_or_tail_of_highest_populated_base_zone",
    "node_zone_effective_ranges_are_pairwise_disjoint",
    "node_zone_boundary_envelopes_cover_memory",
    "free_area_bound_to_zone",
    "free_area_excludes_reserved_and_unavailable",
    "zone_lists_bound_to_unique_memory_node",
    "zone_lists_is_single_fallback",
    "zone_lists_orders_populated_zones_descending",
    "zone_lists_excludes_empty_zones",
}

MEM_MAP_PREDICATES = {
    "mem_map_bound_to_unique_memory_node",
    "mem_map_covers_populated_memory",
    "mem_map_preserves_nonallocatable_status",
    "mem_map_zone_ownership_consistent",
}


def _target_name(expression):
    parts = []
    cursor = expression
    while cursor.kind in {"member", "path"}:
        parts.append(cursor.value)
        cursor = cursor.children[0]
    parts.append(cursor.value)
    return tuple(reversed(parts))


def _enable(model_object):
    return next(
        transition
        for state in model_object.states
        for transition in state.transitions
        if transition.signal == ("Transition", "Enable")
    )


def _online(model_object):
    return next(
        state
        for state in model_object.states
        if state.name == ("State", "Online")
    )


def _block(handler, kind):
    return next(item for item in handler.blocks if item.kind == kind)


def _invariant_expressions(model_object):
    return tuple(
        expression
        for invariant in _online(model_object).invariants
        for expression in invariant
    )


def _state_dependencies(expressions):
    dependencies = []
    for expression in expressions:
        if expression.kind != "binary" or expression.value != "==":
            continue
        left, right = expression.children
        if left.kind != "member" or left.value != "state":
            continue
        if _target_name(right) != ("State", "Online"):
            continue
        dependencies.append(_target_name(left.children[0])[-1])
    return tuple(dependencies)


def _predicate_calls(expressions):
    return tuple(
        expression.children[0].value
        for expression in expressions
        if expression.kind == "call"
        and expression.children
        and expression.children[0].kind == "identifier"
    )


def _walk_expression(expression):
    yield expression
    for child in expression.children:
        yield from _walk_expression(child)


class MemoryNodeZoneModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = compile_spec(REPOSITORY / "model/main.spec")
        cls.modules = {module.name: module for module in cls.model.modules}
        cls.memory_node_module = cls.modules[("objects", "memory_node")]
        cls.mem_map_module = cls.modules[("objects", "mem_map")]
        cls.zone_module = cls.modules[("objects", "zone")]
        cls.objects = {
            model_object.name[-1]: model_object
            for model_object in cls.memory_node_module.objects
            if model_object.name[-1]
            in {"MemoryNode", "DMA32Zone", "NormalZone", "MovableZone", "ZoneLists"}
        }
        cls.free_areas = {
            model_object.name[-2]: model_object
            for model_object in cls.memory_node_module.objects
            if model_object.name[-1] == "FreeArea"
        }

    def test_model_has_one_memory_node_and_exactly_three_zones(self) -> None:
        self.assertEqual(
            tuple(item.name[-1] for item in self.memory_node_module.types),
            ("MemoryNodeType",),
        )
        self.assertEqual(
            tuple(item.name[-1] for item in self.memory_node_module.objects),
            (
                "MemoryNode",
                "DMA32Zone",
                "FreeArea",
                "MemMap",
                "MovableZone",
                "FreeArea",
                "NormalZone",
                "FreeArea",
                "ZoneLists",
            ),
        )
        self.assertEqual(
            {item.name[-1] for item in self.zone_module.types},
            {
                "DMA32ZoneType",
                "FreeAreaType",
                "MovableZoneType",
                "NormalZoneType",
                "Zone",
                "ZoneListsType",
            },
        )
        self.assertEqual(
            {
                item.name[-1]
                for item in self.memory_node_module.objects
                if len(item.name) == len(self.memory_node_module.name) + 2
            },
            {
                "DMA32Zone",
                "NormalZone",
                "MovableZone",
                "ZoneLists",
                "MemMap",
            },
        )
        self.assertEqual(
            {item.name for item in self.free_areas.values()},
            {
                ("objects", "memory_node", "MemoryNode", "DMA32Zone", "FreeArea"),
                ("objects", "memory_node", "MemoryNode", "NormalZone", "FreeArea"),
                ("objects", "memory_node", "MemoryNode", "MovableZone", "FreeArea"),
            },
        )

        memory_node = self.objects["MemoryNode"]
        self.assertEqual(memory_node.parent.value, "Kernel")
        for name in ("DMA32Zone", "NormalZone", "MovableZone"):
            with self.subTest(zone=name):
                zone = self.objects[name]
                self.assertEqual(zone.base_type.name, (name + "Type",))
                self.assertEqual(
                    _target_name(zone.parent),
                    ("objects", "memory_node", "MemoryNode"),
                )
        zone_lists = self.objects["ZoneLists"]
        self.assertEqual(zone_lists.base_type.name, ("ZoneListsType",))
        self.assertEqual(
            _target_name(zone_lists.parent),
            ("objects", "memory_node", "MemoryNode"),
        )
        for name in ("DMA32ZoneType", "NormalZoneType", "MovableZoneType"):
            model_type = next(item for item in self.zone_module.types if item.name[-1] == name)
            self.assertEqual(model_type.base_type.name, ("Zone",))
            self.assertEqual(model_type.parent_type.name, ("MemoryNodeType",))

        zone_source = (REPOSITORY / "model/objects/zone.spec").read_text(encoding="utf-8")
        for name in ("DMA32ZoneType", "NormalZoneType", "MovableZoneType"):
            declaration = zone_source.split(f"type {name}: Zone", 1)[1]
            declaration = declaration.split("\n}\n", 1)[0]
            self.assertNotRegex(declaration, r"(?m)^\s*parent\s*:(?!:)")
        self.assertNotIn("object DMA32Zone:", zone_source)
        self.assertNotIn("object NormalZone:", zone_source)
        self.assertNotIn("object MovableZone:", zone_source)

        all_object_names = {
            item.name[-1]
            for module in self.model.modules
            for item in module.objects
        }
        self.assertNotIn("BootMemoryNode", all_object_names)
        self.assertNotIn("DMAZone", all_object_names)
        self.assertNotIn("ThisNode", all_object_names)
        self.assertFalse(
            any("Numa" in name or "NUMA" in name for name in all_object_names)
        )

    def test_node_and_zones_share_minimal_ready_to_online_lifecycle(self) -> None:
        declarations = (
            *self.memory_node_module.types,
            *self.zone_module.types,
            *self.memory_node_module.objects,
            *self.zone_module.objects,
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration.name[-1]):
                self.assertEqual(declaration.initial_state, ("State", "Ready"))
                self.assertEqual(
                    {state.name for state in declaration.states},
                    {("State", "Ready"), ("State", "Online")},
                )
                handlers = tuple(
                    transition
                    for state in declaration.states
                    for transition in state.transitions
                )
                self.assertEqual(len(handlers), 1)
                self.assertEqual(handlers[0].signal, ("Transition", "Enable"))
                self.assertEqual(handlers[0].target_state, ("State", "Online"))

        for model_type in (
            *self.memory_node_module.types,
            *self.zone_module.types,
        ):
            self.assertEqual(model_type.fields, ())
        for model_object in (*self.objects.values(), *self.free_areas.values()):
            self.assertEqual(model_object.attrs, ())
            self.assertEqual(model_object.base_type.arguments, ())

    def test_node_mem_map_has_memory_node_parent_and_complete_lifecycle(self) -> None:
        mem_map = next(
            model_object
            for model_object in self.memory_node_module.objects
            if model_object.name[-1] == "MemMap"
        )
        self.assertEqual(
            mem_map.name,
            ("objects", "memory_node", "MemoryNode", "MemMap"),
        )
        self.assertEqual(mem_map.base_type.name, ("MemMapType",))
        self.assertEqual(
            _target_name(mem_map.parent),
            ("objects", "memory_node", "MemoryNode"),
        )
        mem_map_type = next(
            item
            for item in self.mem_map_module.types
            if item.name[-1] == "MemMapType"
        )
        self.assertEqual(mem_map_type.parent_type.name, ("MemoryNodeType",))
        self.assertEqual(mem_map_type.fields, ())
        self.assertEqual(mem_map.attrs, ())
        self.assertEqual(mem_map.base_type.arguments, ())
        self.assertEqual(
            _state_dependencies(
                _block(_enable(mem_map), "depends_on").expressions
            ),
            (
                "MemoryNode",
                "MemBlockMemory",
                "MemBlockReserved",
                "DMA32Zone",
                "NormalZone",
                "MovableZone",
            ),
        )
        self.assertEqual(
            _state_dependencies(_invariant_expressions(mem_map)),
            (
                "MemoryNode",
                "MemBlockMemory",
                "MemBlockReserved",
                "DMA32Zone",
                "NormalZone",
                "MovableZone",
            ),
        )

    def test_node_mem_map_preserves_population_reservation_and_zone_facts(self) -> None:
        mem_map = next(
            model_object
            for model_object in self.memory_node_module.objects
            if model_object.name[-1] == "MemMap"
        )
        transition = _enable(mem_map)
        depends_on = set(
            _predicate_calls(_block(transition, "depends_on").expressions)
        )
        self.assertEqual(
            depends_on,
            {
                "memory_node_covers_memblock_memory",
                "zone_bound_to_unique_memory_node",
                "dma32_zone_bounded_by_32bit_dma_limit",
                "normal_zone_base_bounds_follow_dma32_and_node_limit",
                "movable_zone_empty_or_tail_of_highest_populated_base_zone",
                "node_zone_effective_ranges_are_pairwise_disjoint",
                "node_zone_boundary_envelopes_cover_memory",
            },
        )
        self.assertEqual(
            _predicate_calls(_block(transition, "establishes").expressions),
            (
                "mem_map_bound_to_unique_memory_node",
                "mem_map_covers_populated_memory",
                "mem_map_preserves_nonallocatable_status",
                "mem_map_zone_ownership_consistent",
            ),
        )
        self.assertTrue(
            set(MEM_MAP_PREDICATES).issubset(
                set(_predicate_calls(_invariant_expressions(mem_map)))
            )
        )

        signatures = {
            predicate.name[-1]: tuple(
                parameter.type.name[-1] for parameter in predicate.parameters
            )
            for predicate in self.mem_map_module.predicates
            if predicate.name[-1] in MEM_MAP_PREDICATES
        }
        self.assertEqual(
            signatures,
            {
                "mem_map_bound_to_unique_memory_node": (
                    "MemMapType",
                    "MemoryNodeType",
                ),
                "mem_map_covers_populated_memory": (
                    "MemMapType",
                    "MemBlockMemoryType",
                ),
                "mem_map_preserves_nonallocatable_status": (
                    "MemMapType",
                    "MemBlockReservedType",
                ),
                "mem_map_zone_ownership_consistent": (
                    "MemMapType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
            },
        )

    def test_global_mem_map_is_one_kernel_alias_of_node_map(self) -> None:
        globals_ = [
            model_object
            for model_object in self.mem_map_module.objects
            if model_object.name[-1] == "GlobalMemMap"
        ]
        self.assertEqual(len(globals_), 1)
        global_mem_map = globals_[0]
        self.assertEqual(global_mem_map.name, ("objects", "mem_map", "GlobalMemMap"))
        self.assertEqual(global_mem_map.base_type.name, ("GlobalMemMapType",))
        self.assertEqual(global_mem_map.parent.value, "Kernel")
        global_type = next(
            item
            for item in self.mem_map_module.types
            if item.name[-1] == "GlobalMemMapType"
        )
        self.assertEqual(global_type.parent_type.name, ("KernelType",))
        transition = _enable(global_mem_map)
        self.assertEqual(
            _state_dependencies(_block(transition, "depends_on").expressions),
            ("MemMap",),
        )
        self.assertEqual(
            _predicate_calls(_block(transition, "establishes").expressions),
            ("global_mem_map_aliases_node_mem_map",),
        )
        alias = next(
            expression
            for expression in _block(transition, "establishes").expressions
            if expression.kind == "call"
        )
        self.assertEqual(_target_name(alias.children[1]), ("GlobalMemMap",))
        self.assertEqual(
            _target_name(alias.children[2])[-2:],
            ("MemoryNode", "MemMap"),
        )
        self.assertEqual(
            tuple(
                predicate.name[-1]
                for predicate in self.mem_map_module.predicates
                if predicate.name[-1].startswith("global_mem_map")
            ),
            ("global_mem_map_aliases_node_mem_map",),
        )

    def test_mem_map_model_has_no_page_or_allocator_layout(self) -> None:
        forbidden = (
            "page",
            "pfn",
            "range",
            "bitmap",
            "free_area",
            "buddy",
            "allocator",
            "order",
        )
        for declaration in (
            *self.mem_map_module.types,
            *self.mem_map_module.objects,
        ):
            name = declaration.name[-1].lower()
            for fragment in forbidden:
                self.assertNotIn(fragment, name)
        self.assertEqual(
            [
                field.name
                for model_type in self.mem_map_module.types
                for field in model_type.fields or ()
            ],
            [],
        )
        self.assertEqual(
            [
                field.name
                for model_object in self.mem_map_module.objects
                for field in model_object.attrs or ()
            ],
            [],
        )

    def test_each_zone_gets_one_bound_empty_capable_free_area(self) -> None:
        for zone_name, free_area in self.free_areas.items():
            with self.subTest(zone=zone_name):
                self.assertEqual(
                    free_area.base_type.name,
                    ("objects", "zone", "FreeAreaType"),
                )
                self.assertEqual(
                    _target_name(free_area.parent),
                    ("objects", "memory_node", "MemoryNode", zone_name),
                )
                depends_on = _block(_enable(free_area), "depends_on").expressions
                self.assertEqual(_state_dependencies(depends_on), (zone_name,))
                self.assertEqual(
                    set(_predicate_calls(depends_on)),
                    {"zone_bound_to_unique_memory_node"},
                )
                established = set(
                    _predicate_calls(
                        _block(_enable(free_area), "establishes").expressions
                    )
                )
                self.assertEqual(
                    established,
                    {
                        "free_area_bound_to_zone",
                        "free_area_excludes_reserved_and_unavailable",
                    },
                )
                invariants = _invariant_expressions(free_area)
                self.assertEqual(_state_dependencies(invariants), (zone_name,))
                self.assertTrue(established.issubset(_predicate_calls(invariants)))
                self.assertNotIn(
                    "parent",
                    {
                        str(node.value)
                        for expression in (*depends_on, *invariants)
                        for node in _walk_expression(expression)
                        if node.kind == "identifier"
                    },
                )

    def test_online_dependencies_follow_the_boundary_fact_chain(self) -> None:
        expected_state_dependencies = {
            "MemoryNode": ("MemBlock",),
            "DMA32Zone": ("MemoryNode",),
            "NormalZone": ("DMA32Zone",),
            "MovableZone": ("DMA32Zone", "NormalZone"),
            "ZoneLists": (
                "MemoryNode",
                "DMA32Zone",
                "NormalZone",
                "MovableZone",
            ),
        }
        for name, expected in expected_state_dependencies.items():
            with self.subTest(object=name):
                depends_on = _block(_enable(self.objects[name]), "depends_on")
                self.assertEqual(
                    _state_dependencies(depends_on.expressions),
                    expected,
                )
                self.assertEqual(
                    _state_dependencies(_invariant_expressions(self.objects[name])),
                    expected,
                )

        normal_dependencies = set(
            _predicate_calls(
                _block(
                    _enable(self.objects["NormalZone"]),
                    "depends_on",
                ).expressions
            )
        )
        self.assertEqual(
            normal_dependencies,
            {
                "zone_bound_to_unique_memory_node",
                "dma32_zone_bounded_by_32bit_dma_limit",
            },
        )

        movable_dependencies = set(
            _predicate_calls(
                _block(
                    _enable(self.objects["MovableZone"]),
                    "depends_on",
                ).expressions
            )
        )
        self.assertEqual(
            movable_dependencies,
            {
                "memory_node_covers_memblock_memory",
                "zone_bound_to_unique_memory_node",
                "dma32_zone_bounded_by_32bit_dma_limit",
                "normal_zone_base_bounds_follow_dma32_and_node_limit",
            },
        )

    def test_each_online_invariant_preserves_its_boundary_facts(self) -> None:
        expected_established = {
            "MemoryNode": {"memory_node_covers_memblock_memory"},
            "DMA32Zone": {
                "zone_bound_to_unique_memory_node",
                "dma32_zone_bounded_by_32bit_dma_limit",
            },
            "NormalZone": {
                "zone_bound_to_unique_memory_node",
                "normal_zone_base_bounds_follow_dma32_and_node_limit",
            },
            "MovableZone": {
                "zone_bound_to_unique_memory_node",
                "movable_zone_empty_or_tail_of_highest_populated_base_zone",
                "node_zone_effective_ranges_are_pairwise_disjoint",
                "node_zone_boundary_envelopes_cover_memory",
            },
            "ZoneLists": {
                "zone_lists_bound_to_unique_memory_node",
                "zone_lists_is_single_fallback",
                "zone_lists_orders_populated_zones_descending",
                "zone_lists_excludes_empty_zones",
            },
        }
        for name, expected in expected_established.items():
            with self.subTest(object=name):
                established = set(
                    _predicate_calls(
                        _block(_enable(self.objects[name]), "establishes").expressions
                    )
                )
                invariants = set(
                    _predicate_calls(_invariant_expressions(self.objects[name]))
                )
                self.assertEqual(established, expected)
                self.assertTrue(established.issubset(invariants))

    def test_predicates_capture_empty_tail_disjoint_and_envelope_semantics(self) -> None:
        self.assertEqual(
            {item.name[-1] for item in self.memory_node_module.predicates},
            {"memory_node_covers_memblock_memory"},
        )
        self.assertEqual(
            {item.name[-1] for item in self.zone_module.predicates},
            ZONE_PREDICATES,
        )

        signatures = {
            predicate.name[-1]: tuple(
                parameter.type.name[-1] for parameter in predicate.parameters
            )
            for predicate in self.zone_module.predicates
        }
        self.assertEqual(
            signatures,
            {
                "zone_bound_to_unique_memory_node": ("Zone", "MemoryNodeType"),
                "dma32_zone_bounded_by_32bit_dma_limit": (
                    "Zone",
                    "MemoryNodeType",
                ),
                "normal_zone_base_bounds_follow_dma32_and_node_limit": (
                    "Zone",
                    "Zone",
                    "MemoryNodeType",
                ),
                "movable_zone_empty_or_tail_of_highest_populated_base_zone": (
                    "Zone",
                    "Zone",
                    "Zone",
                ),
                "node_zone_effective_ranges_are_pairwise_disjoint": (
                    "MemoryNodeType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
                "node_zone_boundary_envelopes_cover_memory": (
                    "MemoryNodeType",
                    "MemBlockMemoryType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
                "free_area_bound_to_zone": ("FreeAreaType", "Zone"),
                "free_area_excludes_reserved_and_unavailable": (
                    "FreeAreaType",
                    "Zone",
                ),
                "zone_lists_bound_to_unique_memory_node": (
                    "ZoneListsType",
                    "MemoryNodeType",
                ),
                "zone_lists_is_single_fallback": ("ZoneListsType",),
                "zone_lists_orders_populated_zones_descending": (
                    "ZoneListsType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
                "zone_lists_excludes_empty_zones": (
                    "ZoneListsType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
            },
        )

        movable_facts = set(
            _predicate_calls(
                _block(
                    _enable(self.objects["MovableZone"]),
                    "establishes",
                ).expressions
            )
        )
        self.assertTrue(
            {
                "movable_zone_empty_or_tail_of_highest_populated_base_zone",
                "node_zone_effective_ranges_are_pairwise_disjoint",
                "node_zone_boundary_envelopes_cover_memory",
            }.issubset(movable_facts)
        )

    def test_zones_do_not_store_page_or_implementation_layout_data(self) -> None:
        for predicate in (
            *self.memory_node_module.predicates,
            *self.zone_module.predicates,
        ):
            self.assertIsNone(predicate.body)

        forbidden_fragments = (
            "address",
            "pfn",
            "page_count",
            "pagecount",
            "node_id",
            "nodeid",
            "zonelist",
            "bitmap",
            "order",
        )
        for declaration in (
            *self.memory_node_module.types,
            *self.memory_node_module.objects,
            *self.zone_module.types,
            *self.zone_module.objects,
        ):
            name = declaration.name[-1].lower()
            if name in {"zonelists", "zoneliststype"}:
                continue
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, name)

        for name in ("DMA32Zone", "NormalZone"):
            established = _predicate_calls(
                _block(_enable(self.objects[name]), "establishes").expressions
            )
            self.assertFalse(any("nonempty" in predicate for predicate in established))

    def test_zone_lists_is_one_fallback_with_descending_populated_projection(self) -> None:
        zone_lists = self.objects["ZoneLists"]
        transition = _enable(zone_lists)
        depends_on = _block(transition, "depends_on").expressions
        self.assertEqual(
            _state_dependencies(depends_on),
            ("MemoryNode", "DMA32Zone", "NormalZone", "MovableZone"),
        )
        self.assertEqual(
            set(_predicate_calls(depends_on)),
            {
                "zone_bound_to_unique_memory_node",
                "memory_node_covers_memblock_memory",
                "dma32_zone_bounded_by_32bit_dma_limit",
                "normal_zone_base_bounds_follow_dma32_and_node_limit",
                "movable_zone_empty_or_tail_of_highest_populated_base_zone",
                "node_zone_effective_ranges_are_pairwise_disjoint",
                "node_zone_boundary_envelopes_cover_memory",
            },
        )
        established = _predicate_calls(_block(transition, "establishes").expressions)
        self.assertEqual(
            established,
            (
                "zone_lists_bound_to_unique_memory_node",
                "zone_lists_is_single_fallback",
                "zone_lists_orders_populated_zones_descending",
                "zone_lists_excludes_empty_zones",
            ),
        )
        order_call = next(
            expression
            for expression in _block(transition, "establishes").expressions
            if expression.children[0].value == "zone_lists_orders_populated_zones_descending"
        )
        self.assertEqual(
            tuple(_target_name(argument)[-1] for argument in order_call.children[2:]),
            ("MovableZone", "NormalZone", "DMA32Zone"),
        )

    def test_downstream_allocator_drafts_are_not_in_the_production_model(self) -> None:
        module_names = {module.name for module in self.model.modules}
        for name in ("free_area", "zone_lists", "buddy_system"):
            with self.subTest(module=name):
                self.assertNotIn(("objects", name), module_names)
                self.assertFalse((REPOSITORY / "model/objects" / f"{name}.spec").exists())
        self.assertFalse(
            (REPOSITORY / "tools/tests/test_buddy_system_model.py").exists()
        )

    def test_early_boot_drives_only_memory_node_and_preserves_checkpoint_mappings(
        self,
    ) -> None:
        early_boot_module = self.modules[("phases", "start_kernel", "early_boot")]
        early_boot = early_boot_module.objects[0]
        enter = next(
            action
            for state in early_boot.states
            for action in state.actions
            if action.signal == ("Action", "Enter")
        )
        drives = _block(enter, "drives")
        self.assertEqual(
            tuple(_target_name(signal.target)[-1] for signal in drives.signals),
            (
                "Banner",
                "DtbBlob",
                "MemBlockMemory",
                "SbiCapability",
                "EarlyConsole",
                "MemBlockReserved",
                "MemBlock",
                "SwapperPageTable",
                "MemoryNode",
                "MemBlock",
                "PageAllocator",
                "Cpu0Scheduler",
                "InterruptControlRef",
            ),
        )
        early_boot_source = (
            REPOSITORY / "model/phases/start_kernel/early_boot.spec"
        ).read_text(encoding="utf-8")
        for name in (
            "DMA32Zone",
            "NormalZone",
            "MovableZone",
            "FreeArea",
            "MemMap",
            "ZoneLists",
        ):
            self.assertNotIn(name, early_boot_source)

        memory_node = self.objects["MemoryNode"]
        memory_node_drives = _block(_enable(memory_node), "drives").signals
        self.assertEqual(
            tuple(_target_name(signal.target)[-1] for signal in memory_node_drives),
            ("DMA32Zone", "NormalZone", "MovableZone", "MemMap", "ZoneLists"),
        )
        for zone_name in ("DMA32Zone", "NormalZone", "MovableZone"):
            with self.subTest(zone=zone_name):
                zone_drives = _block(
                    _enable(self.objects[zone_name]), "drives"
                ).signals
                self.assertEqual(
                    tuple(_target_name(signal.target)[-1] for signal in zone_drives),
                    ("FreeArea",),
                )

        counts = []
        for filename in (
            "vm.json",
            "swapper.json",
            "memblock.json",
            "swapper-content.json",
        ):
            with self.subTest(mapping=filename):
                with (REPOSITORY / "tools/checkpoints" / filename).open(
                    encoding="utf-8"
                ) as stream:
                    mapping = load_mapping(stream)
                counts.append(len(build_checkpoints(self.model, mapping)))
        self.assertEqual(tuple(counts), (28, 9, 13, 3))
        self.assertEqual(sum(counts), 53)


if __name__ == "__main__":
    unittest.main()
