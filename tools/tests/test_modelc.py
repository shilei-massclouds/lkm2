from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from model_ir import ModelEntry, ModelIR, dump_model_ir, load_model_ir
from modelc import CompilationError, SourceSpan, compile_spec, parse_spec
from modelc.cli import main


EXPECTED_MODEL = ModelIR(
    schema_version=1,
    entry=ModelEntry(origin=("systems", "Human"), spec=("systems",)),
)

EXPECTED_JSON = """{
  "entry": {
    "origin": [
      "systems",
      "Human"
    ],
    "spec": [
      "systems"
    ]
  },
  "schema_version": 1
}
"""


class ParserAndCompilerTests(unittest.TestCase):
    def test_real_entry_ast_and_ir(self) -> None:
        path = REPOSITORY / "model" / "main.spec"
        source = path.read_text(encoding="utf-8")
        document = parse_spec(source, path)

        self.assertEqual(document.spec.name.parts, ("systems",))
        self.assertEqual(document.origin.name.parts, ("systems", "Human"))
        self.assertEqual(document.spec.name.span, SourceSpan(5, 6, 5, 13))
        self.assertEqual(document.spec.span, SourceSpan(5, 1, 5, 14))
        self.assertEqual(document.origin.name.span, SourceSpan(7, 8, 7, 21))
        self.assertEqual(document.origin.span, SourceSpan(7, 1, 7, 22))
        self.assertEqual(compile_spec(path), EXPECTED_MODEL)

    def test_comments_whitespace_and_long_qualified_names(self) -> None:
        document = parse_spec(
            """
            // entry namespace
            spec alpha.beta_gamma; /* between declarations */
            origin alpha.beta_gamma.Person2; // end
            """
        )
        self.assertEqual(document.spec.name.parts, ("alpha", "beta_gamma"))
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


class ModelIRJSONTests(unittest.TestCase):
    def test_canonical_output_is_repeatable_and_round_trips(self) -> None:
        first = StringIO()
        second = StringIO()
        dump_model_ir(EXPECTED_MODEL, first)
        dump_model_ir(EXPECTED_MODEL, second)

        self.assertEqual(first.getvalue(), EXPECTED_JSON)
        self.assertEqual(first.getvalue().encode(), second.getvalue().encode())
        self.assertEqual(load_model_ir(StringIO(first.getvalue())), EXPECTED_MODEL)

    def test_invalid_documents_are_rejected(self) -> None:
        invalid_documents = [
            "{",
            '{"schema_version": 2, "entry": {"origin": ["a"], "spec": ["b"]}}',
            '{"schema_version": 1, "entry": {"origin": ["a"], "spec": ["b"]}, "extra": 0}',
            '{"schema_version": 1, "entry": {"origin": [], "spec": ["b"]}}',
            '{"schema_version": 1, "entry": {"origin": "a", "spec": ["b"]}}',
            '{"schema_version": 1, "entry": {"origin": ["not-valid"], "spec": ["b"]}}',
            '{"schema_version": true, "entry": {"origin": ["a"], "spec": ["b"]}}',
            '{"schema_version": 1, "entry": {"origin": ["a"], "spec": [1]}}',
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    load_model_ir(StringIO(document))


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
        cases = [
            ("spec systems\norigin systems.Human;", 2, 1),
            ("origin systems.Human;\nspec systems;", 1, 1),
            ("spec bad-name;\norigin systems.Human;", 1, 9),
            ("spec systems;\n/* unterminated", 2, 1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "bad.spec"
            for source, line, column in cases:
                with self.subTest(source=source):
                    input_path.write_text(source, encoding="utf-8")
                    stdout = StringIO()
                    stderr = StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        status = main([str(input_path)])

                    self.assertEqual(status, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertTrue(
                        stderr.getvalue().startswith(
                            f"{input_path}:{line}:{column}: error: "
                        ),
                        stderr.getvalue(),
                    )

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

    def test_argument_errors_exit_two(self) -> None:
        wrapper = REPOSITORY / "tools" / "bin" / "modelc"
        result = subprocess.run(
            [str(wrapper)],
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
