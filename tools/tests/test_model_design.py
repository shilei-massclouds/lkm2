from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from modelc import compile_spec  # noqa: E402
from modelc.design_lint import find_closed_self_validations  # noqa: E402


class ModelDesignLintTests(unittest.TestCase):
    def test_production_model_has_no_closed_self_validation(self) -> None:
        model = compile_spec(REPOSITORY / "model" / "main.spec")
        self.assertEqual(find_closed_self_validations(model), ())

    def test_sbi_console_without_capability_backing_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_root = Path(directory) / "model"
            shutil.copytree(REPOSITORY / "model", model_root)
            path = model_root / "objects" / "early_console.spec"
            source = path.read_text(encoding="utf-8")
            depends_on = """                depends_on {
                    SbiCapability.state == State::Online;
                    sbi_dbcn_available(SbiCapability) ||
                        sbi_v01_console_available(SbiCapability);
                }

"""
            backing = """            !sbi_console_uses_dbcn(self) ||
                sbi_dbcn_available(SbiCapability);
            !sbi_console_uses_v01(self) ||
                (sbi_v01_console_available(SbiCapability) &&
                 !sbi_dbcn_available(SbiCapability));
"""
            self.assertIn(depends_on, source)
            self.assertIn(backing, source)
            path.write_text(
                source.replace(depends_on, "", 1).replace(backing, "", 1),
                encoding="utf-8",
            )
            model = compile_spec(model_root / "main.spec")

        findings = find_closed_self_validations(model)
        sbi_finding = next(
            item for item in findings if item.object[-1] == "SbiConsole"
        )
        self.assertEqual(sbi_finding.signal, ("Transition", "Enable"))
        self.assertEqual(sbi_finding.target_state, ("State", "Online"))
        self.assertEqual(
            sbi_finding.facts,
            (("predicate", "sbi_console_uses_dbcn"),),
        )


if __name__ == "__main__":
    unittest.main()
