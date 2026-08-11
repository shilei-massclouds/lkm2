from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from model_ir import (
    ModelAction,
    ModelEntry,
    ModelExpression,
    ModelExternal,
    ModelHandlerBlock,
    ModelIR,
    ModelIRValidationError,
    ModelModule,
    ModelObject,
    ModelSignal,
    ModelState,
    ModelTransition,
    ModelType,
    ModelTypeExpression,
    dump_model_ir,
    load_model_ir,
)
from modelc import CompilationError, SourceSpan, compile_spec, parse_spec
from modelc.cli import main


def _signal(
    source: tuple[str, ...],
    target: tuple[str, ...],
    name: str,
    mode: str,
) -> ModelSignal:
    return ModelSignal(source, target, ("Transition", name), mode)


def _target_name(expression: ModelExpression) -> tuple[str, ...]:
    parts: list[str] = []
    cursor = expression
    while cursor.kind == "path":
        parts.append(str(cursor.value))
        cursor = cursor.children[0]
    parts.append(str(cursor.value))
    return tuple(reversed(parts))


def _block(kind: str, *signals: ModelSignal) -> ModelHandlerBlock:
    return ModelHandlerBlock(kind, signals=signals)


def _transition(
    name: str,
    target: str,
    *blocks: ModelHandlerBlock,
) -> ModelTransition:
    return ModelTransition(("Transition", name), ("State", target), blocks)


def _state(
    name: str,
    transition: ModelTransition | None = None,
) -> ModelState:
    return ModelState(
        ("State", name),
        (),
        () if transition is None else (transition,),
        (),
    )


def _standard_states(
    preset_blocks: tuple[ModelHandlerBlock, ...] = (),
    setup_blocks: tuple[ModelHandlerBlock, ...] = (),
    enable_blocks: tuple[ModelHandlerBlock, ...] = (),
) -> tuple[ModelState, ...]:
    return (
        _state("Base", _transition("Preset", "Prepared", *preset_blocks)),
        _state("Online"),
        _state("Prepared", _transition("Setup", "Ready", *setup_blocks)),
        _state("Ready", _transition("Enable", "Online", *enable_blocks)),
    )


def _object_module(
    path: tuple[str, ...],
    type_name: str,
    object_name: str,
    states: tuple[ModelState, ...],
    *,
    initial_state: str = "Base",
    parent: str | None = None,
) -> ModelModule:
    return ModelModule(
        name=path,
        types=(ModelType(path + (type_name,), None),),
        objects=(
            ModelObject(
                path + (object_name,),
                ModelTypeExpression((type_name,)),
                ("State", initial_state),
                None if parent is None else ModelExpression("identifier", parent),
                None,
                None,
                states,
                (),
            ),
        ),
    )


def _task_flow_module() -> ModelModule:
    online = ("State", "Online")
    enter = ("Action", "Enter")
    return ModelModule(
        name=("flows", "task_flow"),
        types=(
            ModelType(
                ("flows", "task_flow", "TaskFlow"),
                (),
                continuation=True,
                initial_state=online,
                states=(ModelState(online, (), (), (ModelAction(enter, (), True),)),),
            ),
        ),
        objects=(
            ModelObject(
                boot_flow_path,
                ModelTypeExpression(("TaskFlow",)),
                online,
                ModelExpression("identifier", "BootTask"),
                None,
                (),
                (
                    ModelState(
                        online,
                        (),
                        (),
                        (
                            ModelAction(
                                enter,
                                (
                                    ModelHandlerBlock(
                                        "drives",
                                        signals=(
                                            ModelSignal(
                                                boot_flow_path,
                                                arch_head_path,
                                                ("Action", "Enter"),
                                                "drive",
                                            ),
                                        ),
                                    ),
                                ),
                                False,
                                True,
                            ),
                        ),
                    ),
                ),
                (),
                True,
            ),
            ModelObject(
                kernel_init_flow_path,
                ModelTypeExpression(("TaskFlow",)),
                online,
                ModelExpression("identifier", "KernelInitTask"),
                None,
                (),
                (
                    ModelState(
                        online,
                        (),
                        (),
                        (
                            ModelAction(
                                enter,
                                (
                                    ModelHandlerBlock(
                                        "drives",
                                        signals=(
                                            ModelSignal(
                                                kernel_init_flow_path,
                                                kernel_init_phase_path,
                                                enter,
                                                "drive",
                                            ),
                                            ModelSignal(
                                                kernel_init_flow_path,
                                                user_run_phase_path,
                                                enter,
                                                "drive",
                                            ),
                                        ),
                                    ),
                                ),
                                False,
                                True,
                            ),
                        ),
                    ),
                ),
                (),
                True,
            ),
        ),
    )


def _phase_type_module() -> ModelModule:
    return ModelModule(
        name=("phases", "phase"),
        types=(
            ModelType(
                ("phases", "phase", "PhaseType"),
                (),
                continuation=True,
                initial_state=("State", "Online"),
                states=(
                    ModelState(
                        ("State", "Online"),
                        (),
                        (),
                        (ModelAction(("Action", "Enter"), (), abstract=True),),
                    ),
                ),
            ),
        ),
    )


def _phase_object_module(
    path: tuple[str, ...],
    object_name: str,
    drive_targets: tuple[tuple[str, ...], ...] = (),
    *,
    parent: str | None = None,
    extra_blocks: tuple[ModelHandlerBlock, ...] = (),
) -> ModelModule:
    object_path = path + (object_name,)
    blocks: tuple[ModelHandlerBlock, ...] = ()
    if drive_targets:
        blocks = (
            ModelHandlerBlock(
                "drives",
                signals=tuple(
                    ModelSignal(
                        object_path, target, ("Action", "Enter"), "drive"
                    )
                    for target in drive_targets
                ),
            ),
        )
    blocks += extra_blocks
    return ModelModule(
        name=path,
        objects=(
            ModelObject(
                object_path,
                ModelTypeExpression(("PhaseType",)),
                ("State", "Online"),
                None if parent is None else ModelExpression("identifier", parent),
                None,
                (),
                (
                    ModelState(
                        ("State", "Online"),
                        (),
                        (),
                        (
                            ModelAction(
                                ("Action", "Enter"),
                                blocks,
                                override=True,
                            ),
                        ),
                    ),
                ),
                (),
                True,
            ),
        ),
    )


def _arch_head_module() -> ModelModule:
    return _phase_object_module(
        ("phases", "arch_head"),
        "ArchHead",
        (start_kernel_path,),
        parent="BootInitFlow",
    )


