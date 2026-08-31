from __future__ import annotations

from pathlib import Path
import unittest

from derive import default_derivation_sequence, derive, load_user_runtime_signals
from modelc import compile_spec


REPOSITORY = Path(__file__).resolve().parents[2]

WIRING_PREDICATES = (
    "page_allocator_uses_node_zones",
    "page_allocator_uses_node_mem_map",
    "page_allocator_uses_zone_lists",
    "page_allocator_uses_zone_free_areas",
)

BACKEND_PREDICATES = (
    "memory_node_covers_memblock_memory",
    "zone_bound_to_unique_memory_node",
    "zone_bound_to_unique_memory_node",
    "zone_bound_to_unique_memory_node",
    "dma32_zone_bounded_by_32bit_dma_limit",
    "normal_zone_base_bounds_follow_dma32_and_node_limit",
    "movable_zone_empty_or_tail_of_highest_populated_base_zone",
    "node_zone_effective_ranges_are_pairwise_disjoint",
    "node_zone_boundary_envelopes_cover_memory",
    "free_area_bound_to_zone",
    "free_area_bound_to_zone",
    "free_area_bound_to_zone",
    "free_area_excludes_reserved_and_unavailable",
    "free_area_excludes_reserved_and_unavailable",
    "free_area_excludes_reserved_and_unavailable",
    "mem_map_bound_to_unique_memory_node",
    "mem_map_covers_populated_memory",
    "mem_map_preserves_nonallocatable_status",
    "mem_map_zone_ownership_consistent",
    "zone_lists_bound_to_unique_memory_node",
    "zone_lists_is_single_fallback",
    "zone_lists_orders_populated_zones_descending",
    "zone_lists_excludes_empty_zones",
)

BACKEND_STATE_PATHS = (
    ("MemoryNode",),
    ("MemoryNode", "DMA32Zone"),
    ("MemoryNode", "NormalZone"),
    ("MemoryNode", "MovableZone"),
    ("MemoryNode", "DMA32Zone", "FreeArea"),
    ("MemoryNode", "NormalZone", "FreeArea"),
    ("MemoryNode", "MovableZone", "FreeArea"),
    ("MemoryNode", "MemMap"),
    ("MemoryNode", "ZoneLists"),
)


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


def _state(model_object, name):
    return next(
        state
        for state in model_object.states
        if state.name == ("State", name)
    )


def _block(handler, kind):
    return next(block for block in handler.blocks if block.kind == kind)


def _invariant_expressions(model_object):
    return tuple(
        expression
        for invariant in _state(model_object, "Online").invariants
        for expression in invariant
    )


def _state_dependency_paths(expressions):
    paths = []
    for expression in expressions:
        if expression.kind != "binary" or expression.value != "==":
            continue
        left, right = expression.children
        if left.kind != "member" or left.value != "state":
            continue
        if _target_name(right) != ("State", "Online"):
            continue
        paths.append(_target_name(left.children[0]))
    return tuple(paths)


def _predicate_calls(expressions):
    return tuple(
        expression
        for expression in expressions
        if expression.kind == "call"
        and expression.children[0].kind == "identifier"
    )


def _call_name(expression):
    return expression.children[0].value


def _all_signals(model):
    for module in model.modules:
        for external in module.externals:
            yield from external.signals
        for declaration in (*module.types, *module.objects):
            for state in declaration.states:
                for handler in (*state.transitions, *state.actions):
                    for block in handler.blocks:
                        yield from block.signals


class PageAllocatorModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = compile_spec(REPOSITORY / "model/main.spec")
        cls.modules = {module.name: module for module in cls.model.modules}
        cls.module = cls.modules[("objects", "page_allocator")]
        cls.allocator_type = cls.module.types[0]
        cls.allocator = cls.module.objects[0]

    def test_has_one_kernel_owned_global_interface(self) -> None:
        module_names = tuple(module.name for module in self.model.modules)
        allocator_index = module_names.index(("objects", "page_allocator"))
        self.assertEqual(
            module_names[allocator_index - 1 : allocator_index + 2],
            (
                ("objects", "memory_node"),
                ("objects", "page_allocator"),
                ("objects", "printk"),
            ),
        )
        self.assertEqual(
            tuple(item.name for item in self.module.types),
            (("objects", "page_allocator", "PageAllocatorType"),),
        )
        self.assertEqual(
            tuple(item.name for item in self.module.objects),
            (("objects", "page_allocator", "PageAllocator"),),
        )
        self.assertEqual(self.allocator.base_type.name, ("PageAllocatorType",))
        self.assertEqual(self.allocator.parent.value, "Kernel")
        self.assertEqual(self.allocator_type.parent_type.name, ("KernelType",))
        self.assertEqual(self.allocator_type.fields, ())
        self.assertEqual(self.allocator.attrs, ())
        self.assertEqual(self.allocator.base_type.arguments, ())
        self.assertFalse(
            any(
                item.name[: len(self.allocator.name)] == self.allocator.name
                and len(item.name) > len(self.allocator.name)
                for item in self.model.objects
            )
        )

    def test_ready_to_online_requires_every_backend_and_relation(self) -> None:
        self.assertEqual(self.allocator.initial_state, ("State", "Ready"))
        self.assertEqual(
            {state.name for state in self.allocator.states},
            {("State", "Ready"), ("State", "Online")},
        )
        transitions = tuple(
            transition
            for state in self.allocator.states
            for transition in state.transitions
        )
        self.assertEqual(len(transitions), 1)
        enable = transitions[0]
        self.assertEqual(enable.signal, ("Transition", "Enable"))
        self.assertEqual(enable.target_state, ("State", "Online"))

        dependencies = _block(enable, "depends_on").expressions
        self.assertEqual(_state_dependency_paths(dependencies), BACKEND_STATE_PATHS)
        self.assertEqual(
            tuple(_call_name(call) for call in _predicate_calls(dependencies)),
            BACKEND_PREDICATES,
        )

        invariants = _invariant_expressions(self.allocator)
        self.assertEqual(_state_dependency_paths(invariants), BACKEND_STATE_PATHS)
        self.assertEqual(
            tuple(_call_name(call) for call in _predicate_calls(invariants)),
            BACKEND_PREDICATES + WIRING_PREDICATES,
        )

    def test_establishes_the_four_typed_backend_wirings(self) -> None:
        signatures = {
            predicate.name[-1]: tuple(
                parameter.type.name[-1] for parameter in predicate.parameters
            )
            for predicate in self.module.predicates
        }
        self.assertEqual(
            signatures,
            {
                "page_allocator_uses_node_zones": (
                    "PageAllocatorType",
                    "MemoryNodeType",
                    "Zone",
                    "Zone",
                    "Zone",
                ),
                "page_allocator_uses_node_mem_map": (
                    "PageAllocatorType",
                    "MemMapType",
                ),
                "page_allocator_uses_zone_lists": (
                    "PageAllocatorType",
                    "ZoneListsType",
                ),
                "page_allocator_uses_zone_free_areas": (
                    "PageAllocatorType",
                    "FreeAreaType",
                    "FreeAreaType",
                    "FreeAreaType",
                ),
            },
        )
        self.assertTrue(
            all(predicate.body is None for predicate in self.module.predicates)
        )

        established = _predicate_calls(
            _block(_enable(self.allocator), "establishes").expressions
        )
        self.assertEqual(
            tuple(_call_name(call) for call in established),
            WIRING_PREDICATES,
        )
        arguments = {
            _call_name(call): tuple(
                _target_name(argument) for argument in call.children[1:]
            )
            for call in established
        }
        self.assertEqual(
            arguments,
            {
                "page_allocator_uses_node_zones": (
                    ("self",),
                    ("MemoryNode",),
                    ("MemoryNode", "DMA32Zone"),
                    ("MemoryNode", "NormalZone"),
                    ("MemoryNode", "MovableZone"),
                ),
                "page_allocator_uses_node_mem_map": (
                    ("self",),
                    ("MemoryNode", "MemMap"),
                ),
                "page_allocator_uses_zone_lists": (
                    ("self",),
                    ("MemoryNode", "ZoneLists"),
                ),
                "page_allocator_uses_zone_free_areas": (
                    ("self",),
                    ("MemoryNode", "DMA32Zone", "FreeArea"),
                    ("MemoryNode", "NormalZone", "FreeArea"),
                    ("MemoryNode", "MovableZone", "FreeArea"),
                ),
            },
        )

    def test_uses_node_mem_map_and_zone_owned_free_areas_without_ownership(self) -> None:
        identifiers = {
            str(node.value)
            for expression in (
                *_block(_enable(self.allocator), "depends_on").expressions,
                *_block(_enable(self.allocator), "establishes").expressions,
                *_invariant_expressions(self.allocator),
            )
            for node in _walk_expression(expression)
            if node.kind == "identifier"
        }
        self.assertNotIn("GlobalMemMap", identifiers)

        memory_node_module = self.modules[("objects", "memory_node")]
        owned_paths = {item.name for item in memory_node_module.objects}
        self.assertIn(
            ("objects", "memory_node", "MemoryNode", "MemMap"),
            owned_paths,
        )
        for zone in ("DMA32Zone", "NormalZone", "MovableZone"):
            self.assertIn(
                ("objects", "memory_node", "MemoryNode", zone, "FreeArea"),
                owned_paths,
            )

    def test_online_exposes_only_two_parameterless_abstract_actions(self) -> None:
        self.assertEqual(_state(self.allocator, "Ready").actions, ())
        actions = _state(self.allocator, "Online").actions
        self.assertEqual(
            tuple(action.signal for action in actions),
            (("Action", "AllocPages"), ("Action", "FreePages")),
        )
        for action in actions:
            self.assertTrue(action.abstract)
            self.assertEqual(action.parameters, ())
            self.assertEqual(action.blocks, ())

    def test_does_not_introduce_allocator_storage_or_buddy_objects(self) -> None:
        self.assertEqual(
            [field for model_type in self.module.types for field in model_type.fields],
            [],
        )
        self.assertEqual(
            [field for model_object in self.module.objects for field in model_object.attrs],
            [],
        )
        declaration_names = {
            item.name[-1]
            for module in self.model.modules
            for item in (*module.types, *module.objects)
        }
        for forbidden in ("Page", "PageMetadata", "PFN", "BuddySystem"):
            self.assertNotIn(forbidden, declaration_names)
        self.assertNotIn(("objects", "buddy_system"), self.modules)
        self.assertFalse(
            (REPOSITORY / "model/objects/buddy_system.spec").exists()
        )
        self.assertEqual(
            sum(
                item.name[-1] == "MemoryNode"
                for module in self.model.modules
                for item in module.objects
            ),
            1,
        )

    def test_no_phase_or_external_signal_drives_the_allocator(self) -> None:
        self.assertFalse(
            any(
                "PageAllocator" in _target_name(signal.target)
                for signal in _all_signals(self.model)
            )
        )
        for module in self.model.modules:
            if module.name[:1] == ("phases",):
                source = (
                    REPOSITORY / "model" / Path(*module.name)
                ).with_suffix(".spec")
                if source.exists():
                    self.assertNotIn(
                        "PageAllocator",
                        source.read_text(encoding="utf-8"),
                    )

    def test_default_derivation_leaves_allocator_ready(self) -> None:
        with (REPOSITORY / "tools/signals/parked.signals").open(
            encoding="utf-8"
        ) as stream:
            signals = load_user_runtime_signals(stream)
        path = derive(
            self.model,
            default_derivation_sequence(self.model),
            user_runtime_signals=signals,
        ).paths[0]
        states = {item.object: item.state for item in path.final_state}
        self.assertEqual(states[self.allocator.name], ("State", "Ready"))


def _walk_expression(expression):
    yield expression
    for child in expression.children:
        yield from _walk_expression(child)


if __name__ == "__main__":
    unittest.main()
