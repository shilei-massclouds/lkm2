TARGET ?= riscv64imac-unknown-none-elf
RUSTC ?= rustc
RUSTFMT ?= rustfmt
CLIPPY ?= clippy-driver
OBJCOPY ?= rust-objcopy
OBJDUMP ?= rust-objdump
NM ?= rust-nm

BUILD_DIR := build
BUILD_STAMP := $(BUILD_DIR)/.stamp
ELF := $(BUILD_DIR)/lkm2.elf
CLIPPY_ELF := $(BUILD_DIR)/lkm2.clippy.elf
IMAGE := $(BUILD_DIR)/lkm2.bin
CHECKPOINTS_RS := $(BUILD_DIR)/checkpoints.rs
CHECKPOINT_MANIFEST := $(BUILD_DIR)/checkpoints.manifest.json
SWAPPER_CHECKPOINTS_RS := $(BUILD_DIR)/swapper_checkpoints.rs
SWAPPER_CHECKPOINT_MANIFEST := $(BUILD_DIR)/swapper_checkpoints.manifest.json
SWAPPER_CONTENT_CHECKPOINTS_RS := $(BUILD_DIR)/swapper_content_checkpoints.rs
SWAPPER_CONTENT_CHECKPOINT_MANIFEST := $(BUILD_DIR)/swapper_content_checkpoints.manifest.json
MEMBLOCK_CHECKPOINTS_RS := $(BUILD_DIR)/memblock_checkpoints.rs
MEMBLOCK_CHECKPOINT_MANIFEST := $(BUILD_DIR)/memblock_checkpoints.manifest.json
CHECKPOINT_MAPPING := ../tools/checkpoints/vm.json
SWAPPER_CHECKPOINT_MAPPING := ../tools/checkpoints/swapper.json
SWAPPER_CONTENT_CHECKPOINT_MAPPING := ../tools/checkpoints/swapper-content.json
MEMBLOCK_CHECKPOINT_MAPPING := ../tools/checkpoints/memblock.json
CHECKPOINTGEN := ../tools/bin/checkpointgen
MODEL_IR := ../tools/build/modelc/model.ir.json
MODEL_SOURCES := $(shell find ../model -type f -name '*.spec')
CHECKPOINT_HANDLER ?= empty
RUST_SOURCES := main.rs systems.rs systems/kernel.rs systems/kernel/config.rs systems/sbi.rs objects.rs objects/cpu.rs objects/dtb_blob.rs objects/early_console.rs objects/memblock.rs objects/printk.rs objects/ptrace.rs objects/task.rs objects/vm.rs phases.rs phases/arch_head.rs phases/asm_macros.rs phases/csr.rs phases/start_kernel.rs
LINKER_SCRIPT := systems/kernel/linker.ld
DTB_BLOB_TEST := $(BUILD_DIR)/dtb_blob_test
MEMBLOCK_TEST := $(BUILD_DIR)/memblock_test
EARLY_CONSOLE_TEST := $(BUILD_DIR)/early_console_test
SBI_TEST := $(BUILD_DIR)/sbi_test
PRINTK_TEST := $(BUILD_DIR)/printk_test

$(DTB_BLOB_TEST): objects/dtb_blob.rs | $(BUILD_STAMP)
	$(RUSTC) --edition=2024 --test -o $@ $<

$(MEMBLOCK_TEST): tests/memblock_host.rs objects/dtb_blob.rs objects/memblock.rs | $(BUILD_STAMP)
	$(RUSTC) --edition=2024 --test -o $@ $<

$(EARLY_CONSOLE_TEST): objects/early_console.rs | $(BUILD_STAMP)
	$(RUSTC) --edition=2024 --test -o $@ $<

$(SBI_TEST): systems/sbi.rs | $(BUILD_STAMP)
	$(RUSTC) --edition=2024 --test -o $@ $<

$(PRINTK_TEST): objects/printk.rs | $(BUILD_STAMP)
	$(RUSTC) --edition=2024 --test -o $@ $<

ifeq ($(filter $(CHECKPOINT_HANDLER),empty debugcon),)
$(error unknown CHECKPOINT_HANDLER '$(CHECKPOINT_HANDLER)'; expected empty or debugcon)
endif

RUSTC_FLAGS := \
	--edition=2024 \
	--crate-name=lkm2 \
	--crate-type=bin \
	--target=$(TARGET) \
	-C opt-level=2 \
	-C panic=abort \
	-C code-model=medium \
	-C relocation-model=static \
	-C linker=rust-lld \
	-C link-arg=-T$(LINKER_SCRIPT) \
	-C link-arg=--gc-sections \
	-C link-arg=--build-id=none