def _scheduler_module() -> ModelModule:
    ready = ("State", "Ready")
    boot_running = ("State", "BootTaskRunning")
    kernel_init_running = ("State", "KernelInitTaskRunning")
    enable = ("Transition", "Enable")
    switch_to_boot = ("Transition", "SwitchToBootTask")
    switch_to_kernel_init = ("Transition", "SwitchToKernelInitTask")
    schedule = ("Action", "Schedule")

    def scheduler_states(
        source: tuple[str, ...] | None = None,
    ) -> tuple[ModelState, ...]:
        return (
            ModelState(
                boot_running,
                (),
                (ModelTransition(switch_to_kernel_init, kernel_init_running, ()),),
                (
                    ModelAction(schedule, (), abstract=True)
                    if source is None
                    else ModelAction(
                        schedule,
                        (
                            ModelHandlerBlock(
                                "drives",
                                signals=(
                                    ModelSignal(
                                        source,
                                        boot_task_path,
                                        ("Transition", "Suspend"),
                                        "drive",
                                    ),
                                    ModelSignal(
                                        source,
                                        kernel_init_task_path,
                                        ("Transition", "Resume"),
                                        "drive",
                                    ),
                                    ModelSignal(
                                        source,
                                        cpu0_scheduler_path,
                                        switch_to_kernel_init,
                                        "drive",
                                    ),
                                ),
                            ),
                            ModelHandlerBlock(
                                "emits",
                                signals=(
                                    ModelSignal(
                                        source,
                                        kernel_init_flow_path,
                                        ("Action", "Enter"),
                                        "emit",
                                    ),
                                ),
                            ),
                        ),
                        override=True,
                    ),
                ),
            ),
            ModelState(
                kernel_init_running,
                (),
                (ModelTransition(switch_to_boot, boot_running, ()),),
                (
                    ModelAction(schedule, (), abstract=True)
                    if source is None
                    else ModelAction(
                        schedule,
                        (
                            ModelHandlerBlock(
                                "drives",
                                signals=(
                                    ModelSignal(
                                        source,
                                        kernel_init_task_path,
                                        ("Transition", "Suspend"),
                                        "drive",
                                    ),
                                    ModelSignal(
                                        source,
                                        boot_task_path,
                                        ("Transition", "Resume"),
                                        "drive",
                                    ),
                                    ModelSignal(
                                        source,
                                        cpu0_scheduler_path,
                                        switch_to_boot,
                                        "drive",
                                    ),
                                ),
                            ),
                            ModelHandlerBlock(
                                "emits",
                                signals=(
                                    ModelSignal(
                                        source,
                                        boot_flow_path,
                                        ("Action", "Enter"),
                                        "emit",
                                    ),
                                ),
                            ),
                        ),
                        override=True,
                    ),
                ),
            ),
            ModelState(
                ready,
                (),
                (ModelTransition(enable, boot_running, ()),),
                (),
            ),
        )

    return ModelModule(
        name=("objects", "scheduler"),
        types=(
            ModelType(
                scheduler_type_path,
                (),
                initial_state=ready,
                states=scheduler_states(),
            ),
        ),
        objects=(
            ModelObject(
                cpu0_scheduler_path,
                ModelTypeExpression(("Scheduler",)),
                ready,
                None,
                None,
                (),
                scheduler_states(cpu0_scheduler_path),
                (),
            ),
        ),
    )


def _task_module() -> ModelModule:
    states = (
        _state("Base", _transition("Preset", "Prepared")),
        _state("OnCpu", _transition("Suspend", "Online")),
        _state("Online", _transition("Resume", "OnCpu")),
        _state("Prepared", _transition("Setup", "Ready")),
        _state("Ready", _transition("Enable", "Online")),
    )
    return ModelModule(
        name=("objects", "task"),
        types=(
            ModelType(
                ("objects", "task", "Task"),
                (),
                initial_state=("State", "Base"),
                states=states,
            ),
        ),
        objects=tuple(
            ModelObject(
                path,
                ModelTypeExpression(("Task",)),
                ("State", initial_state),
                ModelExpression("identifier", "Kernel"),
                None,
                (),
                states,
                (),
            )
            for path, initial_state in (
                (boot_task_path, "OnCpu"),
                (kernel_init_task_path, "Base"),
            )
        ),
    )


def _json_module(document: dict, *name: str) -> dict:
    return next(
        module
        for module in document["modules"]
        if module["name"] == list(name)
    )


