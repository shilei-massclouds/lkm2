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
    ModelEntry,
    ModelExternal,
    ModelIR,
    ModelIRValidationError,
    ModelModule,
    ModelObject,
    ModelSignal,
    ModelType,
    ModelTypeExpression,
    dump_model_ir,
    load_model_ir,
)
from modelc import CompilationError, SourceSpan, compile_spec, parse_spec
from modelc.cli import main


EXPECTED_MODEL = ModelIR(
    schema_version=3,
    entry=ModelEntry(
        origin=("systems", "human", "Human"), spec=("systems",)
    ),
    modules=(
        ModelModule(name=("systems",)),
        ModelModule(
            name=("systems", "computer"),
            types=(ModelType(("systems", "computer", "ComputerType"), None),),
            objects=(
                ModelObject(
                    ("systems", "computer", "Computer"),
                    ModelTypeExpression(("ComputerType",)),
                    None,
                    None,
                    None,
                    None,
                    (),
                    (),
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
            document.origin.name.parts, ("systems", "human", "Human")
        )
        self.assertEqual(document.spec.name.span, SourceSpan(5, 6, 5, 13))
        self.assertEqual(document.spec.span, SourceSpan(5, 1, 5, 14))
        self.assertEqual(document.origin.name.span, SourceSpan(7, 8, 7, 27))
        self.assertEqual(document.origin.span, SourceSpan(7, 1, 7, 28))
        self.assertEqual(compile_spec(path), EXPECTED_MODEL)

    def test_comments_whitespace_and_long_origin(self) -> None:
        document = parse_spec(
            """
            // entry namespace
            spec alpha; /* between declarations */
            origin alpha.beta_gamma.Person2; // end
            """
        )
        self.assertEqual(document.spec.name.parts, ("alpha",))
        self.assertEqual(
            document.origin.name.parts, ("alpha", "beta_gamma", "Person2")
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
                use crate::root::b::FromCrate;
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


class ModelIRJSONTests(unittest.TestCase):
    def test_canonical_output_is_repeatable_and_round_trips(self) -> None:
        first = StringIO()
        second = StringIO()
        dump_model_ir(EXPECTED_MODEL, first)
        dump_model_ir(compile_spec(REPOSITORY / "model" / "main.spec"), second)

        self.assertEqual(first.getvalue(), EXPECTED_JSON)
        self.assertEqual(first.getvalue().encode(), second.getvalue().encode())
        self.assertEqual(load_model_ir(StringIO(first.getvalue())), EXPECTED_MODEL)

    def test_loader_normalizes_module_order(self) -> None:
        document = json.loads(EXPECTED_JSON)
        document["modules"].reverse()
        model = load_model_ir(StringIO(json.dumps(document)))
        self.assertEqual(
            tuple(module.name for module in model.modules),
            (("systems",), ("systems", "computer"), ("systems", "human")),
        )

    def test_invalid_documents_are_rejected(self) -> None:
        wrong_version = json.loads(EXPECTED_JSON)
        wrong_version["schema_version"] = 2
        unknown_field = json.loads(EXPECTED_JSON)
        unknown_field["extra"] = 0
        duplicate_module = json.loads(EXPECTED_JSON)
        duplicate_module["modules"].append(duplicate_module["modules"][0])
        duplicate_declaration = json.loads(EXPECTED_JSON)
        duplicate_declaration["modules"][1]["types"].append(
            duplicate_declaration["modules"][1]["types"][0]
        )
        unknown_signal_target = json.loads(EXPECTED_JSON)
        unknown_signal_target["modules"][2]["externals"][0]["signals"][0]["target"] = ["missing", "Object"]
        invalid_documents = [
            "{",
            json.dumps(wrong_version),
            json.dumps(unknown_field),
            json.dumps(duplicate_module),
            json.dumps(duplicate_declaration),
            json.dumps(unknown_signal_target),
            EXPECTED_JSON.replace('"schema_version": 3', '"schema_version": true'),
            EXPECTED_JSON.replace('"modules": [', '"modules": "bad", "discard": ['),
            '{"schema_version":3,"schema_version":3}',
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ModelIRValidationError):
                    load_model_ir(StringIO(document))

    def test_in_memory_ir_is_strict_and_sorted(self) -> None:
        model = ModelIR(
            schema_version=3,
            entry=EXPECTED_MODEL.entry,
            modules=tuple(reversed(EXPECTED_MODEL.modules)),
        )
        self.assertEqual(model.modules[0].name, ("systems",))

        with self.assertRaises(ModelIRValidationError):
            ModelIR(
                schema_version=3,
                entry=EXPECTED_MODEL.entry,
                modules=EXPECTED_MODEL.modules + (EXPECTED_MODEL.modules[0],),
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
