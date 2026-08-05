from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORY = REPOSITORY / "tools" / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from model_ir import load_model_ir
from modelc.build_cache import build_model_cache, tool_fingerprints
from derive import default_derivation_sequence, load_derivation_sequence
from derive.sequence_builder import build_default_sequence


class BuildCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.entry = self.root / "model" / "main.spec"
        self.entry.parent.mkdir()
        self.entry.write_text("spec root;\norigin root.Human;\n", encoding="utf-8")
        self.module = self.root / "model" / "root.spec"
        self.module.write_text(
            "object Computer: T {}\nexternal Human { drives { Computer.Transition::Go; } }\n",
            encoding="utf-8",
        )
        self.cache = self.root / "tools" / "build" / "modelc"

    def test_hit_does_not_rewrite_and_source_change_rebuilds(self) -> None:
        self.assertFalse(build_model_cache(self.entry, self.cache, self.root))
        ir = self.cache / "model.ir.json"
        manifest = self.cache / "manifest.json"
        mtimes = (ir.stat().st_mtime_ns, manifest.stat().st_mtime_ns)
        self.assertTrue(build_model_cache(self.entry, self.cache, self.root))
        self.assertEqual(mtimes, (ir.stat().st_mtime_ns, manifest.stat().st_mtime_ns))

        self.module.write_text(
            "type Marker;\nobject Computer: T {}\nexternal Human { drives { Computer.Transition::Go; } }\n",
            encoding="utf-8",
        )
        self.assertFalse(build_model_cache(self.entry, self.cache, self.root))
        with ir.open(encoding="utf-8") as stream:
            model = load_model_ir(stream)
        self.assertEqual(model.modules[0].types[0].name, ("root", "Marker"))

    def test_corruption_and_fingerprint_or_schema_changes_rebuild(self) -> None:
        self.assertFalse(build_model_cache(self.entry, self.cache, self.root))
        ir = self.cache / "model.ir.json"
        ir.write_text("{", encoding="utf-8")
        self.assertFalse(build_model_cache(self.entry, self.cache, self.root))
        with ir.open(encoding="utf-8") as stream:
            load_model_ir(stream)

        changed = dict(tool_fingerprints())
        changed["grammar"] = "0" * 64
        with patch("modelc.build_cache.tool_fingerprints", return_value=changed):
            self.assertFalse(build_model_cache(self.entry, self.cache, self.root))
            self.assertTrue(build_model_cache(self.entry, self.cache, self.root))

        manifest_path = self.cache / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["ir_schema_version"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertFalse(build_model_cache(self.entry, self.cache, self.root))

    def test_default_sequence_is_generated_under_tools_build(self) -> None:
        build_model_cache(self.entry, self.cache, self.root)
        sequence_path = self.root / "tools" / "build" / "derive" / "main.sequence.json"
        self.assertFalse(
            build_default_sequence(self.cache / "model.ir.json", sequence_path)
        )
        modified = sequence_path.stat().st_mtime_ns
        self.assertTrue(
            build_default_sequence(self.cache / "model.ir.json", sequence_path)
        )
        self.assertEqual(sequence_path.stat().st_mtime_ns, modified)
        with (self.cache / "model.ir.json").open(encoding="utf-8") as stream:
            model = load_model_ir(stream)
        with sequence_path.open(encoding="utf-8") as stream:
            selected = load_derivation_sequence(stream)
        self.assertEqual(selected, default_derivation_sequence(model))


if __name__ == "__main__":
    unittest.main()