computer_path = ("systems", "computer", "Computer")
qemu_path = ("systems", "qemu_virt_platform", "QemuVirtPlatform")
opensbi_path = ("systems", "opensbi", "OpenSBI")
kernel_path = ("systems", "kernel", "Kernel")
rootfs_path = ("systems", "rootfs", "RootFs")
boot_flow_path = ("flows", "task_flow", "BootInitFlow")
kernel_init_flow_path = ("flows", "task_flow", "KernelInitFlow")
boot_task_path = ("objects", "task", "BootTask")
kernel_init_task_path = ("objects", "task", "KernelInitTask")
arch_head_path = ("phases", "arch_head", "ArchHead")
kernel_init_phase_path = ("phases", "kernel_init", "KernelInitPhase")
user_run_phase_path = ("phases", "user_run", "UserRunPhase")
start_kernel_path = ("phases", "start_kernel", "StartKernel")
early_boot_path = ("phases", "start_kernel", "early_boot", "EarlyBoot")
boot_setup_path = ("phases", "start_kernel", "boot_setup", "BootSetup")
boot_handoff_path = ("phases", "start_kernel", "boot_handoff", "BootHandoff")
boot_idle_path = ("phases", "start_kernel", "boot_idle", "BootIdle")
scheduler_type_path = ("objects", "scheduler", "Scheduler")
cpu0_scheduler_path = ("objects", "scheduler", "Cpu0Scheduler")
EXPECTED_MODEL = (lambda **_ignored: compile_spec(REPOSITORY / "model" / "main.spec"))(
    schema_version=8,
    entry=ModelEntry(
        origin=("systems", "human", "Human"), spec=("systems",)
    ),
    modules=(
        ModelModule(name=("systems",)),
        _object_module(
            ("systems", "computer"),
            "ComputerType",
            "Computer",
            _standard_states(
                preset_blocks=(
                    _block(
                        "drives",
                        *(
                            _signal(computer_path, target, "Preset", "drive")
                            for target in (
                                qemu_path,
                                opensbi_path,
                                kernel_path,
                                rootfs_path,
                            )
                        ),
                    ),
                ),
                setup_blocks=(
                    _block(
                        "drives",
                        *(
                            _signal(computer_path, target, "Setup", "drive")
                            for target in (
                                qemu_path,
                                opensbi_path,
                                kernel_path,
                                rootfs_path,
                            )
                        ),
                    ),
                ),
                enable_blocks=(
                    _block(
                        "emits",
                        _signal(computer_path, qemu_path, "Enable", "emit"),
                    ),
                ),
            ),
        ),
        ModelModule(
            name=("systems", "human"),
            externals=(
                ModelExternal(
                    ("systems", "human", "Human"),
                    tuple(
                        ModelSignal(
                            ("systems", "human", "Human"),
                            ("systems", "computer", "Computer"),
                            ("Transition", name),
                            mode,
                        )
                        for name, mode in (
                            ("Preset", "drive"),
                            ("Setup", "drive"),
                            ("Enable", "emit"),
                        )
                    ),
                ),
            ),
        ),
        _object_module(
            ("systems", "kernel"),
            "KernelType",
            "Kernel",
            _standard_states(
                enable_blocks=(
                    _block(
                        "emits",
                        ModelSignal(
                            kernel_path,
                            boot_flow_path,
                            ("Action", "Enter"),
                            "emit",
                        ),
                    ),
                ),
            ),
            parent="Computer",
        ),
        ModelModule(name=("flows",)),
        _task_flow_module(),
        ModelModule(name=("objects",)),
        _scheduler_module(),
        _task_module(),
        ModelModule(name=("phases",)),
        _arch_head_module(),
        _phase_object_module(
            ("phases", "kernel_init"),
            "KernelInitPhase",
            extra_blocks=(
                ModelHandlerBlock(
                    "print",
                    expressions=(ModelExpression("string", "kernel init"),),
                ),
            ),
        ),
        _phase_type_module(),
        _phase_object_module(
            ("phases", "start_kernel"),
            "StartKernel",
            (early_boot_path, boot_setup_path, boot_handoff_path, boot_idle_path),
            parent="BootInitFlow",
        ),
        _phase_object_module(
            ("phases", "start_kernel", "boot_handoff"),
            "BootHandoff",
            extra_blocks=(
                ModelHandlerBlock(
                    "yields",
                    signals=(
                        ModelSignal(
                            boot_handoff_path,
                            cpu0_scheduler_path,
                            ("Action", "Schedule"),
                            "yield",
                        ),
                    ),
                ),
            ),
        ),
        _phase_object_module(
            ("phases", "start_kernel", "boot_idle"),
            "BootIdle",
            extra_blocks=(
                ModelHandlerBlock(
                    "yields",
                    signals=(
                        ModelSignal(
                            boot_idle_path,
                            cpu0_scheduler_path,
                            ("Action", "Schedule"),
                            "yield",
                        ),
                    ),
                ),
                ModelHandlerBlock(
                    "panic",
                    expressions=(
                        ModelExpression("string", "boot idle repeated!"),
                    ),
                ),
            ),
        ),
        _phase_object_module(
            ("phases", "start_kernel", "boot_setup"),
            "BootSetup",
            extra_blocks=(
                ModelHandlerBlock(
                    "drives",
                    signals=(
                        ModelSignal(
                            boot_setup_path,
                            cpu0_scheduler_path,
                            ("Transition", "Enable"),
                            "drive",
                        ),
                        ModelSignal(
                            boot_setup_path,
                            kernel_init_task_path,
                            ("Transition", "Preset"),
                            "drive",
                        ),
                        ModelSignal(
                            boot_setup_path,
                            kernel_init_task_path,
                            ("Transition", "Setup"),
                            "drive",
                        ),
                        ModelSignal(
                            boot_setup_path,
                            kernel_init_task_path,
                            ("Transition", "Enable"),
                            "drive",
                        ),
                    ),
                ),
            ),
        ),
        _phase_object_module(
            ("phases", "start_kernel", "early_boot"),
            "EarlyBoot",
            extra_blocks=(
                ModelHandlerBlock(
                    "print", expressions=(ModelExpression("string", "here"),)
                ),
            ),
        ),
        _phase_object_module(
            ("phases", "user_run"),
            "UserRunPhase",
            extra_blocks=(
                ModelHandlerBlock(
                    "yields",
                    signals=(
                        ModelSignal(
                            user_run_phase_path,
                            cpu0_scheduler_path,
                            ("Action", "Schedule"),
                            "yield",
                        ),
                    ),
                ),
            ),
        ),
        _object_module(
            ("systems", "opensbi"),
            "OpenSBIType",
            "OpenSBI",
            _standard_states(
                enable_blocks=(
                    _block(
                        "emits",
                        _signal(opensbi_path, kernel_path, "Enable", "emit"),
                    ),
                ),
            ),
            parent="Computer",
        ),
        _object_module(
            ("systems", "qemu_virt_platform"),
            "QemuVirtPlatformType",
            "QemuVirtPlatform",
            _standard_states(
                enable_blocks=(
                    _block(
                        "emits",
                        _signal(qemu_path, opensbi_path, "Enable", "emit"),
                    ),
                ),
            ),
            parent="Computer",
        ),
        _object_module(
            ("systems", "rootfs"),
            "RootFsType",
            "RootFs",
            _standard_states(),
        ),
    ),
)

_EXPECTED_STREAM = StringIO()
dump_model_ir(EXPECTED_MODEL, _EXPECTED_STREAM)
EXPECTED_JSON = _EXPECTED_STREAM.getvalue()

DEFAULT_ENTRY = "spec root;\norigin root.Root;\n"


@contextmanager
def model_tree(
    files: dict[str, str | bytes],
    entry: str = DEFAULT_ENTRY,
):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        entry_path = root / "main.spec"
        entry_path.write_text(entry, encoding="utf-8")
        for relative_path, contents in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(contents, bytes):
                path.write_bytes(contents)
            else:
                if relative_path == "root.spec" and entry == DEFAULT_ENTRY:
                    contents += "\nexternal Root {}\n"
                path.write_text(contents, encoding="utf-8")
        yield root, entry_path


