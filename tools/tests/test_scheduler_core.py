from __future__ import annotations

from pathlib import Path
from io import StringIO
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools" / "src"))

from derive import (
    default_derivation_sequence,
    derive,
    dump_derivation_result,
    load_derivation_result,
    render_derivation_result,
)
from modelc import CompilationError, compile_spec


BASE = """
type Task {
    initial_state: State::Ready;
    state State::Ready {
        transitions { on Transition::Enable -> State::Online {
            drives Scheduler0.Action::Enqueue;
        } }
    }
    state State::Online {
        transitions { on Transition::Resume -> State::OnCpu {
            drives Scheduler0.Action::Dequeue;
        } }
    }
    state State::OnCpu {
        transitions { on Transition::Suspend -> State::Online {
            drives Scheduler0.Action::Enqueue;
        } }
    }
}
type TaskFlow {
    continuation: true;
    initial_state: State::Online;
    state State::Online { actions { on Action::Enter; } }
}
object Boot: Task {
    initial_state: State::OnCpu;
    state State::Online { transitions {
        override on Transition::Resume -> State::OnCpu {}
    } }
    state State::OnCpu { transitions {
        override on Transition::Suspend -> State::Online {}
    } }
}
object A: Task {}
object B: Task {}
object BootFlow: TaskFlow { parent: Boot; state State::Online { actions {
    override on Action::Enter { print "boot"; }
} } }
object AFlow: TaskFlow { parent: A; state State::Online { actions {
    override on Action::Enter { print "a"; }
} } }
object BFlow: TaskFlow { parent: B; state State::Online { actions {
    override on Action::Enter { print "b"; }
} } }
type Scheduler {
    sched_core: true;
    initial_state: State::Online;
    state State::Online { actions { on Action::Schedule {
        drives CurrentTaskRef.Transition::Suspend;
        switches next;
        drives next.Transition::Resume;
    } } }
}
object Scheduler0: Scheduler { idle_task: Boot; }
"""


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


def _compile(body: str):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    (root / "main.spec").write_text(
        "spec root;\norigin root.Human;\n", encoding="utf-8"
    )
    (root / "root.spec").write_text(body, encoding="utf-8")
    return directory, compile_spec(root / "main.spec")


