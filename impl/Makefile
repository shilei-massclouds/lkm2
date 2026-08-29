.DEFAULT_GOAL := build
.NOTPARALLEL:

include build.mak
include qemu.mak

.PHONY: all build checkpoints checkpoint-sibling-patch checkpoint-sibling-patch-swapper checkpoint-sibling-patch-memblock checkpoint-sibling-patch-swapper-content checkpoint-sibling-apply difftest checkpoint-diff-sv57 test test-dtb-blob test-memblock test-early-console test-sbi test-printk test-smoke test-empty-elision test-checkpoints fmt clippy run clean help FORCE

all: build

build: $(IMAGE)

checkpoints: $(CHECKPOINTS_RS) $(CHECKPOINT_MANIFEST) $(MEMBLOCK_CHECKPOINTS_RS) $(MEMBLOCK_CHECKPOINT_MANIFEST) $(SWAPPER_CHECKPOINTS_RS) $(SWAPPER_CHECKPOINT_MANIFEST) $(SWAPPER_CONTENT_CHECKPOINTS_RS) $(SWAPPER_CONTENT_CHECKPOINT_MANIFEST)

checkpoint-sibling-patch: $(MODEL_IR) $(CHECKPOINT_MAPPING) | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(CHECKPOINT_MAPPING) \
		--repository .. --sibling-patch-output $(BUILD_DIR)/linux-checkpoints.patch
	@git -C ../../linux-6.12 apply --check ../lkm2/impl/$(BUILD_DIR)/linux-checkpoints.patch 2>/dev/null || \
		git -C ../../linux-6.12 apply --reverse --check ../lkm2/impl/$(BUILD_DIR)/linux-checkpoints.patch

checkpoint-sibling-patch-swapper: $(MODEL_IR) $(SWAPPER_CHECKPOINT_MAPPING) | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(SWAPPER_CHECKPOINT_MAPPING) \
		--repository .. --sibling-patch-output $(BUILD_DIR)/linux-swapper-checkpoints.patch
	@git -C ../../linux-6.12 apply --check ../lkm2/impl/$(BUILD_DIR)/linux-swapper-checkpoints.patch 2>/dev/null || \
		git -C ../../linux-6.12 apply --reverse --check ../lkm2/impl/$(BUILD_DIR)/linux-swapper-checkpoints.patch

checkpoint-sibling-patch-memblock: $(MODEL_IR) $(MEMBLOCK_CHECKPOINT_MAPPING) | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(MEMBLOCK_CHECKPOINT_MAPPING) \
		--repository .. --sibling-patch-output $(BUILD_DIR)/linux-memblock-checkpoints.patch
	@git -C ../../linux-6.12 apply --check ../lkm2/impl/$(BUILD_DIR)/linux-memblock-checkpoints.patch 2>/dev/null || \
		git -C ../../linux-6.12 apply --reverse --check ../lkm2/impl/$(BUILD_DIR)/linux-memblock-checkpoints.patch

checkpoint-sibling-patch-swapper-content: $(SWAPPER_CONTENT_CHECKPOINT_MAPPING) | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --mapping $(SWAPPER_CONTENT_CHECKPOINT_MAPPING) \
		--repository .. --sibling-patch-output $(BUILD_DIR)/linux-swapper-content-checkpoints.patch
	@git -C ../../linux-6.12 apply --check ../lkm2/impl/$(BUILD_DIR)/linux-swapper-content-checkpoints.patch 2>/dev/null || \
		git -C ../../linux-6.12 apply --reverse --check ../lkm2/impl/$(BUILD_DIR)/linux-swapper-content-checkpoints.patch

checkpoint-sibling-apply:
	../tools/bin/checkpoint-sibling-apply --sibling ../../linux-6.12 \
		--patch $(BUILD_DIR)/linux-checkpoints.patch --branch dev \
		--patch-base-commit d0fef99b651d141dd6ffbbddeb8b729b2f8faaff \
		--integrated-commit 2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f

difftest:
	../tools/bin/checkpoint-runner --repository .. --sibling ../../linux-6.12 \
		--diff-sv57 --build-sibling

checkpoint-diff-sv57: difftest

test: build fmt clippy test-dtb-blob test-memblock test-early-console test-sbi test-printk test-smoke test-empty-elision test-checkpoints

test-dtb-blob: $(DTB_BLOB_TEST)
	$(DTB_BLOB_TEST)

test-memblock: $(MEMBLOCK_TEST)
	$(MEMBLOCK_TEST)

test-early-console: $(EARLY_CONSOLE_TEST)
	$(EARLY_CONSOLE_TEST)

test-sbi: $(SBI_TEST)
	$(SBI_TEST)

test-printk: $(PRINTK_TEST)
	$(PRINTK_TEST)

test-smoke: build
	./tests/early_console_smoke.sh -- $(QEMU) $(QEMU_FLAGS)

test-empty-elision:
	$(MAKE) --no-print-directory CHECKPOINT_HANDLER=empty build
	@! $(NM) $(ELF) | grep -E 'lkm_checkpoint_' || { echo "error: empty checkpoint symbols remain" >&2; exit 1; }
	@! $(OBJDUMP) -d $(ELF) | grep -E 'lkm_checkpoint_' || { echo "error: empty checkpoint calls remain" >&2; exit 1; }
	@! strings $(ELF) | grep -E 'LKMCP1|LKMRNG1|LKMPTC1|LKMPTI1|LKMPTE1|lkm2\.ptdiag' || { echo "error: empty checkpoint output remains" >&2; exit 1; }

test-checkpoints:
	../tools/bin/checkpoint-runner --repository .. --self all

fmt:
	$(RUSTFMT) --edition 2024 --check $(RUST_SOURCES)