class ParserAndCompilerTests(unittest.TestCase):
    def test_real_entry_ast_and_ir(self) -> None:
        path = REPOSITORY / "model" / "main.spec"
        source = path.read_text(encoding="utf-8")
        document = parse_spec(source, path)

        self.assertEqual(document.spec.name.parts, ("systems",))
        self.assertEqual(
            tuple(spec.name.parts for spec in document.specs),
            (("systems",), ("objects",), ("phases",), ("flows",)),
        )
        self.assertEqual(
            document.origin.name.parts, ("systems", "human", "Human")
        )
        self.assertEqual(document.spec.name.span, SourceSpan(5, 6, 5, 13))
        self.assertEqual(document.spec.span, SourceSpan(5, 1, 5, 14))
        self.assertEqual(document.origin.name.span, SourceSpan(10, 8, 10, 27))
        self.assertEqual(document.origin.span, SourceSpan(10, 1, 10, 28))
        self.assertEqual(compile_spec(path), EXPECTED_MODEL)

    def test_real_task_lifecycle_and_flow_ownership_are_explicit(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        task_module = next(
            module
            for module in model.modules
            if module.name == ("objects", "task")
        )
        flow_module = next(
            module
            for module in model.modules
            if module.name == ("flows", "task_flow")
        )

        task_type = task_module.types[0]
        boot_task, kernel_init_task = task_module.objects
        task_transitions = {
            state.name: tuple(
                (transition.signal, transition.target_state)
                for transition in state.transitions
            )
            for state in task_type.states
        }

        self.assertEqual(task_type.name[-1], "Task")
        self.assertEqual(task_type.initial_state, ("State", "Base"))
        self.assertEqual(
            task_transitions,
            {
                ("State", "Base"): (
                    (("Transition", "Preset"), ("State", "Prepared")),
                ),
                ("State", "OnCpu"): (
                    (("Transition", "Suspend"), ("State", "Online")),
                ),
                ("State", "Online"): (
                    (("Transition", "Resume"), ("State", "OnCpu")),
                ),
                ("State", "Prepared"): (
                    (("Transition", "Setup"), ("State", "Ready")),
                ),
                ("State", "Ready"): (
                    (("Transition", "Enable"), ("State", "Online")),
                ),
            },
        )
        self.assertEqual(boot_task.name[-1], "BootTask")
        self.assertEqual(boot_task.initial_state, ("State", "OnCpu"))
        self.assertEqual(kernel_init_task.name[-1], "KernelInitTask")
        self.assertEqual(kernel_init_task.initial_state, ("State", "Base"))
        self.assertNotIn(
            ("Transition", "Dispatch"),
            {
                transition.signal
                for state in task_type.states
                for transition in state.transitions
            },
        )

        self.assertEqual(flow_module.types[0].name[-1], "TaskFlow")
        boot_flow, kernel_init_flow = flow_module.objects
        self.assertEqual(boot_flow.name[-1], "BootInitFlow")
        self.assertEqual(
            boot_flow.parent,
            ModelExpression("identifier", "BootTask"),
        )
        self.assertEqual(kernel_init_flow.name[-1], "KernelInitFlow")
        self.assertEqual(
            kernel_init_flow.parent,
            ModelExpression("identifier", "KernelInitTask"),
        )
        self.assertTrue(
            all(
                flow.initial_state == ("State", "Online")
                for flow in flow_module.objects
            )
        )

    def test_kernel_init_phase_modules_are_registered_and_driven_in_order(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        modules = {module.name: module for module in model.modules}
        kernel_init_phase = modules[("phases", "kernel_init")].objects[0]
        user_run_phase = modules[("phases", "user_run")].objects[0]
        kernel_init_flow = next(
            item for item in model.objects if item.name == kernel_init_flow_path
        )

        self.assertEqual(kernel_init_phase.name, kernel_init_phase_path)
        self.assertEqual(user_run_phase.name, user_run_phase_path)
        driven = tuple(
            _target_name(signal.target)
            for block in kernel_init_flow.states[0].actions[0].blocks
            if block.kind == "resumes"
            for signal in block.signals
        )
        self.assertEqual(driven, (kernel_init_phase_path, user_run_phase_path))
        printed = kernel_init_phase.states[0].actions[0].blocks[0]
        self.assertEqual(printed.kind, "print")
        self.assertEqual(printed.expressions, (ModelExpression("string", "kernel init"),))
        yielded = user_run_phase.states[0].actions[0].blocks[0]
        self.assertEqual(yielded.kind, "yields")
        self.assertEqual(_target_name(yielded.signals[0].target), cpu0_scheduler_path)
        self.assertEqual(yielded.signals[0].signal, ("Action", "Schedule"))

    def test_real_phase_type_and_arch_head_override_are_preserved(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        phase_type = next(
            module.types[0]
            for module in model.modules
            if module.name == ("phases", "phase")
        )
        arch_head = next(
            module.objects[0]
            for module in model.modules
            if module.name == ("phases", "arch_head")
        )

        abstract_enter = next(
            state.actions[0]
            for state in phase_type.states
            if state.name == ("State", "Online")
        )
        concrete_enter = next(
            state.actions[0]
            for state in arch_head.states
            if state.name == ("State", "Online")
        )
        self.assertTrue(phase_type.continuation)
        self.assertTrue(abstract_enter.abstract)
        self.assertTrue(arch_head.continuation)
        self.assertTrue(concrete_enter.override)
        self.assertFalse(concrete_enter.abstract)

    def test_comments_whitespace_and_long_origin(self) -> None:
        document = parse_spec(
            """
            // entry namespace
            spec alpha; /* between declarations */
            spec beta;
            origin alpha.beta_gamma.Person2; // end
            """
        )
        self.assertEqual(document.spec.name.parts, ("alpha",))
        self.assertEqual(
            tuple(spec.name.parts for spec in document.specs),
            (("alpha",), ("beta",)),
        )
        self.assertEqual(
            document.origin.name.parts, ("alpha", "beta_gamma", "Person2")
        )

    def test_entry_can_compose_peer_root_modules(self) -> None:
        files = {
            "root.spec": "external Origin {}",
            "objects.spec": "type Task;",
            "flows.spec": "type TaskFlow;",
        }
        entry = (
            "spec root;\n"
            "spec objects;\n"
            "spec flows;\n"
            "origin root.Origin;\n"
        )
        with model_tree(files, entry) as (_, entry_path):
            model = compile_spec(entry_path)

        self.assertEqual(model.entry.spec, ("root",))
        self.assertEqual(
            tuple(module.name for module in model.modules),
            (("flows",), ("objects",), ("root",)),
        )

    def test_duplicate_entry_root_is_rejected(self) -> None:
        entry = "spec root;\nspec root;\norigin root.Root;\n"
        with model_tree({"root.spec": "external Root {}"}, entry) as (
            _,
            entry_path,
        ):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)
        self.assertIn(
            "duplicate root module declaration 'root'",
            caught.exception.diagnostic.message,
        )

    def test_syntax_errors_have_source_positions(self) -> None:
        cases = [
            ("spec systems\norigin systems.Human;", 2, 1),
            ("origin systems.Human;\nspec systems;", 1, 1),
            ("spec bad-name;\norigin systems.Human;", 1, 9),
            ("spec systems;\n/* unterminated", 2, 1),
        ]
        for source, line, column in cases:
            with self.subTest(source=source):
                with self.assertRaises(CompilationError) as caught:
                    parse_spec(source, "bad.spec")
                self.assertEqual(caught.exception.diagnostic.path, "bad.spec")
                self.assertEqual(caught.exception.diagnostic.line, line)
                self.assertEqual(caught.exception.diagnostic.column, column)

    def test_recursive_explicit_modules_and_strict_bodies(self) -> None:
        files = {
            "root.spec": """
                external Origin {}
                predicate text() -> bool {
                    "spec fake; use missing::Fake; { }";
                }
                // spec commented;
                /* use missing::Commented; */
                object Body: BodyType {
                    initial_state: State::Base;
                    state State::Base {
                        invariant { "use missing::Nested; }"; }
                    }
                }
                spec alpha;
                use root::alpha::deep::UnknownMember;
            """,
            "root/alpha.spec": "predicate before(x: T) -> bool; spec deep;",
            "root/alpha/deep.spec": "object UnknownMember: MemberType {}",
            "root/orphan.spec": "spec missing_file; use bad::path::Item;",
        }
        entry = "spec root;\norigin root.Origin;\n"
        with model_tree(files, entry) as (_, entry_path):
            model = compile_spec(entry_path)

        self.assertEqual(
            tuple(module.name for module in model.modules),
            (("root",), ("root", "alpha"), ("root", "alpha", "deep")),
        )

    def test_all_simple_use_roots_and_forward_module_references(self) -> None:
        files = {
            "root.spec": "spec a; spec b;",
            "root/a.spec": """
                spec child;
                use model::root::b::FromModel;
                use self::child::FromSelf;
                use super::b::FromSuper;
                use root::b::FromBare;
            """,
            "root/a/child.spec": "use super::super::b::FromRepeatedSuper;",
            "root/b.spec": "use self::MemberThatIsNotValidated;",
        }
        with model_tree(files) as (_, entry_path):
            model = compile_spec(entry_path)
        self.assertEqual(len(model.modules), 4)

    def test_module_and_use_diagnostics(self) -> None:
        cases = [
            (
                {"root.spec": "spec absent;"},
                "module 'root.absent' not found",
            ),
            (
                {"root.spec": "spec bad-name;"},
                "unexpected token '-'",
            ),
            (
                {"root.spec": "spec child; spec child;", "root/child.spec": ""},
                "duplicate module declaration 'child'",
            ),
            (
                {"root.spec": "use super::super::Thing;"},
                "too many leading 'super'",
            ),
            (
                {"root.spec": "use crate::root::Thing;"},
                "'crate' is no longer supported in use paths; use 'model' instead",
            ),
            (
                {"root.spec": "use root::missing::Thing;"},
                "cannot resolve module 'root.missing'",
            ),
            (
                {
                    "root.spec": "spec a; spec b; use root::a::Thing; use root::b::Thing;",
                    "root/a.spec": "",
                    "root/b.spec": "",
                },
                "duplicate local import name 'Thing'",
            ),
            ({"root.spec": "use root::Thing as Alias;"}, "unexpected token 'as'"),
            ({"root.spec": "use root::*;"}, "unexpected token '*'"),
            ({"root.spec": "use root::{Thing};"}, "unexpected token '{'"),
            ({"root.spec": "pub use root::Thing;"}, "unexpected token 'pub'"),
            (
                {"root.spec": "pub(crate) use root::Thing;"},
                "unexpected token 'pub'",
            ),
            (
                {"root.spec": 'include "child.spec";'},
                "unexpected token 'include'",
            ),
            ({"root.spec": "use self;"}, "use path cannot end with 'self'"),
            (
                {"root.spec": "use root::super::Thing;"},
                "'super' is only allowed at the start",
            ),
        ]
        for files, message in cases:
            with self.subTest(message=message):
                with model_tree(files) as (_, entry_path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(entry_path)
                self.assertIn(message, caught.exception.diagnostic.message)

    def test_source_structure_and_utf8_diagnostics(self) -> None:
        cases: list[tuple[str | bytes, str]] = [
            (b"\xff", "source is not valid UTF-8"),
            ("/* no end", "unterminated block comment"),
            ('const value = "no end', "unterminated string literal"),
            ("object X {", "unclosed delimiter '{'"),
            ("object X }", "unmatched closing delimiter '}'"),
            ("object X { value: (]; }", "mismatched closing delimiter ']'"),
        ]
        for source, message in cases:
            with self.subTest(message=message):
                with model_tree({"root.spec": source}) as (_, entry_path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(entry_path)
                self.assertIn(message, caught.exception.diagnostic.message)

    def test_duplicate_physical_module_file_is_rejected(self) -> None:
        with model_tree(
            {
                "root.spec": "spec first; spec second;",
                "root/first.spec": "",
            }
        ) as (root, entry_path):
            (root / "root" / "second.spec").symlink_to("first.spec")
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)
        self.assertIn("already loaded as 'root.first'", caught.exception.diagnostic.message)

    def test_unknown_declarations_keywords_and_invalid_syntax_are_rejected(self) -> None:
        invalid_sources = [
            "unknown declaration;",
            "object Example: ExampleType { unknown_block {} }",
            "predicate broken( -> bool;",
            "type Broken { field Size; }",
            "object Example: ExampleType { state State::Base { invariant { value == ; } } }",
        ]
        for source in invalid_sources:
            with self.subTest(source=source):
                with model_tree({"root.spec": source}) as (_, entry_path):
                    with self.assertRaises(CompilationError):
                        compile_spec(entry_path)

    def test_establishes_blocks_lower_to_model_ir_in_source_order(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    predicate first() -> bool;
                    predicate second() -> bool;
                    object Computer: T {
                        initial_state: State::Idle;
                        state State::Idle {
                            transitions {
                                on Transition::Go -> State::Ready {
                                    establishes { first(); second(); }
                                }
                            }
                        }
                        state State::Ready {}
                    }
                """
            }
        ) as (_, entry_path):
            model = compile_spec(entry_path)
        block = model.objects[0].states[0].transitions[0].blocks[0]
        self.assertEqual(block.kind, "establishes")
        self.assertEqual(
            tuple(expression.children[0].value for expression in block.expressions),
            ("first", "second"),
        )
        output = StringIO()
        dump_model_ir(model, output)
        self.assertEqual(load_model_ir(StringIO(output.getvalue())), model)

    def test_stateful_object_defaults_initial_state_to_base(self) -> None:
        with model_tree(
            {"root.spec": "object Computer: T { state State::Base {} }"}
        ) as (_, entry_path):
            model = compile_spec(entry_path)

        self.assertEqual(model.objects[0].initial_state, ("State", "Base"))

    def test_explicit_initial_state_is_not_overridden(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    object Computer: T {
                        initial_state: State::Idle;
                        state State::Base {}
                        state State::Idle {}
                    }
                """
            }
        ) as (_, entry_path):
            model = compile_spec(entry_path)

        self.assertEqual(model.objects[0].initial_state, ("State", "Idle"))

    def test_stateless_object_keeps_null_initial_state(self) -> None:
        with model_tree({"root.spec": "object Computer: T {}"}) as (
            _,
            entry_path,
        ):
            model = compile_spec(entry_path)

        self.assertIsNone(model.objects[0].initial_state)

    def test_default_initial_state_requires_a_base_state(self) -> None:
        with model_tree(
            {"root.spec": "object Computer: T { state State::Idle {} }"}
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)

        self.assertIn(
            "invalid initial_state 'State::Base'",
            caught.exception.diagnostic.message,
        )

    def test_action_handlers_and_signals_lower_to_strict_names(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    object Computer: T {
                        initial_state: State::Idle;
                        state State::Idle {
                            actions { on Action::Refresh { drives {} } }
                        }
                    }
                    external Human { emits { Computer.Action::Refresh; } }
                """
            },
            entry="spec root;\norigin root.Human;\n",
        ) as (_, entry_path):
            model = compile_spec(entry_path)

        action = model.objects[0].states[0].actions[0]
        signal = model.externals[0].signals[0]
        self.assertEqual(action.signal, ("Action", "Refresh"))
        self.assertEqual(signal.signal, ("Action", "Refresh"))

        output = StringIO()
        dump_model_ir(model, output)
        self.assertEqual(load_model_ir(StringIO(output.getvalue())), model)
        document = json.loads(output.getvalue())
        state = document["modules"][0]["objects"][0]["states"][0]
        self.assertEqual(state["actions"][0]["signal"], ["Action", "Refresh"])

        state["actions"][0]["signal"] = ["Transition", "Refresh"]
        with self.assertRaises(ModelIRValidationError):
            load_model_ir(StringIO(json.dumps(document)))

    def test_startup_alias_is_canonicalized_only_for_signal_calls(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    object Flow: T {
                        state State::Base {
                            transitions {
                                on Transition::Preset -> State::Online {
                                    drives { Flow.Transition::Startup; }
                                    emits { Flow.Transition::Startup; }
                                }
                            }
                            actions { on Action::Startup { drives {} } }
                        }
                        state State::Online {}
                    }
                    external Human {
                        drives {
                            Flow.Transition::Startup;
                            Flow.Transition::startup;
                            Flow.Action::Startup;
                        }
                    }
                """
            },
            entry="spec root;\norigin root.Human;\n",
        ) as (_, entry_path):
            model = compile_spec(entry_path)

        state = model.objects[0].states[0]
        transition = state.transitions[0]
        self.assertEqual(
            tuple(signal.signal for block in transition.blocks for signal in block.signals),
            (("Transition", "Preset"), ("Transition", "Preset")),
        )
        self.assertEqual(state.actions[0].signal, ("Action", "Startup"))
        self.assertEqual(
            tuple(signal.signal for signal in model.externals[0].signals),
            (
                ("Transition", "Preset"),
                ("Transition", "startup"),
                ("Action", "Startup"),
            ),
        )

    def test_startup_alias_is_rejected_as_a_transition_handler_name(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    object Flow: T {
                        state State::Base {
                            transitions {
                                on Transition::Startup -> State::Online {}
                            }
                        }
                        state State::Online {}
                    }
                """
            }
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)

        self.assertIn(
            "transition handler signal Transition::Startup is non-canonical; "
            "use Transition::Preset",
            caught.exception.diagnostic.message,
        )

    def test_model_root_prefix_resolves_signal_targets(self) -> None:
        files = {
            "root.spec": """
                spec child;
                external Human {
                    emits {
                        model::root::child::Computer.Action::Refresh;
                    }
                }
            """,
            "root/child.spec": """
                object Computer: T {
                    initial_state: State::Idle;
                    state State::Idle {
                        actions { on Action::Refresh { drives {} } }
                    }
                }
            """,
        }
        entry = "spec root;\norigin root.Human;\n"
        with model_tree(files, entry) as (_, entry_path):
            model = compile_spec(entry_path)

        self.assertEqual(
            _target_name(model.externals[0].signals[0].target),
            ("root", "child", "Computer"),
        )

    def test_action_handler_rejects_non_action_expressions(self) -> None:
        for accepted_signal in ("Transition::Refresh", "refresh()"):
            with self.subTest(accepted_signal=accepted_signal):
                with model_tree(
                    {
                        "root.spec": f"""
                            object Computer: T {{
                                initial_state: State::Idle;
                                state State::Idle {{
                                    actions {{ on {accepted_signal} {{}} }}
                                }}
                            }}
                        """
                    }
                ) as (_, entry_path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(entry_path)
                self.assertIn(
                    "accepted signal must have the form Action::<Name>",
                    caught.exception.diagnostic.message,
                )

    def test_type_inheritance_expands_fields_states_invariants_and_handlers(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    type Base {
                        first: A;
                        initial_state: State::Idle;
                        state State::Idle {
                            invariant { true; }
                            actions { on Action::Enter; }
                        }
                    }
                    type Mid: Base {
                        first: B;
                        second: C;
                        state State::Idle {
                            invariant { true; }
                            actions {
                                override on Action::Enter { drives {} }
                            }
                        }
                    }
                    type Leaf: Mid {
                        state State::Idle {
                            actions { on Action::Next { drives {} } }
                        }
                    }
                    object Flow: Leaf {
                        attrs { second: D; third: E; }
                        state State::Idle { invariant { false; } }
                    }
                """
            }
        ) as (_, entry_path):
            model = compile_spec(entry_path)

        flow = model.objects[0]
        self.assertEqual(
            tuple((field.name, field.type.name) for field in flow.attrs or ()),
            (("first", ("B",)), ("second", ("D",)), ("third", ("E",))),
        )
        self.assertEqual(flow.initial_state, ("State", "Idle"))
        self.assertEqual(len(flow.states[0].invariants), 3)
        self.assertEqual(
            tuple(action.signal for action in flow.states[0].actions),
            (("Action", "Enter"), ("Action", "Next")),
        )

    def test_type_inheritance_rejects_abstract_override_and_cycle_errors(self) -> None:
        cases = (
            (
                """
                    type Base {
                        state State::Base { actions { on Action::Enter; } }
                    }
                    object Flow: Base {}
                """,
                "does not implement abstract handler",
            ),
            (
                """
                    type Base {
                        state State::Base {
                            actions { on Action::Enter { drives {} } }
                        }
                    }
                    type Child: Base {
                        state State::Base {
                            actions { on Action::Enter { drives {} } }
                        }
                    }
                """,
                "must be declared with override",
            ),
            (
                """
                    type Base {
                        state State::Base {
                            actions {
                                override on Action::Enter { drives {} }
                            }
                        }
                    }
                """,
                "has no inherited handler",
            ),
            ("type First: Second {} type Second: First {}", "inheritance cycle"),
        )
        for source, message in cases:
            with self.subTest(message=message):
                with model_tree({"root.spec": source}) as (_, entry_path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(entry_path)
                self.assertIn(message, caught.exception.diagnostic.message)

    def test_phase_type_requires_an_enable_override(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    type PhaseType {
                        initial_state: State::Ready;
                        state State::Ready {
                            transitions { on Transition::Enable; }
                        }
                        state State::Online {}
                    }
                    object MissingPhase: PhaseType {}
                """
            }
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)

        self.assertIn(
            "does not implement abstract handler State::Ready + "
            "Transition::Enable",
            caught.exception.diagnostic.message,
        )

    def test_continuation_declaration_lifecycle_and_yields_are_strict(self) -> None:
        invalid = (
            (
                "type Flow { continuation: false; }",
                "can only be declared as true",
            ),
            (
                "type Flow; object F: Flow { continuation: true; }",
                "may only be declared by a type",
            ),
            (
                """
                    type Flow {
                        continuation: true;
                        state State::Base { actions { on Action::Enter; } }
                    }
                """,
                "continuation type must have exactly",
            ),
            (
                """
                    object Target: T { state State::Base {} }
                    object Source: T {
                        state State::Base {
                            actions {
                                on Action::Go { yields Target.Transition::Go; }
                            }
                        }
                    }
                """,
                "yields is only allowed",
            ),
            (
                """
                    type Flow {
                        continuation: true;
                        initial_state: State::Online;
                        state State::Online { actions { on Action::Enter; } }
                    }
                    object F: Flow {
                        state State::Online {
                            actions {
                                override on Action::Enter { drives {} }
                            }
                        }
                    }
                    external Human { emits { F.Action::Other; } }
                """,
                "only Action::Enter",
            ),
        )
        for source, message in invalid:
            with self.subTest(message=message):
                with model_tree({"root.spec": source}) as (_, entry_path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(entry_path)
                self.assertIn(message, caught.exception.diagnostic.message)

    def test_action_abstract_and_nonempty_concrete_forms_are_distinct(self) -> None:
        with model_tree(
            {
                "root.spec": """
                    type Base {
                        state State::Base { actions { on Action::Enter; } }
                    }
                    object Flow: Base {
                        state State::Base {
                            actions {
                                override on Action::Enter { drives {} }
                            }
                        }
                    }
                """
            }
        ) as (_, entry_path):
            model = compile_spec(entry_path)
        self.assertFalse(model.objects[0].states[0].actions[0].abstract)

        with model_tree(
            {
                "root.spec": """
                    object Flow: T {
                        state State::Base {
                            actions { on Action::Enter {} }
                        }
                    }
                """
            }
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)
        self.assertIn("must declare at least one block", caught.exception.diagnostic.message)

    def test_invalid_entry_root_and_origin_are_rejected(self) -> None:
        with model_tree(
            {"root.spec": ""},
            "spec root.nested;\norigin root.nested.Thing;\n",
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)
        self.assertIn("unexpected token '.'", caught.exception.diagnostic.message)

        with model_tree(
            {"root.spec": ""},
            "spec root;\norigin root.missing.Thing;\n",
        ) as (_, entry_path):
            with self.assertRaises(CompilationError) as caught:
                compile_spec(entry_path)
        self.assertIn("origin module 'root.missing'", caught.exception.diagnostic.message)


    def test_parameterized_signals_fields_updates_and_collection_lower(self) -> None:
        files = {
            "root.spec": """
                type Ref;
                object FirstRef: Ref {}
                object Queue: Collection<Ref> {}
                object Target: T {
                    mutable current: Ref = FirstRef;
                    state State::Base { actions {
                        on Action::Accept(item: Ref) {
                            updates { self.current = item; }
                            drives Queue.Action::Enqueue(item);
                        }
                    } }
                }
                external Human { drives Target.Action::Accept(FirstRef); }
            """,
        }
        with model_tree(files) as (_, path):
            model = compile_spec(path)
        target = next(item for item in model.objects if item.name[-1] == "Target")
        action = target.states[0].actions[0]
        self.assertEqual(action.parameters[0].name, "item")
        self.assertEqual(action.parameters[0].type.name, ("Ref",))
        self.assertEqual(target.attrs[0].name, "current")
        self.assertTrue(target.attrs[0].mutable)
        self.assertEqual(target.attrs[0].default.value, "FirstRef")
        self.assertEqual(action.blocks[0].kind, "updates")
        self.assertEqual(action.blocks[0].updates[0].value.value, "item")
        self.assertEqual(action.blocks[1].signals[0].arguments[0].value, "item")
        root_signal = model.externals[0].signals[0]
        self.assertEqual(root_signal.arguments[0].value, "FirstRef")

        stream = StringIO()
        dump_model_ir(model, stream)
        self.assertEqual(load_model_ir(StringIO(stream.getvalue())), model)

    def test_single_line_and_block_signal_forms_are_equivalent(self) -> None:
        body = """
            object Target: T {
                state State::Base { actions {
                    on Action::One { drives {} }
                    on Action::Two { drives {} }
                } }
            }
            object Source: T {
                state State::Base { actions {
                    on Action::Run {
                        drives Target.Action::One;
                        drives { Target.Action::Two; }
                        emits Target.Action::One;
                        emits { Target.Action::Two; }
                    }
                } }
            }
            external Human {
                drives Source.Action::Run;
                emits Target.Action::One;
            }
        """
        with model_tree({"root.spec": body}) as (_, path):
            model = compile_spec(path)
        source = next(item for item in model.objects if item.name[-1] == "Source")
        blocks = source.states[0].actions[0].blocks
        self.assertEqual(
            tuple(
                (block.kind, _target_name(block.signals[0].target)[-1])
                for block in blocks
            ),
            (("drives", "Target"),) * 2 + (("emits", "Target"),) * 2,
        )

    def test_parameterized_calls_and_override_signatures_are_checked(self) -> None:
        invalid = (
            (
                "expects 1 argument",
                """
                type Ref; object R: Ref {}
                object Target: T { state State::Base { actions {
                    on Action::Take(item: Ref) { drives {} }
                } } }
                external Human { drives Target.Action::Take; }
                """,
            ),
            (
                "incompatible object type",
                """
                type Ref; type Other; object O: Other {}
                object Target: T { state State::Base { actions {
                    on Action::Take(item: Ref) { drives {} }
                } } }
                external Human { drives Target.Action::Take(O); }
                """,
            ),
            (
                "no resolvable handler signature",
                """
                type Ref; object R: Ref {}
                object Target: T { state State::Base {} }
                external Human { drives Target.Action::Missing(R); }
                """,
            ),
            (
                "preserve its parameter signature",
                """
                type Ref; type Other;
                type Base { state State::Base { actions {
                    on Action::Take(item: Ref);
                } } }
                type Derived: Base { state State::Base { actions {
                    override on Action::Take(item: Other) { drives {} }
                } } }
                object Target: Derived {}
                external Human { drives Target.Action::Take; }
                """,
            ),
        )
        for message, body in invalid:
            with self.subTest(message=message):
                with model_tree({"root.spec": body}) as (_, path):
                    with self.assertRaises(CompilationError) as caught:
                        compile_spec(path)
                self.assertIn(message, caught.exception.diagnostic.message)

    def test_resumes_is_the_only_external_continuation_entry(self) -> None:
        valid = """
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow { state State::Online { actions {
                override on Action::Enter { drives {} }
            } } }
            external Human { resumes Boot.Action::Enter; }
        """
        with model_tree({"root.spec": valid}) as (_, path):
            model = compile_spec(path)
        self.assertEqual(model.externals[0].signals[0].mode, "resume")

        for statement in (
            "emits Boot.Action::Enter;",
            "drives Boot.Action::Enter;",
        ):
            with self.subTest(statement=statement):
                body = valid.replace(
                    "resumes Boot.Action::Enter;", statement
                )
                with model_tree({"root.spec": body}) as (_, path):
                    with self.assertRaises(CompilationError):
                        compile_spec(path)

        non_continuation = """
            object Target: T { state State::Base { actions {
                on Action::Enter { drives {} }
            } } }
            external Human { resumes Target.Action::Enter; }
        """
        with model_tree({"root.spec": non_continuation}) as (_, path):
            with self.assertRaisesRegex(CompilationError, "resumes"):
                compile_spec(path)


class ModelIRJSONTests(unittest.TestCase):
    def test_canonical_output_is_repeatable_and_round_trips(self) -> None:
        first = StringIO()
        second = StringIO()
        dump_model_ir(EXPECTED_MODEL, first)
        dump_model_ir(compile_spec(REPOSITORY / "model" / "main.spec"), second)

        self.assertEqual(first.getvalue(), EXPECTED_JSON)
        self.assertEqual(first.getvalue().encode(), second.getvalue().encode())
        self.assertEqual(load_model_ir(StringIO(first.getvalue())), EXPECTED_MODEL)
        document = json.loads(first.getvalue())
        self.assertEqual(
            _json_module(document, "systems", "computer")["objects"][0][
                "initial_state"
            ],
            ["State", "Base"],
        )

    def test_initial_state_json_field_remains_strict(self) -> None:
        missing = json.loads(EXPECTED_JSON)
        del _json_module(missing, "systems", "computer")["objects"][0][
            "initial_state"
        ]
        null_for_stateful = json.loads(EXPECTED_JSON)
        _json_module(null_for_stateful, "systems", "computer")["objects"][0][
            "initial_state"
        ] = None

        for document in (missing, null_for_stateful):
            with self.subTest(document=document):
                with self.assertRaises(ModelIRValidationError):
                    load_model_ir(StringIO(json.dumps(document)))

    def test_startup_alias_is_rejected_in_model_ir(self) -> None:
        signal_document = json.loads(EXPECTED_JSON)
        kernel = _json_module(signal_document, "systems", "kernel")
        ready = next(
            state
            for state in kernel["objects"][0]["states"]
            if state["name"] == ["State", "Ready"]
        )
        ready["transitions"][0]["blocks"][0]["signals"][0]["signal"] = [
            "Transition",
            "Startup",
        ]

        handler_document = json.loads(EXPECTED_JSON)
        computer = _json_module(handler_document, "systems", "computer")
        computer["objects"][0]["states"][0]["transitions"][0]["signal"] = [
            "Transition",
            "Startup",
        ]

        for document in (signal_document, handler_document):
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    ModelIRValidationError,
                    "must use canonical signal Transition::Preset",
                ):
                    load_model_ir(StringIO(json.dumps(document)))

    def test_loader_normalizes_module_order(self) -> None:
        document = json.loads(EXPECTED_JSON)
        document["modules"].reverse()
        model = load_model_ir(StringIO(json.dumps(document)))
        self.assertEqual(
            tuple(module.name for module in model.modules),
            (
                ("flows",),
                ("flows", "task_flow"),
                ("objects",),
                ("objects", "scheduler"),
                ("objects", "task"),
                ("phases",),
                ("phases", "arch_head"),
                ("phases", "kernel_init"),
                ("phases", "phase"),
                ("phases", "start_kernel"),
                ("phases", "start_kernel", "boot_handoff"),
                ("phases", "start_kernel", "boot_idle"),
                ("phases", "start_kernel", "boot_setup"),
                ("phases", "start_kernel", "early_boot"),
                ("phases", "user_run"),
                ("systems",),
                ("systems", "computer"),
                ("systems", "human"),
                ("systems", "kernel"),
                ("systems", "opensbi"),
                ("systems", "qemu_virt_platform"),
                ("systems", "rootfs"),
            ),
        )

    def test_invalid_documents_are_rejected(self) -> None:
        wrong_version = json.loads(EXPECTED_JSON)
        wrong_version["schema_version"] = 7
        unknown_field = json.loads(EXPECTED_JSON)
        unknown_field["extra"] = 0
        duplicate_module = json.loads(EXPECTED_JSON)
        duplicate_module["modules"].append(duplicate_module["modules"][0])
        duplicate_declaration = json.loads(EXPECTED_JSON)
        computer = _json_module(duplicate_declaration, "systems", "computer")
        computer["types"].append(computer["types"][0])
        unknown_signal_target = json.loads(EXPECTED_JSON)
        human = _json_module(unknown_signal_target, "systems", "human")
        human["externals"][0]["signals"][0]["target"] = {
            "kind": "path",
            "value": "Object",
            "children": [
                {"kind": "identifier", "value": "missing", "children": []}
            ],
        }
        invalid_signal_prefix = json.loads(EXPECTED_JSON)
        human = _json_module(invalid_signal_prefix, "systems", "human")
        human["externals"][0]["signals"][0]["signal"] = ["Effect", "Preset"]
        invalid_documents = [
            "{",
            json.dumps(wrong_version),
            json.dumps(unknown_field),
            json.dumps(duplicate_module),
            json.dumps(duplicate_declaration),
            json.dumps(unknown_signal_target),
            json.dumps(invalid_signal_prefix),
            EXPECTED_JSON.replace('"schema_version": 8', '"schema_version": true'),
            EXPECTED_JSON.replace('"modules": [', '"modules": "bad", "discard": ['),
            '{"schema_version":8,"schema_version":8}',
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ModelIRValidationError):
                    load_model_ir(StringIO(document))

    def test_in_memory_ir_is_strict_and_sorted(self) -> None:
        model = ModelIR(
            schema_version=8,
            entry=EXPECTED_MODEL.entry,
            modules=tuple(reversed(EXPECTED_MODEL.modules)),
        )
        self.assertEqual(model.modules[0].name, ("flows",))

        with self.assertRaises(ModelIRValidationError):
            ModelIR(
                schema_version=8,
                entry=EXPECTED_MODEL.entry,
                modules=EXPECTED_MODEL.modules + (EXPECTED_MODEL.modules[0],),
            )

        with self.assertRaises(ModelIRValidationError):
            ModelIR(
                schema_version=8,
                entry=ModelEntry(
                    origin=EXPECTED_MODEL.entry.origin,
                    spec=("missing",),
                ),
                modules=EXPECTED_MODEL.modules,
            )

        with self.assertRaisesRegex(
            ModelIRValidationError,
            "must use canonical signal Transition::Preset",
        ):
            ModelSignal(
                computer_path,
                kernel_path,
                ("Transition", "Startup"),
                "drive",
            )

        with self.assertRaisesRegex(
            ModelIRValidationError,
            "must use canonical signal Transition::Preset",
        ):
            ModelTransition(
                ("Transition", "Startup"),
                ("State", "Prepared"),
                (),
            )


class CLITests(unittest.TestCase):
    def test_stdout_and_output_file_modes(self) -> None:
        input_path = REPOSITORY / "model" / "main.spec"

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([str(input_path)])
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), EXPECTED_JSON)
        self.assertEqual(stderr.getvalue(), "")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "model.json"
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["-o", str(output_path), str(input_path)])
            self.assertEqual(status, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(output_path.read_text(encoding="utf-8"), EXPECTED_JSON)

    def test_compile_errors_use_stderr_and_exit_one(self) -> None:
        with model_tree({"root.spec": "use missing::module::Thing;"}) as (
            _,
            input_path,
        ):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main([str(input_path)])

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(": error: cannot resolve module", stderr.getvalue())

    def test_input_and_output_io_errors_exit_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            missing_path = directory_path / "missing.spec"
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main([str(missing_path)])
            self.assertEqual(status, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(
                stderr.getvalue().startswith(f"{missing_path}:1:1: error: ")
            )

            stdout = StringIO()
            stderr = StringIO()
            input_path = REPOSITORY / "model" / "main.spec"
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["-o", str(directory_path), str(input_path)])
            self.assertEqual(status, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(
                stderr.getvalue().startswith(f"{directory_path}:1:1: error: ")
            )

    def test_repository_wrapper_works_outside_repository(self) -> None:
        wrapper = REPOSITORY / "tools" / "bin" / "modelc"
        input_path = REPOSITORY / "model" / "main.spec"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(wrapper), str(input_path)],
                cwd=directory,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, EXPECTED_JSON)
        self.assertEqual(result.stderr, "")

    def test_wrapper_defaults_work_outside_repository(self) -> None:
        wrapper = REPOSITORY / "tools" / "bin" / "modelc"
        result = subprocess.run(
            [str(wrapper)],
            cwd=tempfile.gettempdir(),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, EXPECTED_JSON)
        self.assertEqual(result.stderr, "")

    def test_argument_errors_exit_two(self) -> None:
        wrapper = REPOSITORY / "tools" / "bin" / "modelc"
        result = subprocess.run(
            [str(wrapper), "first.spec", "second.spec"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage: modelc", result.stderr)


if __name__ == "__main__":
    unittest.main()
