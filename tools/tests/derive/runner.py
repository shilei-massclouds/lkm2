from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import difflib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
TESTS_DIRECTORY = REPOSITORY / "tools" / "tests"
CASES_DIRECTORY = Path(__file__).resolve().parent / "cases"
sys.path.insert(0, str(TESTS_DIRECTORY))
sys.path.insert(0, str(SOURCE_DIRECTORY))

from _compact_result import CompactTextTestResult  # noqa: E402
from derive import (  # noqa: E402
    DerivationEvent,
    DerivationSequence,
    DerivationValidationError,
    default_derivation_sequence,
    derive,
    dump_derivation_result,
    dump_derivation_sequence,
    load_derivation_result,
    load_derivation_sequence,
    render_derivation_result,
)
from derive.cli import main as derive_main  # noqa: E402
from modelc import CompilationError, compile_spec  # noqa: E402


class _SyntheticSuccess(unittest.TestCase):
    def test_success(self) -> None:
        pass


class _SyntheticFailure(unittest.TestCase):
    def test_failure(self) -> None:
        self.fail("synthetic ordinary failure")


class _SyntheticSubtestFailure(unittest.TestCase):
    def test_subtests(self) -> None:
        with self.subTest(value="bad"):
            self.fail("synthetic subtest failure")


def _compile_text(body: str):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    (root / "main.spec").write_text(
        "spec root;\norigin root.Human;\n", encoding="utf-8"
    )
    (root / "root.spec").write_text(body, encoding="utf-8")
    return directory, compile_spec(root / "main.spec")


def _sequence(*events: tuple[str, str, str, str]) -> DerivationSequence:
    return DerivationSequence(
        2,
        tuple(
            DerivationEvent(
                tuple(source.split(".")),
                tuple(target.split(".")),
                ("Transition", signal),
                mode,
            )
            for source, target, signal, mode in events
        ),
    )


class _DiffingTestCase(unittest.TestCase):
    def assert_bytes_equal(
        self, actual: bytes, expected: bytes, actual_name: str, expected_name: str
    ) -> None:
        if actual == expected:
            return
        actual_text = actual.decode("utf-8", errors="replace").splitlines(keepends=True)
        expected_text = expected.decode("utf-8", errors="replace").splitlines(
            keepends=True
        )
        diff = "".join(
            difflib.unified_diff(
                expected_text,
                actual_text,
                fromfile=expected_name,
                tofile=actual_name,
            )
        )
        self.fail(f"byte mismatch:\n{diff}")


class SmokeGoldenTests(_DiffingTestCase):
    def test_cases(self) -> None:
        cases = tuple(
            path
            for path in sorted(CASES_DIRECTORY.iterdir(), key=lambda item: item.name)
            if path.is_dir()
        )
        self.assertEqual(len(cases), 16)
        for case in cases:
            with self.subTest(case=case.name):
                expected_json_path = case / "expected.result.json"
                expected_stdout_path = case / "expected.stdout"
                expected_json = expected_json_path.read_bytes()
                expected_stdout = expected_stdout_path.read_bytes()

                model = compile_spec(case / "main.spec")
                selected = default_derivation_sequence(model)
                first = derive(model, selected)
                second = derive(model, selected)
                first_json = StringIO()
                second_json = StringIO()
                dump_derivation_result(first, first_json)
                dump_derivation_result(second, second_json)
                self.assert_bytes_equal(
                    first_json.getvalue().encode(),
                    expected_json,
                    f"{case.name}/actual.result.json",
                    str(expected_json_path.relative_to(REPOSITORY)),
                )
                self.assertEqual(first, second)
                self.assertEqual(first_json.getvalue(), second_json.getvalue())

                loaded = load_derivation_result(
                    StringIO(expected_json.decode("utf-8"))
                )
                rendered = StringIO()
                render_derivation_result(loaded, rendered)
                self.assert_bytes_equal(
                    rendered.getvalue().encode(),
                    expected_stdout,
                    f"{case.name}/actual.stdout",
                    str(expected_stdout_path.relative_to(REPOSITORY)),
                )
                rerendered = StringIO()
                render_derivation_result(second, rerendered)
                self.assertEqual(rendered.getvalue(), rerendered.getvalue())

                completed = subprocess.run(
                    [
                        str(REPOSITORY / "tools" / "bin" / "derive"),
                        "--model",
                        str(case / "main.spec"),
                    ],
                    cwd=case,
                    text=False,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0 if loaded.status == "passed" else 1,
                    completed.stderr.decode("utf-8", errors="replace"),
                )
                self.assert_bytes_equal(
                    completed.stdout,
                    expected_stdout,
                    f"{case.name}/cli.stdout",
                    str(expected_stdout_path.relative_to(REPOSITORY)),
                )
                self.assertEqual(completed.stderr, b"")


