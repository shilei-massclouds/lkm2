from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from _derive_harness import with_cpu_line
from derive import (
    DerivationSequence,
    default_derivation_sequence,
    derive,
    dump_derivation_result,
    load_derivation_result,
    render_derivation_result,
)
from modelc import CompilationError, compile_spec
from model_ir import dump_model_ir, load_model_ir
from model_ir import ModelIRValidationError
from derive import DerivationValidationError


FIXTURE = Path(__file__).parent / "fixtures" / "early_console"


class FiniteRelationTests(unittest.TestCase):
    def _compile(self, body: str | None = None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "main.spec").write_text(
            "spec root;\norigin root.Test;\n", encoding="utf-8"
        )
        source = (
            (FIXTURE / "root.spec").read_text(encoding="utf-8")
            if body is None
            else body
        )
        (root / "root.spec").write_text(with_cpu_line(source), encoding="utf-8")
        return compile_spec(root / "main.spec")

    @staticmethod
    def _state(path, suffix: str):
        return next(item.state for item in path.final_state if item.object[-1] == suffix)

    @staticmethod
    def _field(path, owner: str, field: str):
        return next(
            (item for item in path.final_values if item.object[-1] == owner and item.field == field),
            None,
        )

    def test_early_console_success_ir_and_result_round_trip(self) -> None:
        model = self._compile()
        self.assertEqual(model.schema_version, 14)
        ir = StringIO()
        dump_model_ir(model, ir)
        self.assertEqual(load_model_ir(StringIO(ir.getvalue())), model)
        old_ir = json.loads(ir.getvalue())
        old_ir["schema_version"] = 12
        with self.assertRaises(ModelIRValidationError):
            load_model_ir(StringIO(json.dumps(old_ir)))

        result = derive(model, default_derivation_sequence(model))
        path = result.paths[0]
        self.assertEqual((result.schema_version, result.status), (12, "passed"))
        self.assertEqual(self._state(path, "EarlyConsole"), ("State", "Online"))
        self.assertEqual(
            self._field(path, "EarlyConsole", "backend").values[0][-1],
            "SbiConsole",
        )
        enable = path.units[1]
        self.assertEqual(
            tuple((item.name, item.value.value[-1] if item.value.kind == "object" else item.value.value)
                  for item in enable.bindings),
            (("value", "sbi"), ("backend", "SbiConsole")),
        )
        self.assertEqual(len(path.tuples), 2)
        human = StringIO()
        render_derivation_result(result, human)
        self.assertIn("binds value = 'sbi'", human.getvalue())
        self.assertIn("binds backend = root::SbiConsole", human.getvalue())
        output = StringIO()
        dump_derivation_result(result, output)
        self.assertEqual(load_derivation_result(StringIO(output.getvalue())), result)
        old_result = json.loads(output.getvalue())
        old_result["schema_version"] = 11
        with self.assertRaises(DerivationValidationError):
            load_derivation_result(StringIO(json.dumps(old_result)))

        dependent = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        binding_block = '''                binds {
                    value := BootCommandLine.unique_value("earlycon");
                    backend := EarlyConTable.lookup(value);
                }
                depends_on {
                    backend.state == State::Online;
                }'''
        dependent_first = '''                depends_on {
                    backend.state == State::Online;
                }
                binds {
                    value := BootCommandLine.unique_value("earlycon");
                    backend := EarlyConTable.lookup(value);
                }'''
        reordered_model = self._compile(dependent.replace(binding_block, dependent_first))
        self.assertEqual(
            derive(reordered_model, default_derivation_sequence(reordered_model)).status,
            "passed",
        )

    def test_relation_queries_and_missing_or_ambiguous_witnesses(self) -> None:
        source = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        queried = source.replace(
            "BootCommandLine.state == State::Ready;",
            'BootCommandLine.state == State::Ready;\n'
            '                    BootCommandLine.contains("earlycon", "sbi");\n'
            '                    BootCommandLine.has_key("earlycon");\n'
            '                    EarlyConTable.contains("sbi", SbiConsole);\n'
            '                    EarlyConTable.has_key("sbi");',
        )
        self.assertEqual(
            derive(self._compile(queried), default_derivation_sequence(self._compile(queried))).status,
            "passed",
        )

        false_query = queried.replace(
            'BootCommandLine.contains("earlycon", "sbi")',
            'BootCommandLine.contains("earlycon", "uart")',
            1,
        )
        false_model = self._compile(false_query)
        false_path = derive(false_model, default_derivation_sequence(false_model)).paths[0]
        self.assertEqual(false_path.status, "depends_on_failed")
        self.assertEqual(false_path.units[1].bindings, ())

        missing = source.replace(
            '                    BootCommandLine.contains("earlycon", "sbi");\n', ""
        )
        missing_model = self._compile(missing)
        missing_path = derive(missing_model, default_derivation_sequence(missing_model)).paths[0]
        self.assertEqual(missing_path.status, "relation_key_missing")
        self.assertEqual(missing_path.units[1].bindings[0].failure_code, "relation_key_missing")
        missing_human = StringIO()
        render_derivation_result(
            derive(missing_model, default_derivation_sequence(missing_model)),
            missing_human,
        )
        self.assertIn("relation_key_missing", missing_human.getvalue())
        self.assertIn("key 'earlycon'", missing_human.getvalue())

        ambiguous = source.replace(
            'BootCommandLine.contains("earlycon", "sbi");',
            'BootCommandLine.contains("earlycon", "sbi");\n'
            '                    BootCommandLine.contains("earlycon", "uart");',
        )
        ambiguous_model = self._compile(ambiguous)
        ambiguous_path = derive(
            ambiguous_model, default_derivation_sequence(ambiguous_model)
        ).paths[0]
        self.assertEqual(ambiguous_path.status, "relation_key_ambiguous")
        self.assertEqual(
            tuple(item.value for item in ambiguous_path.units[1].bindings[0].candidates),
            ("sbi", "uart"),
        )

    def test_map_missing_backend_and_backend_state_failure_are_atomic(self) -> None:
        source = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        unregistered = source.replace(
            'BootCommandLine.contains("earlycon", "sbi")',
            'BootCommandLine.contains("earlycon", "uart")',
        )
        model = self._compile(unregistered)
        path = derive(model, default_derivation_sequence(model)).paths[0]
        self.assertEqual(path.status, "map_key_missing")
        self.assertEqual(tuple(item.status for item in path.units[1].bindings), ("passed", "failed"))
        self.assertEqual(self._state(path, "EarlyConsole"), ("State", "Ready"))
        self.assertIsNone(self._field(path, "EarlyConsole", "backend"))
        self.assertFalse(any(fact.predicate[-1] == "early_console_bound_from_registry" for fact in path.facts))

        offline = source.replace(
            "object SbiConsole: EarlyConBackendType {\n    initial_state: State::Online;",
            "object SbiConsole: EarlyConBackendType {\n    initial_state: State::Offline;",
        )
        offline_model = self._compile(offline)
        offline_path = derive(
            offline_model, default_derivation_sequence(offline_model)
        ).paths[0]
        self.assertEqual(offline_path.status, "depends_on_failed")
        self.assertEqual(tuple(item.status for item in offline_path.units[1].bindings), ("passed", "passed"))
        self.assertEqual(self._state(offline_path, "EarlyConsole"), ("State", "Ready"))
        self.assertIsNone(self._field(offline_path, "EarlyConsole", "backend"))

    def test_map_idempotence_conflict_and_pre_drive_atomicity(self) -> None:
        source = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        duplicate = source.replace(
            "EarlyConTable.contains(\"sbi\", SbiConsole);",
            "EarlyConTable.contains(\"sbi\", SbiConsole);\n"
            "                    EarlyConTable.contains(\"sbi\", SbiConsole);",
        )
        duplicate_model = self._compile(duplicate)
        duplicate_path = derive(
            duplicate_model, default_derivation_sequence(duplicate_model)
        ).paths[0]
        self.assertEqual(duplicate_path.status, "passed")
        self.assertEqual(len(duplicate_path.tuples), 2)

        conflict = source.replace(
            "object EarlyConsole: EarlyConsoleType {",
            "object OtherConsole: EarlyConBackendType { initial_state: State::Online; }\n\n"
            "object Probe: ProbeType { initial_state: State::Ready; }\n"
            "type ProbeType {\n"
            "  initial_state: State::Ready;\n"
            "  state State::Ready { transitions { on Transition::Touch -> State::Touched {} } }\n"
            "  state State::Touched {}\n"
            "}\n\nobject EarlyConsole: EarlyConsoleType {",
        ).replace(
            "EarlyConTable.contains(\"sbi\", SbiConsole);",
            "EarlyConTable.contains(\"sbi\", SbiConsole);\n"
            "                    EarlyConTable.contains(\"sbi\", OtherConsole);",
        ).replace(
            "            override on Action::Install {\n                establishes",
            "            override on Action::Install {\n"
            "                drives Probe.Transition::Touch;\n"
            "                establishes",
        )
        conflict_model = self._compile(conflict)
        conflict_path = derive(
            conflict_model, default_derivation_sequence(conflict_model)
        ).paths[0]
        self.assertEqual(conflict_path.status, "map_key_conflict")
        self.assertEqual(conflict_path.units[0].drives, ())
        self.assertEqual(self._state(conflict_path, "Probe"), ("State", "Ready"))
        self.assertEqual(conflict_path.tuples, ())
        self.assertTrue(
            any(item.status == "failed" for item in conflict_path.units[0].relation_effects)
        )

        snapshot_conflict = source.replace(
            "object EarlyConsole: EarlyConsoleType {",
            "object OtherConsole: EarlyConBackendType { initial_state: State::Online; }\n\n"
            "object Probe: ProbeType { initial_state: State::Ready; }\n"
            "type ProbeType {\n"
            "  initial_state: State::Ready;\n"
            "  state State::Ready { transitions { on Transition::Touch -> State::Touched {} } }\n"
            "  state State::Touched {}\n"
            "}\n\nobject EarlyConsole: EarlyConsoleType {",
        ).replace(
            "actions { on Action::Install; }",
            "actions { on Action::Install; on Action::Conflict; }",
        ).replace(
            "            override on Action::Install {",
            "            override on Action::Conflict {\n"
            "                establishes { EarlyConTable.contains(\"sbi\", OtherConsole); }\n"
            "                drives Probe.Transition::Touch;\n"
            "            }\n"
            "            override on Action::Install {",
        ).replace(
            "        Setup.Action::Install;\n        EarlyConsole.Transition::Enable;",
            "        Setup.Action::Install;\n"
            "        Setup.Action::Conflict;\n"
            "        EarlyConsole.Transition::Enable;",
        )
        snapshot_model = self._compile(snapshot_conflict)
        snapshot_path = derive(
            snapshot_model, default_derivation_sequence(snapshot_model)
        ).paths[0]
        self.assertEqual(snapshot_path.status, "map_key_conflict")
        self.assertEqual(tuple(unit.status for unit in snapshot_path.units), ("passed", "map_key_conflict"))
        self.assertEqual(snapshot_path.units[1].drives, ())
        self.assertEqual(self._state(snapshot_path, "Probe"), ("State", "Ready"))
        self.assertEqual(len(snapshot_path.tuples), 2)

    def test_tuple_establishment_order_is_canonical(self) -> None:
        source = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        first = 'BootCommandLine.contains("earlycon", "sbi");'
        second = 'EarlyConTable.contains("sbi", SbiConsole);'
        swapped = source.replace(first, "__SWAP__").replace(second, first).replace("__SWAP__", second)
        model_a = self._compile(source)
        model_b = self._compile(swapped)
        result_a = derive(model_a, default_derivation_sequence(model_a))
        result_b = derive(model_b, default_derivation_sequence(model_b))
        out_a, out_b = StringIO(), StringIO()
        dump_derivation_result(result_a, out_a)
        dump_derivation_result(result_b, out_b)
        self.assertEqual(out_a.getvalue(), out_b.getvalue())

    def test_binding_lookup_is_not_repeated_after_continuation_yield(self) -> None:
        model = self._compile(
            r'''
object Values: Relation<String, String> {
  initial_state: State::Ready;
  state State::Ready {}
}
type SetupType { initial_state: State::Ready; state State::Ready { actions { on Action::Install; } } }
object Setup: SetupType { state State::Ready { actions { override on Action::Install {
  establishes { Values.contains("key", "one"); }
} } } }
object Mutator: SetupType { state State::Ready { actions { override on Action::Install {
  establishes { Values.contains("key", "two"); }
} } } }
type SinkType { initial_state: State::Ready; state State::Ready { actions { on Action::Accept(value: String) { drives {} } } } }
object Sink: SinkType {}
type Flow { continuation: true; initial_state: State::Online;
  state State::Online { actions { on Action::Enter; } }
}
object Boot: Flow { state State::Online { actions { override on Action::Enter {
  binds { value := Values.unique_value("key"); }
  yields Mutator.Action::Install;
  drives Sink.Action::Accept(value);
} } } }
external Test {
  drives Setup.Action::Install;
  resumes Boot.Action::Enter;
  resumes Boot.Action::Enter;
}
'''
        )
        sequence = default_derivation_sequence(model)
        suspended = derive(model, DerivationSequence(3, sequence.events[:2]))
        self.assertEqual(suspended.status, "yielded")
        frame_binding = suspended.continuations[0].frames[0].bindings[0]
        self.assertEqual((frame_binding.name, frame_binding.term.value), ("value", "one"))
        complete = derive(model, sequence)
        self.assertEqual(complete.status, "passed")
        accepted = complete.paths[0].units[2].drives[0]
        self.assertEqual(accepted.event.arguments[0].value, "one")
        self.assertEqual(len(complete.paths[0].tuples), 2)

    def test_static_container_and_binding_errors(self) -> None:
        source = (FIXTURE / "root.spec").read_text(encoding="utf-8")
        invalid_sources = (
            source.replace("Relation<String, String>", "Relation<String>"),
            source.replace(
                'BootCommandLine.contains("earlycon", "sbi")',
                'BootCommandLine.contains(SbiConsole, "sbi")',
            ),
            source.replace("EarlyConTable.lookup(value)", "EarlyConTable.unique_value(value)"),
            source.replace(
                'value := BootCommandLine.unique_value("earlycon");\n'
                '                    backend := EarlyConTable.lookup(value);',
                'backend := EarlyConTable.lookup(value);\n'
                '                    value := BootCommandLine.unique_value("earlycon");',
            ),
            source.replace(
                'backend := EarlyConTable.lookup(value);',
                'backend := EarlyConTable.lookup(missing);',
            ),
            source.replace(
                'backend := EarlyConTable.lookup(value);',
                'value := BootCommandLine.unique_value("earlycon");',
            ),
            source.replace(
                "updates {\n                    self.backend = backend;",
                "updates {\n                    backend = SbiConsole;",
            ),
            source.replace(
                "on Transition::Enable -> State::Online",
                "on Transition::Enable(value: String) -> State::Online",
            ).replace(
                "EarlyConsole.Transition::Enable;",
                'EarlyConsole.Transition::Enable("unused");',
            ),
            source.replace(
                "type EarlyConsoleType {",
                "type EarlyConsoleType { mutable note: String;",
            ),
        )
        for invalid in invalid_sources:
            with self.subTest(source=invalid_sources.index(invalid)):
                with self.assertRaises(CompilationError):
                    self._compile(invalid)


if __name__ == "__main__":
    unittest.main()
