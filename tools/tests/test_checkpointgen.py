from __future__ import annotations

from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools" / "src"))

from checkpointgen import (  # noqa: E402
    CheckpointGenerationError,
    build_checkpoints,
    load_mapping,
    render_manifest,
    render_rust,
)
from checkpointgen.generator import MappingCheckpoint  # noqa: E402
from checkpointgen.runner import CheckpointRunError, parse_record, validate_records  # noqa: E402
from checkpointgen.sibling import validate_sibling  # noqa: E402
from model_ir import load_model_ir  # noqa: E402


class CheckpointGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        model_path = REPOSITORY / "tools" / "build" / "modelc" / "model.ir.json"
        if not model_path.exists():
            cls.skipTest(cls, "tools model cache is missing; run make -C tools build")
        with model_path.open(encoding="utf-8") as stream:
            cls.model = load_model_ir(stream)
        with (REPOSITORY / "tools" / "checkpoints" / "vm.json").open(
            encoding="utf-8"
        ) as stream:
            cls.mapping = load_mapping(stream)

    def test_vm_lifecycle_extracts_exactly_the_frozen_28(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        self.assertEqual(len(checkpoints), 28)
        self.assertEqual(
            tuple(item.canonical_id for item in checkpoints),
            tuple(item.canonical_id for item in self.mapping.checkpoints),
        )

    def test_vm_checkpoint_scope_is_frozen(self) -> None:
        self.assertEqual(self.mapping.module, ("objects", "vm"))
        self.assertEqual(self.mapping.root_object, "Vm")
        self.assertEqual(
            self.mapping.begins_after,
            "arch_head_stack_established",
        )

    def test_vm_ready_and_early_mapping_checkpoint_ids_remain_present(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        ids = {item.canonical_id for item in checkpoints}
        self.assertTrue(
            {
                "Vm.Setup.Ensures.KernelMap.Ready",
                "Vm.Setup.Ensures.TrampolinePageTable.Ready",
                "Vm.Setup.Ensures.EarlyPageTable.Ready",
                "Vm.Ready.Invariant.KernelMap.Ready",
                "Vm.Ready.Invariant.TrampolinePageTable.Ready",
                "Vm.Ready.Invariant.EarlyPageTable.Ready",
                "EarlyPageTable.Ready.Invariant.early_kernel_image_mapping_established",
                "EarlyPageTable.Ready.Invariant.early_dtb_four_mib_mapping_established",
            }.issubset(ids)
        )

    def test_arch_head_objects_do_not_enter_vm_checkpoint_ids(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        id_parts = {
            part
            for item in checkpoints
            for part in item.canonical_id.split(".")
        }
        self.assertTrue({"ArchHead", "KernelImage", "BootStack"}.isdisjoint(id_parts))

    def test_inherited_invariants_are_concrete_object_declarations(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        ids = {item.canonical_id: item.hash16 for item in checkpoints}
        self.assertIn("KernelMap.Ready.Invariant.kernel_map_established", ids)
        self.assertIn("Vm.Ready.Invariant.kernel_map_established", ids)
        self.assertNotEqual(
            ids["KernelMap.Ready.Invariant.kernel_map_established"],
            ids["Vm.Ready.Invariant.kernel_map_established"],
        )

    def test_reordering_does_not_rename_checkpoints(self) -> None:
        forward = build_checkpoints(self.model, self.mapping)
        reordered = replace(self.mapping, checkpoints=tuple(reversed(self.mapping.checkpoints)))
        reverse = build_checkpoints(self.model, reordered)
        self.assertEqual(
            {item.canonical_id: (item.hash16, item.symbol) for item in forward},
            {item.canonical_id: (item.hash16, item.symbol) for item in reverse},
        )

    def test_truncated_hash_collision_is_fatal(self) -> None:
        with self.assertRaisesRegex(CheckpointGenerationError, "hash collision"):
            build_checkpoints(
                self.model,
                self.mapping,
                hash_function=lambda _value: "0" * 16,
            )

    def test_missing_mapping_is_fatal(self) -> None:
        mapping = replace(self.mapping, checkpoints=self.mapping.checkpoints[:-1])
        with self.assertRaisesRegex(CheckpointGenerationError, "no implementation mapping"):
            build_checkpoints(self.model, mapping)

    def test_extra_mapping_is_fatal(self) -> None:
        extra = MappingCheckpoint("Vm.Ready.Invariant.not_in_model", "setup_complete", ())
        mapping = replace(self.mapping, checkpoints=(*self.mapping.checkpoints, extra))
        with self.assertRaisesRegex(CheckpointGenerationError, "no reachable checkpoint"):
            build_checkpoints(self.model, mapping)

    def test_scope_must_exclude_pre_stack_region(self) -> None:
        raw = json.loads(
            (REPOSITORY / "tools" / "checkpoints" / "vm.json").read_text(
                encoding="utf-8"
            )
        )
        raw["scope"]["begins_after"] = "reset_entry"
        with self.assertRaisesRegex(CheckpointGenerationError, "pre-stack"):
            load_mapping(StringIO(json.dumps(raw)))

    def test_wrong_sibling_baseline_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "checkpointgen.sibling._git",
                side_effect=(self.mapping.sibling_branch, "", "0" * 40),
            ):
                with self.assertRaisesRegex(CheckpointGenerationError, "sibling HEAD"):
                    validate_sibling(Path(directory), self.mapping)

    def test_sibling_mapping_freezes_patch_and_integrated_revisions(self) -> None:
        self.assertEqual(
            self.mapping.sibling_patch_base.commit,
            "d0fef99b651d141dd6ffbbddeb8b729b2f8faaff",
        )
        self.assertEqual(
            self.mapping.sibling_integrated.commit,
            "2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f",
        )
        self.assertEqual(len(self.mapping.sibling_patch_base.files), 2)
        self.assertEqual(len(self.mapping.sibling_integrated.files), 4)

    def test_rust_and_manifest_generation_are_repeatable(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        self.assertEqual(render_manifest(checkpoints), render_manifest(checkpoints))
        self.assertEqual(
            render_rust(checkpoints, self.mapping, "debugcon"),
            render_rust(checkpoints, self.mapping, "debugcon"),
        )

    def test_unknown_handler_is_fatal(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        with self.assertRaisesRegex(CheckpointGenerationError, "unknown"):
            render_rust(checkpoints, self.mapping, "buffered")

    def test_missing_c_abi_handler_is_a_link_error(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        symbol = checkpoints[0].symbol
        source = f'''unsafe extern "C" {{
    #[link_name = "{symbol}"]
    fn checkpoint(value: u64);
}}
fn main() {{ unsafe {{ checkpoint(0) }} }}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.rs"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [
                    "rustc",
                    "--edition=2024",
                    str(path),
                    "-o",
                    str(Path(directory) / "fixture"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(symbol, result.stderr)

    def test_missing_c_handler_is_a_riscv_link_error(self) -> None:
        compiler = shutil.which("riscv64-linux-gnu-gcc")
        if compiler is None:
            self.skipTest("riscv64-linux-gnu-gcc is unavailable")
        checkpoints = build_checkpoints(self.model, self.mapping)
        symbol = checkpoints[0].symbol
        source = f'''typedef unsigned long uint64_t;
extern void {symbol}(uint64_t value);
void _start(void) {{ {symbol}(0); }}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "missing.c"
            object_path = root / "missing.o"
            path.write_text(source, encoding="utf-8")
            compile_result = subprocess.run(
                [compiler, "-ffreestanding", "-c", str(path), "-o", str(object_path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            link_result = subprocess.run(
                [compiler, "-nostdlib", str(object_path), "-o", str(root / "fixture")],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertNotEqual(link_result.returncode, 0)
        self.assertIn(symbol, link_result.stderr)

    def test_record_parser_preserves_parameter_order(self) -> None:
        record = parse_record(
            "LKMCP1 id=X hash=0123456789abcdef b=0x0000000000000002 a=0x0000000000000001"
        )
        self.assertEqual(record[2], (("b", 2), ("a", 1)))

    def test_self_validation_rejects_missing_record(self) -> None:
        checkpoints = build_checkpoints(self.model, self.mapping)
        manifest = tuple(json.loads(render_manifest(checkpoints))["checkpoints"])
        with self.assertRaisesRegex(CheckpointRunError, "record count"):
            validate_records((), manifest, "sv57")


if __name__ == "__main__":
    unittest.main()
