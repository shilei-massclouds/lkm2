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
        selects next;
        drives next.Transition::Resume;
    } } }
}
object Scheduler0: Scheduler { idle_task: Boot; }
"""


def _compile(body: str):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    (root / "main.spec").write_text(
        "spec root;\norigin root.Human;\n", encoding="utf-8"
    )
    (root / "root.spec").write_text(body, encoding="utf-8")
    return directory, compile_spec(root / "main.spec")


class SchedulerCoreEngineTests(unittest.TestCase):
    def test_fifo_candidates_expand_to_isolated_paths_without_dequeueing_selection(self) -> None:
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
        self.assertEqual(tuple(item.current_task[-1] for item in contexts), ("A", "B"))
        self.assertEqual(
            tuple(tuple(task[-1] for task in item.runq) for item in contexts),
            (("B",), ("A",)),
        )
        selections = tuple(
            unit.selections[0]
            for path in result.paths
            for unit in path.units
            if unit.event.signal == ("Action", "Schedule")
        )
        self.assertEqual(tuple(item.task[-1] for item in selections), ("A", "B"))
        self.assertTrue(all(not item.idle_fallback for item in selections))

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
        self.assertEqual(path.schedulers[0].current_task[-1], "Boot")
        self.assertEqual(path.schedulers[0].runq, ())
        self.assertTrue(path.units[0].selections[0].idle_fallback)

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
        self.assertEqual(path.schedulers[0].current_task[-1], "Boot")
        self.assertEqual(tuple(task[-1] for task in path.schedulers[0].runq), ("B",))

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
        self.assertEqual(result.paths[0].schedulers[0].current_task[-1], "A")
        self.assertEqual(result.paths[1].schedulers[0].current_task[-1], "Boot")


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

    def test_core_actions_cannot_be_declared_by_the_model(self) -> None:
        self._rejects(
            BASE.replace(
                "state State::Online { actions { on Action::Schedule {",
                "state State::Online { actions { on Action::Enqueue { drives {} } on Action::Schedule {",
            )
            + "external Human {}",
            "must not declare",
        )

    def test_select_binding_is_stream_scoped_and_unique(self) -> None:
        forward = BASE.replace(
            "drives CurrentTaskRef.Transition::Suspend;",
            "drives next.Transition::Suspend;",
        )
        self._rejects(
            forward + "external Human {}",
            "not in scope|unknown object|dynamic signal target",
        )
        duplicate = BASE.replace(
            "selects next;",
            "selects next; selects next;",
        )
        self._rejects(duplicate + "external Human {}", "duplicate selects")

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

    def test_selects_is_rejected_outside_sched_core_actions(self) -> None:
        invalid = BASE.replace(
            "object A: Task {}",
            """object A: Task { state State::Ready { actions {
                on Action::Bad { selects selected; }
            } } }""",
        )
        self._rejects(
            invalid + "external Human { drives A.Action::Bad; }",
            "selects is allowed",
        )


if __name__ == "__main__":
    unittest.main()
