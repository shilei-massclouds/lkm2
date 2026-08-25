.DEFAULT_GOAL := build
.NOTPARALLEL:

include build.mak
include qemu.mak

.PHONY: all build checkpoints checkpoint-sibling-patch checkpoint-sibling-apply difftest checkpoint-diff-sv57 test test-empty-elision test-checkpoints fmt clippy run clean help FORCE

all: build

build: $(IMAGE)

checkpoints: $(CHECKPOINTS_RS) $(CHECKPOINT_MANIFEST)

checkpoint-sibling-patch: $(MODEL_IR) $(CHECKPOINT_MAPPING) | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(CHECKPOINT_MAPPING) \
		--repository .. --sibling-patch-output $(BUILD_DIR)/linux-checkpoints.patch
	@git -C ../../linux-6.12 apply --check ../lkm2/impl/$(BUILD_DIR)/linux-checkpoints.patch 2>/dev/null || \
		git -C ../../linux-6.12 apply --reverse --check ../lkm2/impl/$(BUILD_DIR)/linux-checkpoints.patch

checkpoint-sibling-apply:
	../tools/bin/checkpoint-sibling-apply --sibling ../../linux-6.12 \
		--patch $(BUILD_DIR)/linux-checkpoints.patch --branch dev \
		--patch-base-commit d0fef99b651d141dd6ffbbddeb8b729b2f8faaff \
		--integrated-commit 2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f

difftest:
	../tools/bin/checkpoint-runner --repository .. --sibling ../../linux-6.12 \
		--diff-sv57 --build-sibling

checkpoint-diff-sv57: difftest

test: build fmt clippy test-empty-elision test-checkpoints

test-empty-elision:
	$(MAKE) --no-print-directory CHECKPOINT_HANDLER=empty build
	@! $(NM) $(ELF) | grep -E 'lkm_checkpoint_' || { echo "error: empty checkpoint symbols remain" >&2; exit 1; }
	@! $(OBJDUMP) -d $(ELF) | grep -E 'lkm_checkpoint_' || { echo "error: empty checkpoint calls remain" >&2; exit 1; }
	@! strings $(ELF) | grep -F 'LKMCP1' || { echo "error: empty checkpoint output remains" >&2; exit 1; }

test-checkpoints:
	../tools/bin/checkpoint-runner --repository .. --self all

fmt:
	$(RUSTFMT) --edition 2024 --check $(RUST_SOURCES)

clippy: $(CHECKPOINTS_RS) | $(BUILD_STAMP)
	$(CLIPPY) $(RUSTC_FLAGS) -D warnings -o $(CLIPPY_ELF) main.rs

$(ELF): $(RUST_SOURCES) $(CHECKPOINTS_RS) $(LINKER_SCRIPT) | $(BUILD_STAMP)
	$(RUSTC) $(RUSTC_FLAGS) -o $@ main.rs

$(MODEL_IR): $(MODEL_SOURCES)
	$(MAKE) --no-print-directory -C ../tools build

$(CHECKPOINTS_RS) $(CHECKPOINT_MANIFEST) &: FORCE $(MODEL_IR) $(CHECKPOINT_MAPPING) $(CHECKPOINTGEN) $(shell find ../tools/src/checkpointgen -type f -name '*.py') | $(BUILD_STAMP)
	$(CHECKPOINTGEN) --model-ir $(MODEL_IR) --mapping $(CHECKPOINT_MAPPING) \
		--handler $(CHECKPOINT_HANDLER) --rust-output $(CHECKPOINTS_RS) \
		--manifest-output $(CHECKPOINT_MANIFEST)

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
	@echo "  checkpoint-sibling-apply Apply the reviewed patch or recognize its integrated commit"
	@echo "  difftest Build sibling and run the strict Sv57 differential"
	@echo "  checkpoint-diff-sv57 Compatibility alias for difftest"
	@echo "  test   Build and run formatting and Clippy checks"
	@echo "  test-checkpoints Run Sv57/Sv48/Sv39 lkm2 checkpoint self-tests"
	@echo "  test-empty-elision Verify the optimized empty handler leaves no calls or output"
	@echo "  fmt    Check Rust source formatting"
	@echo "  clippy Check Rust source lints"
	@echo "  run    Run the raw image on QEMU virt/OpenSBI (exit with Ctrl-a x)"
	@echo "  clean  Remove impl/build"
	@echo "  CHECKPOINT_HANDLER=empty|debugcon (default: empty)"
	@echo "  help   Show this help"

FORCE:
