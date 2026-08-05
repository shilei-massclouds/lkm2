from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from derive import (
    DerivationEvent,
    DerivationSequence,
    DerivationValidationError,
    default_derivation_sequence,
    derive,
    dump_derivation_result,
    dump_derivation_sequence,
    load_derivation_result,
    load_derivation_sequence,
)
from derive.cli import main
from model_ir import ModelIRValidationError, dump_model_ir, load_model_ir
from modelc import CompilationError, compile_spec


def write_model(directory: Path, body: str) -> Path:
    entry = directory / "main.spec"
    entry.write_text("spec root;\norigin root.Human;\n", encoding="utf-8")
    (directory / "root.spec").write_text(body, encoding="utf-8")
    return entry


def sequence(*events: tuple[str, str, str, str]) -> DerivationSequence:
    return DerivationSequence(
        1,
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


class RepositoryDerivationTests(unittest.TestCase):
    def test_current_model_lowers_all_active_declarations(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        self.assertEqual(model.schema_version, 3)
        self.assertEqual(
            tuple(item.name for item in model.objects),
            (("systems", "computer", "Computer"),),
        )
        self.assertEqual(model.objects[0].initial_state, None)
        self.assertEqual(
            tuple(signal.signal for signal in model.externals[0].signals),
            (("Transition", "Preset"), ("Transition", "Setup"), ("Transition", "Enable")),
        )

        output = StringIO()
        dump_model_ir(model, output)
        self.assertEqual(load_model_ir(StringIO(output.getvalue())), model)

    def test_current_sequence_stops_at_first_unhandled_signal(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        selected = default_derivation_sequence(model)
        result = derive(model, selected)

        self.assertEqual(result.status, "unhandled_signal")
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(result.trace[0].index, 0)
        self.assertEqual(result.final_state[0].state, None)
        self.assertEqual(result.pending_signals, (selected.events[0],))
        self.assertEqual(result.failure.event_index, 0)

    def test_cli_prints_failure_json_and_exits_one(self) -> None:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                ["--model", str(REPOSITORY / "model" / "main.spec")]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["status"], "unhandled_signal")

    def test_wrapper_defaults_work_outside_repository(self) -> None:
        completed = subprocess.run(
            [str(REPOSITORY / "tools" / "bin" / "derive")],
            cwd=tempfile.gettempdir(),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["status"], "unhandled_signal")
        self.assertEqual(completed.stderr, "")

    def test_model_and_sequence_options_are_mutually_exclusive(self) -> None:
        completed = subprocess.run(
            [
                str(REPOSITORY / "tools" / "bin" / "derive"),
                "--model",
                str(REPOSITORY / "model" / "main.spec"),
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

    def test_explicit_sequence_uses_default_model(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        with tempfile.TemporaryDirectory() as directory:
            sequence_path = Path(directory) / "selected.sequence.json"
            with sequence_path.open("w", encoding="utf-8") as stream:
                dump_derivation_sequence(default_derivation_sequence(model), stream)
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(
                    ["--sequence", str(sequence_path)],
                    default_model=REPOSITORY / "model" / "main.spec",
                )
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "unhandled_signal")
        self.assertEqual(stderr.getvalue(), "")


class EngineTests(unittest.TestCase):
    CHAIN_MODEL = """
        object Worker: ObjectType {
            initial_state: State::Idle;
            state State::Idle {
                transitions { on Transition::Work -> State::Done {} }
            }
            state State::Done {}
        }
        object Logger: ObjectType {
            initial_state: State::Idle;
            state State::Idle {
                transitions { on Transition::Log -> State::Done {} }
            }
            state State::Done {}
        }
        object Computer: ObjectType {
            initial_state: State::Base;
            state State::Base {
                transitions {
                    on Transition::Preset -> State::Ready {
                        drives { Worker.Transition::Work; }
                        emits { Logger.Transition::Log; }
                    }
                }
            }
            state State::Ready {}
        }
        external Human { drives { Computer.Transition::Preset; } }
    """

    def compile(self, body: str | None = None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return compile_spec(write_model(Path(directory.name), body or self.CHAIN_MODEL))

    def test_generated_events_are_ordered_and_explicitly_selected(self) -> None:
        model = self.compile()
        selected = sequence(
            ("root.Human", "root.Computer", "Preset", "drive"),
            ("root.Computer", "root.Logger", "Log", "emit"),
            ("root.Computer", "root.Worker", "Work", "drive"),
        )
        first = derive(model, selected)
        second = derive(model, selected)
        self.assertEqual(first.status, "passed")
        self.assertEqual(
            first.trace[0].generated,
            (
                DerivationEvent(("root", "Computer"), ("root", "Worker"), ("Transition", "Work"), "drive"),
                DerivationEvent(("root", "Computer"), ("root", "Logger"), ("Transition", "Log"), "emit"),
            ),
        )
        self.assertEqual(first, second)
        first_json, second_json = StringIO(), StringIO()
        dump_derivation_result(first, first_json)
        dump_derivation_result(second, second_json)
        self.assertEqual(first_json.getvalue().encode(), second_json.getvalue().encode())
        self.assertEqual(load_derivation_result(StringIO(first_json.getvalue())), first)

    def test_missing_pending_and_incomplete_sequence_fail(self) -> None:
        model = self.compile()
        absent = derive(
            model,
            sequence(("root.Computer", "root.Worker", "Work", "drive")),
        )
        self.assertEqual(absent.status, "signal_not_pending")

        incomplete = derive(
            model,
            sequence(("root.Human", "root.Computer", "Preset", "drive")),
        )
        self.assertEqual(incomplete.status, "sequence_incomplete")
        self.assertEqual(len(incomplete.pending_signals), 2)

    def test_advanced_semantics_are_rejected_before_execution(self) -> None:
        model = self.compile(
            """
            object Computer: ObjectType {
                initial_state: State::Base;
                attrs { value: Size; }
                state State::Base {
                    invariant { ready(); }
                    transitions {
                        on Transition::Preset -> State::Ready {
                            depends_on { ready(); }
                            may_change { value; }
                            ensures { done(); }
                            deferred x.001 {
                                category: Category::Detail;
                                summary: "later";
                                evidence { ready(); }
                                close_when: "implemented";
                            }
                        }
                    }
                    actions { on Action::Refresh {} }
                }
                state State::Ready {}
                reference reference_impl { value = symbol("value"); }
            }
            external Human { drives { Computer.Transition::Preset; } }
            """
        )
        result = derive(
            model,
            sequence(("root.Human", "root.Computer", "Preset", "drive")),
        )
        self.assertEqual(result.status, "unsupported_feature")
        self.assertEqual(result.trace, ())
        self.assertEqual(
            result.failure.features,
            ("action", "attrs", "deferred", "depends_on", "ensures", "invariant", "may_change", "reference"),
        )

    def test_invalid_transition_models_are_compile_errors(self) -> None:
        cases = (
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
            """
            object Computer: T {}
            external Human { drives { Missing.Transition::Go; } }
            """,
        )
        for body in cases:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(CompilationError):
                    compile_spec(write_model(Path(directory), body))


class DerivationJSONTests(unittest.TestCase):
    def test_sequence_is_strict_and_canonical_result_round_trips(self) -> None:
        valid = json.dumps(
            {
                "schema_version": 1,
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
        self.assertEqual(parsed.events[0].signal, ("Transition", "Go"))
        invalid = (
            valid.replace('"schema_version": 1', '"schema_version": 2'),
            valid.replace('"events":', '"extra": 0, "events":'),
            valid.replace('["Transition", "Go"]', '["Action", "Go"]'),
            valid.replace('"drive"', '"unknown"'),
            '{"schema_version":1,"events":[],"events":[]}',
        )
        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaises(DerivationValidationError):
                    load_derivation_sequence(StringIO(document))


if __name__ == "__main__":
    unittest.main()