class EngineTests(unittest.TestCase):
    def test_sequence_selects_roots_and_engine_schedules_nested_units(self) -> None:
        directory, model = _compile_text(
            """
            object Child: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Work -> State::Done {} }
                }
                state State::Done {}
            }
            object Parent: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Start -> State::Done {
                        drives { Child.Transition::Work; }
                    } }
                }
                state State::Done {}
            }
            external Human { drives { Parent.Transition::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        selected = default_derivation_sequence(model)
        self.assertEqual(len(selected.events), 1)
        result = derive(model, selected)
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.units), 1)
        self.assertEqual(result.units[0].kind, "root")
        self.assertEqual(result.units[0].drives[0].kind, "drive")

    def test_completed_child_is_kept_when_later_drive_fails(self) -> None:
        case = CASES_DIRECTORY / "07-second-drive-fails"
        model = compile_spec(case / "main.spec")
        result = derive(model, default_derivation_sequence(model))
        states = {item.object[-1]: item.state for item in result.final_state}
        self.assertEqual(result.status, "unhandled_signal")
        self.assertEqual(states["First"], ("State", "Done"))
        self.assertEqual(states["Second"], ("State", "Idle"))
        self.assertEqual(states["Parent"], ("State", "Idle"))
        self.assertEqual(result.units[0].status, "stopped")

    def test_external_action_succeeds_without_changing_state(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    actions { on Action::Refresh { drives {} } }
                }
            }
            external Human { drives { Computer.Action::Refresh; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        unit = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(unit.handler, ("Action", "Refresh"))
        self.assertIsNone(unit.candidate_state)
        self.assertEqual(unit.state_before, ("State", "Idle"))
        self.assertEqual(unit.state_after, ("State", "Idle"))
        self.assertEqual(result.final_state[0].state, ("State", "Idle"))
        output = StringIO()
        dump_derivation_result(result, output)
        self.assertEqual(load_derivation_result(StringIO(output.getvalue())), result)
        rendered = StringIO()
        render_derivation_result(result, rendered)
        self.assertEqual(
            rendered.getvalue(),
            "Human -> Computer: drives Action::Refresh\n"
            "  current state: State::Idle\n"
            "  commit state: unchanged\n"
            "\n"
            "Derivation passed!\n",
        )

    def test_model_root_prefix_resolves_object_references(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    actions {
                        on Action::Refresh {
                            depends_on {
                                model::root::Computer.state == State::Idle;
                            }
                        }
                    }
                }
            }
            external Human { drives { Computer.Action::Refresh; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.units[0].depends_on[0].status, "passed")

    def test_startup_request_is_canonicalized_and_matches_preset(self) -> None:
        directory, model = _compile_text(
            """
            object Flow: T {
                state State::Base {
                    transitions {
                        on Transition::Preset -> State::Online {}
                    }
                }
                state State::Online {}
            }
            external Human { drives { Flow.Transition::Preset; } }
            """
        )
        self.addCleanup(directory.cleanup)
        requested = DerivationEvent(
            ("root", "Human"),
            ("root", "Flow"),
            ("Transition", "Startup"),
            "drive",
        )
        self.assertEqual(requested.signal, ("Transition", "Preset"))

        result = derive(model, DerivationSequence(2, (requested,)))
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.units[0].event.signal, ("Transition", "Preset"))
        self.assertEqual(result.units[0].handler, ("Transition", "Preset"))

    def test_drives_and_emits_can_both_carry_actions(self) -> None:
        directory, model = _compile_text(
            """
            predicate prepared() -> bool;
            object Worker: T {
                initial_state: State::Idle;
                state State::Idle {
                    actions {
                        on Action::Prepare { establishes { prepared(); } }
                        on Action::Finish { depends_on { prepared(); } }
                    }
                }
            }
            object Parent: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Run -> State::Done {
                        drives { Worker.Action::Prepare; }
                        emits { Worker.Action::Finish; }
                    }
                } }
                state State::Done {}
            }
            external Human { drives { Parent.Transition::Run; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        unit = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(unit.drives[0].event.signal, ("Action", "Prepare"))
        self.assertEqual(unit.emits[0].event.signal, ("Action", "Finish"))
        self.assertEqual(unit.drives[0].state_after, ("State", "Idle"))
        self.assertEqual(unit.emits[0].state_after, ("State", "Idle"))

    def test_action_stages_facts_for_its_current_state_invariant(self) -> None:
        directory, model = _compile_text(
            """
            predicate refreshed() -> bool;
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    invariant { refreshed(); }
                    actions {
                        on Action::Refresh { establishes { refreshed(); refreshed(); } }
                    }
                }
            }
            external Human { drives { Computer.Action::Refresh; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        unit = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(unit.establishes), 2)
        self.assertEqual(unit.invariants[0].status, "passed")
        self.assertEqual(len(result.facts), 1)

    def test_action_checks_post_drive_current_state_without_rolling_back_drive(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    invariant { false; }
                    transitions { on Transition::Advance -> State::Ready {} }
                    actions { on Action::Start {
                        drives { Computer.Transition::Advance; }
                    } }
                }
                state State::Ready { invariant { true; } }
            }
            external Human { drives { Computer.Action::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        unit = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(unit.state_before, ("State", "Idle"))
        self.assertEqual(unit.state_after, ("State", "Idle"))
        self.assertEqual(unit.drives[0].state_after, ("State", "Ready"))
        self.assertEqual(unit.invariants[0].status, "passed")
        self.assertEqual(result.final_state[0].state, ("State", "Ready"))

    def test_failed_action_invariant_discards_facts_and_does_not_emit(self) -> None:
        directory, model = _compile_text(
            """
            predicate attempted() -> bool;
            object Child: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Run -> State::Done {} }
                }
                state State::Done {}
            }
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    invariant { false; }
                    actions { on Action::Refresh {
                        establishes { attempted(); }
                        emits { Child.Transition::Run; }
                    } }
                }
            }
            external Human { drives { Computer.Action::Refresh; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        states = {item.object[-1]: item.state for item in result.final_state}
        unit = result.units[0]
        self.assertEqual(result.status, "invariant_failed")
        self.assertEqual(result.failure.path, "units[0].invariants[0]")
        self.assertEqual(result.facts, ())
        self.assertEqual(unit.emits, ())
        self.assertIsNone(unit.state_after)
        self.assertEqual(states["Computer"], ("State", "Idle"))
        self.assertEqual(states["Child"], ("State", "Idle"))
        rendered = StringIO()
        render_derivation_result(result, rendered)
        self.assertEqual(
            rendered.getvalue(),
            "Human -> Computer: drives Action::Refresh\n"
            "  current state: State::Idle\n"
            "  commit state: not committed ✗\n"
            "\n"
            "stopped: invariant_failed\n",
        )

    def test_action_requires_a_handler_in_the_current_state(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {}
                state State::Ready {
                    actions { on Action::Refresh { drives {} } }
                }
            }
            external Human { drives { Computer.Action::Refresh; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "unhandled_signal")
        self.assertEqual(result.failure.path, "units[0].handler")
        self.assertIsNone(result.units[0].handler)
        self.assertIsNone(result.units[0].candidate_state)

    def test_mixed_transition_action_chain_is_recursive(self) -> None:
        directory, model = _compile_text(
            """
            predicate complete() -> bool;
            object Third: T {
                initial_state: State::Idle;
                state State::Idle {
                    actions { on Action::Finish { establishes { complete(); } } }
                }
            }
            object Second: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Go -> State::Done {
                        emits { Third.Action::Finish; }
                    }
                } }
                state State::Done {}
            }
            object First: T {
                initial_state: State::Idle;
                state State::Idle { actions {
                    on Action::Start {
                        drives { Second.Transition::Go; }
                        ensures { complete(); }
                    }
                } }
            }
            external Human { drives { First.Action::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        root = result.units[0]
        transition = root.drives[0]
        action = transition.emits[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(root.event.signal, ("Action", "Start"))
        self.assertEqual(transition.event.signal, ("Transition", "Go"))
        self.assertEqual(action.event.signal, ("Action", "Finish"))
        self.assertEqual(root.ensures[0].status, "passed")

    def test_emits_execute_depth_first_before_the_next_sibling(self) -> None:
        directory, model = _compile_text(
            """
            predicate c_done() -> bool;
            object Worker: T {
                initial_state: State::Idle;
                state State::Idle { actions {
                    on Action::A { emits { Worker.Action::C; } }
                    on Action::B { depends_on { c_done(); } }
                    on Action::C { establishes { c_done(); } }
                } }
            }
            object Parent: T {
                initial_state: State::Idle;
                state State::Idle { actions {
                    on Action::Start {
                        emits { Worker.Action::A; Worker.Action::B; }
                    }
                } }
            }
            external Human { drives { Parent.Action::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        root = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(
            (
                root.emits[0].event.signal,
                root.emits[0].emits[0].event.signal,
                root.emits[1].event.signal,
            ),
            (("Action", "A"), ("Action", "C"), ("Action", "B")),
        )
        self.assertEqual(root.emits[1].depends_on[0].status, "passed")

    def test_emit_failure_keeps_commits_and_stops_later_siblings(self) -> None:
        directory, model = _compile_text(
            """
            predicate produced() -> bool;
            predicate first_done() -> bool;
            predicate rejected() -> bool;
            object First: T {
                initial_state: State::Idle;
                state State::Idle { actions {
                    on Action::Run { establishes { first_done(); } }
                } }
            }
            object Broken: T {
                initial_state: State::Idle;
                state State::Idle {
                    invariant { false; }
                    actions { on Action::Run { establishes { rejected(); } } }
                }
            }
            object Later: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Run -> State::Done {}
                } }
                state State::Done {}
            }
            object Producer: T {
                initial_state: State::Idle;
                state State::Idle { actions {
                    on Action::Start {
                        establishes { produced(); }
                        emits {
                            First.Action::Run;
                            Broken.Action::Run;
                            Later.Transition::Run;
                        }
                    }
                } }
            }
            external Human { drives { Producer.Action::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        root = result.units[0]
        facts = {item.predicate[-1] for item in result.facts}
        states = {item.object[-1]: item.state for item in result.final_state}
        self.assertEqual(result.status, "invariant_failed")
        self.assertEqual(root.status, "passed")
        self.assertEqual(len(root.emits), 2)
        self.assertEqual(root.emits[0].status, "passed")
        self.assertEqual(root.emits[1].status, "invariant_failed")
        self.assertEqual(facts, {"produced", "first_done"})
        self.assertEqual(states["Later"], ("State", "Idle"))
        output = StringIO()
        dump_derivation_result(result, output)
        self.assertEqual(load_derivation_result(StringIO(output.getvalue())), result)
        rendered = StringIO()
        render_derivation_result(result, rendered)
        self.assertEqual(
            rendered.getvalue(),
            "Human -> Producer: drives Action::Start\n"
            "  current state: State::Idle\n"
            "  commit state: unchanged\n"
            "\n"
            "Producer -> First: emits Action::Run\n"
            "  current state: State::Idle\n"
            "  commit state: unchanged\n"
            "\n"
            "Producer -> Broken: emits Action::Run\n"
            "  current state: State::Idle\n"
            "  commit state: not committed ✗\n"
            "\n"
            "stopped: invariant_failed\n",
        )

    def test_failed_invariant_rolls_back_state_and_staged_facts(self) -> None:
        case = CASES_DIRECTORY / "13-invariant-rollback"
        model = compile_spec(case / "main.spec")
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "invariant_failed")
        self.assertEqual(result.final_state[0].state, ("State", "Idle"))
        self.assertEqual(result.facts, ())
        self.assertEqual(result.failure.path, "units[0].invariants[0]")

    def test_unsupported_expression_fails_at_its_clause(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Go -> State::Ready {
                        depends_on { 1 + 2 == 3; }
                    }
                } }
                state State::Ready {}
            }
            external Human { drives { Computer.Transition::Go; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "unsupported_feature")
        self.assertEqual(result.failure.path, "units[0].depends_on[0]")
        self.assertEqual(result.units[0].depends_on[0].status, "unsupported")

    def test_unimplemented_model_features_fail_before_execution(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                attrs { value: Size; }
                state State::Idle {
                    transitions {
                        on Transition::Go -> State::Ready {
                            may_change { value; }
                            deferred x.001 {
                                category: Category::Detail;
                                summary: "later";
                                evidence { true; }
                                close_when: "implemented";
                            }
                        }
                    }
                    actions { on Action::Refresh { drives {} } }
                }
                state State::Ready {}
                reference implementation { value = symbol("value"); }
            }
            external Human { drives { Computer.Transition::Go; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "unsupported_feature")
        self.assertEqual(result.units, ())
        self.assertEqual(
            result.failure.features,
            ("attrs", "deferred", "may_change", "reference"),
        )

    def test_ensures_cannot_read_facts_staged_by_the_same_unit(self) -> None:
        directory, model = _compile_text(
            """
            predicate ready() -> bool;
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Go -> State::Ready {
                        establishes { ready(); }
                        ensures { ready(); }
                    }
                } }
                state State::Ready {}
            }
            external Human { drives { Computer.Transition::Go; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "ensures_failed")
        self.assertEqual(result.units[0].establishes, ())
        self.assertEqual(result.facts, ())

    def test_depends_on_runs_before_drives_regardless_of_block_order(self) -> None:
        directory, model = _compile_text(
            """
            object Child: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Work -> State::Done {} }
                }
                state State::Done {}
            }
            object Parent: T {
                initial_state: State::Idle;
                state State::Idle { transitions {
                    on Transition::Start -> State::Done {
                        drives { Child.Transition::Work; }
                        depends_on { false; }
                    }
                } }
                state State::Done {}
            }
            external Human { drives { Parent.Transition::Start; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        states = {item.object[-1]: item.state for item in result.final_state}
        self.assertEqual(result.status, "depends_on_failed")
        self.assertEqual(result.units[0].drives, ())
        self.assertEqual(states["Child"], ("State", "Idle"))

    def test_target_invariants_see_the_candidate_state(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Go -> State::Ready {} }
                }
                state State::Ready {
                    invariant { Computer == State::Ready; }
                }
            }
            external Human { drives { Computer.Transition::Go; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.units[0].invariants[0].status, "passed")

    def test_external_emit_is_still_a_selected_root_unit(self) -> None:
        directory, model = _compile_text(
            """
            object Computer: T {
                initial_state: State::Idle;
                state State::Idle {
                    transitions { on Transition::Go -> State::Ready {} }
                }
                state State::Ready {}
            }
            external Human { emits { Computer.Transition::Go; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.units[0].kind, "root")
        self.assertEqual(result.units[0].event.mode, "emit")

    def test_undeclared_sequence_event_is_rejected_as_a_root(self) -> None:
        model = compile_spec(CASES_DIRECTORY / "02-simple-transition" / "main.spec")
        selected = _sequence(("root.Computer", "root.Computer", "Go", "drive"))
        result = derive(model, selected)
        self.assertEqual(result.status, "undeclared_external_signal")
        self.assertEqual(result.failure.path, "units[0]")
        rendered = StringIO()
        render_derivation_result(result, rendered)
        self.assertEqual(
            rendered.getvalue(),
            "Computer -> Computer: drives Transition::Go\n"
            "  current state: State::Idle\n"
            "  commit state: not committed ✗\n"
            "\n"
            "stopped: undeclared_external_signal\n",
        )

    def test_invalid_transition_models_are_compile_errors(self) -> None:
        bodies = (
            """
            object Computer: T { initial_state: State::A; state State::A {
              transitions { on Transition::Go -> State::Missing {} }
            } }
            external Human { drives { Computer.Transition::Go; } }
            """,
            """
            object Computer: T { initial_state: State::A; state State::A {
              transitions {
                on Transition::Go -> State::A {}
                on Transition::Go -> State::A {}
              }
            } }
            external Human { drives { Computer.Transition::Go; } }
            """,
        )
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(CompilationError):
                    directory, _ = _compile_text(body)
                    directory.cleanup()

    def test_continuation_yields_immediately_and_resumes_exactly_once(self) -> None:
        directory, model = _compile_text(
            """
            object Worker: T {
                state State::Base {
                    actions { on Action::Step { drives {} } }
                }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow {
                state State::Online {
                    actions { override on Action::Enter {
                        yields Worker.Action::Step;
                        yields Worker.Action::Step;
                        ensures { true; }
                    } }
                }
            }
            external Human { emits {
                Boot.Action::Enter;
                Boot.Action::Enter;
                Boot.Action::Enter;
            } }
            """
        )
        self.addCleanup(directory.cleanup)
        selected = default_derivation_sequence(model)
        first = derive(model, DerivationSequence(2, selected.events[:1]))
        self.assertEqual(first.status, "yielded")
        self.assertEqual(first.units[0].status, "yielded")
        self.assertEqual(first.units[0].yields[0].status, "passed")
        self.assertEqual(first.continuations[0].frames[0].control_index, 1)
        self.assertEqual(first.continuations[0].generation, 1)
        serialized = StringIO()
        dump_derivation_result(first, serialized)
        self.assertEqual(
            load_derivation_result(StringIO(serialized.getvalue())), first
        )

        complete = derive(model, selected)
        self.assertEqual(
            tuple(unit.status for unit in complete.units),
            ("yielded", "yielded", "passed"),
        )
        first_token = complete.units[0].yield_token_created
        second_token = complete.units[1].yield_token_created
        self.assertEqual(complete.units[1].yield_token_consumed, first_token)
        self.assertEqual(complete.units[2].yield_token_consumed, second_token)
        self.assertEqual(first_token.generation, 1)
        self.assertEqual(second_token.generation, 2)
        self.assertEqual(complete.continuations, ())

    def test_nested_continuation_action_uses_one_resume_frame_chain(self) -> None:
        directory, model = _compile_text(
            """
            object Worker: T {
                state State::Base { actions { on Action::Step { drives {} } } }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online {
                    actions { on Action::Enter; on Action::Inner; }
                }
            }
            object Boot: Flow {
                state State::Online { actions {
                    override on Action::Enter {
                        drives { Boot.Action::Inner; }
                    }
                    override on Action::Inner {
                        yields Worker.Action::Step;
                    }
                } }
            }
            external Human { emits { Boot.Action::Enter; Boot.Action::Enter; } }
            """
        )
        self.addCleanup(directory.cleanup)
        selected = default_derivation_sequence(model)
        first = derive(model, DerivationSequence(2, selected.events[:1]))
        self.assertEqual(
            tuple(frame.handler for frame in first.continuations[0].frames),
            (("Action", "Enter"), ("Action", "Inner")),
        )
        complete = derive(model, selected)
        self.assertEqual(
            tuple(unit.status for unit in complete.units), ("yielded", "passed")
        )
        self.assertEqual(complete.units[1].drives[0].handler, ("Action", "Inner"))
        self.assertEqual(complete.continuations, ())

    def test_two_continuations_pause_independently_and_yield_transition_commits(self) -> None:
        directory, model = _compile_text(
            """
            object Worker: T {
                state State::Base {
                    transitions { on Transition::Step -> State::Done {} }
                }
                state State::Done {}
            }
            object Waiter: T {
                state State::Base {
                    actions { on Action::Wait { drives {} } }
                }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object First: Flow {
                state State::Online { actions {
                    override on Action::Enter { yields Worker.Transition::Step; }
                } }
            }
            object Second: Flow {
                state State::Online { actions {
                    override on Action::Enter { yields Waiter.Action::Wait; }
                } }
            }
            external Human { emits { First.Action::Enter; Second.Action::Enter; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "yielded")
        worker = next(item for item in result.final_state if item.object[-1] == "Worker")
        self.assertEqual(worker.state, ("State", "Done"))
        self.assertEqual(
            tuple(unit.status for unit in result.units), ("yielded", "yielded")
        )
        snapshots = {item.object[-1]: item for item in result.continuations}
        self.assertEqual(snapshots["First"].generation, 1)
        self.assertEqual(snapshots["Second"].generation, 1)

    def test_reentry_and_exit_report_precise_continuation_failures(self) -> None:
        directory, model = _compile_text(
            """
            object Worker: T {
                state State::Base { actions {
                    on Action::Step { drives { Boot.Action::Enter; } }
                } }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow {
                state State::Online { actions {
                    override on Action::Enter { yields Worker.Action::Step; }
                } }
            }
            external Human { emits { Boot.Action::Enter; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        self.assertEqual(result.status, "continuation_reentry")
        self.assertEqual(result.units[0].status, "yielded")
        self.assertEqual(result.continuations[0].generation, 1)
        self.assertIsNotNone(result.continuations[0].yield_token)

        directory_emit, model_emit = _compile_text(
            """
            object Worker: T {
                state State::Base { actions {
                    on Action::Step { emits { Boot.Action::Enter; } }
                } }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow {
                state State::Online { actions {
                    override on Action::Enter { yields Worker.Action::Step; }
                } }
            }
            external Human { emits { Boot.Action::Enter; } }
            """
        )
        self.addCleanup(directory_emit.cleanup)
        resumed = derive(model_emit, default_derivation_sequence(model_emit))
        self.assertEqual(resumed.status, "passed")
        self.assertEqual(resumed.continuations, ())
        resumed_unit = resumed.units[0].yields[0].emits[0]
        self.assertEqual(
            resumed_unit.yield_token_consumed,
            resumed.units[0].yield_token_created,
        )

        directory2, model2 = _compile_text(
            """
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow {
                state State::Online { actions {
                    override on Action::Enter { drives {} }
                } }
            }
            external Human { emits { Boot.Action::Enter; Boot.Action::Enter; } }
            """
        )
        self.addCleanup(directory2.cleanup)
        exited = derive(model2, default_derivation_sequence(model2))
        self.assertEqual(exited.status, "no_resumable_continuation")
        self.assertEqual(
            tuple(unit.status for unit in exited.units),
            ("passed", "no_resumable_continuation"),
        )
        self.assertEqual(exited.continuations, ())


class DerivationJSONTests(unittest.TestCase):
    def test_sequence_is_strict(self) -> None:
        valid = json.dumps(
            {
                "schema_version": 2,
                "events": [
                    {
                        "source": ["root", "Human"],
                        "target": ["root", "Computer"],
                        "signal": ["Transition", "Go"],
                        "mode": "drive",
                    }
                ],
            }
        )
        parsed = load_derivation_sequence(StringIO(valid))
        output = StringIO()
        dump_derivation_sequence(parsed, output)
        self.assertEqual(load_derivation_sequence(StringIO(output.getvalue())), parsed)
        action_document = json.loads(valid)
        action_document["events"][0]["signal"] = ["Action", "Refresh"]
        action = load_derivation_sequence(StringIO(json.dumps(action_document)))
        self.assertEqual(action.events[0].signal, ("Action", "Refresh"))
        action_output = StringIO()
        dump_derivation_sequence(action, action_output)
        self.assertEqual(
            load_derivation_sequence(StringIO(action_output.getvalue())), action
        )

        startup_document = json.loads(valid)
        startup_document["events"][0]["signal"] = ["Transition", "Startup"]
        startup = load_derivation_sequence(StringIO(json.dumps(startup_document)))
        self.assertEqual(startup.events[0].signal, ("Transition", "Preset"))
        startup_output = StringIO()
        dump_derivation_sequence(startup, startup_output)
        self.assertNotIn("Startup", startup_output.getvalue())
        self.assertIn('"Preset"', startup_output.getvalue())

        action_startup_document = json.loads(valid)
        action_startup_document["events"][0]["signal"] = ["Action", "Startup"]
        action_startup = load_derivation_sequence(
            StringIO(json.dumps(action_startup_document))
        )
        self.assertEqual(action_startup.events[0].signal, ("Action", "Startup"))
        invalid = (
            valid.replace('"schema_version": 2', '"schema_version": 1'),
            valid.replace('"events":', '"extra": 0, "events":'),
            valid.replace('["Transition", "Go"]', '["Effect", "Go"]'),
            valid.replace('"drive"', '"unknown"'),
            '{"schema_version":2,"events":[],"events":[]}',
        )
        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaises(DerivationValidationError):
                    load_derivation_sequence(StringIO(document))

    def test_result_is_strict_canonical_and_round_trips(self) -> None:
        case = CASES_DIRECTORY / "11-establishes-invariant"
        model = compile_spec(case / "main.spec")
        result = derive(model, default_derivation_sequence(model))
        output = StringIO()
        dump_derivation_result(result, output)
        self.assertEqual(load_derivation_result(StringIO(output.getvalue())), result)
        document = json.loads(output.getvalue())
        self.assertEqual(document["schema_version"], 4)
        self.assertNotIn("failure", document)

        startup_event = json.loads(output.getvalue())
        startup_event["units"][0]["event"]["signal"] = [
            "Transition",
            "Startup",
        ]
        startup_event["units"][0]["handler"] = ["Transition", "Preset"]
        startup_handler = json.loads(output.getvalue())
        startup_handler["units"][0]["event"]["signal"] = [
            "Transition",
            "Preset",
        ]
        startup_handler["units"][0]["handler"] = ["Transition", "Startup"]
        for alias_document in (startup_event, startup_handler):
            with self.subTest(alias_document=alias_document):
                with self.assertRaisesRegex(
                    DerivationValidationError,
                    "must use canonical signal Transition::Preset",
                ):
                    load_derivation_result(StringIO(json.dumps(alias_document)))

        invalid_documents = []
        wrong_version = dict(document)
        wrong_version["schema_version"] = 1
        invalid_documents.append(json.dumps(wrong_version))
        unknown = dict(document)
        unknown["trace"] = []
        invalid_documents.append(json.dumps(unknown))
        missing = dict(document)
        del missing["facts"]
        invalid_documents.append(json.dumps(missing))
        invalid_documents.append('{"schema_version":4,"status":"passed","status":"passed"}')
        for invalid in invalid_documents:
            with self.subTest(document=invalid):
                with self.assertRaises(DerivationValidationError):
                    load_derivation_result(StringIO(invalid))


class CLITests(unittest.TestCase):
    def test_cli_renders_human_success_and_semantic_failure_to_stdout(self) -> None:
        for case_name, expected_status in (
            ("02-simple-transition", 0),
            ("01-no-handler", 1),
        ):
            with self.subTest(case=case_name):
                case = CASES_DIRECTORY / case_name
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = derive_main(["--model", str(case / "main.spec")])
                self.assertEqual(status, expected_status)
                self.assertEqual(
                    stdout.getvalue(),
                    (case / "expected.stdout").read_text(encoding="utf-8"),
                )
                self.assertEqual(stderr.getvalue(), "")

    def test_input_errors_use_stderr(self) -> None:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = derive_main(["--model", "/definitely/missing/model.spec"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())

    def test_explicit_sequence_uses_the_supplied_default_model(self) -> None:
        case = CASES_DIRECTORY / "02-simple-transition"
        model = compile_spec(case / "main.spec")
        with tempfile.TemporaryDirectory() as directory:
            sequence_path = Path(directory) / "selected.sequence.json"
            with sequence_path.open("w", encoding="utf-8") as stream:
                dump_derivation_sequence(default_derivation_sequence(model), stream)
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = derive_main(
                    ["--sequence", str(sequence_path)],
                    default_model=case / "main.spec",
                )
        self.assertEqual(status, 0)
        self.assertTrue(stdout.getvalue().endswith("Derivation passed!\n"))
        self.assertEqual(stderr.getvalue(), "")

    def test_wrapper_default_works_outside_the_repository(self) -> None:
        completed = subprocess.run(
            [str(REPOSITORY / "tools" / "bin" / "derive")],
            cwd=tempfile.gettempdir(),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        for signal in (
            "Transition::Preset",
            "Transition::Setup",
            "Transition::Enable",
        ):
            with self.subTest(signal=signal):
                self.assertIn(signal, completed.stdout)
        self.assertTrue(completed.stdout.endswith("Derivation passed!\n"))
        self.assertEqual(completed.stderr, "")

    def test_model_and_sequence_options_are_mutually_exclusive(self) -> None:
        completed = subprocess.run(
            [
                str(REPOSITORY / "tools" / "bin" / "derive"),
                "--model",
                str(CASES_DIRECTORY / "01-no-handler" / "main.spec"),
                "--sequence",
                "sequence.json",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("not allowed with argument", completed.stderr)


class TestRunnerOutputTests(unittest.TestCase):
    def _run_synthetic(
        self, test_case: type[unittest.TestCase], verbosity: int = 2
    ) -> tuple[unittest.TestResult, str]:
        stream = StringIO()
        result = unittest.TextTestRunner(
            stream=stream,
            verbosity=verbosity,
            resultclass=CompactTextTestResult,
        ).run(unittest.TestLoader().loadTestsFromTestCase(test_case))
        return result, stream.getvalue()

    def test_verbose_success_uses_only_the_method_name(self) -> None:
        result, output = self._run_synthetic(_SyntheticSuccess)
        self.assertTrue(result.wasSuccessful())
        self.assertTrue(output.startswith("test_success ... ok\n"), output)
        self.assertNotIn("_SyntheticSuccess", output.splitlines()[0])

        quiet_result, quiet_output = self._run_synthetic(
            _SyntheticSuccess, verbosity=1
        )
        self.assertTrue(quiet_result.wasSuccessful())
        self.assertTrue(quiet_output.startswith("."), quiet_output)

    def test_failure_summary_keeps_the_complete_test_identity(self) -> None:
        result, output = self._run_synthetic(_SyntheticFailure)
        self.assertFalse(result.wasSuccessful())
        self.assertTrue(output.startswith("test_failure ... FAIL\n"), output)
        self.assertIn(
            "FAIL: test_failure (" + __name__ + "._SyntheticFailure.test_failure)",
            output,
        )
        self.assertIn("Traceback (most recent call last):", output)
        self.assertIn("synthetic ordinary failure", output)

    def test_subtest_failure_keeps_identity_and_parameters(self) -> None:
        result, output = self._run_synthetic(_SyntheticSubtestFailure)
        self.assertFalse(result.wasSuccessful())
        self.assertTrue(output.startswith("test_subtests ... \n"), output)
        description = (
            "test_subtests ("
            + __name__
            + "._SyntheticSubtestFailure.test_subtests) (value='bad')"
        )
        self.assertIn("FAIL: " + description, output)
        self.assertIn("synthetic subtest failure", output)


def _suite(smoke_only: bool) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(SmokeGoldenTests))
    if not smoke_only:
        for test_case in (
            EngineTests,
            DerivationJSONTests,
            CLITests,
            TestRunnerOutputTests,
        ):
            suite.addTests(loader.loadTestsFromTestCase(test_case))
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run derive unit and golden tests")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()
    result = unittest.TextTestRunner(
        verbosity=1 if arguments.quiet else 2,
        resultclass=CompactTextTestResult,
    ).run(_suite(arguments.smoke_only))
    raise SystemExit(0 if result.wasSuccessful() else 1)