clippy: $(CHECKPOINTS_RS) $(MEMBLOCK_CHECKPOINTS_RS) $(SWAPPER_CHECKPOINTS_RS) $(SWAPPER_CONTENT_CHECKPOINTS_RS) | $(BUILD_STAMP)
	$(CLIPPY) $(RUSTC_FLAGS) -D warnings -o $(CLIPPY_ELF) main.rs

$(ELF): $(RUST_SOURCES) $(CHECKPOINTS_RS) $(MEMBLOCK_CHECKPOINTS_RS) $(SWAPPER_CHECKPOINTS_RS) $(SWAPPER_CONTENT_CHECKPOINTS_RS) $(LINKER_SCRIPT) | $(BUILD_STAMP)
	$(RUSTC) $(RUSTC_FLAGS) -o $@ main.rs

$(MODEL_IR): $(MODEL_SOURCES)
	$(MAKE) --no-print-directory -C ../tools build

$(CHECKPOINTS_RS) $(CHECKPOINT_MANIFEST) &: FORCE $(MODEL_IR) $(CHECKPOINT_MAPPING) $(CHECKPOINTGEN) $(shell find ../tools/src/checkpointgen -type f -name '*.py') | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(CHECKPOINT_MAPPING) \
		--handler $(CHECKPOINT_HANDLER) --rust-output $(CHECKPOINTS_RS) \
		--manifest-output $(CHECKPOINT_MANIFEST)

$(SWAPPER_CHECKPOINTS_RS) $(SWAPPER_CHECKPOINT_MANIFEST) &: FORCE $(MODEL_IR) $(SWAPPER_CHECKPOINT_MAPPING) $(CHECKPOINTGEN) $(shell find ../tools/src/checkpointgen -type f -name '*.py') | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(SWAPPER_CHECKPOINT_MAPPING) \
		--handler $(CHECKPOINT_HANDLER) --rust-output $(SWAPPER_CHECKPOINTS_RS) \
		--manifest-output $(SWAPPER_CHECKPOINT_MANIFEST)

$(SWAPPER_CONTENT_CHECKPOINTS_RS) $(SWAPPER_CONTENT_CHECKPOINT_MANIFEST) &: FORCE $(SWAPPER_CONTENT_CHECKPOINT_MAPPING) $(CHECKPOINTGEN) $(shell find ../tools/src/checkpointgen -type f -name '*.py') | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --mapping $(SWAPPER_CONTENT_CHECKPOINT_MAPPING) \
		--handler $(CHECKPOINT_HANDLER) --rust-output $(SWAPPER_CONTENT_CHECKPOINTS_RS) \
		--manifest-output $(SWAPPER_CONTENT_CHECKPOINT_MANIFEST)

$(MEMBLOCK_CHECKPOINTS_RS) $(MEMBLOCK_CHECKPOINT_MANIFEST) &: FORCE $(MODEL_IR) $(MEMBLOCK_CHECKPOINT_MAPPING) $(CHECKPOINTGEN) $(shell find ../tools/src/checkpointgen -type f -name '*.py') | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(MEMBLOCK_CHECKPOINT_MAPPING) \
		--handler $(CHECKPOINT_HANDLER) --rust-output $(MEMBLOCK_CHECKPOINTS_RS) \
		--manifest-output $(MEMBLOCK_CHECKPOINT_MANIFEST)

$(IMAGE): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(BUILD_STAMP):
	mkdir -p $(BUILD_DIR)
	touch $@

run: build
	$(QEMU) $(QEMU_FLAGS)

clean:
	rm -rf $(BUILD_DIR)

help:
	@echo "Implementation component targets:"
	@echo "  all    Build the kernel ELF and raw image (default)"
	@echo "  build  Build build/lkm2.elf and build/lkm2.bin"
	@echo "  checkpoints Generate the Model IR checkpoint manifest and Rust code"
	@echo "  checkpoint-sibling-patch Generate and check (but do not apply) the frozen Linux patch"
	@echo "  checkpoint-sibling-patch-swapper Generate and check the incremental M1 Linux patch"
	@echo "  checkpoint-sibling-patch-memblock Generate and check the incremental MemBlock Linux patch"
	@echo "  checkpoint-sibling-patch-swapper-content Generate and check the page-table content Linux patch"
	@echo "  checkpoint-sibling-apply Apply the reviewed patch or recognize its integrated commit"
	@echo "  difftest Build sibling and run the strict Sv57 differential"
	@echo "  checkpoint-diff-sv57 Compatibility alias for difftest"
	@echo "  test   Build and run formatting and Clippy checks"
	@echo "  test-dtb-blob Run host-side DTB mapping and FDT parser unit tests"
	@echo "  test-memblock Run host-side MemBlock memory and reservation tests"
	@echo "  test-early-console Run host-side boot command line and registry unit tests"
	@echo "  test-sbi Run host-side SBI capability and DBCN unit tests"
	@echo "  test-printk Run host-side early Printk buffer and replay unit tests"
	@echo "  test-smoke Confirm the DBCN banner once and cleanly stop QEMU"
	@echo "  test-checkpoints Run Sv57/Sv48/Sv39 lkm2 checkpoint self-tests"
	@echo "  test-empty-elision Verify the optimized empty handler leaves no calls or output"
	@echo "  fmt    Check Rust source formatting"
	@echo "  clippy Check Rust source lints"
	@echo "  run    Run the raw image on QEMU virt/OpenSBI (exit with Ctrl-a x)"
	@echo "  clean  Remove impl/build"
	@echo "  CHECKPOINT_HANDLER=empty|debugcon (default: empty)"
	@echo "  help   Show this help"

FORCE:
