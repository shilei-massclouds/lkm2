from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))
sys.path.insert(0, str(REPOSITORY / "tools" / "tests"))

from _derive_harness import with_cpu_line  # noqa: E402

from derive import (  # noqa: E402
    DerivationDirective,
    DerivationSequence,
    DerivationValidationError,
    default_derivation_sequence,
    derive,
    dump_derivation_result,
    load_derivation_result,
    render_derivation_result,
)
from derive.cli import main as derive_main  # noqa: E402
from model_ir import (  # noqa: E402
    ModelAction,
    ModelExpression,
    ModelHandlerBlock,
    ModelIRValidationError,
    dump_model_ir,
    load_model_ir,
)
from modelc import CompilationError, compile_spec  # noqa: E402


def _source_tree(body: str) -> tuple[tempfile.TemporaryDirectory, Path]:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    (root / "main.spec").write_text(
        "spec root;\norigin root.Human;\n", encoding="utf-8"
    )
    (root / "root.spec").write_text(with_cpu_line(body), encoding="utf-8")
    return directory, root / "main.spec"


def _compile_text(body: str):
    directory, entry = _source_tree(body)
    return directory, entry, compile_spec(entry)


def _all_units(units):
    for unit in units:
        yield unit
        yield from _all_units(unit.drives)
        yield from _all_units(unit.yields)
        yield from _all_units(unit.emits)
        yield from _all_units(unit.resumes)


class ModelDirectiveTests(unittest.TestCase):
    def test_print_and_panic_are_valid_as_the_only_action_content(self) -> None:
        directory, _, model = _compile_text(
            """
            object Computer: T {
                state State::Base { actions {
                    on Action::Show { print "hello"; }
                    on Action::Stop { panic "halt"; }
                } }
            }
            external Human { drives { Computer.Action::Show; } }
            """
        )
        self.addCleanup(directory.cleanup)
        actions = model.objects[0].states[0].actions
        self.assertEqual(
            tuple(action.blocks[0].kind for action in actions), ("print", "panic")
        )

    def test_directives_lower_to_strict_model_ir_v13_blocks(self) -> None:
        directory, _, model = _compile_text(
            """
            object Computer: T {
                state State::Base { actions {
                    on Action::Show { print "你好"; panic "停止"; }
                } }
            }
            external Human { drives { Computer.Action::Show; } }
            """
        )
        self.addCleanup(directory.cleanup)
        blocks = model.objects[0].states[0].actions[0].blocks
        self.assertEqual(model.schema_version, 13)
        self.assertEqual(tuple(block.kind for block in blocks), ("print", "panic"))
        self.assertEqual(
            tuple(block.expressions[0].value for block in blocks), ("你好", "停止")
        )

        output = StringIO()
        dump_model_ir(model, output)
        document = json.loads(output.getvalue())
        self.assertEqual(document["schema_version"], 13)
        self.assertEqual(load_model_ir(StringIO(output.getvalue())), model)

        action = document["modules"][0]["objects"][0]["states"][0]["actions"][0]
        action["blocks"][0]["expressions"] = []
        with self.assertRaisesRegex(
            ModelIRValidationError, "print block requires exactly one string expression"
        ):
            load_model_ir(StringIO(json.dumps(document)))

        for kind, expressions in (
            ("print", ()),
            ("panic", (ModelExpression("identifier", "message"),)),
            (
                "print",
                (ModelExpression("string", "one"), ModelExpression("string", "two")),
            ),
        ):
            with self.subTest(kind=kind, expressions=expressions):
                with self.assertRaisesRegex(
                    ModelIRValidationError,
                    f"{kind} block requires exactly one string expression",
                ):
                    ModelHandlerBlock(kind, expressions=expressions)

    def test_empty_action_and_invalid_directive_syntax_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            ModelIRValidationError,
            "concrete action must contain at least one handler block",
        ):
            ModelAction(("Action", "Run"), ())

        cases = {
            "empty": "on Action::Run {}",
            "non_string_print": "on Action::Run { print message; }",
            "non_string_panic": "on Action::Run { panic 1; }",
            "missing_print_argument": "on Action::Run { print; }",
            "missing_panic_argument": "on Action::Run { panic; }",
            "multiple_arguments": 'on Action::Run { print "one", "two"; }',
            "parenthesized_argument": 'on Action::Run { print ("one"); }',
            "string_expression": 'on Action::Run { panic "one" + "two"; }',
            "missing_semicolon": 'on Action::Run { panic "halt" }',
        }
        for label, handler in cases.items():
            with self.subTest(label=label):
                directory, entry = _source_tree(
                    f"""
                    object Computer: T {{
                        state State::Base {{ actions {{ {handler} }} }}
                    }}
                    """
                )
                self.addCleanup(directory.cleanup)
                with self.assertRaises(CompilationError) as caught:
                    compile_spec(entry)
                self.assertGreaterEqual(caught.exception.diagnostic.line, 1)
                self.assertGreaterEqual(caught.exception.diagnostic.column, 1)


class DerivationDirectiveTests(unittest.TestCase):
    def test_print_preserves_state_and_cli_succeeds_with_single_line_unicode(self) -> None:
        directory, entry, model = _compile_text(
            r'''
            object Computer: T {
                state State::Base { actions {
                    on Action::Show {
                        print "你好\nline\tend\u0001";
                        print "done";
                    }
                } }
            }
            external Human { drives { Computer.Action::Show; } }
            '''
        )
        self.addCleanup(directory.cleanup)
        sequence = default_derivation_sequence(model)
        self.assertEqual(sequence.schema_version, 3)
        result = derive(model, sequence)
        unit = result.units[0]
        self.assertEqual(result.status, "passed")
        self.assertEqual(
            unit.directives,
            (
                DerivationDirective("print", "你好\nline\tend\x01"),
                DerivationDirective("print", "done"),
            ),
        )
        self.assertEqual(unit.state_before, unit.state_after)
        self.assertEqual(unit.drives, ())
        self.assertEqual(unit.emits, ())
        self.assertEqual(result.facts, ())

        rendered = StringIO()
        render_derivation_result(result, rendered)
        expected = (
            "Human -> Computer: drives Action::Show\n"
            "  current state: State::Base\n"
            "  print: 你好\\nline\\tend\\u0001\n"
            "  print: done\n"
            "  commit state: unchanged\n"
            "\n"
            "Derivation passed!\n"
        )
        self.assertEqual(rendered.getvalue(), expected)

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = derive_main(["--model", str(entry)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), expected)
        self.assertEqual(stderr.getvalue(), "")

        serialized = StringIO()
        dump_derivation_result(result, serialized)
        document = json.loads(serialized.getvalue())
        self.assertEqual(document["schema_version"], 12)
        self.assertEqual(load_derivation_result(StringIO(serialized.getvalue())), result)

        missing_directives = json.loads(serialized.getvalue())
        del missing_directives["paths"][0]["units"][0]["directives"]
        with self.assertRaises(DerivationValidationError):
            load_derivation_result(StringIO(json.dumps(missing_directives)))

        invalid_directive = json.loads(serialized.getvalue())
        invalid_directive["paths"][0]["units"][0]["directives"][0]["kind"] = "unknown"
        with self.assertRaises(DerivationValidationError):
            load_derivation_result(StringIO(json.dumps(invalid_directive)))

    def test_panic_does_not_commit_facts_or_emits_and_cli_fails(self) -> None:
        directory, entry, model = _compile_text(
            """
            predicate committed() -> bool;
            object Child: T {
                state State::Base { actions {
                    on Action::Run { print "child ran"; }
                } }
            }
            object Computer: T {
                state State::Base { transitions {
                    on Transition::Run -> State::Done {
                        panic "boom";
                        establishes { committed(); }
                        emits { Child.Action::Run; }
                    }
                } }
                state State::Done {}
            }
            external Human { drives { Computer.Transition::Run; } }
            """
        )
        self.addCleanup(directory.cleanup)
        result = derive(model, default_derivation_sequence(model))
        unit = result.units[0]
        states = {item.object[-1]: item.state for item in result.final_state}
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.paths[0].status, "panic")
        self.assertEqual(result.failure.message, "boom")
        self.assertEqual(result.failure.path, "units[0].directives[0]")
        self.assertEqual(unit.status, "panic")
        self.assertEqual(unit.directives, (DerivationDirective("panic", "boom"),))
        self.assertEqual(states["Computer"], ("State", "Base"))
        self.assertEqual(states["Child"], ("State", "Base"))
        self.assertEqual(result.facts, ())
        self.assertEqual(unit.establishes, ())
        self.assertEqual(unit.emits, ())

        expected = (
            "Human -> Computer: drives Transition::Run\n"
            "  current state: State::Base\n"
            "  panic: boom ✗\n"
            "  commit state: not committed ✗\n"
            "\n"
            "stopped: panic\n"
        )
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = derive_main(["--model", str(entry)])
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), expected)
        self.assertEqual(stderr.getvalue(), "")

        serialized = StringIO()
        dump_derivation_result(result, serialized)
        self.assertEqual(load_derivation_result(StringIO(serialized.getvalue())), result)

    def test_panic_keeps_only_drives_completed_before_it(self) -> None:
        directory, _, model = _compile_text(
            """
            predicate parent_committed() -> bool;
            object First: T {
                state State::Base { transitions {
                    on Transition::Run -> State::Done { print "inside first"; }
                } }
                state State::Done {}
            }
            object Second: T {
                state State::Base { transitions {
                    on Transition::Run -> State::Done {}
                } }
                state State::Done {}
            }
            object Parent: T {
                state State::Base { transitions {
                    on Transition::Run -> State::Done {
                        drives { First.Transition::Run; }
                        print "first complete";
                        panic "stop now";
                        drives { Second.Transition::Run; }
                        establishes { parent_committed(); }
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
        states = {item.object[-1]: item.state for item in result.final_state}
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.paths[0].status, "panic")
        self.assertEqual(len(unit.drives), 1)
        self.assertEqual(unit.drives[0].event.target[-1], "First")
        self.assertEqual(states["First"], ("State", "Done"))
        self.assertEqual(states["Second"], ("State", "Base"))
        self.assertEqual(states["Parent"], ("State", "Base"))
        self.assertEqual(
            unit.directives,
            (
                DerivationDirective("print", "first complete"),
                DerivationDirective("panic", "stop now"),
            ),
        )
        self.assertEqual(result.facts, ())

        rendered = StringIO()
        render_derivation_result(result, rendered)
        output = rendered.getvalue()
        child_print = output.index("    print: inside first\n")
        parent_print = output.index("  print: first complete\n")
        parent_panic = output.index("  panic: stop now ✗\n")
        self.assertLess(child_print, parent_print)
        self.assertLess(parent_print, parent_panic)

    def test_continuation_resume_does_not_repeat_print_and_panic_clears_frame(self) -> None:
        directory, _, model = _compile_text(
            """
            object Worker: T {
                state State::Base { actions {
                    on Action::Step { drives {} }
                } }
            }
            type Flow {
                continuation: true;
                initial_state: State::Online;
                state State::Online { actions { on Action::Enter; } }
            }
            object Boot: Flow {
                state State::Online { actions {
                    override on Action::Enter {
                        print "before yield";
                        yields Worker.Action::Step;
                        print "after resume";
                        panic "resume panic";
                    }
                } }
            }
            external Human {
                resumes Boot.Action::Enter;
                resumes Boot.Action::Enter;
            }
            """
        )
        self.addCleanup(directory.cleanup)
        selected = default_derivation_sequence(model)
        self.assertEqual(len(selected.events), 2)
        result = derive(model, DerivationSequence(3, selected.events))
        directives = tuple(
            directive
            for unit in _all_units(result.units)
            for directive in unit.directives
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.paths[0].status, "panic")
        self.assertEqual(
            directives,
            (
                DerivationDirective("print", "before yield"),
                DerivationDirective("print", "after resume"),
                DerivationDirective("panic", "resume panic"),
            ),
        )
        self.assertEqual(tuple(unit.status for unit in result.units), ("yielded", "panic"))
        self.assertEqual(result.continuations, ())


if __name__ == "__main__":
    unittest.main()