class SchedulerCoreEngineTests(unittest.TestCase):
    def test_derivation_line_requires_exactly_one_scheduler(self) -> None:
        directory, no_scheduler = _compile(
            "object Computer: T {} external Human {}"
        )
        self.addCleanup(directory.cleanup)
        missing = derive(no_scheduler, default_derivation_sequence(no_scheduler))
        self.assertEqual(missing.paths[0].status, "invalid_derivation_line")
        self.assertIsNone(missing.paths[0].current_task_ref)
        self.assertEqual(missing.paths[0].schedulers, ())

        directory2, two_schedulers = _compile(
            BASE.replace(
                "object Scheduler0: Scheduler { idle_task: Boot; }",
                """object Scheduler0: Scheduler { idle_task: Boot; }
                object Scheduler1: Scheduler { idle_task: Boot; }""",
            )
            + "external Human {}"
        )
        self.addCleanup(directory2.cleanup)
        multiple = derive(
            two_schedulers, default_derivation_sequence(two_schedulers)
        )
        self.assertEqual(multiple.paths[0].status, "invalid_derivation_line")
        self.assertIsNone(multiple.paths[0].current_task_ref)
        self.assertEqual(multiple.paths[0].schedulers, ())

    def test_schedule_requires_line_current_to_be_on_cpu(self) -> None:
        directory, model = _compile(
            BASE.replace("initial_state: State::OnCpu;", "initial_state: State::Online;", 1)
            + "external Human { drives Scheduler0.Action::Schedule; }"
        )
        self.addCleanup(directory.cleanup)
        path = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(path.status, "invalid_current_task_ref")
        self.assertEqual(path.current_task_ref[-1], "Boot")
        self.assertEqual(path.units[0].drives, ())

    def test_current_task_ref_is_readable_from_any_handler_and_as_argument(self) -> None:
        body = BASE.replace(
            "state State::OnCpu { transitions {",
            """state State::OnCpu { actions { on Action::Observe { print \"current\"; } }
            transitions {""",
            1,
        ).replace(
            "object B: Task {}",
            """object B: Task {}
            object Queue: Collection<Task> {}
            object Controller: T { state State::Base { actions {
                on Action::Read { drives {
                    CurrentTaskRef.Action::Observe;
                    Queue.Action::Enqueue(CurrentTaskRef);
                } }
            } } }""",
        )
        directory, model = _compile(
            body + "external Human { drives Controller.Action::Read; }"
        )
        self.addCleanup(directory.cleanup)
        path = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(path.status, "passed")
        self.assertEqual(path.units[0].drives[0].event.target[-1], "Boot")
        queue = next(value for value in path.final_values if value.collection)
        self.assertEqual(queue.values[0][-1], "Boot")

    def test_fifo_candidates_expand_to_isolated_paths_without_dequeueing_switch(self) -> None:
        directory, model = _compile(
            BASE
            + """
            external Human { drives {
                A.Transition::Enable;
                B.Transition::Enable;
                Scheduler0.Action::Schedule;
            } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.paths), 2)
        contexts = tuple(path.schedulers[0] for path in result.paths)
        self.assertEqual(tuple(path.current_task_ref[-1] for path in result.paths), ("A", "B"))
        self.assertEqual(
            tuple(tuple(task[-1] for task in item.runq) for item in contexts),
            (("B",), ("A",)),
        )
        switches = tuple(
            unit.switches[0]
            for path in result.paths
            for unit in path.units
            if unit.event.signal == ("Action", "Schedule")
        )
        self.assertEqual(tuple(item.task[-1] for item in switches), ("A", "B"))
        self.assertTrue(all(not item.idle_fallback for item in switches))

        serialized = StringIO()
        dump_derivation_result(result, serialized)
        self.assertEqual(
            load_derivation_result(StringIO(serialized.getvalue())), result
        )
        rendered = StringIO()
        render_derivation_result(result, rendered)
        self.assertIn("Path 1 [passed]", rendered.getvalue())
        self.assertIn("Path 2 [passed]", rendered.getvalue())

    def test_empty_runq_uses_idle_task(self) -> None:
        directory, model = _compile(
            BASE + "external Human { drives Scheduler0.Action::Schedule; }"
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        path = result.paths[0]

        self.assertEqual(result.status, "passed")
        self.assertEqual(path.current_task_ref[-1], "Boot")
        self.assertEqual(path.schedulers[0].runq, ())
        self.assertTrue(path.units[0].switches[0].idle_fallback)

    def test_failed_resume_does_not_commit_current(self) -> None:
        body = BASE.replace(
            "object B: Task {}",
            """object B: Task {
                initial_state: State::OnCpu;
                state State::OnCpu { actions { on Action::Queue {
                    drives Scheduler0.Action::Enqueue;
                } } }
            }""",
        )
        directory, model = _compile(
            body
            + """external Human { drives {
                B.Action::Queue;
                Scheduler0.Action::Schedule;
            } }"""
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        path = result.paths[0]

        self.assertEqual(result.status, "failed")
        self.assertEqual(path.status, "unhandled_signal")
        self.assertEqual(path.current_task_ref[-1], "Boot")
        self.assertEqual(tuple(task[-1] for task in path.schedulers[0].runq), ("B",))
        self.assertFalse(
            any(unit.event.target[-1] == "BFlow" for unit in path.units[1].resumes)
        )

    def test_failed_dequeue_or_scheduler_validation_never_enters_flow(self) -> None:
        double_dequeue = BASE.replace(
            "drives Scheduler0.Action::Dequeue;",
            """drives {
                Scheduler0.Action::Dequeue;
                Scheduler0.Action::Dequeue;
            }""",
        )
        directory, model = _compile(
            double_dequeue + """external Human { drives {
                A.Transition::Enable;
                Scheduler0.Action::Schedule;
            } }"""
        )
        self.addCleanup(directory.cleanup)
        dequeue_failure = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(dequeue_failure.status, "task_not_queued")
        self.assertEqual(dequeue_failure.current_task_ref[-1], "Boot")
        self.assertFalse(
            any(unit.event.target[-1] == "AFlow" for unit in _all_units(dequeue_failure.units))
        )

        invalid_handler = BASE.replace(
            "drives next.Transition::Resume;",
            "drives next.Transition::Resume; ensures { false; }",
        )
        directory2, model2 = _compile(
            invalid_handler + """external Human { drives {
                A.Transition::Enable;
                Scheduler0.Action::Schedule;
            } }"""
        )
        self.addCleanup(directory2.cleanup)
        handler_failure = derive(model2, default_derivation_sequence(model2)).paths[0]
        self.assertEqual(handler_failure.status, "ensures_failed")
        self.assertEqual(handler_failure.current_task_ref[-1], "Boot")
        self.assertFalse(
            any(unit.event.target[-1] == "AFlow" for unit in _all_units(handler_failure.units))
        )

    def test_task_resume_enters_flow_after_current_switch_commits(self) -> None:
        body = BASE.replace(
            "object A: Task {}",
            """object A: Task { state State::OnCpu { actions {
                on Action::Observe { print "a is current"; }
            } } }""",
        ).replace(
            'override on Action::Enter { print "a"; }',
            "override on Action::Enter { drives Scheduler0.Action::Probe; }",
        ).replace(
            "state State::Online { actions { on Action::Schedule {",
            """state State::Online { actions {
                on Action::Probe { drives CurrentTaskRef.Action::Observe; }
                on Action::Schedule {""",
        )
        directory, model = _compile(
            body
            + """external Human { drives {
                A.Transition::Enable;
                Scheduler0.Action::Schedule;
            } }"""
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        schedule = result.units[-1]
        resume = schedule.drives[-1]
        flow = resume.resumes[0]
        probe = flow.drives[0]

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.paths[0].current_task_ref[-1], "A")
        self.assertEqual(flow.event.target[-1], "AFlow")
        self.assertEqual(probe.event.target[-1], "Scheduler0")
        self.assertEqual(probe.drives[0].event.target[-1], "A")

    def test_task_resume_starts_and_recovers_its_unique_flow(self) -> None:
        body = BASE.replace(
            "initial_state: State::OnCpu;",
            "initial_state: State::Online;",
            1,
        ).replace(
            'override on Action::Enter { print "boot"; }',
            """override on Action::Enter {
                yields Waiter.Action::Wait;
            }""",
        ).replace(
            "object A: Task {}",
            """object A: Task {}
            object Waiter: T { state State::Base { actions {
                on Action::Wait { drives {} }
            } } }""",
        )
        directory, model = _compile(
            body + """external Human { drives {
                Boot.Transition::Resume;
            } }"""
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))

        self.assertEqual(result.status, "yielded")
        self.assertEqual(tuple(unit.event.target[-1] for unit in result.units), ("Boot",))
        self.assertEqual(
            tuple(unit.resumes[0].event.target[-1] for unit in result.units),
            ("BootFlow",),
        )

    def test_runq_rejects_duplicate_enqueue_and_missing_dequeue(self) -> None:
        duplicate_body = BASE.replace(
            "object A: Task {}",
            """object A: Task { state State::Ready { actions {
                on Action::Double { drives {
                    Scheduler0.Action::Enqueue;
                    Scheduler0.Action::Enqueue;
                } }
            } } }""",
        )
        directory, model = _compile(
            duplicate_body + "external Human { drives A.Action::Double; }"
        )
        self.addCleanup(directory.cleanup)
        duplicate = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(duplicate.status, "duplicate_runq_task")
        self.assertEqual(tuple(task[-1] for task in duplicate.schedulers[0].runq), ("A",))

        missing_body = BASE.replace(
            "object A: Task {}",
            """object A: Task { state State::Ready { actions {
                on Action::Remove { drives Scheduler0.Action::Dequeue; }
            } } }""",
        )
        directory2, model2 = _compile(
            missing_body + "external Human { drives A.Action::Remove; }"
        )
        self.addCleanup(directory2.cleanup)
        missing = derive(model2, default_derivation_sequence(model2)).paths[0]
        self.assertEqual(missing.status, "task_not_queued")

    def test_runq_actions_are_gated_by_scheduler_online_state(self) -> None:
        offline = BASE.replace(
            "type Scheduler {\n    sched_core: true;\n    initial_state: State::Online;",
            """type Scheduler {
    sched_core: true;
    initial_state: State::Ready;
    state State::Ready { transitions {
        on Transition::Enable -> State::Online {}
    } }""",
        )
        directory, model = _compile(
            offline + "external Human { drives A.Transition::Enable; }"
        )
        self.addCleanup(directory.cleanup)
        path = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(path.status, "unhandled_signal")
        self.assertEqual(path.schedulers[0].runq, ())

    def test_any_failed_branch_fails_the_aggregate_without_contaminating_siblings(self) -> None:
        body = BASE.replace(
            "object B: Task {}",
            """object B: Task {
                initial_state: State::OnCpu;
                state State::OnCpu { actions { on Action::Queue {
                    drives Scheduler0.Action::Enqueue;
                } } }
            }""",
        )
        directory, model = _compile(
            body
            + """external Human { drives {
                A.Transition::Enable;
                B.Action::Queue;
                Scheduler0.Action::Schedule;
            } }"""
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))

        self.assertEqual(result.status, "failed")
        self.assertEqual(tuple(path.status for path in result.paths), ("passed", "unhandled_signal"))
        self.assertEqual(result.paths[0].current_task_ref[-1], "A")
        self.assertEqual(result.paths[1].current_task_ref[-1], "Boot")


class SchedulerCoreCompilerTests(unittest.TestCase):
    def _rejects(self, body: str, message: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.spec").write_text(
                "spec root;\norigin root.Human;\n", encoding="utf-8"
            )
            (root / "root.spec").write_text(body, encoding="utf-8")
            with self.assertRaisesRegex(CompilationError, message):
                compile_spec(root / "main.spec")

    def test_sched_core_requires_idle_task(self) -> None:
        self._rejects(
            BASE.replace("object Scheduler0: Scheduler { idle_task: Boot; }", "object Scheduler0: Scheduler {}")
            + "external Human {}",
            "requires idle_task",
        )

    def test_task_resume_is_only_online_to_on_cpu(self) -> None:
        self._rejects(
            BASE.replace(
                "override on Transition::Suspend -> State::Online {}",
                """override on Transition::Suspend -> State::Online {}
                on Transition::Resume -> State::OnCpu {}""",
            )
            + "external Human {}",
            "Resume is only allowed from State::Online",
        )
        self._rejects(
            BASE.replace(
                "on Transition::Resume -> State::OnCpu {",
                "on Transition::Resume -> State::Online {",
                1,
            )
            + "external Human {}",
            "Resume is only allowed from State::Online",
        )

    def test_current_task_ref_cannot_be_declared_or_assigned(self) -> None:
        self._rejects(
            BASE + "object CurrentTaskRef: T {} external Human {}",
            "reserved runtime selector",
        )
        self._rejects(
            BASE.replace(
                "object B: Task {}",
                """object B: Task { reference Bad {
                    CurrentTaskRef = Boot;
                } }""",
            )
            + "external Human {}",
            "read-only runtime selector",
        )

    def test_core_actions_cannot_be_declared_by_the_model(self) -> None:
        self._rejects(
            BASE.replace(
                "state State::Online { actions { on Action::Schedule {",
                "state State::Online { actions { on Action::Enqueue { drives {} } on Action::Schedule {",
            )
            + "external Human {}",
            "must not declare",
        )

    def test_switch_binding_is_stream_scoped_and_unique(self) -> None:
        forward = BASE.replace(
            "drives CurrentTaskRef.Transition::Suspend;",
            "drives next.Transition::Suspend;",
        )
        self._rejects(
            forward + "external Human {}",
            "not in scope|unknown object|dynamic signal target",
        )
        duplicate = BASE.replace(
            "switches next;",
            "switches next; switches next;",
        )
        self._rejects(duplicate + "external Human {}", "duplicate switches")

    def test_each_task_requires_exactly_one_task_flow(self) -> None:
        self._rejects(
            BASE.replace(
                "object BFlow: TaskFlow { parent: B;",
                "object BFlow: TaskFlow { parent: A;",
            )
            + "external Human {}",
            "requires exactly one parent TaskFlow",
        )

    def test_idle_and_core_signal_source_types_are_checked(self) -> None:
        self._rejects(
            BASE.replace("idle_task: Boot", "idle_task: BootFlow")
            + "external Human {}",
            "idle_task must reference a Task",
        )
        self._rejects(
            BASE + "external Human { drives Scheduler0.Action::Enqueue; }",
            "source must be a Task",
        )

    def test_switches_is_rejected_outside_sched_core_actions(self) -> None:
        invalid = BASE.replace(
            "object A: Task {}",
            """object A: Task { state State::Ready { actions {
                on Action::Bad { switches selected; }
            } } }""",
        )
        self._rejects(
            invalid + "external Human { drives A.Action::Bad; }",
            "switches is allowed",
        )

    def test_legacy_selects_syntax_is_strictly_rejected(self) -> None:
        self._rejects(
            BASE.replace("switches next;", "selects next;") + "external Human {}",
            "unexpected token 'selects'",
        )

    def test_model_authored_task_flow_entry_is_always_rejected(self) -> None:
        for mode in ("drives", "emits", "resumes"):
            with self.subTest(source="handler", mode=mode):
                controller = BASE.replace(
                    "object B: Task {}",
                    f"""object B: Task {{}}
                    object Controller: T {{ state State::Base {{ actions {{
                        on Action::Enter {{ {mode} AFlow.Action::Enter; }}
                    }} }} }}""",
                )
                self._rejects(
                    controller + "external Human { drives Controller.Action::Enter; }",
                    "TaskFlow Action::Enter is derived exclusively|continuation entry|resumes must",
                )

            with self.subTest(source="external", mode=mode):
                self._rejects(
                    BASE + f"external Human {{ {mode} AFlow.Action::Enter; }}",
                    "TaskFlow Action::Enter is derived exclusively|continuation entry|resumes must",
                )


if __name__ == "__main__":
    unittest.main()
