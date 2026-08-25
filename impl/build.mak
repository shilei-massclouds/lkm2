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
CHECKPOINT_MAPPING := ../tools/checkpoints/vm.json
CHECKPOINTGEN := ../tools/bin/checkpointgen
MODEL_IR := ../tools/build/modelc/model.ir.json
MODEL_SOURCES := $(shell find ../model -type f -name '*.spec')
CHECKPOINT_HANDLER ?= empty
RUST_SOURCES := main.rs systems.rs systems/kernel.rs systems/kernel/config.rs objects.rs objects/cpu.rs objects/ptrace.rs objects/task.rs objects/vm.rs phases.rs phases/arch_head.rs phases/asm_macros.rs phases/csr.rs phases/start_kernel.rs
LINKER_SCRIPT := systems/kernel/linker.ld

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
